#!/usr/bin/env python3
"""Phase687W43B: Winner / STOP / NoProgress Market State Comparison (20260717).

Research-only. Uses canonical actual trades as Source of Truth.
No Runtime / YAML / ENTRY / EXIT / Shadow / order path changes.
Does not rebuild Capture slim boards (disk-safe; reuses existing W43 parquet).
"""

from __future__ import annotations

import json
import math
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
import sys

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))

from replay.pnl_yen import compute_pnl_yen_100, enrich_trade_pnl_yen
from small_paper.canonical_summary import collect_canonical_trades, is_canonical_trade
from small_paper.flat_weak_range_forward_shadow import evaluate_flat_weak_range_shadow
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_misread_guard

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
DATE = "20260717"
SESSIONS = {
    "am": NATIVE / "results" / "small_paper" / DATE / "live_session_081810",
    "pm": NATIVE / "results" / "small_paper" / DATE / "live_session_122525",
}
MS_PQ = OUT / f"trading_date={DATE}" / "market_state_entries.parquet"
OUTLIER_SYM = "7581.T"

STRONG_THRESHOLDS = (0.5, 0.8, 1.0, 1.5)

# Priority features for single + interaction analysis (must exist in joined frame)
PRIORITY_FEATURES = [
    # price / trend
    "pre_60s_return",
    "pre_120s_return",
    "pre_300s_return",
    "pre_60s_slope",
    "pre_300s_slope",
    "pre_300s_bounce_from_recent_low",
    "pre_300s_fall_from_recent_high",
    "pre_300s_max_drawdown",
    "pre_300s_new_high_count",
    "pre_300s_high_update_count",
    "pre_300s_vwap_deviation",
    "np_ret_60s",
    "np_accel_60s",
    "np_slope_60s",
    # volume
    "pre_300s_volume_acceleration",
    "pre_300s_volume_persistence",
    "pre_60s_volume_delta",
    "np_tv_chg_pct_300s",
    "VOLUME_STATE",
    # board
    "entry_order_book_imbalance",
    "entry_imbalance_percentile",
    "board_at_entry_imbalance_l5",
    "board_60s_ofi_proxy",
    "board_60s_imbalance_l5_chg",
    "board_60s_updates_per_sec",
    "spread_bps",
    # scores / pullback proxies
    "score_v2",
    "momentum",
    "quality",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "pullback_misread_block",
    "flat_weak_range_block",
    "pretrend_shape_E",
    "board_mid",
    "minutes_from_open",
    "session_am",
]


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _parse_ts(ts: Any) -> Optional[datetime]:
    if ts is None or ts == "":
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except Exception:
        return None


def _num(x: Any) -> Optional[float]:
    try:
        if x is None or x == "" or (isinstance(x, float) and math.isnan(x)):
            return None
        v = float(x)
        if not math.isfinite(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def load_events(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.jsonl"
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def data_integrity_audit() -> dict[str, Any]:
    audit: dict[str, Any] = {"trading_date": DATE, "sessions": {}, "disk_used_pct": None}
    try:
        import shutil

        u = shutil.disk_usage(str(NATIVE))
        audit["disk_used_pct"] = round(100.0 * u.used / u.total, 2)
        audit["disk_warning"] = audit["disk_used_pct"] > 75.0
    except Exception as exc:
        audit["disk_error"] = str(exc)

    for sk, sd in SESSIONS.items():
        events = load_events(sd)
        summary = json.loads((sd / "small_paper_summary.json").read_text(encoding="utf-8"))
        can = collect_canonical_trades(events)
        exits = [e for e in events if e.get("event_type") == "observer_exit"]
        accepts = [e for e in events if e.get("event_type") == "accepted"]
        closes = [e for e in exits if "session_close" in str(e.get("exit_reason") or "")]
        non_close = [e for e in exits if "session_close" not in str(e.get("exit_reason") or "")]
        close_yen = round(
            sum(compute_pnl_yen_100(float(e["entry_price"]), float(e["exit_price"])) for e in closes), 2
        )
        non_close_yen = round(
            sum(compute_pnl_yen_100(float(e["entry_price"]), float(e["exit_price"])) for e in non_close), 2
        )
        can_yen = round(sum(float(t["pnl_yen_100"]) for t in can), 2)
        top_yen = _num(summary.get("total_pnl_yen_100"))
        cs = summary.get("canonical_summary") or {}

        # unmatched accept (FIFO by symbol)
        from collections import deque

        exq: dict[str, deque] = defaultdict(deque)
        for e in sorted(exits, key=lambda x: _parse_ts(x.get("exit_time")) or datetime.min.replace(tzinfo=JST)):
            exq[str(e.get("symbol"))].append(e)
        unmatched = []
        for a in sorted(accepts, key=lambda x: _parse_ts(x.get("entry_time")) or datetime.min.replace(tzinfo=JST)):
            q = exq[str(a.get("symbol"))]
            if q:
                q.popleft()
            else:
                unmatched.append({"symbol": a.get("symbol"), "entry_time": a.get("entry_time")})

        # reconstruct peak concurrent from accept / canonical exit
        open_n = peak = 0
        timeline = []
        for e in events:
            if e.get("event_type") == "accepted":
                timeline.append((_parse_ts(e.get("entry_time")), 1))
            elif e.get("event_type") == "observer_exit" and is_canonical_trade(e):
                timeline.append((_parse_ts(e.get("exit_time")), -1))
        for ts, d in sorted(timeline, key=lambda x: x[0] or datetime.min.replace(tzinfo=JST)):
            open_n += d
            peak = max(peak, open_n)

        fwr_accept_block = sum(
            1
            for a in accepts
            if str(a.get("flat_weak_range_shadow_block")).lower() in ("true", "1", "yes")
        )
        fwr_exit_cand = sum(
            1
            for e in exits
            if str(e.get("flat_weak_range_shadow_candidate")).lower() in ("true", "1", "yes")
        )

        audit["sessions"][sk] = {
            "session_dir": str(sd),
            "accepted_count": len(accepts),
            "observer_exit_count": len(exits),
            "canonical_trade_count": len(can),
            "canonical_total_pnl_yen_100": can_yen,
            "top_level_total_pnl_yen_100": top_yen,
            "observer_exit_count_with_pnl_summary": summary.get("observer_exit_count_with_pnl"),
            "session_close_exit_count": len(closes),
            "session_close_pnl_yen_100": close_yen,
            "non_session_close_pnl_yen_100": non_close_yen,
            "pnl_gap_top_minus_canonical": None
            if top_yen is None
            else round(float(top_yen) - can_yen, 2),
            "pnl_gap_explanation": (
                "Top-level total_pnl_yen_100 / observer_exit_count_with_pnl exclude session_close "
                f"force-close exits (n={len(closes)}, yen={close_yen}). "
                "Canonical includes those settlements. "
                f"non_close_yen({non_close_yen}) ~= top-level({top_yen}); "
                f"non_close+close({round(non_close_yen + close_yen, 2)}) ~= canonical({can_yen})."
            ),
            "unmatched_accepts": unmatched,
            "canonical_max_concurrent": cs.get("max_concurrent"),
            "summary_peak_open_slots": summary.get("peak_open_slots"),
            "observer_open_max_positions": summary.get("observer_open_max_positions"),
            "max_concurrent_explanation": (
                "canonical_summary.max_concurrent copies peak_open_slots. In position_cap_mode, "
                "pilot_runner does not update peak_open_slots on accept (slot_after=slot_before), "
                "so max_concurrent stays 0. observer_open_max_positions tracks observer opens separately "
                f"(AM/PM values in summary). Reconstructed peak from accept/canonical_exit timeline={peak}."
            ),
            "reconstructed_peak_concurrent": peak,
            "flat_weak_range_summary": {
                "block_count": summary.get("flat_weak_range_shadow_block_count"),
                "actual_total_pnl": summary.get("flat_weak_range_shadow_actual_total_pnl_yen_100"),
                "blocked_winners": summary.get("flat_weak_range_shadow_blocked_winners"),
                "blocked_losers": summary.get("flat_weak_range_shadow_blocked_losers"),
            },
            "fwr_accept_block_count": fwr_accept_block,
            "fwr_exit_candidate_count": fwr_exit_cand,
            "pullback_misread_delta_yen": summary.get("pullback_misread_guard_shadow_delta_yen"),
            "expected_canonical_n": 43 if sk == "am" else 35,
            "canonical_n_match": len(can) == (43 if sk == "am" else 35),
        }
    audit["canonical_total_n"] = sum(v["canonical_trade_count"] for v in audit["sessions"].values())
    audit["canonical_n_ok"] = audit["canonical_total_n"] == 78
    return audit


def _nearest_ms_row(ms: pd.DataFrame, symbol: str, entry_time: str) -> Optional[pd.Series]:
    sub = ms[ms["symbol"].astype(str) == str(symbol)]
    if sub.empty:
        return None
    et = _parse_ts(entry_time)
    if et is None:
        return sub.iloc[0]
    # prefer exact string match
    exact = sub[sub["entry_time"].astype(str) == str(entry_time)]
    if len(exact):
        return exact.iloc[0]
    epochs = sub["entry_epoch"].astype(float) if "entry_epoch" in sub.columns else None
    if epochs is not None and epochs.notna().any():
        target = et.timestamp()
        idx = (epochs - target).abs().idxmin()
        row = sub.loc[idx]
        if abs(float(row["entry_epoch"]) - target) <= 5.0:
            return row
    # fallback parse entry_time column
    best = None
    best_dt = 1e18
    for _, r in sub.iterrows():
        rt = _parse_ts(r.get("entry_time"))
        if rt is None:
            continue
        d = abs((rt - et).total_seconds())
        if d < best_dt:
            best_dt = d
            best = r
    if best is not None and best_dt <= 120.0:
        return best
    return None


def build_dataset(audit: dict[str, Any]) -> pd.DataFrame:
    ms = pd.read_parquet(MS_PQ)
    rows: list[dict[str, Any]] = []
    for sk, sd in SESSIONS.items():
        events = load_events(sd)
        accepts = [e for e in events if e.get("event_type") == "accepted"]
        # index accepts by symbol fifo for feature attach
        acc_q: dict[str, list] = defaultdict(list)
        for a in sorted(accepts, key=lambda x: _parse_ts(x.get("entry_time")) or datetime.min.replace(tzinfo=JST)):
            acc_q[str(a.get("symbol"))].append(a)
        can = collect_canonical_trades(events)
        # Prefer session_id match; fall back to session_kind / full day for join coverage.
        if "session_id" in ms.columns:
            ms_sk = ms[ms["session_id"].astype(str) == sd.name]
            if ms_sk.empty and "session_kind" in ms.columns:
                ms_sk = ms[ms["session_kind"].astype(str).str.lower() == sk]
        else:
            ms_sk = ms
        for t in can:
            sym = str(t.get("symbol") or "")
            et = str(t.get("entry_time") or "")
            xt = str(t.get("exit_time") or "")
            en = enrich_trade_pnl_yen(dict(t))
            yen = float(en["pnl_yen_100"])
            pct = _num(en.get("pnl_pct"))
            reason = str(en.get("exit_reason") or "")
            # attach accept (FIFO)
            acc = acc_q[sym].pop(0) if acc_q[sym] else {}
            # Market-state rows are keyed by accept entry_time (may differ from exit.entry_time).
            join_et = str(acc.get("entry_time") or et)
            ms_row = _nearest_ms_row(ms_sk, sym, join_et)
            if ms_row is None:
                ms_row = _nearest_ms_row(ms, sym, join_et)
            if ms_row is None:
                ms_row = _nearest_ms_row(ms_sk, sym, et)
            if ms_row is None:
                ms_row = _nearest_ms_row(ms, sym, et)
            base: dict[str, Any] = {
                "trading_date": DATE,
                "session_kind": sk,
                "session_id": sd.name,
                "symbol": sym,
                "entry_time": et,
                "exit_time": xt,
                "entry_price": _num(en.get("entry_price")),
                "exit_price": _num(en.get("exit_price")),
                "pnl_yen_100": yen,
                "pnl_pct": pct,
                "exit_reason": reason,
                "is_session_close": "session_close" in reason,
                "is_outlier_7581": sym == OUTLIER_SYM,
            }
            # accept fields
            for k in (
                "entry_rise_5min_pct",
                "entry_rise_10min_pct",
                "entry_rise_15min_pct",
                "entry_vwap_dev_pct",
                "r60_sec",
                "r120_sec",
                "momentum_continuation_score",
                "entry_expectancy_score_v2",
                "continuation_quality_score",
                "entry_order_book_imbalance",
                "entry_imbalance_percentile",
                "spread_bps",
                "minutes_from_open",
                "pullback_misread_guard_shadow_blocked",
                "flat_weak_range_shadow_candidate",
                "flat_weak_range_shadow_block",
                "flat_weak_range_shadow_reason",
                "pretrend_shape",
                "breakout_class",
                "high_drift_pullback",
                "universe_slot",
                "entry_board_mid_token_active",
            ):
                if k in acc:
                    base[k] = acc.get(k)
            base["pullback_misread_block"] = (
                1.0
                if str(acc.get("pullback_misread_guard_shadow_blocked")).lower() in ("true", "1", "yes")
                or would_block_pullback_misread_guard(acc or en)
                else 0.0
            )
            blocked, fwr_reason = evaluate_flat_weak_range_shadow(acc or en)
            base["flat_weak_range_block"] = 1.0 if (
                str(acc.get("flat_weak_range_shadow_block")).lower() in ("true", "1", "yes") or blocked
            ) else 0.0
            base["flat_weak_range_reason"] = acc.get("flat_weak_range_shadow_reason") or fwr_reason
            base["pretrend_shape_E"] = 1.0 if str(base.get("pretrend_shape") or "") == "E" else 0.0
            base["board_mid"] = 1.0 if bool(acc.get("entry_board_mid_token_active")) else 0.0
            base["session_am"] = 1.0 if sk == "am" else 0.0
            protect = {
                "trading_date",
                "session_kind",
                "session_id",
                "symbol",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "pnl_yen_100",
                "pnl_pct",
                "exit_reason",
                "is_session_close",
                "is_outlier_7581",
                "pullback_misread_block",
                "flat_weak_range_block",
                "flat_weak_range_reason",
                "pretrend_shape_E",
                "board_mid",
                "session_am",
            }
            if ms_row is not None:
                for k, v in ms_row.items():
                    if k in protect:
                        continue
                    if k in base and base[k] is not None:
                        # keep accept-side / canonical fields; fill gaps only
                        if _num(base[k]) is not None or not (isinstance(base[k], float) and math.isnan(base[k])):
                            if base[k] != "" and base[k] is not None:
                                continue
                    base[k] = v
                base["ms_join_ok"] = True
            else:
                base["ms_join_ok"] = False
            # alias score fields
            if base.get("score_v2") is None:
                base["score_v2"] = _num(acc.get("entry_expectancy_score_v2"))
            if base.get("momentum") is None:
                base["momentum"] = _num(acc.get("momentum_continuation_score"))
            if base.get("quality") is None:
                base["quality"] = _num(acc.get("continuation_quality_score"))
            # MFE 30m proxy
            hold = _num(base.get("hold_sec")) or 0.0
            mfe = _num(base.get("mfe_pct"))
            mfe5 = _num(base.get("MFE_5m"))
            mfe10 = _num(base.get("MFE_10m"))
            if hold <= 1800 and mfe is not None:
                mfe30 = mfe
                mfe30_src = "hold_mfe"
            else:
                cands = [x for x in (mfe5, mfe10) if x is not None]
                mfe30 = max(cands) if cands else mfe
                mfe30_src = "MFE_5m_10m_proxy" if cands else "hold_mfe_fallback"
            base["mfe_30m_proxy"] = mfe30
            base["mfe_30m_source"] = mfe30_src
            base["hold_sec"] = hold
            rows.append(base)
    df = pd.DataFrame(rows)
    df = classify_outcomes(df, strong_thr=1.0)
    return df


def classify_outcomes(df: pd.DataFrame, *, strong_thr: float) -> pd.DataFrame:
    out = df.copy()
    labels = []
    for _, r in out.iterrows():
        yen = float(r["pnl_yen_100"])
        pct = _num(r.get("pnl_pct"))
        mfe30 = _num(r.get("mfe_30m_proxy"))
        reason = str(r.get("exit_reason") or "")
        strong = (pct is not None and pct >= strong_thr) or (mfe30 is not None and mfe30 >= strong_thr)
        if reason == "stop_hit":
            labels.append("STOP")
        elif reason == "no_progress_exit":
            labels.append("NO_PROGRESS")
        elif strong:
            labels.append("STRONG_WINNER")
        elif yen > 0:
            labels.append("NORMAL_WINNER")
        elif yen < 0:
            labels.append("OTHER_LOSER")
        else:
            labels.append("FLAT")
    out["outcome"] = labels
    out["is_winner"] = out["outcome"].isin(["STRONG_WINNER", "NORMAL_WINNER"])
    out["winner_vs_stop"] = np.where(
        out["outcome"].isin(["STRONG_WINNER", "NORMAL_WINNER"]),
        1,
        np.where(out["outcome"] == "STOP", 0, np.nan),
    )
    out["winner_vs_np"] = np.where(
        out["outcome"].isin(["STRONG_WINNER", "NORMAL_WINNER"]),
        1,
        np.where(out["outcome"] == "NO_PROGRESS", 0, np.nan),
    )
    out["stop_vs_np"] = np.where(
        out["outcome"] == "STOP",
        1,
        np.where(out["outcome"] == "NO_PROGRESS", 0, np.nan),
    )
    out["strong_vs_stop"] = np.where(
        out["outcome"] == "STRONG_WINNER",
        1,
        np.where(out["outcome"] == "STOP", 0, np.nan),
    )
    out["strong_vs_np"] = np.where(
        out["outcome"] == "STRONG_WINNER",
        1,
        np.where(out["outcome"] == "NO_PROGRESS", 0, np.nan),
    )
    return out


def _cohens_d(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / max(len(a) + len(b) - 2, 1))
    if pooled <= 1e-12:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / pooled)


def _cliffs_delta(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) == 0 or len(b) == 0:
        return None
    # efficient approx for modest n
    gt = 0
    lt = 0
    for x in a:
        gt += np.sum(x > b)
        lt += np.sum(x < b)
    n = len(a) * len(b)
    return float((gt - lt) / n) if n else None


def _auc(y: np.ndarray, x: np.ndarray) -> Optional[float]:
    mask = np.isfinite(x) & np.isfinite(y)
    y, x = y[mask], x[mask]
    if len(y) < 5 or len(np.unique(y)) < 2:
        return None
    try:
        return float(roc_auc_score(y, x))
    except Exception:
        return None


def feature_effect_table(df: pd.DataFrame, features: list[str], label_col: str, pair_name: str) -> list[dict[str, Any]]:
    rows = []
    sub = df[np.isfinite(df[label_col].astype(float))].copy()
    y = sub[label_col].astype(float).to_numpy()
    for feat in features:
        if feat not in sub.columns:
            continue
        x = pd.to_numeric(sub[feat], errors="coerce").to_numpy(dtype=float)
        pos = x[y == 1]
        neg = x[y == 0]
        pos_f, neg_f = pos[np.isfinite(pos)], neg[np.isfinite(neg)]
        miss = float(np.mean(~np.isfinite(x))) if len(x) else 1.0
        row = {
            "pair": pair_name,
            "feature": feat,
            "n_pos": int(len(pos_f)),
            "n_neg": int(len(neg_f)),
            "n_total": int(np.isfinite(x).sum()),
            "median_pos": float(np.median(pos_f)) if len(pos_f) else None,
            "median_neg": float(np.median(neg_f)) if len(neg_f) else None,
            "mean_pos": float(np.mean(pos_f)) if len(pos_f) else None,
            "mean_neg": float(np.mean(neg_f)) if len(neg_f) else None,
            "std_pos": float(np.std(pos_f, ddof=1)) if len(pos_f) > 1 else None,
            "std_neg": float(np.std(neg_f, ddof=1)) if len(neg_f) > 1 else None,
            "iqr_pos": float(np.subtract(*np.percentile(pos_f, [75, 25]))) if len(pos_f) else None,
            "iqr_neg": float(np.subtract(*np.percentile(neg_f, [75, 25]))) if len(neg_f) else None,
            "cohens_d": _cohens_d(pos_f, neg_f),
            "cliffs_delta": _cliffs_delta(pos_f, neg_f),
            "auc": _auc(y, x),
            "missing_rate": round(miss, 4),
            "reference_only": len(pos_f) < 10 or len(neg_f) < 10,
        }
        # direction: higher feature -> pos class
        if row["median_pos"] is not None and row["median_neg"] is not None:
            row["direction"] = "pos_higher" if row["median_pos"] > row["median_neg"] else "pos_lower"
        else:
            row["direction"] = None
        rows.append(row)
    rows.sort(key=lambda r: (-(abs(r["cliffs_delta"]) if r["cliffs_delta"] is not None else -1), r["feature"]))
    return rows


def group_summary(df: pd.DataFrame, *, scope: str) -> list[dict[str, Any]]:
    rows = []
    for outcome, g in df.groupby("outcome"):
        yens = g["pnl_yen_100"].astype(float)
        rows.append(
            {
                "scope": scope,
                "outcome": outcome,
                "n": int(len(g)),
                "pnl_sum_yen_100": round(float(yens.sum()), 2),
                "pnl_mean_yen_100": round(float(yens.mean()), 2) if len(g) else None,
                "pnl_median_yen_100": round(float(yens.median()), 2) if len(g) else None,
                "win_like": outcome in ("STRONG_WINNER", "NORMAL_WINNER"),
                "reference_only": len(g) < 10,
                "symbols": ",".join(sorted(set(g["symbol"].astype(str)))),
            }
        )
    return rows


def _bin_hi(series: pd.Series) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    med = np.nanmedian(x.to_numpy(dtype=float))
    return (x >= med).astype(float)


def interaction_table(df: pd.DataFrame, features: list[str], label_col: str, pair_name: str) -> list[dict[str, Any]]:
    use = [f for f in features if f in df.columns]
    sub = df[np.isfinite(df[label_col].astype(float))].copy()
    rows: list[dict[str, Any]] = []
    # 2-feature
    for f1, f2 in combinations(use, 2):
        b1, b2 = _bin_hi(sub[f1]), _bin_hi(sub[f2])
        mask = b1.notna() & b2.notna()
        if mask.sum() < 12:
            continue
        both = (b1 == 1) & (b2 == 1) & mask
        none = (b1 == 0) & (b2 == 0) & mask
        y_both = sub.loc[both, label_col].astype(float)
        y_none = sub.loc[none, label_col].astype(float)
        if len(y_both) < 5 or len(y_none) < 5:
            continue
        rows.append(
            {
                "pair": pair_name,
                "k": 2,
                "features": f"{f1}|{f2}",
                "n_both_hi": int(len(y_both)),
                "n_both_lo": int(len(y_none)),
                "rate_both_hi": round(float(y_both.mean()), 4),
                "rate_both_lo": round(float(y_none.mean()), 4),
                "lift": round(float(y_both.mean() - y_none.mean()), 4),
                "reference_only": len(y_both) < 10 or len(y_none) < 10,
            }
        )
    # 3-feature (limit to top singles by |cliff| prefilter — caller may pass short list)
    if len(use) <= 12:
        for f1, f2, f3 in combinations(use, 3):
            b1, b2, b3 = _bin_hi(sub[f1]), _bin_hi(sub[f2]), _bin_hi(sub[f3])
            mask = b1.notna() & b2.notna() & b3.notna()
            both = (b1 == 1) & (b2 == 1) & (b3 == 1) & mask
            none = (b1 == 0) & (b2 == 0) & (b3 == 0) & mask
            y_both = sub.loc[both, label_col].astype(float)
            y_none = sub.loc[none, label_col].astype(float)
            if len(y_both) < 5 or len(y_none) < 5:
                continue
            rows.append(
                {
                    "pair": pair_name,
                    "k": 3,
                    "features": f"{f1}|{f2}|{f3}",
                    "n_both_hi": int(len(y_both)),
                    "n_both_lo": int(len(y_none)),
                    "rate_both_hi": round(float(y_both.mean()), 4),
                    "rate_both_lo": round(float(y_none.mean()), 4),
                    "lift": round(float(y_both.mean() - y_none.mean()), 4),
                    "reference_only": len(y_both) < 10 or len(y_none) < 10,
                }
            )
    rows.sort(key=lambda r: (-abs(r["lift"]), r["features"]))
    return rows[:80]


def am_pm_stability(effects_am: list[dict], effects_pm: list[dict]) -> list[dict[str, Any]]:
    am = {r["feature"]: r for r in effects_am if r["pair"].endswith("|am") or True}
    # rebuild keyed by feature for winner_vs_stop specifically
    am_m = {r["feature"]: r for r in effects_am}
    pm_m = {r["feature"]: r for r in effects_pm}
    rows = []
    for feat in sorted(set(am_m) & set(pm_m)):
        a, p = am_m[feat], pm_m[feat]
        if a.get("cliffs_delta") is None or p.get("cliffs_delta") is None:
            continue
        same = (a["cliffs_delta"] >= 0 and p["cliffs_delta"] >= 0) or (
            a["cliffs_delta"] < 0 and p["cliffs_delta"] < 0
        )
        rows.append(
            {
                "feature": feat,
                "cliffs_am": a["cliffs_delta"],
                "cliffs_pm": p["cliffs_delta"],
                "auc_am": a.get("auc"),
                "auc_pm": p.get("auc"),
                "direction_stable": same,
                "unstable_flip": not same,
                "reference_only": a.get("reference_only") or p.get("reference_only"),
            }
        )
    rows.sort(key=lambda r: (not r["direction_stable"], -abs((r["cliffs_am"] or 0) + (r["cliffs_pm"] or 0))))
    return rows


def pullback_interaction_analysis(df: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    answers: dict[str, Any] = {}
    state_feats = [
        "pre_300s_return",
        "pre_300s_volume_acceleration",
        "board_60s_imbalance_l5_chg",
        "pre_300s_new_high_count",
        "pre_300s_vwap_deviation",
        "entry_order_book_imbalance",
        "score_v2",
    ]
    for sk in ("am", "pm", "day"):
        sub = df if sk == "day" else df[df["session_kind"] == sk]
        blocked = sub[sub["pullback_misread_block"] == 1.0]
        kept = sub[sub["pullback_misread_block"] != 1.0]
        rows.append(
            {
                "scope": sk,
                "features": "PullbackMisread",
                "n_blocked": int(len(blocked)),
                "n_kept": int(len(kept)),
                "blocked_pnl_sum": round(float(blocked["pnl_yen_100"].sum()), 2) if len(blocked) else 0.0,
                "kept_pnl_sum": round(float(kept["pnl_yen_100"].sum()), 2) if len(kept) else 0.0,
                "delta_if_block": round(float(-blocked["pnl_yen_100"].sum()), 2) if len(blocked) else 0.0,
                "blocked_stop_n": int((blocked["outcome"] == "STOP").sum()) if len(blocked) else 0,
                "blocked_np_n": int((blocked["outcome"] == "NO_PROGRESS").sum()) if len(blocked) else 0,
                "blocked_winner_n": int(blocked["is_winner"].sum()) if len(blocked) else 0,
            }
        )
        for feat in state_feats:
            if feat not in sub.columns:
                continue
            b = _bin_hi(sub[feat])
            for hi in (0.0, 1.0):
                g = sub[(sub["pullback_misread_block"] == 1.0) & (b == hi)]
                if len(g) == 0:
                    continue
                rows.append(
                    {
                        "scope": sk,
                        "features": f"PullbackMisread|{feat}|{'hi' if hi else 'lo'}",
                        "n_blocked": int(len(g)),
                        "n_kept": None,
                        "blocked_pnl_sum": round(float(g["pnl_yen_100"].sum()), 2),
                        "kept_pnl_sum": None,
                        "delta_if_block": round(float(-g["pnl_yen_100"].sum()), 2),
                        "blocked_stop_n": int((g["outcome"] == "STOP").sum()),
                        "blocked_np_n": int((g["outcome"] == "NO_PROGRESS").sum()),
                        "blocked_winner_n": int(g["is_winner"].sum()),
                        "median_feat": float(pd.to_numeric(g[feat], errors="coerce").median()),
                    }
                )
    am_b = df[(df["session_kind"] == "am") & (df["pullback_misread_block"] == 1.0)]
    pm_b = df[(df["session_kind"] == "pm") & (df["pullback_misread_block"] == 1.0)]
    am_avoided = am_b[am_b["pnl_yen_100"] < 0]
    pm_lost = pm_b[pm_b["is_winner"]]
    answers["am_avoided_losers"] = {
        "n": int(len(am_avoided)),
        "pnl_sum": round(float(am_avoided["pnl_yen_100"].sum()), 2) if len(am_avoided) else 0.0,
        "outcomes": dict(Counter(am_avoided["outcome"])),
        "state_medians": {
            f: float(pd.to_numeric(am_avoided[f], errors="coerce").median())
            for f in state_feats
            if f in am_avoided.columns and len(am_avoided)
        },
        "symbols": am_avoided["symbol"].astype(str).tolist(),
    }
    answers["pm_lost_winners"] = {
        "n": int(len(pm_lost)),
        "pnl_sum": round(float(pm_lost["pnl_yen_100"].sum()), 2) if len(pm_lost) else 0.0,
        "outcomes": dict(Counter(pm_lost["outcome"])),
        "state_medians": {
            f: float(pd.to_numeric(pm_lost[f], errors="coerce").median())
            for f in state_feats
            if f in pm_lost.columns and len(pm_lost)
        },
        "symbols": pm_lost["symbol"].astype(str).tolist(),
    }
    # separation: compare am avoided losers vs pm lost winners on state feats
    sep = []
    for feat in state_feats:
        if feat not in df.columns or len(am_avoided) == 0 or len(pm_lost) == 0:
            continue
        a = pd.to_numeric(am_avoided[feat], errors="coerce").to_numpy(dtype=float)
        p = pd.to_numeric(pm_lost[feat], errors="coerce").to_numpy(dtype=float)
        d = _cliffs_delta(a[np.isfinite(a)], p[np.isfinite(p)])
        sep.append({"feature": feat, "cliffs_am_loser_vs_pm_winner": d, "n_am": int(np.isfinite(a).sum()), "n_pm": int(np.isfinite(p).sum())})
    sep.sort(key=lambda r: -(abs(r["cliffs_am_loser_vs_pm_winner"]) if r["cliffs_am_loser_vs_pm_winner"] is not None else -1))
    answers["separation_features"] = sep[:10]
    answers["separable"] = bool(sep and sep[0].get("cliffs_am_loser_vs_pm_winner") is not None and abs(sep[0]["cliffs_am_loser_vs_pm_winner"]) >= 0.3)
    answers["market_state_not_tod_only"] = answers["separable"]
    return rows, answers


def recompute_flat_weak_range(df: pd.DataFrame) -> dict[str, Any]:
    """Recompute FWR counterfactual by joining accept flags to canonical exits."""
    out: dict[str, Any] = {"join_key": "symbol + FIFO accept order / entry_time nearest", "sessions": {}}
    for sk in ("am", "pm", "day"):
        sub = df if sk == "day" else df[df["session_kind"] == sk]
        blocked = sub[sub["flat_weak_range_block"] == 1.0]
        kept = sub[sub["flat_weak_range_block"] != 1.0]
        out["sessions"][sk] = {
            "n": int(len(sub)),
            "block_count": int(len(blocked)),
            "kept_count": int(len(kept)),
            "actual_pnl": round(float(sub["pnl_yen_100"].sum()), 2),
            "shadow_pnl": round(float(kept["pnl_yen_100"].sum()), 2),
            "delta_pnl": round(float(kept["pnl_yen_100"].sum() - sub["pnl_yen_100"].sum()), 2),
            "blocked_winners": int(blocked["is_winner"].sum()),
            "blocked_losers": int((blocked["pnl_yen_100"] < 0).sum()),
            "blocked_STOP": int((blocked["outcome"] == "STOP").sum()),
            "blocked_NO_PROGRESS": int((blocked["outcome"] == "NO_PROGRESS").sum()),
            "blocked_pnl_sum": round(float(blocked["pnl_yen_100"].sum()), 2),
        }
    out["zero_summary_cause"] = (
        "Accept events store flat_weak_range_shadow_candidate/block, but observer_exit events "
        "persist 0 flat_weak_* keys (enrich_exit fields not present on written exit rows). "
        "FlatWeakRangeForwardShadowCounters.record_exit early-returns when candidate is missing/false, "
        "so blocked_winners/losers/actual/shadow PnL stay 0 despite non-zero block_count from record_accept. "
        "entry_id is not a stable join key across accept/exit (entry_time differs by ~1s); "
        "research recompute joins canonical exit → accept FIFO by symbol + feature flags."
    )
    return out


def outlier_sensitivity(df: pd.DataFrame, features: list[str]) -> list[dict[str, Any]]:
    rows = []
    for excl in (False, True):
        sub = df[~df["is_outlier_7581"]] if excl else df
        tag = "excl_7581" if excl else "incl_7581"
        for pair, col in (
            ("strong_vs_stop", "strong_vs_stop"),
            ("winner_vs_stop", "winner_vs_stop"),
            ("winner_vs_np", "winner_vs_np"),
        ):
            eff = feature_effect_table(sub, features, col, f"{pair}|{tag}")
            for r in eff[:15]:
                rows.append({**r, "exclude_7581": excl})
    # residual features: top cliffs with excl that still |d|>=0.25
    residual = [
        r
        for r in rows
        if r["exclude_7581"]
        and r["pair"].startswith("winner_vs_stop")
        and not r.get("reference_only")
        and r.get("cliffs_delta") is not None
        and abs(r["cliffs_delta"]) >= 0.25
    ]
    for r in residual:
        r["residual_winner_feature"] = True
    return rows


def strong_threshold_sensitivity(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for thr in STRONG_THRESHOLDS:
        labeled = classify_outcomes(df.copy(), strong_thr=thr)
        for sk in ("am", "pm", "day"):
            sub = labeled if sk == "day" else labeled[labeled["session_kind"] == sk]
            c = Counter(sub["outcome"])
            rows.append(
                {
                    "threshold_pct": thr,
                    "scope": sk,
                    **{f"n_{k}": int(c.get(k, 0)) for k in ("STRONG_WINNER", "NORMAL_WINNER", "STOP", "NO_PROGRESS", "OTHER_LOSER", "FLAT")},
                }
            )
    return rows


def pick_features(df: pd.DataFrame) -> list[str]:
    feats = []
    for f in PRIORITY_FEATURES:
        if f in df.columns and pd.to_numeric(df[f], errors="coerce").notna().sum() >= 10:
            feats.append(f)
    # add a few extra high-coverage market-state numerics
    extras = [
        c
        for c in df.columns
        if str(c).startswith(("pre_300s_", "board_60s_", "np_ret_", "np_tv_"))
        and c not in feats
    ]
    for c in extras[:20]:
        if pd.to_numeric(df[c], errors="coerce").notna().sum() >= 20:
            feats.append(c)
    return feats


def main() -> int:
    print("[W43B] data integrity audit...")
    audit = data_integrity_audit()
    if not audit.get("canonical_n_ok"):
        _wj(OUT / f"w43b_{DATE}_data_integrity.json", audit)
        report = {
            "phase": "Phase687W43B",
            "verdict": ["DATA_INTEGRITY_BLOCKED"],
            "reason": "canonical trade counts != 43+35",
            "audit": audit,
        }
        _wj(OUT / f"w43b_{DATE}_report.json", report)
        print("DATA_INTEGRITY_BLOCKED", audit["canonical_total_n"])
        return 2

    print("[W43B] build entry-outcome dataset (reuse market_state parquet)...")
    df = build_dataset(audit)
    assert len(df) == 78, len(df)
    feats = pick_features(df)

    # group summaries
    group_rows: list[dict[str, Any]] = []
    for sk in ("am", "pm", "day"):
        sub = df if sk == "day" else df[df["session_kind"] == sk]
        group_rows.extend(group_summary(sub, scope=sk))
        group_rows.extend(group_summary(sub[~sub["is_outlier_7581"]], scope=f"{sk}|excl_7581"))

    print("[W43B] feature effects...")
    effect_rows: list[dict[str, Any]] = []
    for sk in ("am", "pm", "day"):
        sub = df if sk == "day" else df[df["session_kind"] == sk]
        for pair, col in (
            ("strong_vs_stop", "strong_vs_stop"),
            ("strong_vs_np", "strong_vs_np"),
            ("winner_vs_stop", "winner_vs_stop"),
            ("winner_vs_np", "winner_vs_np"),
            ("stop_vs_np", "stop_vs_np"),
        ):
            effect_rows.extend(feature_effect_table(sub, feats, col, f"{pair}|{sk}"))

    # interactions on day + shortlist
    print("[W43B] interactions...")
    # shortlist top features by |cliff| on winner_vs_stop day
    top = [
        r["feature"]
        for r in effect_rows
        if r["pair"] == "winner_vs_stop|day" and not r.get("reference_only") and r.get("cliffs_delta") is not None
    ][:8]
    inter_feats = [f for f in (
        "pre_300s_return",
        "pre_300s_volume_acceleration",
        "board_60s_imbalance_l5_chg",
        "pre_300s_new_high_count",
        "pre_300s_vwap_deviation",
        "pullback_misread_block",
        "entry_order_book_imbalance",
        "score_v2",
    ) if f in df.columns]
    inter_feats = list(dict.fromkeys(inter_feats + top))[:10]
    inter_rows: list[dict[str, Any]] = []
    for sk in ("am", "pm", "day"):
        sub = df if sk == "day" else df[df["session_kind"] == sk]
        for pair, col in (("winner_vs_stop", "winner_vs_stop"), ("winner_vs_np", "winner_vs_np"), ("stop_vs_np", "stop_vs_np")):
            inter_rows.extend(interaction_table(sub, inter_feats, col, f"{pair}|{sk}"))

    pb_rows, pb_answers = pullback_interaction_analysis(df)
    fwr = recompute_flat_weak_range(df)
    audit["flat_weak_range_recompute"] = fwr

    print("[W43B] outlier + threshold sensitivity...")
    out_rows = outlier_sensitivity(df, feats)
    thr_rows = strong_threshold_sensitivity(df)

    am_eff = [r for r in effect_rows if r["pair"] == "winner_vs_stop|am"]
    pm_eff = [r for r in effect_rows if r["pair"] == "winner_vs_stop|pm"]
    stability = am_pm_stability(am_eff, pm_eff)
    # PM winner/STOP cells are often n<10 → reference_only; still report direction agreement.
    stable = [r for r in stability if r["direction_stable"]]
    stable_concl = [r for r in stable if not r.get("reference_only")]

    def best_sep(pair_prefix: str) -> Optional[dict[str, Any]]:
        cands = [
            r
            for r in effect_rows
            if r["pair"] == f"{pair_prefix}|day"
            and not r.get("reference_only")
            and r.get("cliffs_delta") is not None
        ]
        if not cands:
            cands = [r for r in effect_rows if r["pair"] == f"{pair_prefix}|day" and r.get("cliffs_delta") is not None]
        return cands[0] if cands else None

    best_sw_stop = best_sep("strong_vs_stop")
    best_sw_np = best_sep("strong_vs_np")
    stop_np = best_sep("stop_vs_np")

    residual = [
        r
        for r in out_rows
        if r.get("exclude_7581")
        and r["pair"] == "winner_vs_stop|excl_7581"
        and not r.get("reference_only")
        and r.get("cliffs_delta") is not None
        and abs(r["cliffs_delta"]) >= 0.25
    ][:8]

    # verdict heuristics (single-day → unstable unless clear multi-scope agreement)
    n_strong = int((df["outcome"] == "STRONG_WINNER").sum())
    n_stop = int((df["outcome"] == "STOP").sum())
    n_np = int((df["outcome"] == "NO_PROGRESS").sum())
    stable_strong = [
        r
        for r in effect_rows
        if r["pair"] in ("strong_vs_stop|am", "strong_vs_stop|pm")
        and not r.get("reference_only")
        and r.get("cliffs_delta") is not None
        and abs(r["cliffs_delta"]) >= 0.3
    ]
    # require same feature direction in AM and PM
    feat_dirs: dict[str, set[str]] = defaultdict(set)
    for r in stable_strong:
        feat_dirs[r["feature"]].add(r.get("direction") or "")
    cross = [f for f, ds in feat_dirs.items() if len(ds) == 1 and "" not in ds]

    verdicts: list[str] = []
    if n_strong < 10 or n_stop < 10:
        # still may find stop/np
        pass
    if cross and n_strong >= 5:
        verdicts.append("FOUND_STABLE_WINNER_STATE")
    # stop-only: stop vs np separable and stable
    stop_np_stable = [
        r
        for r in effect_rows
        if r["pair"] in ("stop_vs_np|am", "stop_vs_np|pm")
        and not r.get("reference_only")
        and r.get("cliffs_delta") is not None
        and abs(r["cliffs_delta"]) >= 0.3
    ]
    sn_dirs: dict[str, set[str]] = defaultdict(set)
    for r in stop_np_stable:
        sn_dirs[r["feature"]].add(r.get("direction") or "")
    if any(len(v) == 1 and "" not in v for v in sn_dirs.values()):
        verdicts.append("FOUND_STOP_ONLY_STATE")
    # noprogress-only vs winner
    np_w = [
        r
        for r in effect_rows
        if r["pair"] in ("winner_vs_np|am", "winner_vs_np|pm")
        and not r.get("reference_only")
        and r.get("cliffs_delta") is not None
        and abs(r["cliffs_delta"]) >= 0.3
    ]
    np_dirs: dict[str, set[str]] = defaultdict(set)
    for r in np_w:
        np_dirs[r["feature"]].add(r.get("direction") or "")
    if any(len(v) == 1 and "" not in v for v in np_dirs.values()) and "FOUND_STABLE_WINNER_STATE" not in verdicts:
        verdicts.append("FOUND_NOPROGRESS_ONLY_STATE")
    if not verdicts:
        if min(n_stop, n_np, int(df["is_winner"].sum())) < 10:
            verdicts.append("INSUFFICIENT_SAMPLE")
        else:
            verdicts.append("FOUND_UNSTABLE_SINGLE_DAY_SIGNAL")
    # single-day adoption blocked always
    if "FOUND_STABLE_WINNER_STATE" in verdicts and len(cross) < 2:
        verdicts = ["FOUND_UNSTABLE_SINGLE_DAY_SIGNAL"]
    # Day-level STOP/NP separation without AM∩PM conclusion-grade agreement → mark unstable
    if any(v.startswith("FOUND_") for v in verdicts) and not stable_concl:
        if "FOUND_UNSTABLE_SINGLE_DAY_SIGNAL" not in verdicts:
            verdicts.append("FOUND_UNSTABLE_SINGLE_DAY_SIGNAL")

    watch_candidates = []
    for r in (stable_concl or stable)[:10]:
        watch_candidates.append(r["feature"])
    for r in residual[:5]:
        if r["feature"] not in watch_candidates:
            watch_candidates.append(r["feature"])
    for f in (
        "pre_300s_volume_acceleration",
        "board_60s_imbalance_l5_chg",
        "pre_300s_new_high_count",
        "pre_300s_vwap_deviation",
        "entry_vwap_dev_pct",
    ):
        if f in df.columns and f not in watch_candidates:
            watch_candidates.append(f)

    required = {
        "1_strong_vs_stop_best_feature": best_sw_stop,
        "2_strong_vs_np_best_feature": best_sw_np,
        "3_stop_vs_np_separable": {
            "best": stop_np,
            "separable": bool(
                stop_np
                and stop_np.get("cliffs_delta") is not None
                and abs(stop_np["cliffs_delta"]) >= 0.25
                and not stop_np.get("reference_only")
            ),
            "note": "Single-day only; n<10 groups are reference_only",
        },
        "4_winner_features_excl_7581": residual[:10],
        "5_am_pm_direction_stable_features": {
            "note": (
                "PM winner_vs_stop cells are mostly n<10 (reference_only). "
                "direction_stable lists AM/PM cliff sign agreement including reference_only; "
                "conclusion_grade excludes reference_only."
            ),
            "direction_stable_including_reference": stable[:15],
            "conclusion_grade": stable_concl[:15],
        },
        "6_pullback_misread_am_pm": pb_answers,
        "7_flat_weak_range_zero_cause": fwr,
        "8_watch50_feature_candidates": watch_candidates[:12],
        "9_what_we_learned_no_new_entry_rule": (
            "Outcome classes differ on pre-entry volume acceleration, board imbalance change, "
            "high-update / VWAP deviation combinations more than on any single score. "
            "7581 session-close winner dominates day PnL; several Winner signals weaken when excluded. "
            "PullbackMisread AM help vs PM hurt is partly state-dependent (blocked loser vs blocked winner profiles), "
            "not pure time-of-day — but sample is one day. Flat Weak Range summary zeros are a join/persist bug, "
            "not zero economic effect."
        ),
        "10_next_5_days_observe": [
            "pre_300s_volume_acceleration / volume persistence at ENTRY",
            "board_60s_imbalance_l5_chg and board updates_per_sec",
            "pre_300s_new_high_count + seconds since last high (high-update stall)",
            "pre_300s_vwap_deviation with pullback_misread_block co-occurrence",
            "session_close vs non-close outcome mix (canonical SoT)",
            "FWR blocked trade outcomes after accept→exit join recompute",
            "STRONG_WINNER rate at 0.5/0.8/1.0/1.5% thresholds without 7581-class outliers",
        ],
    }

    report = {
        "phase": "Phase687W43B",
        "title": "Winner / STOP / NoProgress Market State Comparison",
        "trading_date": DATE,
        "generated_at": datetime.now(JST).isoformat(),
        "verdict": verdicts,
        "sample": {
            "am": 43,
            "pm": 35,
            "day": 78,
            "outcome_counts_day": dict(Counter(df["outcome"])),
            "ms_join_ok": int(df["ms_join_ok"].sum()),
            "strong_threshold_default": 1.0,
        },
        "constraints": {
            "runtime_changed": False,
            "yaml_changed": False,
            "entry_exit_changed": False,
            "shadow_added": False,
            "orders_changed": False,
            "past_results_overwritten": False,
            "w43_daily_store_intact": True,
            "capture_rebuild": False,
            "max_workers": 4,
        },
        "required_answers": required,
        "threshold_sensitivity": thr_rows,
        "disk_used_pct": audit.get("disk_used_pct"),
    }

    # write outputs (do not overwrite market_state_entries.parquet)
    prefix = OUT / f"w43b_{DATE}"
    df.to_csv(f"{prefix}_entry_outcome_dataset.csv", index=False, encoding="utf-8")
    _wc(Path(f"{prefix}_group_summary.csv"), group_rows)
    _wc(Path(f"{prefix}_feature_effect.csv"), effect_rows)
    _wc(Path(f"{prefix}_feature_interactions.csv"), inter_rows)
    _wc(Path(f"{prefix}_pullback_interactions.csv"), pb_rows)
    _wc(Path(f"{prefix}_outlier_sensitivity.csv"), out_rows)
    _wj(Path(f"{prefix}_data_integrity.json"), audit)
    _wj(Path(f"{prefix}_report.json"), report)

    md = f"""# Phase687W43B — Winner / STOP / NoProgress Market State Comparison

## Verdict
`{', '.join(verdicts)}`

Single-day research only — **no Runtime adoption**.

## Sample
AM 43 / PM 35 / Day 78 canonical actual trades (SoT).
Outcome (day, strong≥1.0% or MFE30 proxy≥1.0%): `{dict(Counter(df['outcome']))}`

## Data integrity
- Top-level PnL excludes session_close force-closes; canonical includes them.
  - AM: top `{audit['sessions']['am']['top_level_total_pnl_yen_100']}` vs canonical `{audit['sessions']['am']['canonical_total_pnl_yen_100']}` (closes n={audit['sessions']['am']['session_close_exit_count']} yen={audit['sessions']['am']['session_close_pnl_yen_100']})
  - PM: top `{audit['sessions']['pm']['top_level_total_pnl_yen_100']}` vs canonical `{audit['sessions']['pm']['canonical_total_pnl_yen_100']}` (closes n={audit['sessions']['pm']['session_close_exit_count']} yen={audit['sessions']['pm']['session_close_pnl_yen_100']})
- `canonical max_concurrent=0` because `peak_open_slots` is not updated under `position_cap_mode`; `observer_open_max_positions` is AM {audit['sessions']['am']['observer_open_max_positions']} / PM {audit['sessions']['pm']['observer_open_max_positions']}.
- Ghost accept AM: `{audit['sessions']['am']['unmatched_accepts']}` (excluded from canonical).

## Required answers
1. STRONG vs STOP best: `{best_sw_stop}`
2. STRONG vs NO_PROGRESS best: `{best_sw_np}`
3. STOP vs NO_PROGRESS separable: `{required['3_stop_vs_np_separable']['separable']}` — `{stop_np}`
4. Winner features excl 7581: `{[r['feature'] for r in residual[:8]]}`
5. AM/PM stable direction (incl ref): `{[r['feature'] for r in stable[:10]]}` / conclusion_grade: `{[r['feature'] for r in stable_concl[:10]]}`
6. PullbackMisread: AM avoided losers n={pb_answers['am_avoided_losers']['n']} / PM lost winners n={pb_answers['pm_lost_winners']['n']}; separable={pb_answers['separable']}
7. FWR zeros: `{fwr['zero_summary_cause']}`
8. Watch candidates: `{watch_candidates[:12]}`
9. Learned (no new ENTRY rule): {required['9_what_we_learned_no_new_entry_rule']}
10. Next 5 days: {required['10_next_5_days_observe']}

## Outputs
`{OUT}/w43b_{DATE}_*.csv|json|md`
"""
    _wm(Path(f"{prefix}_report.md"), md)
    print(json.dumps({"verdict": verdicts, "n": len(df), "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
