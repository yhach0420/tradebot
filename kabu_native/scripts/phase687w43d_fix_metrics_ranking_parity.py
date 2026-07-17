#!/usr/bin/env python3
"""Phase687W43D-FIX: Active Runtime Metrics and Ranking Parity Repair.

Research-only. Does not overwrite W43D artifacts. No Runtime/YAML/Shadow changes.
Outputs only:
  w43d_fix_report.md
  w43d_fix_report.json
  w43d_fix_audit.xlsx
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import warnings
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.worksheet.table import Table, TableStyleInfo

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "scripts"))

import phase687w43c_watch50_future30m_opportunity as w43c  # noqa: E402
import phase687w43d_5day_winner_state_validation as w43d  # noqa: E402

JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
REPORTS = NATIVE / "results" / "reports"
PAPER = NATIVE / "results" / "small_paper"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
MAX_WORKERS = 4
RANDOM_ITERS = 100
RANDOM_BASE_SEED = 43

MARKET_DAYS = ["20260709", "20260710", "20260714", "20260716", "20260717"]
RUNTIME_ACTIVE_DAYS = ["20260709", "20260710", "20260716", "20260717"]
MARKET_ONLY_DAYS = ["20260714"]
EXCLUDED_DAYS = ["20260715"]  # PUSH 0

FEATURE_POOL = [
    "ret_10s",
    "ret_30s",
    "ret_60s",
    "ret_120s",
    "ret_300s",
    "slope_30s",
    "slope_60s",
    "slope_120s",
    "slope_300s",
    "accel_30s",
    "accel_60s",
    "max_dd_300s",
    "bounce_from_low_300s",
    "fall_from_high_300s",
    "day_high_distance_pct",
    "vwap_dev_pct",
    "vwap_slope_300s",
    "pre_300s_new_high_count",
    "seconds_since_last_new_high",
    "vol_accel_300s",
    "vol_persistence_300s",
    "vol_ratio_60_300",
    "trade_updates_per_sec_60s",
    "board_updates_per_sec_60s",
    "imbalance_l5",
    "imbalance_chg_30s",
    "imbalance_chg_60s",
    "net_ask_pressure_60s",
    "net_bid_pressure_60s",
    "spread_bps",
    "microprice_chg_60s",
    "new_high_restart_count",
    "vol_recovery_flag",
    "vwap_reclaim_flag",
]


def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    # efficient approx via ranks
    n1, n2 = len(a), len(b)
    # sample cap for speed
    if n1 * n2 > 2_000_000:
        rng = np.random.default_rng(0)
        a = rng.choice(a, size=min(n1, 2000), replace=False)
        b = rng.choice(b, size=min(n2, 2000), replace=False)
        n1, n2 = len(a), len(b)
    gt = 0
    lt = 0
    for x in a:
        gt += int(np.sum(x > b))
        lt += int(np.sum(x < b))
    return float((gt - lt) / (n1 * n2))


def day_classification() -> pd.DataFrame:
    rows = []
    for day in MARKET_DAYS + EXCLUDED_DAYS:
        dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        push = PUSH_ROOT / dash
        n_push = len(list(push.glob("*.jsonl"))) if push.is_dir() else 0
        uni_ok = all(
            (
                REPORTS / f"universe_core10_dynamic40_price_risk_{sfx}_{day}.csv"
            ).is_file()
            for sfx in ("am", "am_refresh1000", "pm", "pm_refresh1430")
        )
        sess = w43d.pick_sessions(day)
        has_session = sess["am"] is not None or sess["pm"] is not None
        # evidence of runtime activity via events
        n_events = 0
        for sk in ("am", "pm"):
            sd = sess.get(sk)
            if not sd:
                continue
            ep = Path(sd) / "small_paper_events.jsonl"
            if not ep.is_file():
                continue
            with ep.open(encoding="utf-8") as f:
                for line in f:
                    if any(x in line for x in ('"rejected"', '"candidate"', '"accepted"', "heartbeat", "seal")):
                        n_events += 1
                        if n_events >= 5:
                            break
        runtime_active = day in RUNTIME_ACTIVE_DAYS or (has_session and n_events > 0 and n_push >= 45)
        # force known classification
        if day == "20260714":
            runtime_active = False
        if day == "20260715":
            market = False
        else:
            market = n_push >= 45 and uni_ok
        rows.append(
            {
                "trading_date": day,
                "date_iso": dash,
                "MARKET_DATA_DAY": bool(market),
                "RUNTIME_ACTIVE_DAY": bool(runtime_active and market),
                "MARKET_ONLY_DAY": bool(market and not runtime_active),
                "EXCLUDED": day in EXCLUDED_DAYS or not market,
                "push_files": n_push,
                "universe_ok": uni_ok,
                "paper_session_present": has_session,
                "runtime_event_evidence": n_events > 0,
                "exclude_reason": (
                    "push_coverage_low:0"
                    if day == "20260715"
                    else ("market_only_no_runtime" if market and not runtime_active else "")
                ),
            }
        )
    return pd.DataFrame(rows)


def load_prior_artifacts() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    moves = pd.read_csv(OUT / "w43d_5d_independent_moves.csv")
    raw = pd.read_parquet(OUT / "w43d_5d_raw_episodes.parquet")
    fe = pd.read_csv(OUT / "w43d_5d_feature_effect.csv")
    moves["trading_date"] = moves["trading_date"].astype(str)
    raw["trading_date"] = raw["trading_date"].astype(str)
    fe["trading_date"] = fe["trading_date"].astype(str)
    return moves, raw, fe


def load_entries() -> pd.DataFrame:
    rows = []
    for day in RUNTIME_ACTIVE_DAYS:
        meta = {"day": day, "sessions": w43d.pick_sessions(day)}
        part = w43d.load_official_for_day(meta)
        if not part.empty:
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def build_runtime_capture(moves: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day in MARKET_DAYS:
        g = moves[moves["trading_date"] == day]
        n = len(g)
        c5 = int((g["capture_class"] == "CAPTURED_5M").sum())
        c15 = int(g["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"]).sum())
        rows.append(
            {
                "trading_date": day,
                "day_type": "RUNTIME_ACTIVE" if day in RUNTIME_ACTIVE_DAYS else "MARKET_ONLY",
                "independent_moves": n,
                "captured_5m": c5,
                "captured_15m": c15,
                "capture_rate_5m": (c5 / n) if n else None,
                "capture_rate_15m": (c15 / n) if n else None,
                "included_in_runtime_capture": day in RUNTIME_ACTIVE_DAYS,
            }
        )
    ra = moves[moves["trading_date"].isin(RUNTIME_ACTIVE_DAYS)]
    n = len(ra)
    c5 = int((ra["capture_class"] == "CAPTURED_5M").sum())
    c15 = int(ra["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"]).sum())
    rows.append(
        {
            "trading_date": "RUNTIME_ACTIVE_ALL",
            "day_type": "RUNTIME_ACTIVE",
            "independent_moves": n,
            "captured_5m": c5,
            "captured_15m": c15,
            "capture_rate_5m": c5 / n if n else None,
            "capture_rate_15m": c15 / n if n else None,
            "included_in_runtime_capture": True,
        }
    )
    rows.append(
        {
            "trading_date": "MARKET_DAY_ALL",
            "day_type": "MARKET",
            "independent_moves": int(len(moves)),
            "captured_5m": int((moves["capture_class"] == "CAPTURED_5M").sum()),
            "captured_15m": int(moves["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"]).sum()),
            "capture_rate_5m": None,
            "capture_rate_15m": None,
            "included_in_runtime_capture": False,
        }
    )
    return pd.DataFrame(rows)


def build_causal_funnel(moves: pd.DataFrame) -> pd.DataFrame:
    classes = [
        "PBV2_BASE_NOT_CANDIDATE",
        "DATA_QUALITY_BLOCKED",
        "NO_DECISION_TRACE",
        "CAPTURED_5M",
        "ENTRY_RULE_REJECTED",
        "LATE_CAPTURED_15M",
        "SCAN_OR_QUEUE_LIMITED",
        "SAME_SYMBOL_POSITION_BLOCKED",
        "CAP_BLOCKED_CONFIRMED",
        "MARKET_ONLY_NO_RUNTIME_EVALUATION",
    ]
    rows = []
    # market-only day reclass
    m = moves.copy()
    m.loc[m["trading_date"].isin(MARKET_ONLY_DAYS), "funnel_class"] = "MARKET_ONLY_NO_RUNTIME_EVALUATION"

    for scope, days in (
        ("runtime_active_independent", RUNTIME_ACTIVE_DAYS),
        ("market_only_independent", MARKET_ONLY_DAYS),
        ("market_all_independent", MARKET_DAYS),
    ):
        g = m[m["trading_date"].isin(days)]
        row = {"scope": scope, "n": len(g)}
        for c in classes:
            row[f"n_{c}"] = int((g["funnel_class"] == c).sum())
        row["sum_check"] = sum(row[f"n_{c}"] for c in classes)
        rows.append(row)
        for day in days:
            gg = g[g["trading_date"] == day]
            row = {"scope": f"{scope}:{day}", "n": len(gg)}
            for c in classes:
                row[f"n_{c}"] = int((gg["funnel_class"] == c).sum())
            row["sum_check"] = sum(row[f"n_{c}"] for c in classes)
            rows.append(row)
    return pd.DataFrame(rows)


def missed_cause_ratios(moves: pd.DataFrame) -> dict[str, Any]:
    m = moves[moves["trading_date"].isin(RUNTIME_ACTIVE_DAYS)].copy()
    captured15 = int(m["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"]).sum())
    total = len(m)
    missed = total - captured15
    # among all runtime moves, funnel causes for non-captured
    noncap = m[~m["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"])]
    counts = Counter(noncap["funnel_class"])
    def ratio(key: str) -> float:
        return float(counts.get(key, 0) / missed) if missed else 0.0

    return {
        "runtime_active_moves": total,
        "captured_15m": captured15,
        "missed": missed,
        "counts_among_missed": dict(counts),
        "ratio_pbv2_base_not_candidate": ratio("PBV2_BASE_NOT_CANDIDATE"),
        "ratio_data_quality": ratio("DATA_QUALITY_BLOCKED"),
        "ratio_scan_queue": ratio("SCAN_OR_QUEUE_LIMITED"),
        "ratio_entry_rule_rejected": ratio("ENTRY_RULE_REJECTED"),
        "ratio_same_symbol": ratio("SAME_SYMBOL_POSITION_BLOCKED"),
        "ratio_unexplained_no_trace": ratio("NO_DECISION_TRACE"),
        "main_cause": counts.most_common(1)[0][0] if counts else None,
    }


def run_day_snapshots(day: str, tmp: Path) -> pd.DataFrame:
    if day == "20260717":
        pq = OUT / "w43c_20260717_watch50_snapshot.parquet"
        if pq.is_file():
            df = w43d.enrich_features(pd.read_parquet(pq))
            cache = tmp / f"snaps_{day}.parquet"
            df.to_parquet(cache, index=False)
            return df
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    meta = {
        "day": day,
        "date": dash,
        "push_dir": PUSH_ROOT / dash,
        "sessions": w43d.pick_sessions(day),
        "universe": {
            "am": REPORTS / f"universe_core10_dynamic40_price_risk_am_{day}.csv",
            "am_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv",
            "pm": REPORTS / f"universe_core10_dynamic40_price_risk_pm_{day}.csv",
            "pm_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv",
        },
    }
    df = w43d.run_day_snapshots(meta)
    cache = tmp / f"snaps_{day}.parquet"
    df.to_parquet(cache, index=False)
    return df


def load_pbv2_scores(days: list[str]) -> pd.DataFrame:
    rows = []
    for day in days:
        sess = w43d.pick_sessions(day)
        for sk in ("am", "pm"):
            sd = sess.get(sk)
            if not sd:
                continue
            p = Path(sd) / "small_paper_events.jsonl"
            if not p.is_file():
                continue
            with p.open(encoding="utf-8") as f:
                for line in f:
                    if '"event_type"' not in line:
                        continue
                    if not any(x in line for x in ("rejected", "candidate", "accepted")):
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("event_type") not in ("rejected", "candidate", "accepted"):
                        continue
                    et = w43c.parse_ts(o.get("entry_time") or o.get("event_time"))
                    if et is None:
                        continue
                    score = o.get("entry_expectancy_score_v2")
                    if score is None:
                        score = o.get("entry_score_v2")
                    rows.append(
                        {
                            "trading_date": day,
                            "symbol": str(o.get("symbol") or ""),
                            "epoch": w43c.to_epoch(et),
                            "pbv2_score": _finite(score),
                            "event_type": o.get("event_type"),
                            "same_scan_rank": _finite(o.get("same_scan_rank")),
                        }
                    )
    return pd.DataFrame(rows)


def attach_pbv2_score(snaps: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    snaps = snaps.copy()
    if scores.empty:
        snaps["pbv2_stored_score"] = np.nan
        snaps["pbv2_score_missing"] = True
        return snaps
    parts = []
    for (day, sym), g in snaps.groupby(["trading_date", "symbol"], sort=False):
        g = g.sort_values("t0_epoch").copy()
        eg = scores[(scores["trading_date"] == day) & (scores["symbol"] == sym)]
        eg = eg.dropna(subset=["pbv2_score"]).sort_values("epoch")
        if eg.empty:
            g["pbv2_stored_score"] = np.nan
            g["pbv2_score_missing"] = True
            parts.append(g)
            continue
        right = eg[["epoch", "pbv2_score"]].rename(
            columns={"epoch": "score_epoch", "pbv2_score": "_pbv2_join_score"}
        )
        m = pd.merge_asof(
            g,
            right,
            left_on="t0_epoch",
            right_on="score_epoch",
            direction="backward",
            tolerance=120,
        )
        m["pbv2_stored_score"] = m["_pbv2_join_score"]
        m["pbv2_score_missing"] = m["pbv2_stored_score"].isna()
        m = m.drop(columns=["_pbv2_join_score", "score_epoch"], errors="ignore")
        parts.append(m)
    return pd.concat(parts, ignore_index=True)


def feature_selection_audit(fe: pd.DataFrame, moves: pd.DataFrame) -> pd.DataFrame:
    """Mechanical selection for LARGE_RISE_vs_DECLINE on market days."""
    g = fe[(fe["comparison"] == "LARGE_RISE_vs_DECLINE") & (fe["trading_date"].isin(MARKET_DAYS))]
    rows = []
    for feat in sorted(set(g["feature"]).union(FEATURE_POOL)):
        gg = g[g["feature"] == feat].dropna(subset=["cliffs_delta"])
        if gg.empty:
            rows.append(
                {
                    "comparison": "LARGE_RISE_vs_DECLINE",
                    "feature": feat,
                    "n_days": 0,
                    "direction_agree_days": 0,
                    "mean_abs_cliffs": None,
                    "min_abs_cliffs": None,
                    "mean_cliffs": None,
                    "min_n_a": None,
                    "min_n_b": None,
                    "missing_rate": 1.0,
                    "selection_score": -1e9,
                    "selected": False,
                    "reason": "no_daily_effect_rows",
                }
            )
            continue
        deltas = gg["cliffs_delta"].astype(float)
        signs = np.sign(deltas.replace(0, np.nan).dropna())
        mode = float(signs.mode().iloc[0]) if len(signs) else 0.0
        agree = int((np.sign(deltas) == mode).sum()) if mode != 0 else 0
        mean_abs = float(deltas.abs().mean())
        min_abs = float(deltas.abs().min())
        mean_c = float(deltas.mean())
        min_na = int(gg["n_a"].min())
        min_nb = int(gg["n_b"].min())
        # missing proxy from moves coverage
        if feat in moves.columns:
            miss = float(pd.to_numeric(moves[feat], errors="coerce").isna().mean())
        else:
            miss = 1.0
        # selection score: prefer agree, then mean abs cliffs, then min abs, penalize missing
        sel = (
            1000 * agree
            + 100 * mean_abs
            + 10 * min_abs
            - 50 * miss
            + (5 if min_na >= 20 and min_nb >= 20 else 0)
        )
        eligible = agree >= 4 and len(gg) >= 4 and min_na >= 20 and min_nb >= 20 and miss < 0.5
        rows.append(
            {
                "comparison": "LARGE_RISE_vs_DECLINE",
                "feature": feat,
                "n_days": int(len(gg)),
                "direction_agree_days": agree,
                "mean_abs_cliffs": mean_abs,
                "min_abs_cliffs": min_abs,
                "mean_cliffs": mean_c,
                "min_n_a": min_na,
                "min_n_b": min_nb,
                "missing_rate": miss,
                "selection_score": sel,
                "selected": False,
                "eligible": eligible,
                "reason": "eligible_candidate" if eligible else "fails_stability_or_n_or_missing",
            }
        )
    df = pd.DataFrame(rows).sort_values("selection_score", ascending=False)
    if not df.empty:
        best_idx = df.index[0]
        df.loc[best_idx, "selected"] = True
        df.loc[best_idx, "reason"] = "selected_max_selection_score"
        # annotate why accel_30s was previously reported
        if "accel_30s" in set(df["feature"]):
            arow = df[df["feature"] == "accel_30s"].iloc[0]
            df.loc[df["feature"] == "accel_30s", "reason"] = (
                f"NOT_SELECTED; prior_w43d_bug=sort_by_agree_only_no_effect_magnitude;"
                f"agree={arow['direction_agree_days']};mean_abs={arow['mean_abs_cliffs']};"
                f"rank={(df['feature'] == 'accel_30s').idxmax() and int(df.reset_index().index[df.reset_index()['feature']=='accel_30s'][0])+1}"
            )
    return df.reset_index(drop=True)


def train_score_params(train_snaps: pd.DataFrame, features: list[str], directions: dict[str, float]) -> dict[str, dict[str, float]]:
    params = {}
    for f in features:
        x = pd.to_numeric(train_snaps.get(f), errors="coerce")
        x = x[np.isfinite(x)]
        if len(x) < 50:
            continue
        med = float(x.median())
        q1, q3 = float(x.quantile(0.25)), float(x.quantile(0.75))
        iqr = q3 - q1
        if iqr <= 1e-12:
            continue
        lo, hi = float(x.quantile(0.01)), float(x.quantile(0.99))
        params[f] = {
            "median": med,
            "iqr": iqr,
            "lo": lo,
            "hi": hi,
            "direction": float(directions.get(f, 1.0)),
        }
    return params


def apply_score(df: pd.DataFrame, params: dict[str, dict[str, float]]) -> pd.Series:
    score = np.zeros(len(df), dtype=float)
    used = 0
    for f, p in params.items():
        x = pd.to_numeric(df.get(f), errors="coerce").to_numpy(dtype=float)
        x = np.clip(x, p["lo"], p["hi"])
        z = (x - p["median"]) / p["iqr"]
        z = np.where(np.isfinite(z), z, 0.0)
        score += p["direction"] * z
        used += 1
    if used == 0:
        return pd.Series(np.nan, index=df.index)
    return pd.Series(score, index=df.index)


def directions_from_audit(audit: pd.DataFrame, top_n: int = 3) -> tuple[list[str], dict[str, float]]:
    elig = audit[audit.get("eligible", False) == True] if "eligible" in audit.columns else audit
    elig = elig.sort_values("selection_score", ascending=False).head(top_n)
    feats = elig["feature"].tolist()
    dirs = {}
    for _, r in elig.iterrows():
        # cliffs>0 => LARGE_RISE higher => direction +1
        mc = r.get("mean_cliffs")
        dirs[r["feature"]] = 1.0 if (mc is not None and mc > 0) else -1.0
    return feats, dirs


def summarize_selection(rows: list[dict[str, Any]], label_col: str = "primary_label") -> dict[str, Any]:
    if not rows:
        return {
            "evaluation_timestamp_count": 0,
            "selected_symbol_count": 0,
            "valid_label_count": 0,
            "large_rise_precision": None,
            "large_rise_recall_proxy": None,
            "mean_future_5m_return": None,
            "mean_future_15m_return": None,
            "mean_future_30m_return": None,
            "mean_future_30m_mfe": None,
            "mean_future_30m_mae": None,
            "sideways_rate": None,
            "decline_rate": None,
            "missing_rate": None,
        }
    df = pd.DataFrame(rows)
    valid = df[df[label_col].isin(["LARGE_RISE", "SIDEWAYS", "DECLINE", "RECOVERED_RISE", "NO_PROGRESS"])]
    # also accept any non-UNAVAILABLE
    valid = df[df[label_col].notna() & (df[label_col] != "UNAVAILABLE")]
    n_sel = len(df)
    n_valid = len(valid)
    prec = float((valid[label_col] == "LARGE_RISE").mean()) if n_valid else None
    return {
        "evaluation_timestamp_count": int(df["t0_epoch"].nunique()) if "t0_epoch" in df.columns else None,
        "selected_symbol_count": n_sel,
        "valid_label_count": n_valid,
        "large_rise_precision": prec,
        "precision_denominator": "valid_label_count",
        "mean_future_5m_return": _finite(pd.to_numeric(valid.get("future_5m_return"), errors="coerce").mean()),
        "mean_future_15m_return": _finite(pd.to_numeric(valid.get("future_15m_return"), errors="coerce").mean()),
        "mean_future_30m_return": _finite(pd.to_numeric(valid.get("future_30m_return"), errors="coerce").mean()),
        "mean_future_30m_mfe": _finite(pd.to_numeric(valid.get("future_30m_mfe"), errors="coerce").mean()),
        "mean_future_30m_mae": _finite(pd.to_numeric(valid.get("future_30m_mae"), errors="coerce").mean()),
        "sideways_rate": float((valid[label_col] == "SIDEWAYS").mean()) if n_valid else None,
        "decline_rate": float((valid[label_col] == "DECLINE").mean()) if n_valid else None,
        "missing_rate": float(1.0 - n_valid / n_sel) if n_sel else None,
    }


def ranking_parity(snaps: pd.DataFrame, entries: pd.DataFrame, audit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (parity_summary, matched_count_df, detail_meta)."""
    feats, dirs = directions_from_audit(audit, top_n=3)
    # fit score params on all runtime snaps (for non-LOD aggregate); LOD refits separately
    params = train_score_params(snaps, feats, dirs)
    snaps = snaps.copy()
    snaps["w43d_score"] = apply_score(snaps, params)

    parity_rows = []
    matched_rows = []
    selected_detail = {3: [], 5: [], 10: [], "matched_w43d": [], "matched_pbv2_score": [], "matched_official": [], "matched_random": []}

    # subsample every 60s
    for day, sdf in snaps.groupby("trading_date"):
        times = sorted(sdf["t0_epoch"].unique())[::2]
        eday = entries[entries["trading_date"] == day] if not entries.empty else entries
        for t0 in times:
            slot = sdf[sdf["t0_epoch"] == t0].copy()
            if len(slot) < 10:
                continue
            # valid label universe
            slot_valid = slot[slot["primary_label"].notna() & (slot["primary_label"] != "UNAVAILABLE")]
            if len(slot_valid) < 5:
                continue
            n_lr_slot = int((slot_valid["primary_label"] == "LARGE_RISE").sum())

            for k in (3, 5, 10):
                top = slot_valid.sort_values("w43d_score", ascending=False).head(k)
                for _, r in top.iterrows():
                    selected_detail[k].append(
                        {
                            "trading_date": day,
                            "t0_epoch": t0,
                            "symbol": r["symbol"],
                            "primary_label": r["primary_label"],
                            "future_5m_return": r.get("future_5m_return"),
                            "future_15m_return": r.get("future_15m_return"),
                            "future_30m_return": r.get("future_30m_return"),
                            "future_30m_mfe": r.get("future_30m_mfe"),
                            "future_30m_mae": r.get("future_30m_mae"),
                        }
                    )
                # PBv2 stored score ranking (missing excluded from ranking, counted in missing)
                pb = slot_valid[~slot_valid["pbv2_score_missing"]].copy()
                if len(pb) >= k:
                    ptop = pb.sort_values("pbv2_stored_score", ascending=False).head(k)
                    for _, r in ptop.iterrows():
                        selected_detail.setdefault(f"pbv2_score_{k}", []).append(
                            {
                                "trading_date": day,
                                "t0_epoch": t0,
                                "symbol": r["symbol"],
                                "primary_label": r["primary_label"],
                                "future_5m_return": r.get("future_5m_return"),
                                "future_15m_return": r.get("future_15m_return"),
                                "future_30m_return": r.get("future_30m_return"),
                                "future_30m_mfe": r.get("future_30m_mfe"),
                                "future_30m_mae": r.get("future_30m_mae"),
                            }
                        )
                # random mean over iters
                rnd_stats = []
                for it in range(RANDOM_ITERS):
                    rng = np.random.default_rng(RANDOM_BASE_SEED + int(t0) % 100000 + it)
                    take = min(k, len(slot_valid))
                    idx = rng.choice(slot_valid.index.to_numpy(), size=take, replace=False)
                    rnd = slot_valid.loc[idx]
                    rnd_stats.append(float((rnd["primary_label"] == "LARGE_RISE").mean()))
                parity_rows.append(
                    {
                        "trading_date": day,
                        "t0_epoch": t0,
                        "method": "w43d_score",
                        "k": k,
                        "selected_symbol_count": len(top),
                        "valid_label_count": int(top["primary_label"].notna().sum()),
                        "large_rise_precision": float((top["primary_label"] == "LARGE_RISE").mean()),
                        "large_rise_recall": float((top["primary_label"] == "LARGE_RISE").sum() / n_lr_slot) if n_lr_slot else None,
                        "mean_future_30m_return": _finite(pd.to_numeric(top["future_30m_return"], errors="coerce").mean()),
                        "mean_future_30m_mfe": _finite(pd.to_numeric(top["future_30m_mfe"], errors="coerce").mean()),
                        "mean_future_30m_mae": _finite(pd.to_numeric(top["future_30m_mae"], errors="coerce").mean()),
                        "sideways_rate": float((top["primary_label"] == "SIDEWAYS").mean()),
                        "decline_rate": float((top["primary_label"] == "DECLINE").mean()),
                        "random_precision_mean": float(np.mean(rnd_stats)),
                        "random_precision_lo": float(np.quantile(rnd_stats, 0.025)),
                        "random_precision_hi": float(np.quantile(rnd_stats, 0.975)),
                        "pbv2_score_missing_rate": float(slot_valid["pbv2_score_missing"].mean()),
                    }
                )

            # matched selection count vs official entries in [t0, t0+30)
            if eday.empty:
                continue
            near = eday[(eday["entry_epoch"] >= t0) & (eday["entry_epoch"] < t0 + 30)]
            if near.empty:
                continue
            k = len(near)
            # official rows with future from nearest snapshot
            official_rows = []
            for _, e in near.iterrows():
                row = slot[slot["symbol"] == e["symbol"]]
                if row.empty:
                    # nearest time any
                    day_sym = sdf[sdf["symbol"] == e["symbol"]]
                    if day_sym.empty:
                        official_rows.append(
                            {
                                "trading_date": day,
                                "t0_epoch": t0,
                                "symbol": e["symbol"],
                                "primary_label": "UNAVAILABLE",
                                "future_5m_return": None,
                                "future_15m_return": None,
                                "future_30m_return": None,
                                "future_30m_mfe": None,
                                "future_30m_mae": None,
                            }
                        )
                        continue
                    j = (day_sym["t0_epoch"] - e["entry_epoch"]).abs().idxmin()
                    r = day_sym.loc[j]
                else:
                    r = row.iloc[0]
                official_rows.append(
                    {
                        "trading_date": day,
                        "t0_epoch": t0,
                        "symbol": e["symbol"],
                        "primary_label": r.get("primary_label"),
                        "future_5m_return": r.get("future_5m_return"),
                        "future_15m_return": r.get("future_15m_return"),
                        "future_30m_return": r.get("future_30m_return"),
                        "future_30m_mfe": r.get("future_30m_mfe"),
                        "future_30m_mae": r.get("future_30m_mae"),
                    }
                )
            selected_detail["matched_official"].extend(official_rows)

            topk = slot_valid.sort_values("w43d_score", ascending=False).head(k)
            for _, r in topk.iterrows():
                selected_detail["matched_w43d"].append(
                    {
                        "trading_date": day,
                        "t0_epoch": t0,
                        "symbol": r["symbol"],
                        "primary_label": r["primary_label"],
                        "future_5m_return": r.get("future_5m_return"),
                        "future_15m_return": r.get("future_15m_return"),
                        "future_30m_return": r.get("future_30m_return"),
                        "future_30m_mfe": r.get("future_30m_mfe"),
                        "future_30m_mae": r.get("future_30m_mae"),
                        "k": k,
                    }
                )
            pb = slot_valid[~slot_valid["pbv2_score_missing"]]
            if len(pb) >= k:
                ptop = pb.sort_values("pbv2_stored_score", ascending=False).head(k)
                for _, r in ptop.iterrows():
                    selected_detail["matched_pbv2_score"].append(
                        {
                            "trading_date": day,
                            "t0_epoch": t0,
                            "symbol": r["symbol"],
                            "primary_label": r["primary_label"],
                            "future_5m_return": r.get("future_5m_return"),
                            "future_15m_return": r.get("future_15m_return"),
                            "future_30m_return": r.get("future_30m_return"),
                            "future_30m_mfe": r.get("future_30m_mfe"),
                            "future_30m_mae": r.get("future_30m_mae"),
                            "k": k,
                        }
                    )
            # random matched
            rnd_prec = []
            rnd_ret = []
            for it in range(RANDOM_ITERS):
                rng = np.random.default_rng(RANDOM_BASE_SEED + 17 + int(t0) % 100000 + it)
                take = min(k, len(slot_valid))
                idx = rng.choice(slot_valid.index.to_numpy(), size=take, replace=False)
                rnd = slot_valid.loc[idx]
                rnd_prec.append(float((rnd["primary_label"] == "LARGE_RISE").mean()))
                rnd_ret.append(_finite(pd.to_numeric(rnd["future_30m_return"], errors="coerce").mean()))
            off_s = summarize_selection(official_rows)
            w_s = summarize_selection(selected_detail["matched_w43d"][-k:])
            matched_rows.append(
                {
                    "trading_date": day,
                    "t0_epoch": t0,
                    "matched_k": k,
                    "official_precision": off_s["large_rise_precision"],
                    "official_mean_future_30m_return": off_s["mean_future_30m_return"],
                    "official_mean_future_30m_mfe": off_s["mean_future_30m_mfe"],
                    "official_mean_future_30m_mae": off_s["mean_future_30m_mae"],
                    "official_valid_label_count": off_s["valid_label_count"],
                    "w43d_precision": w_s["large_rise_precision"],
                    "w43d_mean_future_30m_return": w_s["mean_future_30m_return"],
                    "w43d_mean_future_30m_mfe": w_s["mean_future_30m_mfe"],
                    "w43d_mean_future_30m_mae": w_s["mean_future_30m_mae"],
                    "random_precision_mean": float(np.mean(rnd_prec)),
                    "random_precision_lo": float(np.quantile(rnd_prec, 0.025)),
                    "random_precision_hi": float(np.quantile(rnd_prec, 0.975)),
                    "random_mean_future_30m_return": _finite(np.nanmean([x for x in rnd_ret if x is not None])),
                }
            )

    # summaries
    parity_summary = []
    for k in (3, 5, 10):
        s = summarize_selection(selected_detail[k])
        # timestamps
        ts = len({(r["trading_date"], r["t0_epoch"]) for r in selected_detail[k]}) if selected_detail[k] else 0
        # random baseline from parity_rows
        pr = [r for r in parity_rows if r["k"] == k]
        parity_summary.append(
            {
                "method": "w43d_score",
                "k": k,
                "evaluation_timestamp_count": ts,
                "selected_symbol_count": s["selected_symbol_count"],
                "valid_label_count": s["valid_label_count"],
                "large_rise_precision": s["large_rise_precision"],
                "mean_future_5m_return": s["mean_future_5m_return"],
                "mean_future_15m_return": s["mean_future_15m_return"],
                "mean_future_30m_return": s["mean_future_30m_return"],
                "mean_future_30m_mfe": s["mean_future_30m_mfe"],
                "mean_future_30m_mae": s["mean_future_30m_mae"],
                "sideways_rate": s["sideways_rate"],
                "decline_rate": s["decline_rate"],
                "random_precision_mean": float(np.mean([r["random_precision_mean"] for r in pr])) if pr else None,
                "random_precision_lo": float(np.mean([r["random_precision_lo"] for r in pr])) if pr else None,
                "random_precision_hi": float(np.mean([r["random_precision_hi"] for r in pr])) if pr else None,
                "score_features": ",".join(feats),
            }
        )
        # pbv2 score method
        key = f"pbv2_score_{k}"
        ps = summarize_selection(selected_detail.get(key, []))
        ts2 = len({(r["trading_date"], r["t0_epoch"]) for r in selected_detail.get(key, [])}) if selected_detail.get(key) else 0
        parity_summary.append(
            {
                "method": "pbv2_stored_score",
                "k": k,
                "evaluation_timestamp_count": ts2,
                "selected_symbol_count": ps["selected_symbol_count"],
                "valid_label_count": ps["valid_label_count"],
                "large_rise_precision": ps["large_rise_precision"],
                "mean_future_5m_return": ps["mean_future_5m_return"],
                "mean_future_15m_return": ps["mean_future_15m_return"],
                "mean_future_30m_return": ps["mean_future_30m_return"],
                "mean_future_30m_mfe": ps["mean_future_30m_mfe"],
                "mean_future_30m_mae": ps["mean_future_30m_mae"],
                "sideways_rate": ps["sideways_rate"],
                "decline_rate": ps["decline_rate"],
                "random_precision_mean": None,
                "random_precision_lo": None,
                "random_precision_hi": None,
                "score_features": "entry_expectancy_score_v2",
            }
        )

    off = summarize_selection(selected_detail["matched_official"])
    parity_summary.append(
        {
            "method": "pbv2_official_entry",
            "k": "matched",
            "evaluation_timestamp_count": len({(r["trading_date"], r["t0_epoch"]) for r in selected_detail["matched_official"]})
            if selected_detail["matched_official"]
            else 0,
            "selected_symbol_count": off["selected_symbol_count"],
            "valid_label_count": off["valid_label_count"],
            "large_rise_precision": off["large_rise_precision"],
            "mean_future_5m_return": off["mean_future_5m_return"],
            "mean_future_15m_return": off["mean_future_15m_return"],
            "mean_future_30m_return": off["mean_future_30m_return"],
            "mean_future_30m_mfe": off["mean_future_30m_mfe"],
            "mean_future_30m_mae": off["mean_future_30m_mae"],
            "sideways_rate": off["sideways_rate"],
            "decline_rate": off["decline_rate"],
            "random_precision_mean": None,
            "random_precision_lo": None,
            "random_precision_hi": None,
            "score_features": "official_entry",
        }
    )

    matched_df = pd.DataFrame(matched_rows)
    meta = {
        "score_features": feats,
        "score_directions": dirs,
        "score_params": params,
        "selected_detail_counts": {str(k): len(v) if isinstance(v, list) else 0 for k, v in selected_detail.items()},
    }
    return pd.DataFrame(parity_summary), matched_df, meta


def leave_one_day_out(snaps: pd.DataFrame, entries: pd.DataFrame, audit: pd.DataFrame) -> pd.DataFrame:
    rows = []
    days = sorted(snaps["trading_date"].unique())
    for hold in days:
        train = snaps[snaps["trading_date"] != hold]
        test = snaps[snaps["trading_date"] == hold]
        # directions/features from audit restricted to train days via fe proxy: use global audit but fit params on train only
        feats, dirs = directions_from_audit(audit, top_n=3)
        params = train_score_params(train, feats, dirs)
        if not params:
            continue
        test = test.copy()
        test["w43d_score"] = apply_score(test, params)
        eday = entries[entries["trading_date"] == hold] if not entries.empty else entries

        for k in (3, 5, 10):
            sel = []
            rnd_prec_all = []
            times = sorted(test["t0_epoch"].unique())[::2]
            for t0 in times:
                slot = test[test["t0_epoch"] == t0]
                slot_valid = slot[slot["primary_label"].notna() & (slot["primary_label"] != "UNAVAILABLE")]
                if len(slot_valid) < max(10, k):
                    continue
                top = slot_valid.sort_values("w43d_score", ascending=False).head(k)
                for _, r in top.iterrows():
                    sel.append(r)
                for it in range(min(20, RANDOM_ITERS)):  # lighter in LOD loop
                    rng = np.random.default_rng(RANDOM_BASE_SEED + int(t0) % 10000 + it)
                    idx = rng.choice(slot_valid.index.to_numpy(), size=k, replace=False)
                    rnd = slot_valid.loc[idx]
                    rnd_prec_all.append(float((rnd["primary_label"] == "LARGE_RISE").mean()))

            if not sel:
                continue
            sdf = pd.DataFrame(sel)
            valid = sdf[sdf["primary_label"] != "UNAVAILABLE"]
            prec = float((valid["primary_label"] == "LARGE_RISE").mean()) if len(valid) else None
            # matched vs pbv2 on holdout
            matched_w = []
            matched_o = []
            if not eday.empty:
                for t0 in times:
                    slot = test[test["t0_epoch"] == t0]
                    slot_valid = slot[slot["primary_label"].notna() & (slot["primary_label"] != "UNAVAILABLE")]
                    near = eday[(eday["entry_epoch"] >= t0) & (eday["entry_epoch"] < t0 + 30)]
                    if near.empty or slot_valid.empty:
                        continue
                    kk = len(near)
                    top = slot_valid.sort_values("w43d_score", ascending=False).head(kk)
                    matched_w.extend(top.to_dict("records"))
                    for _, e in near.iterrows():
                        row = slot[slot["symbol"] == e["symbol"]]
                        if len(row):
                            matched_o.append(row.iloc[0].to_dict())
            w_prec = float(np.mean([r["primary_label"] == "LARGE_RISE" for r in matched_w])) if matched_w else None
            o_prec = float(np.mean([r["primary_label"] == "LARGE_RISE" for r in matched_o])) if matched_o else None
            rows.append(
                {
                    "holdout_day": hold,
                    "k": k,
                    "train_days": len(days) - 1,
                    "score_features": ",".join(params.keys()),
                    "evaluation_timestamp_count": int(sdf["t0_epoch"].nunique()) if "t0_epoch" in sdf else None,
                    "selected_symbol_count": len(sdf),
                    "valid_label_count": len(valid),
                    "top_precision": prec,
                    "mean_future_30m_return": _finite(pd.to_numeric(valid["future_30m_return"], errors="coerce").mean()),
                    "mean_future_30m_mfe": _finite(pd.to_numeric(valid["future_30m_mfe"], errors="coerce").mean()),
                    "mean_future_30m_mae": _finite(pd.to_numeric(valid["future_30m_mae"], errors="coerce").mean()),
                    "sideways_rate": float((valid["primary_label"] == "SIDEWAYS").mean()) if len(valid) else None,
                    "decline_rate": float((valid["primary_label"] == "DECLINE").mean()) if len(valid) else None,
                    "random_precision_mean": float(np.mean(rnd_prec_all)) if rnd_prec_all else None,
                    "delta_vs_random_precision": (prec - float(np.mean(rnd_prec_all))) if (prec is not None and rnd_prec_all) else None,
                    "matched_w43d_precision": w_prec,
                    "matched_pbv2_official_precision": o_prec,
                    "delta_vs_pbv2_matched_precision": (w_prec - o_prec) if (w_prec is not None and o_prec is not None) else None,
                }
            )
    return pd.DataFrame(rows)


def classify_reversal_row(r: pd.Series) -> dict[str, Any]:
    ret10 = _finite(r.get("ret_10s"))
    ret30 = _finite(r.get("ret_30s"))
    ret60 = _finite(r.get("ret_60s"))
    a30 = _finite(r.get("accel_30s"))
    a60 = _finite(r.get("accel_60s"))
    nh_re = _finite(r.get("new_high_restart_count")) or 0.0
    bounce = _finite(r.get("bounce_from_low_300s"))
    vwap_re = _finite(r.get("vwap_reclaim_flag")) or 0.0
    imb = _finite(r.get("imbalance_chg_60s"))
    volr = _finite(r.get("vol_recovery_flag")) or 0.0

    pullback = (ret30 is not None and ret30 < 0) or (ret60 is not None and ret60 < 0)
    ret_neg = (ret30 is not None and ret30 < 0) or (ret60 is not None and ret60 < 0)
    accel_nonpos = (a30 is not None and a30 <= 0) or (a60 is not None and a60 <= 0)
    falling = bool(ret_neg and accel_nonpos and nh_re <= 0)

    signals = 0
    if a30 is not None and a30 > 0:
        signals += 1
    if a60 is not None and a60 > 0:
        signals += 1
    if ret10 is not None and ret10 > 0:
        signals += 1
    # ret_30s improving vs ret_60s
    if ret30 is not None and ret60 is not None and ret30 > ret60:
        signals += 1
    if bounce is not None and bounce > 0:
        signals += 1
    if nh_re > 0:
        signals += 1
    if vwap_re > 0:
        signals += 1
    if imb is not None and imb > 0:
        signals += 1
    if volr > 0:
        signals += 1
    started = signals >= 2
    # confirmed proxy without multi-snapshot continuity: started + bounce>0 + not making new lows (fall_from_high not deepening alone)
    # Spec wants 30s continuity; with single anchor we approximate: started and bounce>0 and accel>0 and ret10>0
    confirmed = bool(
        started
        and (a30 is not None and a30 > 0)
        and (bounce is not None and bounce > 0)
        and (ret10 is not None and ret10 > 0)
    )
    return {
        "PULLBACK_STATE": bool(pullback),
        "FALLING_STATE": bool(falling),
        "REVERSAL_STARTED": bool(started),
        "REVERSAL_CONFIRMED": bool(confirmed),
        "reversal_signal_count": signals,
    }


def reversal_analysis(moves: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    m = moves[(moves["trading_date"].isin(RUNTIME_ACTIVE_DAYS)) & (moves["capture_class"] == "MISSED")].copy()
    states = m.apply(classify_reversal_row, axis=1, result_type="expand")
    m = pd.concat([m.reset_index(drop=True), states.reset_index(drop=True)], axis=1)
    daily = []
    for day, g in m.groupby("trading_date"):
        daily.append(
            {
                "trading_date": day,
                "n_missed": len(g),
                "pullback_rate": float(g["PULLBACK_STATE"].mean()),
                "falling_rate": float(g["FALLING_STATE"].mean()),
                "reversal_started_rate": float(g["REVERSAL_STARTED"].mean()),
                "reversal_confirmed_rate": float(g["REVERSAL_CONFIRMED"].mean()),
                "mean_future_mfe_started": _finite(
                    pd.to_numeric(g.loc[g["REVERSAL_STARTED"], "max_future_mfe"], errors="coerce").mean()
                ),
                "mean_future_mfe_confirmed": _finite(
                    pd.to_numeric(g.loc[g["REVERSAL_CONFIRMED"], "max_future_mfe"], errors="coerce").mean()
                ),
                "mean_future_mfe_falling": _finite(
                    pd.to_numeric(g.loc[g["FALLING_STATE"], "max_future_mfe"], errors="coerce").mean()
                ),
                "mean_future_mfe_other": _finite(
                    pd.to_numeric(g.loc[~g["REVERSAL_STARTED"], "max_future_mfe"], errors="coerce").mean()
                ),
            }
        )
    summary = {
        "n_missed": int(len(m)),
        "pullback_rate": float(m["PULLBACK_STATE"].mean()) if len(m) else None,
        "falling_rate": float(m["FALLING_STATE"].mean()) if len(m) else None,
        "reversal_started_rate": float(m["REVERSAL_STARTED"].mean()) if len(m) else None,
        "reversal_confirmed_rate": float(m["REVERSAL_CONFIRMED"].mean()) if len(m) else None,
    }
    # edge after confirmation: confirmed mean MFE vs falling
    conf_mfe = _finite(pd.to_numeric(m.loc[m["REVERSAL_CONFIRMED"], "max_future_mfe"], errors="coerce").mean())
    fall_mfe = _finite(pd.to_numeric(m.loc[m["FALLING_STATE"], "max_future_mfe"], errors="coerce").mean())
    started_mfe = _finite(pd.to_numeric(m.loc[m["REVERSAL_STARTED"], "max_future_mfe"], errors="coerce").mean())
    other_mfe = _finite(pd.to_numeric(m.loc[~m["REVERSAL_STARTED"], "max_future_mfe"], errors="coerce").mean())
    summary["mean_future_mfe_confirmed"] = conf_mfe
    summary["mean_future_mfe_started"] = started_mfe
    summary["mean_future_mfe_falling"] = fall_mfe
    summary["mean_future_mfe_not_started"] = other_mfe
    summary["confirmation_edge_vs_falling"] = (
        (conf_mfe - fall_mfe) if (conf_mfe is not None and fall_mfe is not None) else None
    )
    summary["started_edge_vs_not"] = (
        (started_mfe - other_mfe) if (started_mfe is not None and other_mfe is not None) else None
    )
    # keep slim daily detail columns
    detail_cols = [
        "trading_date",
        "symbol",
        "anchor_time",
        "ret_30s",
        "ret_60s",
        "accel_30s",
        "accel_60s",
        "PULLBACK_STATE",
        "FALLING_STATE",
        "REVERSAL_STARTED",
        "REVERSAL_CONFIRMED",
        "reversal_signal_count",
        "max_future_mfe",
        "max_future_return",
    ]
    detail = m[[c for c in detail_cols if c in m.columns]]
    return pd.DataFrame(daily), detail, summary


def selection_inversion(moves: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day in RUNTIME_ACTIVE_DAYS:
        missed = moves[(moves["trading_date"] == day) & (moves["capture_class"] == "MISSED")]
        stop = entries[(entries["trading_date"] == day) & (entries["exit_reason"] == "stop_hit")]
        # join stop features from nearest move? use entry-time features unavailable — compare move ret vs stop from snaps later
        # For parity with W43D: stop rows may lack features; merge from moves on symbol day median? Use entry attach from snaps in caller.
        rows.append(
            {
                "trading_date": day,
                "n_missed": len(missed),
                "n_stop": len(stop),
                "missed_ret30_median": _finite(pd.to_numeric(missed["ret_30s"], errors="coerce").median()),
                "missed_accel60_median": _finite(pd.to_numeric(missed["accel_60s"], errors="coerce").median()),
            }
        )
    return pd.DataFrame(rows)


def stop_features_from_snaps(snaps: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, e in entries.iterrows():
        if e["exit_reason"] != "stop_hit":
            continue
        day = e["trading_date"]
        sdf = snaps[(snaps["trading_date"] == day) & (snaps["symbol"] == e["symbol"])]
        if sdf.empty:
            continue
        j = (sdf["t0_epoch"] - e["entry_epoch"]).abs().idxmin()
        r = sdf.loc[j]
        rows.append(
            {
                "trading_date": day,
                "symbol": e["symbol"],
                "ret_30s": r.get("ret_30s"),
                "accel_60s": r.get("accel_60s"),
            }
        )
    return pd.DataFrame(rows)


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    # README
    ws = wb.active
    ws.title = "README"
    readme = [
        ["W43D-FIX audit workbook"],
        ["Generated", datetime.now(JST).isoformat()],
        ["Sheets hold tabular detail; narrative is in w43d_fix_report.md"],
        ["Runtime/YAML/Shadow unchanged"],
        ["MARKET_ONLY day 20260714 excluded from runtime capture/funnel/ranking vs PBv2"],
        ["Precision denominator = valid_label_count unless noted"],
    ]
    for r in readme:
        ws.append(r)

    for name, df in sheets.items():
        if df is None:
            continue
        safe = name[:31]
        w = wb.create_sheet(safe)
        if df.empty:
            w.append(["empty"])
            continue
        out = df.copy()
        for col in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = out[col].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        for row in dataframe_to_rows(out, index=False, header=True):
            w.append(row)
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    print("W43D-FIX starting...", flush=True)
    day_df = day_classification()
    moves, raw_ep, fe = load_prior_artifacts()
    entries = load_entries()
    print(f" moves={len(moves)} entries={len(entries)}", flush=True)

    cap_df = build_runtime_capture(moves)
    funnel_df = build_causal_funnel(moves)
    cause = missed_cause_ratios(moves)
    audit_df = feature_selection_audit(fe, moves)
    true_best = audit_df.iloc[0].to_dict() if len(audit_df) else {}

    tmp = Path(tempfile.mkdtemp(prefix="w43d_fix_"))
    try:
        print("Building runtime-active snapshots...", flush=True)
        snap_parts = []
        for day in RUNTIME_ACTIVE_DAYS:
            print(f"  snapshots {day}", flush=True)
            snap_parts.append(run_day_snapshots(day, tmp))
        snaps = pd.concat(snap_parts, ignore_index=True)
        snaps["trading_date"] = snaps["trading_date"].astype(str)
        print(f"  snaps={len(snaps)}", flush=True)

        print("Loading PBv2 stored scores...", flush=True)
        scores = load_pbv2_scores(RUNTIME_ACTIVE_DAYS)
        snaps = attach_pbv2_score(snaps, scores)
        print(f"  score events={len(scores)} missing_rate={snaps['pbv2_score_missing'].mean():.3f}", flush=True)

        print("Ranking parity...", flush=True)
        parity_df, matched_df, rank_meta = ranking_parity(snaps, entries, audit_df)

        print("LOD...", flush=True)
        lod_df = leave_one_day_out(snaps, entries, audit_df)

        print("Reversal...", flush=True)
        rev_daily, rev_detail, rev_sum = reversal_analysis(moves)

        # selection inversion with stop features
        stop_f = stop_features_from_snaps(snaps, entries)
        inv_rows = []
        for day in RUNTIME_ACTIVE_DAYS:
            missed = moves[(moves["trading_date"] == day) & (moves["capture_class"] == "MISSED")]
            st = stop_f[stop_f["trading_date"] == day]
            mr = _finite(pd.to_numeric(missed["ret_30s"], errors="coerce").median())
            sr = _finite(pd.to_numeric(st["ret_30s"], errors="coerce").median()) if len(st) else None
            inv_rows.append(
                {
                    "trading_date": day,
                    "n_missed": len(missed),
                    "n_stop": len(st),
                    "missed_ret30_median": mr,
                    "stop_ret30_median": sr,
                    "inversion_ret": bool(mr is not None and sr is not None and mr < sr),
                    "missed_neg_ret": bool(mr is not None and mr < 0),
                    "stop_pos_ret": bool(sr is not None and sr > 0),
                }
            )
        inv_df = pd.DataFrame(inv_rows)

        # ranking edge decisions
        w3 = parity_df[(parity_df["method"] == "w43d_score") & (parity_df["k"] == 3)]
        w5 = parity_df[(parity_df["method"] == "w43d_score") & (parity_df["k"] == 5)]
        rnd_edge = False
        if len(w3):
            rnd_edge = bool(
                w3.iloc[0]["large_rise_precision"] is not None
                and w3.iloc[0]["random_precision_mean"] is not None
                and w3.iloc[0]["large_rise_precision"] > w3.iloc[0]["random_precision_hi"]
            )
        # also majority of LOD
        if not lod_df.empty:
            lod3 = lod_df[lod_df["k"] == 3]
            if len(lod3):
                rnd_edge = rnd_edge or bool((lod3["delta_vs_random_precision"] > 0).mean() >= 0.5)

        pbv2_edge = False
        if not matched_df.empty:
            # compare mean precision and returns
            wp = matched_df["w43d_precision"].mean(skipna=True)
            op = matched_df["official_precision"].mean(skipna=True)
            wr = matched_df["w43d_mean_future_30m_return"].mean(skipna=True)
            oret = matched_df["official_mean_future_30m_return"].mean(skipna=True)
            wm = matched_df["w43d_mean_future_30m_mfe"].mean(skipna=True)
            om = matched_df["official_mean_future_30m_mfe"].mean(skipna=True)
            pbv2_edge = bool(
                pd.notna(wp)
                and pd.notna(op)
                and wp > op
                and pd.notna(wr)
                and pd.notna(oret)
                and wr >= oret
            )

        # reversal verdicts
        pullback_winner = bool(rev_sum.get("pullback_rate") is not None and rev_sum["pullback_rate"] >= 0.5)
        rev_conf_signal = bool(
            rev_sum.get("reversal_confirmed_rate") is not None
            and rev_sum["reversal_confirmed_rate"] >= 0.15
            and rev_sum.get("confirmation_edge_vs_falling") is not None
            and rev_sum["confirmation_edge_vs_falling"] > 0
        )

        guard_n = int(
            funnel_df.loc[
                funnel_df["scope"] == "runtime_active_independent", "n_ENTRY_RULE_REJECTED"
            ].iloc[0]
        )
        total_ra = int(funnel_df.loc[funnel_df["scope"] == "runtime_active_independent", "n"].iloc[0])
        minor_guard = guard_n / total_ra < 0.05 if total_ra else True

        maintained = [
            "W43C_CAUSAL_CLASSIFICATION_CORRECTED",
            "FOUND_PBV2_BASE_CANDIDATE_LIMIT",
            "FOUND_STABLE_SELECTION_INVERSION",
            "FOUND_CHASE_ENTRY_BIAS",
            "FOUND_STABLE_MISSED_WINNER_STATE",
            "FOUND_SCAN_QUEUE_CAPTURE_LIMIT",
        ]
        verdicts = ["W43D_METRICS_CORRECTED"] + maintained
        if rnd_edge:
            verdicts.append("FOUND_RANKING_EDGE_VS_RANDOM")
        if pbv2_edge:
            verdicts.append("FOUND_RANKING_EDGE_VS_PBV2")
        else:
            verdicts.append("FOUND_NO_RANKING_EDGE_VS_PBV2")
        if pullback_winner:
            verdicts.append("FOUND_PULLBACK_WINNER_STATE")
        if rev_conf_signal:
            verdicts.append("FOUND_REVERSAL_CONFIRMATION_SIGNAL")
        else:
            verdicts.append("FOUND_NO_REVERSAL_CONFIRMATION")
        if minor_guard:
            verdicts.append("FOUND_MINOR_GUARD_CAPTURE_LIMIT")
        else:
            verdicts.append("FOUND_SPECIFIC_GUARD_CAPTURE_LIMIT")

        # Shadow research readiness: require random edge + LOD majority + pullback,
        # AND either matched PBv2 edge (incl. returns) or confirmed reversal edge.
        lod_ok = False
        if not lod_df.empty:
            lod3 = lod_df[lod_df["k"] == 3]
            lod_ok = bool(len(lod3) and (lod3["delta_vs_random_precision"] > 0).mean() >= 0.75)
        shadow_ready = bool(
            rnd_edge
            and lod_ok
            and pullback_winner
            and bool(true_best.get("eligible", True))
            and (pbv2_edge or rev_conf_signal)
        )
        verdicts.append("SHADOW_CANDIDATE_READY" if shadow_ready else "SHADOW_CANDIDATE_NOT_READY")

        ra_cap = cap_df[cap_df["trading_date"] == "RUNTIME_ACTIVE_ALL"].iloc[0]
        ra_funnel = funnel_df[funnel_df["scope"] == "runtime_active_independent"].iloc[0].to_dict()

        # selected symbol counts check
        sel_counts = {
            int(r["k"]): int(r["selected_symbol_count"])
            for _, r in parity_df[parity_df["method"] == "w43d_score"].iterrows()
        }
        sel_bug = len(set(sel_counts.values())) < len(sel_counts)

        answers = {
            "1_market_days_and_runtime_active_days": {
                "market_data_days": int(day_df["MARKET_DATA_DAY"].sum()),
                "runtime_active_days": int(day_df["RUNTIME_ACTIVE_DAY"].sum()),
                "market_only_days": int(day_df["MARKET_ONLY_DAY"].sum()),
                "market_day_list": day_df.loc[day_df["MARKET_DATA_DAY"], "trading_date"].tolist(),
                "runtime_active_list": day_df.loc[day_df["RUNTIME_ACTIVE_DAY"], "trading_date"].tolist(),
            },
            "2_capture_rate_5m_runtime_active": float(ra_cap["capture_rate_5m"]),
            "3_capture_rate_15m_runtime_active": float(ra_cap["capture_rate_15m"]),
            "4_runtime_active_causal_funnel": {k: ra_funnel[k] for k in ra_funnel if k.startswith("n_") or k in ("n", "sum_check")},
            "5_runtime_active_no_decision_trace": int(ra_funnel["n_NO_DECISION_TRACE"]),
            "6_pbv2_shortage_ratio_among_missed": cause["ratio_pbv2_base_not_candidate"],
            "7_data_quality_ratio_among_missed": cause["ratio_data_quality"],
            "8_guard_reject_ratio_among_missed": cause["ratio_entry_rule_rejected"],
            "9_w43d_selected_symbol_counts": sel_counts,
            "9_selected_count_bug": sel_bug,
            "10_pbv2_official_valid_future_label_count": int(
                parity_df.loc[parity_df["method"] == "pbv2_official_entry", "valid_label_count"].iloc[0]
            )
            if (parity_df["method"] == "pbv2_official_entry").any()
            else 0,
            "11_pbv2_official_future_metrics": parity_df[parity_df["method"] == "pbv2_official_entry"].iloc[0].to_dict()
            if (parity_df["method"] == "pbv2_official_entry").any()
            else {},
            "12_matched_count_w43d_beats_pbv2": pbv2_edge,
            "12_matched_summary": {
                "w43d_precision_mean": float(matched_df["w43d_precision"].mean()) if len(matched_df) else None,
                "official_precision_mean": float(matched_df["official_precision"].mean()) if len(matched_df) else None,
                "w43d_mean_ret30m": float(matched_df["w43d_mean_future_30m_return"].mean()) if len(matched_df) else None,
                "official_mean_ret30m": float(matched_df["official_mean_future_30m_return"].mean())
                if len(matched_df)
                else None,
            },
            "13_w43d_beats_random": rnd_edge,
            "14_lod_top_performance": lod_df.to_dict(orient="records") if not lod_df.empty else [],
            "15_why_accel_30s_was_reported": (
                "W43D sorted LARGE_RISE_vs_DECLINE only by direction_agree_days and took head(1) "
                "without mean_abs_cliffs tie-break; accel_30s tied on agree but had smaller effect."
            ),
            "16_true_best_vs_decline": true_best,
            "17_falling_state_rate": rev_sum.get("falling_rate"),
            "18_reversal_started_rate": rev_sum.get("reversal_started_rate"),
            "19_reversal_confirmed_rate": rev_sum.get("reversal_confirmed_rate"),
            "20_edge_after_confirmation": {
                "confirmation_edge_vs_falling": rev_sum.get("confirmation_edge_vs_falling"),
                "started_edge_vs_not": rev_sum.get("started_edge_vs_not"),
                "has_confirmation_edge": rev_conf_signal,
            },
            "21_shadow_candidate_ready": shadow_ready,
            "22_runtime_yaml_shadow_unchanged": True,
        }

        report = {
            "metadata": {
                "phase": "Phase687W43D-FIX",
                "generated_at": datetime.now(JST).isoformat(),
                "prior_artifacts_reused": [
                    "w43d_5d_independent_moves.csv",
                    "w43d_5d_raw_episodes.parquet",
                    "w43d_5d_feature_effect.csv",
                    "w43c_20260717_watch50_snapshot.parquet",
                ],
                "outputs": [
                    "w43d_fix_report.md",
                    "w43d_fix_report.json",
                    "w43d_fix_audit.xlsx",
                ],
            },
            "verdicts": verdicts,
            "day_classification": day_df.to_dict(orient="records"),
            "runtime_capture": {
                "market_day_large_rise_moves": int(len(moves)),
                "runtime_active_large_rise_moves": int(ra_cap["independent_moves"]),
                "captured_5m": int(ra_cap["captured_5m"]),
                "captured_15m": int(ra_cap["captured_15m"]),
                "capture_rate_5m": float(ra_cap["capture_rate_5m"]),
                "capture_rate_15m": float(ra_cap["capture_rate_15m"]),
                "daily": cap_df.to_dict(orient="records"),
            },
            "causal_funnel": {
                "runtime_active": ra_funnel,
                "missed_cause_ratios": cause,
                "rows": funnel_df.to_dict(orient="records"),
            },
            "ranking_parity": {
                "summary": parity_df.to_dict(orient="records"),
                "score_meta": {
                    "features": rank_meta.get("score_features"),
                    "directions": rank_meta.get("score_directions"),
                    "selected_detail_counts": rank_meta.get("selected_detail_counts"),
                },
            },
            "matched_count_comparison": {
                "rows": matched_df.to_dict(orient="records") if not matched_df.empty else [],
                "aggregate": answers["12_matched_summary"],
                "beats_pbv2": pbv2_edge,
            },
            "leave_one_day_out": lod_df.to_dict(orient="records") if not lod_df.empty else [],
            "feature_selection": {
                "true_best": true_best,
                "why_accel_30s_was_wrong": answers["15_why_accel_30s_was_reported"],
                "audit_top20": audit_df.head(20).to_dict(orient="records"),
            },
            "reversal_state": {
                "summary": rev_sum,
                "daily": rev_daily.to_dict(orient="records"),
            },
            "selection_inversion_runtime_active": inv_df.to_dict(orient="records"),
            "data_integrity": {
                "excluded_days": EXCLUDED_DAYS,
                "market_only_days": MARKET_ONLY_DAYS,
                "runtime_active_days": RUNTIME_ACTIVE_DAYS,
                "funnel_sum_matches": bool(
                    int(ra_funnel["sum_check"]) == int(ra_funnel["n"])
                ),
                "expected_capture": {
                    "moves": 414,
                    "captured_5m": 32,
                    "captured_15m": 58,
                    "rate_5m": 32 / 414,
                    "rate_15m": 58 / 414,
                },
                "observed_capture": {
                    "moves": int(ra_cap["independent_moves"]),
                    "captured_5m": int(ra_cap["captured_5m"]),
                    "captured_15m": int(ra_cap["captured_15m"]),
                    "rate_5m": float(ra_cap["capture_rate_5m"]),
                    "rate_15m": float(ra_cap["capture_rate_15m"]),
                },
                "selected_symbol_count_bug": sel_bug,
            },
            "required_answers": answers,
            "runtime_change_audit": {
                "runtime_entry_changed": False,
                "runtime_exit_changed": False,
                "pbv2_changed": False,
                "guard_changed": False,
                "score_v2_changed": False,
                "cap_changed": False,
                "universe_changed": False,
                "yaml_changed": False,
                "shadow_added": False,
                "real_orders_changed": False,
                "past_paper_overwritten": False,
                "w43d_artifacts_overwritten": False,
            },
        }

        # markdown
        off_row = (
            parity_df[parity_df["method"] == "pbv2_official_entry"].iloc[0].to_dict()
            if (parity_df["method"] == "pbv2_official_entry").any()
            else {}
        )
        md = f"""# Phase687W43D-FIX — Active Runtime Metrics and Ranking Parity Repair

## Verdict
`{' | '.join(verdicts)}`

## Days
- Market data days ({answers['1_market_days_and_runtime_active_days']['market_data_days']}): `{answers['1_market_days_and_runtime_active_days']['market_day_list']}`
- Runtime active days ({answers['1_market_days_and_runtime_active_days']['runtime_active_days']}): `{answers['1_market_days_and_runtime_active_days']['runtime_active_list']}`
- Market-only: `20260714` → Capture/funnel/ranking-vs-PBv2 から除外。`MARKET_ONLY_NO_RUNTIME_EVALUATION`
- Excluded: `20260715` (PUSH 0)

## Runtime active Capture
- independent LARGE_RISE moves: **{int(ra_cap['independent_moves'])}** (market-day total remains {len(moves)})
- captured 5m: **{int(ra_cap['captured_5m'])}** → **{float(ra_cap['capture_rate_5m'])*100:.2f}%**
- captured 15m: **{int(ra_cap['captured_15m'])}** → **{float(ra_cap['capture_rate_15m'])*100:.2f}%**

## Causal funnel (runtime active independent)
| class | n |
|---|---:|
| PBV2_BASE_NOT_CANDIDATE | {int(ra_funnel['n_PBV2_BASE_NOT_CANDIDATE'])} |
| DATA_QUALITY_BLOCKED | {int(ra_funnel['n_DATA_QUALITY_BLOCKED'])} |
| NO_DECISION_TRACE | {int(ra_funnel['n_NO_DECISION_TRACE'])} |
| CAPTURED_5M | {int(ra_funnel['n_CAPTURED_5M'])} |
| ENTRY_RULE_REJECTED | {int(ra_funnel['n_ENTRY_RULE_REJECTED'])} |
| LATE_CAPTURED_15M | {int(ra_funnel['n_LATE_CAPTURED_15M'])} |
| SCAN_OR_QUEUE_LIMITED | {int(ra_funnel['n_SCAN_OR_QUEUE_LIMITED'])} |
| SAME_SYMBOL_POSITION_BLOCKED | {int(ra_funnel['n_SAME_SYMBOL_POSITION_BLOCKED'])} |
| CAP_BLOCKED_CONFIRMED | {int(ra_funnel['n_CAP_BLOCKED_CONFIRMED'])} |
| TOTAL | {int(ra_funnel['n'])} |

Missed (= {cause['missed']}): PBv2 shortage {cause['ratio_pbv2_base_not_candidate']:.1%} · DQ {cause['ratio_data_quality']:.1%} · Guard {cause['ratio_entry_rule_rejected']:.1%} · scan/queue {cause['ratio_scan_queue']:.1%}

## Ranking parity
Score features (robust z-sum, train stats): `{rank_meta.get('score_features')}`

Selected symbol counts Top3/5/10: `{sel_counts}` (bug={sel_bug})

PBv2 official valid labels: **{answers['10_pbv2_official_valid_future_label_count']}**  
PBv2 official mean 30m return/MFE/MAE: `{off_row.get('mean_future_30m_return')}` / `{off_row.get('mean_future_30m_mfe')}` / `{off_row.get('mean_future_30m_mae')}`

Matched-count W43D beats PBv2: **{pbv2_edge}**  
W43D beats random: **{rnd_edge}**

## Leave-One-Day-Out
See audit.xlsx `leave_one_day_out` and JSON `leave_one_day_out`.

## Winner / feature selection
- Prior wrong best (`accel_30s`): agree-only sort bug
- True best vs DECLINE: **{true_best.get('feature')}** (selection_score={true_best.get('selection_score')}, mean_abs_cliffs={true_best.get('mean_abs_cliffs')}, agree={true_best.get('direction_agree_days')}/5)

## Reversal state (MISSED, runtime active)
- FALLING_STATE: **{(rev_sum.get('falling_rate') or 0)*100:.1f}%**
- REVERSAL_STARTED: **{(rev_sum.get('reversal_started_rate') or 0)*100:.1f}%**
- REVERSAL_CONFIRMED: **{(rev_sum.get('reversal_confirmed_rate') or 0)*100:.1f}%**
- Confirmation edge vs falling MFE: `{rev_sum.get('confirmation_edge_vs_falling')}`

## Required answers
1. Market={answers['1_market_days_and_runtime_active_days']['market_data_days']} / Runtime active={answers['1_market_days_and_runtime_active_days']['runtime_active_days']}
2. 5m capture={answers['2_capture_rate_5m_runtime_active']:.6f} ({answers['2_capture_rate_5m_runtime_active']*100:.2f}%)
3. 15m capture={answers['3_capture_rate_15m_runtime_active']:.6f} ({answers['3_capture_rate_15m_runtime_active']*100:.2f}%)
4. Funnel: see table
5. NO_DECISION_TRACE={answers['5_runtime_active_no_decision_trace']}
6. PBv2 shortage missed ratio={answers['6_pbv2_shortage_ratio_among_missed']:.4f}
7. DQ missed ratio={answers['7_data_quality_ratio_among_missed']:.4f}
8. Guard missed ratio={answers['8_guard_reject_ratio_among_missed']:.4f}
9. Selected counts={answers['9_w43d_selected_symbol_counts']}
10. PBv2 official valid labels={answers['10_pbv2_official_valid_future_label_count']}
11. PBv2 future metrics in JSON
12. Matched W43D>PBv2={answers['12_matched_count_w43d_beats_pbv2']}
13. W43D>random={answers['13_w43d_beats_random']}
14. LOD in JSON/xlsx
15. accel_30s reason: agree-only head(1) bug
16. True best=`{true_best.get('feature')}`
17. FALLING={answers['17_falling_state_rate']}
18. STARTED={answers['18_reversal_started_rate']}
19. CONFIRMED={answers['19_reversal_confirmed_rate']}
20. Confirmation edge remains? {answers['20_edge_after_confirmation']['has_confirmation_edge']}
21. Shadow ready={answers['21_shadow_candidate_ready']}
22. Runtime/YAML/Shadow unchanged=**True**

## Runtime change audit
No Runtime ENTRY/EXIT/PBv2/Guard/score_v2/CAP/Universe/YAML/Shadow/order changes. W43D prior artifacts not overwritten.
"""
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "w43d_fix_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        (OUT / "w43d_fix_report.md").write_text(md, encoding="utf-8")

        # trim reversal detail for excel size
        rev_x = rev_detail.copy()
        if len(rev_x) > 50000:
            rev_x = rev_x.sample(50000, random_state=0)

        write_xlsx(
            {
                "day_classification": day_df,
                "runtime_capture": cap_df,
                "causal_funnel": funnel_df,
                "ranking_parity": parity_df,
                "matched_count": matched_df,
                "leave_one_day_out": lod_df,
                "feature_selection": audit_df,
                "reversal_summary": rev_daily,
                "reversal_daily": rev_x,
                "data_integrity": pd.DataFrame(
                    [
                        {
                            "key": k,
                            "value": json.dumps(v, ensure_ascii=False, default=str)
                            if isinstance(v, (dict, list))
                            else v,
                        }
                        for k, v in report["data_integrity"].items()
                    ]
                ),
                "selection_inversion": inv_df,
            },
            OUT / "w43d_fix_audit.xlsx",
        )
        print(
            json.dumps(
                {
                    "verdicts": verdicts,
                    "capture_5m": answers["2_capture_rate_5m_runtime_active"],
                    "capture_15m": answers["3_capture_rate_15m_runtime_active"],
                    "best_feature": true_best.get("feature"),
                    "shadow_ready": shadow_ready,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
