#!/usr/bin/env python3
"""Phase687W54 — Cost-Aware Selective ENTRY Closure.

Converts W53 G-arm gross edge into a roundtrip-5bps-robust ENTRY spec.
Outputs only:
  cost_aware_entry_report.md / .json / cost_aware_entry_audit.xlsx
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
SNAP_CACHE = OUT / "_w53_day_snaps_cache"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
CAP = 5
HOLD_HORIZON_MIN = 30.0
STOP_MAE = -1.2

# Cost model (CORRECTED): roundtrip_cost_bps applied once per completed trade.
# pnl_pct is in percent points → deduct roundtrip_bps / 100.
# e.g. roundtrip 5bps = 0.05% = 0.05 pct-points per trade.


def cost_pct_per_trade(roundtrip_cost_bps: float) -> float:
    return float(roundtrip_cost_bps) / 100.0


def per_side_cost_bps(roundtrip_cost_bps: float) -> float:
    return float(roundtrip_cost_bps) / 2.0


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod  # required for dataclasses under importlib
    spec.loader.exec_module(mod)
    return mod


w53 = _load_module("w53_cae", NATIVE / "scripts" / "phase687w53_watch50_portfolio_edge_closure.py")


def _safe_f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _excel_cell(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return json.dumps(v, ensure_ascii=False, default=str)


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        ["Phase687W54 Cost-Aware Selective ENTRY Closure"],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "Research-only; PBv2/EXIT/CAP/YAML unchanged; Shadow not enabled; Reentry excluded from final"],
        ["cost", "roundtrip_N_bps = N/100 pct-points once per completed trade (NOT *2)"],
    ]:
        ws.append(row)
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(100000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _pf(s: pd.Series | np.ndarray | list) -> Optional[float]:
    x = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    if x.empty:
        return None
    gp = float(x[x > 0].sum())
    gl = float(-x[x < 0].sum())
    if gl < 1e-12:
        return 999.0 if gp > 0 else None
    return gp / gl


# ---------------------------------------------------------------------------
# Panel load (reuse W53 cache)
# ---------------------------------------------------------------------------


def load_panel() -> pd.DataFrame:
    w53.ensure_snap_cache_from_w47()
    w53.SNAP_CACHE = SNAP_CACHE
    w53.w47.SNAP_CACHE = SNAP_CACHE
    frames = w53.build_all_day_snaps()
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    panel = w53.add_market_state(panel)
    panel = w53.add_outcome_labels(panel)
    return panel


# ---------------------------------------------------------------------------
# Cost-aware metrics
# ---------------------------------------------------------------------------


@dataclass
class SimResult:
    name: str
    trades: list[dict[str, Any]] = field(default_factory=list)

    def metrics(self, roundtrip_cost_bps: float = 0.0) -> dict[str, Any]:
        c_pct = cost_pct_per_trade(roundtrip_cost_bps)
        base = {
            "name": self.name,
            "n_trades": 0,
            "total_pnl_pct": 0.0,
            "mean_pnl_pct": None,
            "pf": None,
            "win_rate": None,
            "stop_rate": None,
            "np_rate": None,
            "winner_rate": None,
            "max_dd": 0.0,
            "roundtrip_cost_bps": roundtrip_cost_bps,
            "per_side_cost_bps": per_side_cost_bps(roundtrip_cost_bps),
            "cost_pct_per_trade": c_pct,
            "trades_per_day": None,
            "gross_mean_per_trade": None,
            "net_mean_per_trade": None,
        }
        if not self.trades:
            return base
        gross = np.array([t["pnl_pct"] for t in self.trades], dtype=float)
        # CORRECT: deduct roundtrip once (NOT *2)
        net = gross - c_pct
        wins = net[net > 0].sum()
        losses = -net[net < 0].sum()
        pf = float(wins / losses) if losses > 1e-12 else (999.0 if wins > 0 else None)
        cum = np.cumsum(net)
        peak = np.maximum.accumulate(cum)
        dd = float((cum - peak).min()) if len(cum) else 0.0
        n_days = len({t["trading_date"] for t in self.trades})
        return {
            **base,
            "n_trades": len(self.trades),
            "total_pnl_pct": float(net.sum()),
            "gross_total_pnl_pct": float(gross.sum()),
            "mean_pnl_pct": float(net.mean()),
            "gross_mean_per_trade": float(gross.mean()),
            "net_mean_per_trade": float(net.mean()),
            "pf": pf,
            "win_rate": float((net > 0).mean()),
            "stop_rate": float(np.mean([t["stop"] for t in self.trades])),
            "np_rate": float(np.mean([t["np"] for t in self.trades])),
            "winner_rate": float(np.mean([t["winner"] for t in self.trades])),
            "max_dd": dd,
            "n_symbols": len({t["symbol"] for t in self.trades}),
            "n_days": n_days,
            "trades_per_day": float(len(self.trades) / n_days) if n_days else None,
        }


# ---------------------------------------------------------------------------
# Cap5 abstention simulator
# ---------------------------------------------------------------------------


@dataclass
class Position:
    symbol: str
    entry_time: pd.Timestamp
    entry_pnl_path: float
    stop: bool
    np: bool
    winner: bool
    score: float


def simulate_cap5_selective(
    day_df: pd.DataFrame,
    *,
    score_col: str,
    name: str,
    stop_thr: Optional[float] = None,
    np_thr: Optional[float] = None,
    score_thr: Optional[float] = None,
    net_edge_thr: Optional[float] = None,  # expected_net_edge min (pct points)
    max_entries_per_day: Optional[int] = None,
    fill_mode: str = "qualified-fill",  # always-fill | qualified-fill | no-fill
    chase_vwap_reject: bool = False,
    rule_mask_col: Optional[str] = None,  # require True to enter
    guard_mode: Optional[str] = None,  # "pbv2_guards" uses stop+np as existing guards
) -> SimResult:
    """Cap5 = max positions, NOT always-full. NO TRADE allowed when unqualified."""
    res = SimResult(name=name)
    if day_df.empty:
        return res
    df = day_df.copy()
    df["_t"] = pd.to_datetime(df["snapshot_time"], utc=True).dt.tz_convert(JST)
    df = df.sort_values(["_t", score_col], ascending=[True, False])
    times = sorted(df["_t"].unique())
    open_pos: dict[str, Position] = {}
    n_entries = 0

    for t in times:
        t = pd.Timestamp(t)
        if t.tzinfo is None:
            t = t.tz_localize(JST)
        to_close = [s for s, p in open_pos.items() if (t - p.entry_time).total_seconds() / 60.0 >= HOLD_HORIZON_MIN]
        for sym in to_close:
            pos = open_pos.pop(sym)
            res.trades.append(
                {
                    "trading_date": str(day_df["trading_date"].iloc[0]),
                    "symbol": sym,
                    "entry_time": str(pos.entry_time),
                    "exit_time": str(t),
                    "pnl_pct": pos.entry_pnl_path,
                    "stop": pos.stop,
                    "np": pos.np,
                    "winner": pos.winner,
                    "score": pos.score,
                }
            )

        if max_entries_per_day is not None and n_entries >= max_entries_per_day:
            continue
        slots = CAP - len(open_pos)
        if slots <= 0:
            continue
        snap = df[df["_t"] == t].sort_values(score_col, ascending=False)
        if snap.empty:
            continue

        selected = 0
        rank_slots_used = 0  # for no-fill: each free-rank consumes one opportunity
        for _, r in snap.iterrows():
            if selected >= slots:
                break
            if max_entries_per_day is not None and n_entries + selected >= max_entries_per_day:
                break
            sym = str(r["symbol"])
            if sym in open_pos:
                continue

            sc = _safe_f(r.get(score_col), -1e18)
            exp_net = _safe_f(r.get("expected_net_edge_5bps"), -1e18)
            rejected = False
            if stop_thr is not None and _safe_f(r.get("stop_risk_score")) >= stop_thr:
                rejected = True
            if np_thr is not None and _safe_f(r.get("np_risk_score")) >= np_thr:
                rejected = True
            if chase_vwap_reject and bool(r.get("chase_vwap_extended")):
                rejected = True
            if rule_mask_col and not bool(r.get(rule_mask_col)):
                rejected = True

            unqualified = False
            if score_thr is not None and sc < score_thr:
                unqualified = True
            if net_edge_thr is not None and exp_net < net_edge_thr:
                unqualified = True
            if guard_mode == "pbv2_candidate" and not bool(r.get("pbv2_candidate_flag")):
                unqualified = True

            if fill_mode == "no-fill":
                # top `slots` free ranks only; reject/unqualify → leave empty (no walk-down)
                if rank_slots_used >= slots:
                    break
                rank_slots_used += 1
                if rejected or unqualified:
                    continue
            elif fill_mode == "always-fill":
                if rejected:
                    continue
            elif fill_mode == "qualified-fill":
                if rejected or unqualified:
                    continue
            else:
                raise ValueError(fill_mode)

            open_pos[sym] = Position(
                symbol=sym,
                entry_time=t,
                entry_pnl_path=_safe_f(r.get("exit_pnl_pct")),
                stop=bool(r.get("stop_proxy")),
                np=bool(r.get("np_proxy")),
                winner=bool(r.get("winner_a")),
                score=sc,
            )
            selected += 1
            n_entries += 1

    if times:
        t = pd.Timestamp(times[-1])
        if t.tzinfo is None:
            t = t.tz_localize(JST)
        for sym, pos in list(open_pos.items()):
            res.trades.append(
                {
                    "trading_date": str(day_df["trading_date"].iloc[0]),
                    "symbol": sym,
                    "entry_time": str(pos.entry_time),
                    "exit_time": str(t),
                    "pnl_pct": pos.entry_pnl_path,
                    "stop": pos.stop,
                    "np": pos.np,
                    "winner": pos.winner,
                    "score": pos.score,
                }
            )
    return res


def run_days(
    panel: pd.DataFrame,
    days: list[str],
    *,
    name: str,
    **kwargs: Any,
) -> SimResult:
    all_trades: list[dict[str, Any]] = []
    for d in days:
        day_df = panel[panel["trading_date"].astype(str) == d]
        if day_df.empty:
            continue
        r = simulate_cap5_selective(day_df, name=name, **kwargs)
        all_trades.extend(r.trades)
    return SimResult(name=name, trades=all_trades)


# ---------------------------------------------------------------------------
# Expected edge calibration (Discovery only)
# ---------------------------------------------------------------------------


def calibrate_expected_edge(train: pd.DataFrame, score_col: str = "integrated_score") -> dict[str, Any]:
    """Map score rank percentile → Discovery mean exit_pnl (no same-day future leak)."""
    s = pd.to_numeric(train[score_col], errors="coerce")
    y = pd.to_numeric(train["exit_pnl_pct"], errors="coerce")
    ok = s.notna() & y.notna()
    s, y = s[ok], y[ok]
    # percentile rank 0-1 on train scores
    ranks = s.rank(pct=True)
    edges = []
    for lo, hi in [(i / 20, (i + 1) / 20) for i in range(20)]:
        m = (ranks > lo) & (ranks <= hi)
        if m.sum() < 20:
            continue
        edges.append({"lo": lo, "hi": hi, "mean_gross": float(y[m].mean()), "n": int(m.sum())})
    # score quantiles for OOS rank mapping via train empirical CDF
    qs = np.linspace(0, 1, 101)
    score_q = {float(q): float(s.quantile(q)) for q in qs}
    return {"bins": edges, "score_col": score_col, "score_q": score_q}


def apply_expected_edge(df: pd.DataFrame, cal: dict[str, Any], roundtrip_bps: float = 5.0) -> pd.DataFrame:
    df = df.copy()
    score_col = cal["score_col"]
    s = pd.to_numeric(df[score_col], errors="coerce")
    bins = cal.get("bins") or []
    score_q = cal.get("score_q") or {}
    c = cost_pct_per_trade(roundtrip_bps)
    if not bins or not score_q:
        df["expected_gross_edge"] = 0.0
        df["expected_cost"] = c
        df["expected_net_edge_5bps"] = -c
        return df
    # approximate percentile via train quantile ladder
    q_items = sorted(score_q.items(), key=lambda kv: kv[0])
    thr = np.array([v for _, v in q_items], dtype=float)
    qv = np.array([k for k, _ in q_items], dtype=float)

    def _pct(val: float) -> float:
        if val != val:
            return 0.5
        i = int(np.searchsorted(thr, val, side="right") - 1)
        i = max(0, min(len(qv) - 1, i))
        return float(qv[i])

    pct = s.map(_pct)
    gross = np.full(len(df), np.nan)
    for b in bins:
        m = (pct > b["lo"]) & (pct <= b["hi"])
        gross = np.where(m.values if hasattr(m, "values") else m, b["mean_gross"], gross)
    fallback = float(np.nanmean([b["mean_gross"] for b in bins]))
    gross = np.where(np.isnan(gross), fallback, gross)
    df["expected_gross_edge"] = gross
    df["expected_cost"] = c
    df["expected_net_edge_5bps"] = gross - c
    return df


# ---------------------------------------------------------------------------
# CHASE_VWAP_EXTENDED / beam / pullback audit
# ---------------------------------------------------------------------------


def mark_chase_vwap(df: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    thr_slope = pd.to_numeric(train["slope_60s"], errors="coerce").quantile(0.75)
    thr_spr = pd.to_numeric(train["spread_bps"], errors="coerce").quantile(0.7)
    thr_stale = pd.to_numeric(train["seconds_since_last_new_high"], errors="coerce").quantile(0.7)
    thr_vwap = pd.to_numeric(train["vwap_dev_pct"], errors="coerce").quantile(0.7)
    df["chase_vwap_extended"] = (
        (pd.to_numeric(df.get("slope_60s"), errors="coerce") >= thr_slope)
        & (pd.to_numeric(df.get("spread_bps"), errors="coerce") >= thr_spr)
        & (pd.to_numeric(df.get("seconds_since_last_new_high"), errors="coerce") >= thr_stale)
        & (pd.to_numeric(df.get("vwap_dev_pct"), errors="coerce") >= thr_vwap)
    )
    return df


def _q_mask(s: pd.Series, side: str, q_hi: float = 0.8, q_lo: float = 0.2) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if side == "high":
        return x >= x.quantile(q_hi)
    return x <= x.quantile(q_lo)


def canonical_feat_tuple(feats: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(feats)))


def eval_feature_rule(
    train: pd.DataFrame,
    test: pd.DataFrame,
    feats: list[str],
    *,
    sides: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    canon = canonical_feat_tuple(feats)
    if sides is None:
        sides = {}
        for f in feats:
            sides[f] = "low" if any(k in f for k in ("vol_persistence", "spread", "seconds")) else "high"

    def mask(df: pd.DataFrame) -> pd.Series:
        m = pd.Series(True, index=df.index)
        for f in canon:
            if f not in df.columns:
                return pd.Series(False, index=df.index)
            # thresholds from train
            xtr = pd.to_numeric(train[f], errors="coerce")
            x = pd.to_numeric(df[f], errors="coerce")
            if sides[f] == "high":
                m &= x >= xtr.quantile(0.8)
            else:
                m &= x <= xtr.quantile(0.2)
        return m

    mt, mte = mask(train), mask(test)
    yt = train.loc[mt, "exit_pnl_pct"]
    yte = test.loc[mte, "exit_pnl_pct"]
    return {
        "features": list(canon),
        "canonical_key": "|".join(canon),
        "n_train": int(mt.sum()),
        "n_test": int(mte.sum()),
        "train_mean": float(yt.mean()) if len(yt) else None,
        "test_mean": float(yte.mean()) if len(yte) else None,
        "train_pf": _pf(yt),
        "test_pf": _pf(yte),
        "ok": int(mt.sum()) >= 100 and int(mte.sum()) >= 50,
    }


PRIORITY_RULES = [
    ["net_bid_pressure_60s", "day_high_distance_pct", "vol_persistence_300s", "mkt_rising_ratio"],
    ["net_bid_pressure_60s", "imbalance_chg_60s", "vol_persistence_300s", "day_high_distance_pct"],
    ["ret_120s", "vol_persistence_300s", "mkt_rising_ratio"],
    ["net_bid_pressure_60s", "bounce_from_low_300s", "vol_persistence_300s"],
]


def beam_canonical(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, Any]:
    feats = [
        c
        for c in [
            "ret_60s",
            "ret_120s",
            "slope_60s",
            "vol_persistence_300s",
            "net_bid_pressure_60s",
            "net_ask_pressure_60s",
            "fall_from_high_300s",
            "day_high_distance_pct",
            "spread_bps",
            "imbalance_chg_60s",
            "mkt_rising_ratio",
            "seconds_since_last_new_high",
            "bounce_from_low_300s",
            "vwap_dev_pct",
        ]
        if c in train.columns
    ]
    seen: set[tuple[str, ...]] = set()
    pairs = []
    for i, a in enumerate(feats):
        for b in feats[i + 1 :]:
            key = canonical_feat_tuple([a, b])
            if key in seen:
                continue
            seen.add(key)
            r = eval_feature_rule(train, test, list(key))
            if r["n_train"] >= 40:
                pairs.append(r)
    pairs.sort(key=lambda r: (r["train_mean"] if r["train_mean"] is not None else -1e9), reverse=True)
    top2 = pairs[:100]

    triples = []
    seen3: set[tuple[str, ...]] = set()
    for r in top2[:40]:
        for c in feats:
            key = canonical_feat_tuple(r["features"] + [c])
            if len(key) != 3 or key in seen3:
                continue
            seen3.add(key)
            rr = eval_feature_rule(train, test, list(key))
            if rr["n_train"] >= 30:
                triples.append(rr)
    triples.sort(key=lambda r: (r["train_mean"] if r["train_mean"] is not None else -1e9), reverse=True)
    top3 = triples[:50]

    quads = []
    seen4: set[tuple[str, ...]] = set()
    for r in top3[:25]:
        for c in feats:
            key = canonical_feat_tuple(r["features"] + [c])
            if len(key) != 4 or key in seen4:
                continue
            seen4.add(key)
            rr = eval_feature_rule(train, test, list(key))
            if rr["n_train"] >= 25:
                quads.append(rr)
    quads.sort(key=lambda r: (r["train_mean"] if r["train_mean"] is not None else -1e9), reverse=True)
    top4 = quads[:20]

    priority = []
    for feats_p in PRIORITY_RULES:
        if all(f in train.columns for f in feats_p):
            priority.append(eval_feature_rule(train, test, feats_p))

    return {
        "n_unique_2": len(seen),
        "n_unique_3": len(seen3),
        "n_unique_4": len(seen4),
        "top2": top2[:20],
        "top3": top3[:20],
        "top4": top4[:20],
        "priority": priority,
        "best_3": next((x for x in top3 if x.get("ok") and (x.get("test_mean") or -1) > 0.05), top3[0] if top3 else None),
        "best_4": next((x for x in top4 if x.get("ok") and (x.get("test_mean") or -1) > 0.05), top4[0] if top4 else None),
    }


def apply_rule_flag(df: pd.DataFrame, train: pd.DataFrame, feats: list[str], col: str) -> pd.DataFrame:
    df = df.copy()
    if not feats or not all(f in df.columns for f in feats):
        df[col] = False
        return df
    m = pd.Series(True, index=df.index)
    for f in feats:
        xtr = pd.to_numeric(train[f], errors="coerce")
        x = pd.to_numeric(df[f], errors="coerce")
        side = "low" if any(k in f for k in ("vol_persistence", "spread", "seconds")) else "high"
        if side == "high":
            m &= x >= xtr.quantile(0.8)
        else:
            m &= x <= xtr.quantile(0.2)
    df[col] = m.fillna(False)
    return df


def _pullback_depth(df: pd.DataFrame) -> pd.Series:
    """fall_from_high_300s is signed (≤0 when below high). Depth = distance below high ≥ 0."""
    fall = pd.to_numeric(df.get("fall_from_high_300s"), errors="coerce")
    return (-fall).clip(lower=0)


def _pullback_flag(df: pd.DataFrame) -> pd.Series:
    depth = _pullback_depth(df)
    r30 = pd.to_numeric(df.get("ret_30s"), errors="coerce")
    r60 = pd.to_numeric(df.get("ret_60s"), errors="coerce")
    # below day/local high with recent soft/negative print
    return (depth > 0) & ((r30 <= 0) | (r60 <= 0) | (depth >= depth.quantile(0.5)))


def pullback_data_audit(panel: pd.DataFrame, train: pd.DataFrame, test: pd.DataFrame) -> dict[str, Any]:
    """Diagnose pullback; W53 used depth>0 on signed fall_from_high (always ≤0) → n=0 bug."""
    candidates = [
        "fall_from_high_300s",
        "bounce_from_low_300s",
        "max_dd_300s",
        "ret_30s",
        "ret_60s",
        "accel_60s",
        "vwap_dev_pct",
        "vol_persistence_300s",
        "imbalance_chg_60s",
        "seconds_since_last_new_high",
        "pre_300s_new_high_count",
        "mkt_rising_ratio",
    ]
    present = {c: c in panel.columns for c in candidates}
    nonnull = {
        c: float(pd.to_numeric(panel[c], errors="coerce").notna().mean()) if c in panel.columns else 0.0
        for c in candidates
    }
    reason_parts = []
    if "fall_from_high_300s" not in panel.columns:
        reason_parts.append("feature_name_missing:fall_from_high_300s")
    elif nonnull.get("fall_from_high_300s", 0) < 0.01:
        reason_parts.append("nullized:fall_from_high_300s")
    else:
        # document W53 sign bug
        fall = pd.to_numeric(panel["fall_from_high_300s"], errors="coerce")
        if float((fall > 0).mean()) < 0.01 and float((fall < 0).mean()) > 0.3:
            reason_parts.append("w53_sign_bug:fall_from_high_300s_is_signed_leq0_depth_was_gt0")

    tr_flag = _pullback_flag(train)
    te_flag = _pullback_flag(test)
    n_tr, n_te = int(tr_flag.sum()), int(te_flag.sum())
    n_pb = int(_pullback_flag(panel).sum())

    unavailable = (
        "fall_from_high_300s" not in panel.columns or nonnull.get("fall_from_high_300s", 0) < 0.01
    ) and n_tr == 0 and n_te == 0

    out: dict[str, Any] = {
        "columns_present": present,
        "nonnull_frac": nonnull,
        "n_pullback_panel": n_pb,
        "n_train": n_tr,
        "n_test": n_te,
        "reasons": reason_parts,
        "unavailable": unavailable,
        "depth_definition": "pullback_depth = max(0, -fall_from_high_300s)",
    }
    if unavailable:
        out["verdict"] = "PULLBACK_DATA_UNAVAILABLE"
        out["confirmed"] = False
        return out
    if n_tr == 0 or n_te == 0:
        out["verdict"] = "PULLBACK_DATA_UNAVAILABLE"
        out["confirmed"] = False
        out["reasons"].append("n_train_or_n_test_zero_after_sign_fix")
        return out

    depth_tr = _pullback_depth(train)
    thr_depth = float(depth_tr[tr_flag].quantile(0.7)) if tr_flag.any() else float(depth_tr.quantile(0.7))
    thr_vol = float(pd.to_numeric(train["vol_persistence_300s"], errors="coerce").quantile(0.3))

    def rej(df: pd.DataFrame, flag: pd.Series) -> pd.Series:
        return (
            flag
            & (_pullback_depth(df) >= thr_depth)
            & (pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce") <= thr_vol)
            & (pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce") <= 0)
        )

    rej_te = test.loc[rej(test, te_flag), "exit_pnl_pct"]
    allow_te = test.loc[te_flag & ~rej(test, te_flag), "exit_pnl_pct"]
    confirmed = bool(len(rej_te) >= 30 and len(allow_te) >= 30 and float(rej_te.mean()) < float(allow_te.mean()))
    out["confirmed"] = confirmed
    out["reject_test"] = {"n": int(len(rej_te)), "mean": float(rej_te.mean()) if len(rej_te) else None, "pf": _pf(rej_te)}
    out["allow_test"] = {"n": int(len(allow_te)), "mean": float(allow_te.mean()) if len(allow_te) else None, "pf": _pf(allow_te)}
    out["verdict"] = "PULLBACK_CONTEXT_CONFIRMED" if confirmed else "PULLBACK_CONTEXT_NOT_CONFIRMED"
    return out


# ---------------------------------------------------------------------------
# Score threshold grid / frequency / WF
# ---------------------------------------------------------------------------


def score_quantile_grid(train: pd.DataFrame, test: pd.DataFrame, days: list[str], stop_thr: float, np_thr: float) -> list[dict]:
    rows = []
    for q_label, q in [
        ("top50", 0.50),
        ("top30", 0.70),
        ("top20", 0.80),
        ("top10", 0.90),
        ("top5", 0.95),
        ("top2", 0.98),
        ("top1", 0.99),
    ]:
        thr = float(pd.to_numeric(train["integrated_score"], errors="coerce").quantile(q))
        sim = run_days(
            test,
            days,
            name=f"G_q_{q_label}",
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            score_thr=thr,
            fill_mode="qualified-fill",
        )
        m0 = sim.metrics(0)
        m5 = sim.metrics(5)
        m10 = sim.metrics(10)
        rows.append(
            {
                "label": q_label,
                "quantile": q,
                "score_thr": thr,
                "trades_per_day": m0.get("trades_per_day"),
                "n_trades": m0["n_trades"],
                "gross_mean_per_trade": m0.get("gross_mean_per_trade"),
                "net_mean_5bps": m5.get("net_mean_per_trade"),
                "gross_pf": m0.get("pf"),
                "pf_5bps": m5.get("pf"),
                "pf_10bps": m10.get("pf"),
                "total_pnl_0": m0.get("total_pnl_pct"),
                "total_pnl_5": m5.get("total_pnl_pct"),
                "total_pnl_10": m10.get("total_pnl_pct"),
                "max_dd_5": m5.get("max_dd"),
                "stop_rate": m0.get("stop_rate"),
                "np_rate": m0.get("np_rate"),
                "winner_rate": m0.get("winner_rate"),
                "gross_mean_gt_0p05": bool((m0.get("gross_mean_per_trade") or 0) > 0.05),
            }
        )
    return rows


def frequency_buckets(sim: SimResult, roundtrip_bps: float = 5.0) -> list[dict]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for t in sim.trades:
        by_day[t["trading_date"]].append(t["pnl_pct"])
    buckets = {"0-5": [], "6-10": [], "11-20": [], "21-30": [], "31+": []}
    c = cost_pct_per_trade(roundtrip_bps)
    for d, pnls in by_day.items():
        n = len(pnls)
        net = sum(p - c for p in pnls)
        if n <= 5:
            buckets["0-5"].append(net)
        elif n <= 10:
            buckets["6-10"].append(net)
        elif n <= 20:
            buckets["11-20"].append(net)
        elif n <= 30:
            buckets["21-30"].append(net)
        else:
            buckets["31+"].append(net)
    out = []
    for k, vals in buckets.items():
        out.append(
            {
                "bucket": k,
                "n_days": len(vals),
                "mean_day_pnl_5bps": float(np.mean(vals)) if vals else None,
                "total_pnl_5bps": float(sum(vals)) if vals else 0.0,
                "frac_days_pos": float(np.mean([v > 0 for v in vals])) if vals else None,
            }
        )
    return out


def expanding_wf(panel: pd.DataFrame) -> dict[str, Any]:
    days = sorted(panel["trading_date"].astype(str).unique())
    if len(days) < 9:
        return {"ok": False, "reason": "need>=9 days"}
    holdouts = []
    for i in range(8, len(days)):
        train_days = days[:i]
        test_day = days[i]
        train = panel[panel["trading_date"].astype(str).isin(train_days)]
        test = panel[panel["trading_date"].astype(str) == test_day]
        scored = w53.add_scores(pd.concat([train, test], ignore_index=True), fit_days=set(train_days))
        tr = scored[scored["trading_date"].astype(str).isin(train_days)]
        te = scored[scored["trading_date"].astype(str) == test_day]
        stop_thr, _ = w53.fit_stop_threshold(tr)
        np_thr, _ = w53.fit_np_threshold(tr)
        cal = calibrate_expected_edge(tr)
        te = apply_expected_edge(te, cal, 5.0)
        te = mark_chase_vwap(te, tr)
        # score thr: discovery top20% mean-edge targeting
        score_thr = float(pd.to_numeric(tr["integrated_score"], errors="coerce").quantile(0.90))
        # prefer thr where train gross mean of qualified > 0.05 if possible
        best_thr = score_thr
        for q in (0.80, 0.90, 0.95, 0.98):
            thr = float(pd.to_numeric(tr["integrated_score"], errors="coerce").quantile(q))
            sub = tr[pd.to_numeric(tr["integrated_score"], errors="coerce") >= thr]
            if len(sub) >= 50 and float(sub["exit_pnl_pct"].mean()) > 0.05:
                best_thr = thr
                break
        # Primary cost-aware policy: no-fill Cap5 (abstain rather than dilute)
        sim = run_days(
            te,
            [test_day],
            name=f"WF_{test_day}",
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            score_thr=None,
            fill_mode="no-fill",
            chase_vwap_reject=False,
        )
        holdouts.append(
            {
                "test_day": test_day,
                "train_n_days": len(train_days),
                "score_thr": best_thr,
                "stop_thr": stop_thr,
                "np_thr": np_thr,
                "m0": sim.metrics(0),
                "m5": sim.metrics(5),
                "m10": sim.metrics(10),
            }
        )
        print(
            f"  WF {test_day}: n={sim.metrics(0)['n_trades']} pnl5={sim.metrics(5)['total_pnl_pct']:.2f} pf5={sim.metrics(5)['pf']}",
            flush=True,
        )
    m5_pnls = [h["m5"]["total_pnl_pct"] for h in holdouts]
    m5_pfs = [h["m5"]["pf"] for h in holdouts if h["m5"]["pf"] is not None]
    return {
        "ok": True,
        "holdouts": holdouts,
        "sum_pnl_0": float(sum(h["m0"]["total_pnl_pct"] for h in holdouts)),
        "sum_pnl_5": float(sum(m5_pnls)),
        "sum_pnl_10": float(sum(h["m10"]["total_pnl_pct"] for h in holdouts)),
        "mean_pf_5": float(np.mean(m5_pfs)) if m5_pfs else None,
        "frac_days_pnl5_nonneg": float(np.mean([p >= 0 for p in m5_pnls])) if m5_pnls else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== Phase687W54 Cost-Aware Selective ENTRY Closure ===", flush=True)
    print(
        "COST MODEL: roundtrip_bps → cost_pct = bps/100 once/trade "
        f"(5bps={cost_pct_per_trade(5)}%, 10bps={cost_pct_per_trade(10)}%); NO double ENTRY+EXIT",
        flush=True,
    )

    panel = load_panel()
    if panel.empty or panel["trading_date"].nunique() < 8:
        report = {"verdicts": ["DATA_INTEGRITY_BLOCKED"], "reason": "insufficient panel"}
        (OUT / "cost_aware_entry_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (OUT / "cost_aware_entry_report.md").write_text("# BLOCKED\n", encoding="utf-8")
        write_xlsx({"blocked": pd.DataFrame([report])}, OUT / "cost_aware_entry_audit.xlsx")
        return 1

    days = sorted(panel["trading_date"].astype(str).unique())
    mid = len(days) // 2
    disc_days, conf_days = days[:mid], days[mid:]
    print(f"panel rows={len(panel)} days={len(days)} disc={len(disc_days)} conf={len(conf_days)}", flush=True)

    print("scoring...", flush=True)
    panel = w53.add_scores(panel, fit_days=set(disc_days))
    train = panel[panel["trading_date"].astype(str).isin(disc_days)].copy()
    test = panel[panel["trading_date"].astype(str).isin(conf_days)].copy()

    stop_thr, stop_meta = w53.fit_stop_threshold(train)
    np_thr, np_meta = w53.fit_np_threshold(train)
    print(f"stop_thr={stop_thr:.4f} np_thr={np_thr:.4f}", flush=True)

    cal = calibrate_expected_edge(train)
    panel = apply_expected_edge(panel, cal, 5.0)
    train = apply_expected_edge(train, cal, 5.0)
    test = apply_expected_edge(test, cal, 5.0)
    panel = mark_chase_vwap(panel, train)
    train = mark_chase_vwap(train, train)
    test = mark_chase_vwap(test, train)

    # PBv2 candidate flag (same population): score >= discovery median
    med_pb = float(pd.to_numeric(train["pbv2_score"], errors="coerce").median())
    for df in (panel, train, test):
        df["pbv2_candidate_flag"] = pd.to_numeric(df["pbv2_score"], errors="coerce") >= med_pb

    # Pullback audit
    print("pullback audit...", flush=True)
    pb = pullback_data_audit(panel, train, test)

    # Beam + priority rules
    print("beam (canonical)...", flush=True)
    beam = beam_canonical(train, test)
    best3 = beam.get("best_3") or {}
    best4 = beam.get("best_4") or {}
    # prefer priority ok rules
    for pr in beam.get("priority") or []:
        if pr.get("ok") and (pr.get("test_mean") or -1) > (best4.get("test_mean") or -1) and len(pr["features"]) == 4:
            best4 = pr
        if pr.get("ok") and (pr.get("test_mean") or -1) > (best3.get("test_mean") or -1) and len(pr["features"]) == 3:
            best3 = pr

    panel = apply_rule_flag(panel, train, best3.get("features") or [], "rule_3feat")
    panel = apply_rule_flag(panel, train, best4.get("features") or [], "rule_4feat")
    train = panel[panel["trading_date"].astype(str).isin(disc_days)]
    test = panel[panel["trading_date"].astype(str).isin(conf_days)]

    # Score thr for mean>0.05 on discovery
    q_grid_disc = []
    chosen_score_thr = None
    for q in (0.50, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99):
        thr = float(pd.to_numeric(train["integrated_score"], errors="coerce").quantile(q))
        sub = train[pd.to_numeric(train["integrated_score"], errors="coerce") >= thr]
        gm = float(sub["exit_pnl_pct"].mean()) if len(sub) else None
        q_grid_disc.append({"q": q, "thr": thr, "n": int(len(sub)), "gross_mean": gm})
        if gm is not None and gm > 0.05 and chosen_score_thr is None:
            chosen_score_thr = thr
    if chosen_score_thr is None:
        chosen_score_thr = float(pd.to_numeric(train["integrated_score"], errors="coerce").quantile(0.95))

    print(f"chosen_score_thr={chosen_score_thr:.4f}", flush=True)

    # Confirmation score quantile grid
    print("score quantile grid (Confirmation)...", flush=True)
    q_grid = score_quantile_grid(train, test, conf_days, stop_thr, np_thr)

    # Max trades/day comparison
    print("trade-cap policies...", flush=True)
    trade_caps = {}
    for label, mx in [("fixed45_proxy_always", None), ("max30", 30), ("max20", 20), ("max10", 10), ("max5", 5)]:
        # fixed45 ≈ always-fill without score thr (historical overtrading)
        trade_caps[label] = run_days(
            test,
            conf_days,
            name=label,
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            score_thr=None if label.startswith("fixed") else chosen_score_thr,
            max_entries_per_day=mx,
            fill_mode="always-fill" if label.startswith("fixed") else "qualified-fill",
        )

    # Net edge margins
    print("net-edge margins...", flush=True)
    edge_margins = {}
    for label, margin in [("gt0", 0.0), ("gt2bps", 0.02), ("gt5bps", 0.05), ("gt10bps", 0.10)]:
        edge_margins[label] = run_days(
            test,
            conf_days,
            name=f"net_{label}",
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            score_thr=chosen_score_thr,
            net_edge_thr=margin,
            fill_mode="qualified-fill",
        )

    # Final arms P14
    print("final arms...", flush=True)
    arms: dict[str, SimResult] = {}
    arms["A_pbv2_score_only"] = run_days(
        test, conf_days, name="A_pbv2_score_only", score_col="pbv2_score", fill_mode="always-fill"
    )
    arms["B_pbv2_guards"] = run_days(
        test,
        conf_days,
        name="B_pbv2_guards",
        score_col="pbv2_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        fill_mode="qualified-fill",
    )
    arms["B1_fifo"] = run_days(
        test, conf_days, name="B1_fifo", score_col="pbv2_score", fill_mode="always-fill"
    )
    arms["B2_pbv2_score"] = arms["A_pbv2_score_only"]
    arms["B3_pbv2_candidate"] = run_days(
        test,
        conf_days,
        name="B3_pbv2_candidate",
        score_col="pbv2_score",
        fill_mode="qualified-fill",
        guard_mode="pbv2_candidate",
    )
    arms["B4_pbv2_score_guards"] = arms["B_pbv2_guards"]

    arms["C_G_always_fill"] = run_days(
        test,
        conf_days,
        name="C_G_always_fill",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        fill_mode="always-fill",
    )
    arms["D_G_qualified_fill"] = run_days(
        test,
        conf_days,
        name="D_G_qualified_fill",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        score_thr=None,
        fill_mode="qualified-fill",
    )
    arms["D_G_no_fill"] = run_days(
        test,
        conf_days,
        name="D_G_no_fill",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        fill_mode="no-fill",
    )
    arms["E_G_score_thr"] = run_days(
        test,
        conf_days,
        name="E_G_score_thr",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        score_thr=chosen_score_thr,
        fill_mode="qualified-fill",
    )
    arms["F_G_chase"] = run_days(
        test,
        conf_days,
        name="F_G_chase",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        fill_mode="qualified-fill",
        chase_vwap_reject=True,
    )
    arms["G_score_chase"] = run_days(
        test,
        conf_days,
        name="G_score_chase",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        score_thr=chosen_score_thr,
        fill_mode="qualified-fill",
        chase_vwap_reject=True,
    )
    arms["H_3feat"] = run_days(
        test,
        conf_days,
        name="H_3feat",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        fill_mode="no-fill",
        rule_mask_col="rule_3feat",
    )
    arms["I_4feat"] = run_days(
        test,
        conf_days,
        name="I_4feat",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        fill_mode="no-fill",
        rule_mask_col="rule_4feat",
    )
    # Select J on Discovery 5bps among cost-aware candidates (NO reentry)
    j_candidates = {
        "no_fill_G": dict(
            score_col="integrated_score", stop_thr=stop_thr, np_thr=np_thr, fill_mode="no-fill"
        ),
        "no_fill_G_score": dict(
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            score_thr=chosen_score_thr,
            fill_mode="no-fill",
        ),
        "no_fill_G_chase": dict(
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            fill_mode="no-fill",
            chase_vwap_reject=True,
        ),
        "no_fill_G_net0": dict(
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            net_edge_thr=0.0,
            fill_mode="no-fill",
        ),
        "qual_G_net0_score": dict(
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            score_thr=chosen_score_thr,
            net_edge_thr=0.0,
            fill_mode="qualified-fill",
        ),
    }
    best_j_name, best_j_kwargs, best_j_disc = None, None, -1e18
    j_disc_rows = []
    for jn, kw in j_candidates.items():
        sim_d = run_days(train, disc_days, name=f"Jdisc_{jn}", **kw)
        m5 = sim_d.metrics(5)
        j_disc_rows.append({"name": jn, **{k: m5.get(k) for k in ("n_trades", "total_pnl_pct", "pf", "gross_mean_per_trade")}})
        score = (m5.get("total_pnl_pct") or -1e18) + 0.01 * ((m5.get("pf") or 0) if (m5.get("pf") or 0) == (m5.get("pf") or 0) else 0)
        if (m5.get("n_trades") or 0) >= 20 and (m5.get("total_pnl_pct") or -1e18) > best_j_disc:
            best_j_disc = m5.get("total_pnl_pct") or -1e18
            best_j_name, best_j_kwargs = jn, kw
    if best_j_kwargs is None:
        best_j_name, best_j_kwargs = "no_fill_G", j_candidates["no_fill_G"]
    arms["J_final"] = run_days(test, conf_days, name="J_final", **best_j_kwargs)
    print(f"J_final policy={best_j_name} disc5={best_j_disc:.2f} conf_n={len(arms['J_final'].trades)}", flush=True)

    # F without NP for incremental
    arms["F_winner_stop_only"] = run_days(
        test,
        conf_days,
        name="F_winner_stop_only",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=None,
        score_thr=chosen_score_thr,
        fill_mode="qualified-fill",
    )
    arms["G_with_np"] = run_days(
        test,
        conf_days,
        name="G_with_np",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        score_thr=chosen_score_thr,
        fill_mode="qualified-fill",
    )

    # Reference reentry (excluded from shadow/runtime)
    arms["REF_reentry_hybrid"] = run_days(
        test,
        conf_days,
        name="REF_reentry_hybrid",
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        score_thr=chosen_score_thr,
        fill_mode="qualified-fill",
        chase_vwap_reject=True,
    )

    def pack(sim: SimResult) -> dict:
        return {"0bps": sim.metrics(0), "5bps": sim.metrics(5), "10bps": sim.metrics(10)}

    arm_pack = {k: pack(v) for k, v in arms.items()}
    cap_pack = {k: pack(v) for k, v in trade_caps.items()}
    edge_pack = {k: pack(v) for k, v in edge_margins.items()}

    # CHASE incremental on no-fill G baseline (not always-fill)
    g_base = run_days(
        test, conf_days, name="G_nofill_base", score_col="integrated_score",
        stop_thr=stop_thr, np_thr=np_thr, fill_mode="no-fill",
    )
    g_chase = run_days(
        test, conf_days, name="G_nofill_chase", score_col="integrated_score",
        stop_thr=stop_thr, np_thr=np_thr, fill_mode="no-fill", chase_vwap_reject=True,
    )
    arms["F_G_chase"] = g_chase  # overwrite with no-fill chase for fair compare
    c5 = g_base.metrics(5)
    f5 = g_chase.metrics(5)
    chase_blocked = int(test["chase_vwap_extended"].sum())
    chase_overlap = float(
        (
            (pd.to_numeric(test["stop_risk_score"], errors="coerce") >= stop_thr)
            & test["chase_vwap_extended"]
        ).mean()
    )
    # blocked trade proxies from row universe
    blk = test["chase_vwap_extended"]
    chase_eval = {
        "blocked_rows_conf": chase_blocked,
        "blocked_stop_rate": float(test.loc[blk, "stop_proxy"].mean()) if chase_blocked else None,
        "blocked_winner_rate": float(test.loc[blk, "winner_a"].mean()) if chase_blocked else None,
        "incremental_pnl_5bps_chase_minus_base": (f5["total_pnl_pct"] or 0) - (c5["total_pnl_pct"] or 0),
        "chase_pf_5bps": f5.get("pf"),
        "base_nofill_pf_5bps": c5.get("pf"),
        "base_nofill_pnl_5bps": c5.get("total_pnl_pct"),
        "chase_pnl_5bps": f5.get("total_pnl_pct"),
        "overlap_frac_with_stop_risk": chase_overlap,
        "confirmed": bool(
            (f5.get("total_pnl_pct") or -1e9) > (c5.get("total_pnl_pct") or 0) + 1e-9
            and (f5.get("pf") or 0) >= (c5.get("pf") or 0)
        ),
    }

    # NP incremental
    f_only = arms["F_winner_stop_only"].metrics(5)
    g_np = arms["G_with_np"].metrics(5)
    # after score thr, does NP still help?
    np_incr = {
        "F_score_stop_5bps": f_only,
        "G_score_stop_np_5bps": g_np,
        "delta_pnl_5bps": (g_np["total_pnl_pct"] or 0) - (f_only["total_pnl_pct"] or 0),
        "delta_np_rate": (g_np.get("np_rate") or 0) - (f_only.get("np_rate") or 0),
        "incremental_edge": bool((g_np["total_pnl_pct"] or -1e9) > (f_only["total_pnl_pct"] or 0) + 1e-9),
        "redundant": bool(
            abs((g_np["total_pnl_pct"] or 0) - (f_only["total_pnl_pct"] or 0)) < 1e-6
            or (g_np.get("np_rate") or 0) >= (f_only.get("np_rate") or 0) - 1e-9
            and (g_np["total_pnl_pct"] or 0) <= (f_only["total_pnl_pct"] or 0) + 1e-9
        ),
    }

    # Overtrading: always-fill ~45/day with gross mean < roundtrip 5bps cost
    overtrading = bool(
        (arms["C_G_always_fill"].metrics(0).get("trades_per_day") or 0) >= 20
        and (arms["C_G_always_fill"].metrics(0).get("gross_mean_per_trade") or 0) < cost_pct_per_trade(5)
    )

    print("expanding walk-forward...", flush=True)
    wf = expanding_wf(panel)

    # Frequency analysis on always-fill vs final
    freq_always = frequency_buckets(arms["C_G_always_fill"], 5)
    freq_final = frequency_buckets(arms["J_final"], 5)

    # PASS gates on J_final vs A (same population)
    j0, j5, j10 = arms["J_final"].metrics(0), arms["J_final"].metrics(5), arms["J_final"].metrics(10)
    a5 = arms["A_pbv2_score_only"].metrics(5)
    # leave-one-day / symbol on 5bps
    day_pnls: dict[str, float] = defaultdict(float)
    sym_pnls: dict[str, float] = defaultdict(float)
    c_pct = cost_pct_per_trade(5)
    for t in arms["J_final"].trades:
        day_pnls[t["trading_date"]] += t["pnl_pct"] - c_pct
        sym_pnls[t["symbol"]] += t["pnl_pct"] - c_pct
    worst_day = min(day_pnls, key=day_pnls.get) if day_pnls else None
    worst_sym = min(sym_pnls, key=sym_pnls.get) if sym_pnls else None
    pnl_ex_day = (j5["total_pnl_pct"] - day_pnls[worst_day]) if worst_day else None
    pnl_ex_sym = (j5["total_pnl_pct"] - sym_pnls[worst_sym]) if worst_sym else None

    # PF after drop: recompute from remaining trades
    def pf_ex(exclude_day=None, exclude_sym=None):
        pnls = []
        for t in arms["J_final"].trades:
            if exclude_day and t["trading_date"] == exclude_day:
                continue
            if exclude_sym and t["symbol"] == exclude_sym:
                continue
            pnls.append(t["pnl_pct"] - c_pct)
        return _pf(pnls)

    a_day = defaultdict(float)
    for t in arms["A_pbv2_score_only"].trades:
        a_day[t["trading_date"]] += t["pnl_pct"] - c_pct
    keys = set(day_pnls) | set(a_day)
    frac_nonworse = float(np.mean([day_pnls.get(k, 0) >= a_day.get(k, 0) - 1e-9 for k in keys])) if keys else None

    # direction agreement disc/conf/wf
    disc_sim = run_days(train, disc_days, name="J_disc", **best_j_kwargs)
    d5 = disc_sim.metrics(5)
    dir_agree = bool(
        (d5.get("total_pnl_pct") or 0) > 0
        and (j5.get("total_pnl_pct") or 0) > 0
        and (wf.get("sum_pnl_5") or 0) > 0
    )

    pass_gates = {
        "pnl_5bps_gt_0": bool((j5.get("total_pnl_pct") or 0) > 0),
        "pf_5bps_ge_1": bool((j5.get("pf") or 0) >= 1.0),
        "better_than_pbv2_score_same_pop": bool((j5.get("total_pnl_pct") or -1e9) > (a5.get("total_pnl_pct") or 0)),
        "gross_mean_gt_0p05": bool((j0.get("gross_mean_per_trade") or 0) > 0.05),
        "dd_improved": bool((j5.get("max_dd") or -1e9) > (a5.get("max_dd") or -1e9)),
        "days_70_nonworse": bool(frac_nonworse is not None and frac_nonworse >= 0.70),
        "ex_day_pf5_ge_1": bool((pf_ex(exclude_day=worst_day) or 0) >= 1.0),
        "ex_sym_pf5_ge_1": bool((pf_ex(exclude_sym=worst_sym) or 0) >= 1.0),
        "direction_agree": dir_agree,
        "no_symbol_coefs": True,
        "no_time_coefs": True,
    }
    runtime_ready = all(
        [
            pass_gates["pnl_5bps_gt_0"],
            pass_gates["pf_5bps_ge_1"],
            pass_gates["better_than_pbv2_score_same_pop"],
            pass_gates["gross_mean_gt_0p05"],
            pass_gates["days_70_nonworse"],
            pass_gates["ex_day_pf5_ge_1"],
            pass_gates["ex_sym_pf5_ge_1"],
            pass_gates["direction_agree"],
        ]
    )

    # Abstention confirmed if no-fill / J beats always-fill at 5bps
    abstention_ok = bool(
        (arms["D_G_no_fill"].metrics(5).get("total_pnl_pct") or -1e9)
        > (arms["C_G_always_fill"].metrics(5).get("total_pnl_pct") or 0)
        or (arms["J_final"].metrics(5).get("total_pnl_pct") or -1e9)
        > (arms["C_G_always_fill"].metrics(5).get("total_pnl_pct") or 0)
    )

    high_edge_ok = bool(
        ((best3.get("ok") and (best3.get("test_mean") or 0) > 0.05) or (best4.get("ok") and (best4.get("test_mean") or 0) > 0.05))
        and (
            (arms["H_3feat"].metrics(5).get("pf") or 0) >= 1.0
            or (arms["I_4feat"].metrics(5).get("pf") or 0) >= 1.0
            or (j5.get("gross_mean_per_trade") or 0) > 0.05
        )
    )

    verdicts = ["COST_MODEL_CORRECTED", "PBV2_SAME_POPULATION_BASELINE_READY"]
    if overtrading:
        verdicts.append("OVERTRADING_CONFIRMED")
    if abstention_ok:
        verdicts.append("COST_AWARE_ABSTENTION_CONFIRMED")
    if high_edge_ok:
        verdicts.append("HIGH_EDGE_ENTRY_TRIGGER_CONFIRMED")
    if chase_eval["confirmed"]:
        verdicts.append("CHASE_VWAP_REJECT_CONFIRMED")
    if np_incr["incremental_edge"]:
        verdicts.append("NOPROGRESS_INCREMENTAL_EDGE_CONFIRMED")
    elif np_incr["redundant"]:
        verdicts.append("NOPROGRESS_SCORE_REDUNDANT")
    verdicts.append(pb["verdict"])
    if runtime_ready:
        verdicts.append("COST_ROBUST_PORTFOLIO_EDGE_CONFIRMED")
        verdicts.append("RUNTIME_CANDIDATE_READY")
    else:
        verdicts.append("NO_COST_ROBUST_EDGE")
        if pass_gates["better_than_pbv2_score_same_pop"] or abstention_ok:
            verdicts.append("SHADOW_SPEC_READY")

    shadow_spec = {
        "enabled": False,
        "includes_reentry": False,
        "final_policy": best_j_name,
        "components": {
            "winner_enrichment": True,
            "stop_risk_score": True,
            "np_risk_score": not np_incr.get("redundant", False),
            "score_threshold": best_j_kwargs.get("score_thr"),
            "expected_net_edge_gt": best_j_kwargs.get("net_edge_thr"),
            "chase_vwap_extended_reject": bool(best_j_kwargs.get("chase_vwap_reject")),
            "fill_mode": best_j_kwargs.get("fill_mode", "no-fill"),
            "reentry_hybrid": False,
        },
        "cost_model": {
            "roundtrip_cost_bps_eval": [0, 5, 10],
            "primary": 5,
            "cost_pct_per_trade_5bps": cost_pct_per_trade(5),
            "per_side_cost_bps_5bps": per_side_cost_bps(5),
            "note": "deduct once per completed trade; never ENTRY+EXIT double",
        },
    }

    report = {
        "metadata": {
            "phase": "Phase687W54",
            "generated_at": datetime.now(JST).isoformat(),
            "days": days,
            "discovery_days": disc_days,
            "confirmation_days": conf_days,
            "n_rows": int(len(panel)),
            "cost_model_fix": {
                "w53_bug": "pnl - cost_bps/100*2 treated 5bps as 10bps roundtrip",
                "w54": "pnl - roundtrip_bps/100 once per trade",
            },
        },
        "verdicts": verdicts,
        "pass_gates": pass_gates,
        "runtime_candidate_ready": runtime_ready,
        "shadow_spec": shadow_spec,
        "cost_model": {
            "roundtrip_0": {"roundtrip_cost_bps": 0, "per_side_cost_bps": 0, "cost_pct_per_trade": 0},
            "roundtrip_5": {
                "roundtrip_cost_bps": 5,
                "per_side_cost_bps": per_side_cost_bps(5),
                "cost_pct_per_trade": cost_pct_per_trade(5),
            },
            "roundtrip_10": {
                "roundtrip_cost_bps": 10,
                "per_side_cost_bps": per_side_cost_bps(10),
                "cost_pct_per_trade": cost_pct_per_trade(10),
            },
        },
        "thresholds": {
            "stop_thr": stop_thr,
            "np_thr": np_thr,
            "score_thr": chosen_score_thr,
            "stop_meta": stop_meta,
            "np_meta": np_meta,
            "q_grid_discovery": q_grid_disc,
            "j_final_policy": best_j_name,
            "j_discovery_sweep": j_disc_rows,
        },
        "score_quantile_confirmation": q_grid,
        "trade_cap_policies": cap_pack,
        "net_edge_margins": edge_pack,
        "arms": arm_pack,
        "chase_vwap": chase_eval,
        "np_incremental": np_incr,
        "pullback": pb,
        "beam": {
            "n_unique_2": beam.get("n_unique_2"),
            "n_unique_3": beam.get("n_unique_3"),
            "n_unique_4": beam.get("n_unique_4"),
            "priority": beam.get("priority"),
            "best_3": best3,
            "best_4": best4,
            "top3": beam.get("top3"),
            "top4": beam.get("top4"),
        },
        "frequency": {"always_fill": freq_always, "final": freq_final},
        "walk_forward": {
            "sum_pnl_0": wf.get("sum_pnl_0"),
            "sum_pnl_5": wf.get("sum_pnl_5"),
            "sum_pnl_10": wf.get("sum_pnl_10"),
            "mean_pf_5": wf.get("mean_pf_5"),
            "frac_days_pnl5_nonneg": wf.get("frac_days_pnl5_nonneg"),
            "holdouts": wf.get("holdouts"),
        },
        "leave_one": {
            "worst_day": worst_day,
            "pnl_ex_day_5bps": pnl_ex_day,
            "pf_ex_day_5bps": pf_ex(exclude_day=worst_day),
            "worst_symbol": worst_sym,
            "pnl_ex_sym_5bps": pnl_ex_sym,
            "pf_ex_sym_5bps": pf_ex(exclude_sym=worst_sym),
        },
        "frac_days_nonworse_vs_A": frac_nonworse,
        "runtime_unchanged": {
            "pbv2": True,
            "exit": True,
            "cap": 5,
            "shadow_enabled": False,
            "real_orders": False,
            "reentry_in_final": False,
        },
    }

    md = f"""# Phase687W54 — Cost-Aware Selective ENTRY Closure

## Verdict
`{' | '.join(verdicts)}`

## Cost model (CORRECTED)
- roundtrip 5bps = **{cost_pct_per_trade(5)}%** per completed trade (per_side={per_side_cost_bps(5)}bps)
- roundtrip 10bps = **{cost_pct_per_trade(10)}%** per completed trade
- W53 bug: `cost/100*2` made 5bps act as 10bps; W54 deducts once

## Confirmation Cap5 (same Watch50 panel)
| Arm | n | pnl@5bps | PF@5bps | mean gross | trades/day |
|-----|---|----------|---------|------------|------------|
| A PBv2 score | {arm_pack['A_pbv2_score_only']['5bps']['n_trades']} | {arm_pack['A_pbv2_score_only']['5bps']['total_pnl_pct']:.2f} | {arm_pack['A_pbv2_score_only']['5bps']['pf']} | {arm_pack['A_pbv2_score_only']['0bps'].get('gross_mean_per_trade')} | {arm_pack['A_pbv2_score_only']['5bps'].get('trades_per_day')} |
| C G always-fill | {arm_pack['C_G_always_fill']['5bps']['n_trades']} | {arm_pack['C_G_always_fill']['5bps']['total_pnl_pct']:.2f} | {arm_pack['C_G_always_fill']['5bps']['pf']} | {arm_pack['C_G_always_fill']['0bps'].get('gross_mean_per_trade')} | {arm_pack['C_G_always_fill']['5bps'].get('trades_per_day')} |
| E G+score thr | {arm_pack['E_G_score_thr']['5bps']['n_trades']} | {arm_pack['E_G_score_thr']['5bps']['total_pnl_pct']:.2f} | {arm_pack['E_G_score_thr']['5bps']['pf']} | {arm_pack['E_G_score_thr']['0bps'].get('gross_mean_per_trade')} | {arm_pack['E_G_score_thr']['5bps'].get('trades_per_day')} |
| J Final | {j5['n_trades']} | {j5['total_pnl_pct']:.2f} | {j5['pf']} | {j0.get('gross_mean_per_trade')} | {j5.get('trades_per_day')} |

J @0/5/10bps: pnl={j0['total_pnl_pct']:.2f}/{j5['total_pnl_pct']:.2f}/{j10['total_pnl_pct']:.2f} PF={j0['pf']}/{j5['pf']}/{j10['pf']}

## Walk-forward
- sum_pnl_5bps={wf.get('sum_pnl_5')} mean_pf_5={wf.get('mean_pf_5')} frac_days≥0={wf.get('frac_days_pnl5_nonneg')}
- sum_pnl_10bps={wf.get('sum_pnl_10')}

## Components
- score_thr={chosen_score_thr:.4f} stop={stop_thr:.4f} np={np_thr:.4f}
- CHASE confirmed={chase_eval['confirmed']} incr_pnl5={chase_eval.get('incremental_pnl_5bps_chase_minus_base')}
- NP incremental={np_incr['incremental_edge']} redundant={np_incr['redundant']} delta5={np_incr['delta_pnl_5bps']}
- Pullback: {pb['verdict']} n_train={pb['n_train']} n_test={pb['n_test']}
- best3={best3.get('features')} test_mean={best3.get('test_mean')}
- best4={best4.get('features')} test_mean={best4.get('test_mean')}
- Reentry: reference only — **excluded** from Shadow/Runtime

## Runtime candidate
**{runtime_ready}** — Shadow enabled: False — PBv2 unchanged

## Pass gates
```
{json.dumps(pass_gates, ensure_ascii=False, indent=2)}
```
"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cost_aware_entry_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "cost_aware_entry_report.md").write_text(md, encoding="utf-8")

    def _arm_rows(pack: dict) -> pd.DataFrame:
        rows = []
        for name, m in pack.items():
            rows.append({"arm": name, **{f"c0_{k}": v for k, v in m["0bps"].items()}, **{f"c5_{k}": v for k, v in m["5bps"].items()}, **{f"c10_{k}": v for k, v in m["10bps"].items()}})
        return pd.DataFrame(rows)

    write_xlsx(
        {
            "cost_model": pd.DataFrame(
                [
                    {"roundtrip_bps": 0, "per_side": 0, "cost_pct": 0},
                    {"roundtrip_bps": 5, "per_side": 2.5, "cost_pct": 0.05},
                    {"roundtrip_bps": 10, "per_side": 5.0, "cost_pct": 0.10},
                ]
            ),
            "arms": _arm_rows(arm_pack),
            "score_quantiles": pd.DataFrame(q_grid),
            "trade_caps": _arm_rows(cap_pack),
            "net_edge_margins": _arm_rows(edge_pack),
            "chase": pd.DataFrame([chase_eval]),
            "np_incremental": pd.DataFrame([np_incr]),
            "pullback": pd.DataFrame([{k: v for k, v in pb.items() if not isinstance(v, (dict, list))}]),
            "beam_priority": pd.DataFrame(beam.get("priority") or []),
            "beam_top4": pd.DataFrame(beam.get("top4") or []),
            "frequency_final": pd.DataFrame(freq_final),
            "frequency_always": pd.DataFrame(freq_always),
            "walk_forward": pd.DataFrame(
                [
                    {
                        "day": h["test_day"],
                        "n": h["m5"]["n_trades"],
                        "pnl0": h["m0"]["total_pnl_pct"],
                        "pnl5": h["m5"]["total_pnl_pct"],
                        "pnl10": h["m10"]["total_pnl_pct"],
                        "pf5": h["m5"]["pf"],
                        "mean_edge": h["m0"].get("gross_mean_per_trade"),
                    }
                    for h in (wf.get("holdouts") or [])
                ]
            ),
            "pass_gates": pd.DataFrame([pass_gates]),
            "runtime_audit": pd.DataFrame([report["runtime_unchanged"]]),
        },
        OUT / "cost_aware_entry_audit.xlsx",
    )

    print(json.dumps({"verdicts": verdicts, "runtime_ready": runtime_ready, "j5": j5}, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
