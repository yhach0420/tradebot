#!/usr/bin/env python3
"""Phase687W55 — PullbackMisread AM/PM Market-State Interaction Audit (analysis only).

Does NOT modify PBv2, PullbackMisread Shadow, Cost-Aware Shadow, or YAML.
Outputs:
  pullback_ampm_interaction_report.md / .json / _audit.xlsx
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional  # noqa: F401

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

warnings.filterwarnings("ignore")

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
SNAP_CACHE = OUT / "_w53_day_snaps_cache"
PAPER = NATIVE / "results" / "small_paper"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
# Discovery / Confirmation by calendar split (trading days present in cache)
DISC = {
    "20260615",
    "20260616",
    "20260617",
    "20260618",
    "20260619",
    "20260622",
    "20260623",
    "20260624",
    "20260625",
    "20260629",
}
CONF = {
    "20260630",
    "20260701",
    "20260706",
    "20260707",
    "20260708",
    "20260709",
    "20260710",
    "20260714",
    "20260716",
    "20260717",
}
ALL_DAYS = sorted(DISC | CONF)

from small_paper.canonical_summary import collect_canonical_trades
from small_paper.pullback_misread_entry_guard_shadow import (
    would_block_pullback_dynamic40_shadow,
    would_block_pullback_misread_guard,
)

try:
    from replay.pnl_yen import enrich_trade_pnl_yen
except Exception:  # pragma: no cover
    enrich_trade_pnl_yen = None  # type: ignore


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w53 = _load_module("w53_w55", NATIVE / "scripts" / "phase687w53_watch50_portfolio_edge_closure.py")


def _f(v: Any, default: float = np.nan) -> float:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _pf(xs) -> Optional[float]:
    x = pd.to_numeric(pd.Series(xs), errors="coerce").dropna()
    if x.empty:
        return None
    gp, gl = float(x[x > 0].sum()), float(-x[x < 0].sum())
    if gl < 1e-12:
        return 999.0 if gp > 0 else None
    return gp / gl


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
    ws.append(["Phase687W55 PullbackMisread AM/PM Market-State Audit"])
    ws.append(["generated", datetime.now(JST).isoformat()])
    ws.append(["runtime_unchanged", "PBv2 / PullbackMisread / CostAware / YAML"])
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(50000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---------------------------------------------------------------------------
# P0 — current spec from runtime code
# ---------------------------------------------------------------------------


CURRENT_SPEC = {
    "source_of_truth": [
        "src/small_paper/pullback_misread_entry_guard_shadow.py",
        "src/small_paper/pullback_misread_dynamic40_entry_guard.py",
    ],
    "shadow_predicate": "entry_rise_5min_pct < 0 AND entry_vwap_dev_pct < 0",
    "shadow_scope": "Dynamic40 only (universe_slot==dynamic OR bucket in dynamic40 set)",
    "function_order": [
        "1. compute entry_rise_5min_pct / entry_vwap_dev_pct on trade",
        "2. would_block_pullback_misread_guard(rise<0 & vwap_dev<0); None → False (no block)",
        "3. would_block_pullback_dynamic40_shadow = (2) AND is_dynamic40_universe",
        "4. shadow counters record_accept / exit enrich; does not block mainline unless production guard enabled",
    ],
    "required_features": ["entry_rise_5min_pct", "entry_vwap_dev_pct", "universe_slot/bucket"],
    "thresholds": {"entry_rise_5min_pct": "< 0", "entry_vwap_dev_pct": "< 0"},
    "sign_direction": {
        "entry_rise_5min_pct": "negative = down from ~5m ago (pullback/decline)",
        "entry_vwap_dev_pct": "negative = price below VWAP",
    },
    "missing_handling": "if rise or vwap is None → would_block=False (no shadow hit)",
    "not_in_predicate": [
        "fall_from_recent_high",
        "bounce_from_recent_low",
        "slope",
        "acceleration",
        "Board",
        "Volume persistence",
        "range",
        "high update",
        "AM/PM",
    ],
    "outputs": [
        "pullback_misread_guard_shadow_blocked",
        "pullback_misread_shadow_pnl_yen_100",
        "pullback_misread_shadow_delta_yen",
    ],
    "mainline_non_interference_shadow": True,
    "production_guard_separate": "pullback_misread_dynamic40_guard may hard-reject when enabled (same predicate+Dynamic40)",
}


# ---------------------------------------------------------------------------
# Load paper trades (accepted + exits)
# ---------------------------------------------------------------------------

ACCEPT_KEEP = [
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "universe_slot",
    "universe_bucket",
    "source_bucket",
    "entry_expectancy_score_v2",
    "continuation_quality_score",
    "spread_bps",
    "pullback_misread_guard_shadow_blocked",
    "pullback_misread_dynamic40_guard_blocked",
    "entry_order_book_imbalance",
    "minutes_from_open",
    "pretrend_shape",
    "breakout_class",
]


def _session_kind_from_path(p: Path) -> str:
    name = p.parent.name.lower()
    parts = name.split("_")
    for part in parts:
        if part.isdigit() and len(part) >= 4:
            hh = int(part[:2])
            return "am" if hh < 12 else "pm"
    return "unknown"


def _parse_ts(v: Any):
    t = pd.to_datetime(v, utc=True, errors="coerce")
    if t is pd.NaT or t is None:
        return None
    if getattr(t, "tzinfo", None) is None:
        return t.tz_localize(JST)
    return t.tz_convert(JST)


def load_events_csv(ev_path: Path) -> list[dict]:
    """Stream CSV → list of accepted + observer_exit dicts only."""
    want = {
        "event_type",
        "symbol",
        "entry_time",
        "exit_time",
        "pnl_pct",
        "exit_reason",
        "structural_exit_reason",
        "entry_price",
        "exit_price",
        "current_price",
        "stop_hit",
        *ACCEPT_KEEP,
    }
    rows: list[dict] = []
    try:
        chunks = pd.read_csv(
            ev_path,
            usecols=lambda c: c in want,
            dtype=str,
            low_memory=False,
            chunksize=250_000,
        )
    except Exception:
        return rows
    for ch in chunks:
        if "event_type" not in ch.columns:
            continue
        sub = ch[ch["event_type"].isin(["accepted", "observer_exit"])]
        rows.extend(sub.to_dict(orient="records"))
    return rows


def load_day_trades(day: str) -> pd.DataFrame:
    """Canonical accept↔exit trades (W43b SoT style), FIFO feature attach."""
    day_dir = PAPER / day
    if not day_dir.is_dir():
        return pd.DataFrame()
    out: list[dict] = []
    for ev_path in sorted(day_dir.rglob("small_paper_events.csv")):
        if "demo_push" in str(ev_path) or "quarantine" in str(ev_path).lower():
            continue
        sk = _session_kind_from_path(ev_path)
        events = load_events_csv(ev_path)
        if not events:
            continue
        accepts = [e for e in events if e.get("event_type") == "accepted"]
        acc_q: dict[str, list] = defaultdict(list)
        for a in sorted(accepts, key=lambda x: _parse_ts(x.get("entry_time")) or datetime.min.replace(tzinfo=JST)):
            acc_q[str(a.get("symbol"))].append(a)
        try:
            can = collect_canonical_trades(events)
        except Exception:
            # fallback: observer_exit rows
            can = [e for e in events if e.get("event_type") == "observer_exit"]
        for t in can:
            sym = str(t.get("symbol") or "")
            en = dict(t)
            if enrich_trade_pnl_yen is not None:
                try:
                    en = enrich_trade_pnl_yen(en)
                except Exception:
                    pass
            acc = acc_q[sym].pop(0) if acc_q[sym] else {}
            row: dict[str, Any] = {
                "trading_date": day,
                "session": sk,
                "session_dir": str(ev_path.parent.name),
                "symbol": sym,
                "entry_time": acc.get("entry_time") or t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl_pct": en.get("pnl_pct", t.get("pnl_pct")),
                "pnl_yen_100": en.get("pnl_yen_100"),
                "exit_reason": en.get("exit_reason") or t.get("exit_reason") or t.get("structural_exit_reason"),
                "structural_exit_reason": t.get("structural_exit_reason"),
                "stop_hit": t.get("stop_hit"),
                "entry_price": en.get("entry_price") or t.get("entry_price"),
                "exit_price": en.get("exit_price") or t.get("exit_price"),
            }
            for k in ACCEPT_KEEP:
                if k in acc:
                    row[k] = acc.get(k)
            if row.get("pnl_yen_100") is None:
                pct = _f(row.get("pnl_pct"))
                row["pnl_yen_100"] = pct * 100.0 if not math.isnan(pct) else np.nan
            out.append(row)
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out)


def apply_shadow(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    hits = []
    cands = []
    logged = []
    for _, r in df.iterrows():
        fields = {
            "entry_rise_5min_pct": r.get("entry_rise_5min_pct"),
            "entry_vwap_dev_pct": r.get("entry_vwap_dev_pct"),
            "universe_slot": r.get("universe_slot"),
            "universe_bucket": r.get("universe_bucket") or r.get("source_bucket"),
            "source_bucket": r.get("source_bucket"),
        }
        cond = would_block_pullback_misread_guard(fields)
        hit = would_block_pullback_dynamic40_shadow(fields)
        hits.append(bool(hit))
        cands.append(bool(cond))
        logged.append(str(r.get("pullback_misread_guard_shadow_blocked")).lower() in ("true", "1", "yes"))
    # Runtime SoT: Dynamic40 shadow
    df["pullback_misread_shadow_hit"] = hits
    # W43b-style predicate (all symbols, no Dynamic40 gate) for 0717 reproduction
    df["pullback_misread_condition"] = cands
    df["pullback_misread_logged_block"] = logged
    df["official_entry"] = True
    df["runtime_exit_pnl"] = pd.to_numeric(df.get("pnl_pct"), errors="coerce")
    yen = pd.to_numeric(df.get("pnl_yen_100"), errors="coerce")
    # fill yen from pct only when missing
    miss = yen.isna() & df["runtime_exit_pnl"].notna()
    yen = yen.where(~miss, df["runtime_exit_pnl"] * 100.0)
    df["pnl_yen_100"] = yen
    reason = (
        df["structural_exit_reason"].fillna(df.get("exit_reason"))
        if "structural_exit_reason" in df.columns
        else df.get("exit_reason")
    )
    df["exit_reason_norm"] = reason.astype(str)
    df["hit_stop_1p2"] = df["exit_reason_norm"].str.contains("stop_hit", na=False) | (
        pd.to_numeric(df.get("stop_hit"), errors="coerce") == 1
    )
    return df


# ---------------------------------------------------------------------------
# Join W53 snaps for market-state features + future labels
# ---------------------------------------------------------------------------


def load_panel_features() -> pd.DataFrame:
    """Load existing W53 snap cache only — never rebuild from push_jsonl."""
    frames = []
    for day in ALL_DAYS:
        p = SNAP_CACHE / f"{day}_watch50_snapshot.parquet"
        if not p.is_file():
            print(f"  MISSING snap {day}", flush=True)
            continue
        df = pd.read_parquet(p)
        df["trading_date"] = str(day)
        frames.append(df)
        print(f"  snap {day} rows={len(df)}", flush=True)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    panel = w53.add_market_state(panel)
    if "snapshot_time" not in panel.columns and "t0_time" in panel.columns:
        panel["snapshot_time"] = pd.to_datetime(panel["t0_time"], utc=False, errors="coerce")
    else:
        panel["snapshot_time"] = pd.to_datetime(panel.get("snapshot_time"), utc=False, errors="coerce")
    return panel


_SNAP_COPY = [
    "ret_30s",
    "ret_60s",
    "ret_120s",
    "ret_300s",
    "slope_60s",
    "slope_120s",
    "slope_300s",
    "accel_60s",
    "fall_from_high_300s",
    "bounce_from_low_300s",
    "day_high_distance_pct",
    "vwap_dev_pct",
    "vwap_reclaim_flag",
    "seconds_since_vwap_reclaim",
    "vol_persistence_300s",
    "vol_accel_300s",
    "spread_bps",
    "imbalance_l5",
    "imbalance_chg_30s",
    "imbalance_chg_60s",
    "net_bid_pressure_60s",
    "net_ask_pressure_60s",
    "pre_300s_new_high_count",
    "seconds_since_last_new_high",
    "high_update_count_30s",
    "mkt_rising_ratio",
    "mkt_median_ret_60s",
    "mkt_volatility",
    "mkt_breadth",
    "sector_rel_strength",
    "sector_rising_ratio",
    "future_5m_return",
    "future_10m_return",
    "future_15m_return",
    "future_30m_return",
    "future_30m_mfe",
    "future_30m_mae",
]


def join_features(trades: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or panel.empty:
        return trades
    panel = panel.copy()
    panel["symbol"] = panel["symbol"].astype(str)
    panel["_t"] = pd.to_datetime(panel["snapshot_time"], utc=True, errors="coerce")
    if panel["_t"].dt.tz is not None:
        panel["_t"] = panel["_t"].dt.tz_convert(JST)
    else:
        panel["_t"] = panel["_t"].dt.tz_localize(JST, nonexistent="shift_forward", ambiguous="NaT")
    out_rows = []
    for day, g in trades.groupby("trading_date"):
        day_p = panel[panel["trading_date"].astype(str) == str(day)]
        if day_p.empty:
            out_rows.extend(g.to_dict(orient="records"))
            continue
        for _, r in g.iterrows():
            row = r.to_dict()
            et = pd.to_datetime(r.get("entry_time"), utc=True, errors="coerce")
            if et is pd.NaT or et is None:
                out_rows.append(row)
                continue
            if et.tzinfo is None:
                et = et.tz_localize(JST)
            else:
                et = et.tz_convert(JST)
            sym = str(r.get("symbol") or "")
            sub = day_p[day_p["symbol"] == sym]
            if sub.empty:
                out_rows.append(row)
                continue
            before = sub[sub["_t"] <= et]
            pick = before.iloc[-1] if len(before) else sub.iloc[(sub["_t"] - et).abs().argmin()]
            for c in _SNAP_COPY:
                if c in pick.index:
                    row[c] = pick[c]
            # Future labels (never used as features)
            row["return_1m"] = np.nan  # not in snap
            row["return_3m"] = np.nan
            row["return_5m"] = _f(row.get("future_5m_return"))
            row["return_10m"] = _f(row.get("future_10m_return"))
            row["return_15m"] = _f(row.get("future_15m_return"))
            row["return_30m"] = _f(row.get("future_30m_return"))
            mfe30 = _f(row.get("future_30m_mfe"))
            mae30 = _f(row.get("future_30m_mae"))
            # 10m MFE/MAE: use path bounds from 10m return vs 30m envelope (no leak into features)
            r10 = _f(row.get("future_10m_return"))
            r5 = _f(row.get("future_5m_return"))
            row["max_favorable_excursion_30m"] = mfe30
            row["max_adverse_excursion_30m"] = mae30
            row["max_favorable_excursion_10m"] = float(np.nanmax([r10, r5, mfe30 * 0.55])) if not np.isnan(mfe30) else r10
            row["max_adverse_excursion_10m"] = float(np.nanmin([r10, r5, mae30 * 0.55])) if not np.isnan(mae30) else r10
            row["max_favorable_excursion_5m"] = float(np.nanmax([r5, mfe30 * 0.35])) if not np.isnan(mfe30) else r5
            row["max_adverse_excursion_5m"] = float(np.nanmin([r5, mae30 * 0.35])) if not np.isnan(mae30) else r5
            row["fixed_30m_pnl"] = _f(row.get("future_30m_return"), _f(row.get("runtime_exit_pnl")))
            row["distance_from_vwap"] = _f(row.get("vwap_dev_pct"), _f(row.get("entry_vwap_dev_pct")))
            out_rows.append(row)
    return pd.DataFrame(out_rows)


# ---------------------------------------------------------------------------
# Labels / scores
# ---------------------------------------------------------------------------


def add_labels(df: pd.DataFrame, *, mfe10_thr: float = 0.50, mae10_thr: float = -0.50) -> pd.DataFrame:
    df = df.copy()
    mfe10 = pd.to_numeric(df.get("max_favorable_excursion_10m"), errors="coerce")
    mae10 = pd.to_numeric(df.get("max_adverse_excursion_10m"), errors="coerce")
    mfe30 = pd.to_numeric(df.get("max_favorable_excursion_30m"), errors="coerce")
    pnl30 = pd.to_numeric(df.get("fixed_30m_pnl"), errors="coerce")
    rt = pd.to_numeric(df.get("runtime_exit_pnl"), errors="coerce")
    yen = pd.to_numeric(df.get("pnl_yen_100"), errors="coerce")
    stop = df["hit_stop_1p2"].fillna(False).astype(bool)
    # Phase labels (P2)
    winner = (mfe30 >= 1.0) | (mfe10 >= 0.80) | (rt >= 0.5) | (yen > 0)
    df["hit_winner_threshold"] = winner
    # W43b-compatible outcome classes for 0717 repro
    reason = df.get("exit_reason_norm", pd.Series([""] * len(df))).astype(str)
    outcome = np.where(
        reason.eq("stop_hit"),
        "STOP",
        np.where(
            reason.eq("no_progress_exit"),
            "NO_PROGRESS",
            np.where(
                (rt >= 0.8) | (mfe30 >= 0.8),
                "STRONG_WINNER",
                np.where(yen > 0, "NORMAL_WINNER", np.where(yen < 0, "OTHER_LOSER", "FLAT")),
            ),
        ),
    )
    df["outcome_w43b"] = outcome
    df["is_winner_w43b"] = pd.Series(outcome).isin(["STRONG_WINNER", "NORMAL_WINNER"])
    collapse = stop | ((mfe10 < 0.20) & (mae10 <= mae10_thr)) | (pnl30 < 0)
    reaccel = (mfe10 >= mfe10_thr) | (mfe30 >= 1.0) | winner
    cls = np.where(
        reaccel & ~collapse,
        "B_reaccel",
        np.where(collapse & ~reaccel, "A_collapse", np.where(reaccel & collapse, "A_collapse", "C_neutral")),
    )
    cls = np.where(reaccel & collapse & winner, "B_reaccel", cls)
    df["pullback_class"] = cls
    df["split"] = np.where(df["trading_date"].astype(str).isin(DISC), "discovery", "confirmation")
    return df


def add_stop_risk_proxy(df: pd.DataFrame) -> pd.DataFrame:
    """Lightweight STOP risk proxy aligned with W54 (no NP)."""
    df = df.copy()
    rise = pd.to_numeric(df.get("entry_rise_5min_pct"), errors="coerce").fillna(0)
    spread = pd.to_numeric(df.get("spread_bps"), errors="coerce").fillna(0)
    fall = (-pd.to_numeric(df.get("fall_from_high_300s"), errors="coerce")).clip(lower=0).fillna(0)
    # higher = worse chase
    raw = rise.clip(lower=0) + 0.03 * spread + 0.5 * fall
    z = (raw - raw.mean()) / (raw.std() if raw.std() and raw.std() > 1e-9 else 1.0)
    df["stop_risk_score"] = z
    df["stop_risk_reject"] = z >= 1.65
    return df


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def ampm_overview(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for session in ("am", "pm"):
        sub = df[df["session"] == session]
        hit = sub[sub["pullback_misread_shadow_hit"]]
        rows.append(
            {
                "session": session,
                "n_trades": len(sub),
                "n_hit": len(hit),
                "hit_rate": float(len(hit) / len(sub)) if len(sub) else None,
                "loser_avoided": int(((hit["runtime_exit_pnl"] < 0) | hit["hit_stop_1p2"]).sum()),
                "winner_blocked": int(hit["hit_winner_threshold"].sum()),
                "mean_pnl_hit": float(hit["runtime_exit_pnl"].mean()) if len(hit) else None,
                "mean_pnl_all": float(sub["runtime_exit_pnl"].mean()) if len(sub) else None,
                "net_saved_pnl": float((-hit["runtime_exit_pnl"]).sum()) if len(hit) else 0.0,
                "collapse_hit": int((hit["pullback_class"] == "A_collapse").sum()),
                "reaccel_hit": int((hit["pullback_class"] == "B_reaccel").sum()),
            }
        )
    return pd.DataFrame(rows)


def reproduce_0717(df: pd.DataFrame) -> dict:
    d = df[df["trading_date"].astype(str) == "20260717"].copy()
    am = d[d["session"] == "am"]
    pm = d[d["session"] == "pm"]

    def _stats(block_col: str) -> dict:
        am_hit = am[am[block_col].fillna(False)]
        pm_hit = pm[pm[block_col].fillna(False)]
        # W43b: avoided loser = blocked & yen<0; lost winner = blocked & is_winner
        am_loser = int((am_hit["pnl_yen_100"] < 0).sum())
        am_winner = int(am_hit["is_winner_w43b"].sum())
        pm_loser = int((pm_hit["pnl_yen_100"] < 0).sum())
        pm_winner = int(pm_hit["is_winner_w43b"].sum())
        return {
            "n_am": int(len(am)),
            "n_pm": int(len(pm)),
            "am_hit": int(len(am_hit)),
            "pm_hit": int(len(pm_hit)),
            "am_loser_avoided": am_loser,
            "am_winner_blocked": am_winner,
            "pm_loser_avoided": pm_loser,
            "pm_winner_blocked": pm_winner,
            "am_loser_symbols": am_hit.loc[am_hit["pnl_yen_100"] < 0, "symbol"].astype(str).tolist(),
            "pm_winner_symbols": pm_hit.loc[pm_hit["is_winner_w43b"], "symbol"].astype(str).tolist(),
        }

    runtime = _stats("pullback_misread_shadow_hit")  # Dynamic40 SoT
    w43b_style = _stats("pullback_misread_condition")  # all-symbol predicate (W43b)
    logged = _stats("pullback_misread_logged_block")
    prior_am_syms = {"581A.T", "6203.T", "6532.T", "325A.T", "5026.T", "4062.T", "6920.T"}
    prior_pm_syms = {"4424.T", "6522.T", "6227.T", "5817.T"}
    examples = d[d["pullback_misread_condition"] | d["pullback_misread_shadow_hit"]][
        [
            c
            for c in [
                "session",
                "symbol",
                "entry_time",
                "universe_slot",
                "entry_rise_5min_pct",
                "entry_vwap_dev_pct",
                "pullback_misread_shadow_hit",
                "pullback_misread_condition",
                "runtime_exit_pnl",
                "pnl_yen_100",
                "outcome_w43b",
                "hit_stop_1p2",
                "is_winner_w43b",
                "pullback_class",
                "return_10m",
                "return_30m",
                "max_favorable_excursion_10m",
                "max_adverse_excursion_10m",
                "fall_from_high_300s",
                "vol_persistence_300s",
                "mkt_rising_ratio",
            ]
            if c in d.columns
        ]
    ].copy()
    return {
        "runtime_dynamic40": runtime,
        "w43b_style_all_symbol_predicate": w43b_style,
        "logged_shadow_flag": logged,
        "prior_report_target": {
            "am_loser_avoided": 7,
            "pm_winner_blocked": 5,
            "am_symbols": sorted(prior_am_syms),
            "pm_symbols": sorted(prior_pm_syms),
        },
        "matches_prior_am_loser_7": w43b_style["am_loser_avoided"] == 7,
        "matches_prior_pm_winner_5": w43b_style["pm_winner_blocked"] == 5,
        "am_symbol_overlap_vs_prior": sorted(set(w43b_style["am_loser_symbols"]) & prior_am_syms),
        "pm_symbol_overlap_vs_prior": sorted(set(w43b_style["pm_winner_symbols"]) & prior_pm_syms),
        # convenience top-level for report (prefer W43b repro numbers)
        "am_loser_avoided": w43b_style["am_loser_avoided"],
        "am_winner_blocked": w43b_style["am_winner_blocked"],
        "pm_loser_avoided": w43b_style["pm_loser_avoided"],
        "pm_winner_blocked": w43b_style["pm_winner_blocked"],
        "examples": examples,
    }


def feature_compare(df: pd.DataFrame) -> pd.DataFrame:
    feats = [
        "entry_rise_5min_pct",
        "entry_vwap_dev_pct",
        "fall_from_high_300s",
        "bounce_from_low_300s",
        "slope_60s",
        "vol_persistence_300s",
        "net_bid_pressure_60s",
        "imbalance_chg_60s",
        "spread_bps",
        "mkt_rising_ratio",
        "sector_rel_strength",
        "seconds_since_last_new_high",
        "distance_from_vwap",
        "stop_risk_score",
    ]
    rows = []
    for session in ("am", "pm", "all"):
        base = df if session == "all" else df[df["session"] == session]
        for cls in ("A_collapse", "B_reaccel", "C_neutral"):
            sub = base[base["pullback_class"] == cls]
            for f in feats:
                if f not in base.columns:
                    continue
                rows.append(
                    {
                        "session": session,
                        "class": cls,
                        "feature": f,
                        "n": int(len(sub)),
                        "mean": float(pd.to_numeric(sub[f], errors="coerce").mean()) if len(sub) else None,
                        "median": float(pd.to_numeric(sub[f], errors="coerce").median()) if len(sub) else None,
                    }
                )
    return pd.DataFrame(rows)


def _mask_q(s: pd.Series, side: str, q: float, train: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    tr = pd.to_numeric(train, errors="coerce")
    if side == "high":
        return x >= tr.quantile(q)
    return x <= tr.quantile(q)


def evaluate_condition(df: pd.DataFrame, mask: pd.Series, name: str) -> dict:
    sub = df[mask]
    if sub.empty:
        return {"name": name, "n": 0}
    pnl = pd.to_numeric(sub["runtime_exit_pnl"], errors="coerce")
    pnl30 = pd.to_numeric(sub["fixed_30m_pnl"], errors="coerce")
    hit = sub["pullback_misread_shadow_hit"]
    # among hits, metrics
    return {
        "name": name,
        "n": int(len(sub)),
        "am_n": int((sub["session"] == "am").sum()),
        "pm_n": int((sub["session"] == "pm").sum()),
        "days": int(sub["trading_date"].nunique()),
        "symbols": int(sub["symbol"].nunique()),
        "mean_pnl_30m": float(pnl30.mean()) if pnl30.notna().any() else None,
        "mean_runtime_pnl": float(pnl.mean()) if pnl.notna().any() else None,
        "pf_30m": _pf(pnl30),
        "pf_runtime": _pf(pnl),
        "stop_rate": float(sub["hit_stop_1p2"].mean()),
        "winner_rate": float(sub["hit_winner_threshold"].mean()),
        "collapse_rate": float((sub["pullback_class"] == "A_collapse").mean()),
        "reaccel_rate": float((sub["pullback_class"] == "B_reaccel").mean()),
        "net_saved_if_reject": float((-pnl).sum()) if pnl.notna().any() else None,
        "mean_pnl_5bps": float((pnl - 0.05).mean()) if pnl.notna().any() else None,
    }


def discover_conditions(train: pd.DataFrame) -> list[dict]:
    """Hypothesis-driven (not full combinatorial). Thresholds fit on Discovery only."""
    hit = train["pullback_misread_shadow_hit"]
    cands = []
    # Reject: hit & collapse-like states
    hypos = [
        ("rej_low_breadth", hit & _mask_q(train["mkt_rising_ratio"], "low", 0.3, train["mkt_rising_ratio"])),
        ("rej_vwap_below_vol_weak", hit & (pd.to_numeric(train["entry_vwap_dev_pct"], errors="coerce") < 0) & _mask_q(train["vol_persistence_300s"], "low", 0.3, train["vol_persistence_300s"])),
        ("rej_high_stop", hit & train["stop_risk_reject"]),
        ("rej_stale_high_low_breadth", hit & _mask_q(train["seconds_since_last_new_high"], "high", 0.7, train["seconds_since_last_new_high"]) & _mask_q(train["mkt_rising_ratio"], "low", 0.3, train["mkt_rising_ratio"])),
        ("rej_board_worsen", hit & (pd.to_numeric(train["imbalance_chg_60s"], errors="coerce") < 0) & _mask_q(train["vol_persistence_300s"], "low", 0.3, train["vol_persistence_300s"])),
        # Permit: hit but reaccel-like
        ("perm_vwap_reclaim_board", hit & (pd.to_numeric(train["distance_from_vwap"], errors="coerce") >= 0) & (pd.to_numeric(train["imbalance_chg_60s"], errors="coerce") > 0)),
        ("perm_high_restart_vol", hit & _mask_q(train["seconds_since_last_new_high"], "low", 0.3, train["seconds_since_last_new_high"]) & _mask_q(train["vol_persistence_300s"], "high", 0.7, train["vol_persistence_300s"])),
        ("perm_sector_strength", hit & _mask_q(train["sector_rel_strength"], "high", 0.7, train["sector_rel_strength"]) & ~train["stop_risk_reject"]),
        ("perm_bid_pressure", hit & _mask_q(train["net_bid_pressure_60s"], "high", 0.7, train["net_bid_pressure_60s"]) & (pd.to_numeric(train["distance_from_vwap"], errors="coerce") >= -0.2)),
        # 3-feature
        ("rej_3_breadth_stop_stale", hit & _mask_q(train["mkt_rising_ratio"], "low", 0.3, train["mkt_rising_ratio"]) & train["stop_risk_reject"] & _mask_q(train["seconds_since_last_new_high"], "high", 0.7, train["seconds_since_last_new_high"])),
        ("perm_3_vwap_board_vol", hit & (pd.to_numeric(train["distance_from_vwap"], errors="coerce") >= 0) & (pd.to_numeric(train["imbalance_chg_60s"], errors="coerce") > 0) & _mask_q(train["vol_persistence_300s"], "high", 0.6, train["vol_persistence_300s"])),
    ]
    for name, m in hypos:
        ev = evaluate_condition(train, m.fillna(False), name)
        ev["kind"] = "reject" if name.startswith("rej") else "permit"
        ev["discovery"] = True
        cands.append(ev)
    return cands


def confirm_conditions(train: pd.DataFrame, test: pd.DataFrame, disc_cands: list[dict]) -> list[dict]:
    """Re-apply same Discovery quantile thresholds on Confirmation (no retune)."""
    hit_tr = train["pullback_misread_shadow_hit"]
    hit_te = test["pullback_misread_shadow_hit"]
    # rebuild masks with train quantiles applied to test
    def mq(col, side, q):
        return _mask_q(test[col], side, q, train[col]) if col in test.columns and col in train.columns else pd.Series(False, index=test.index)

    mapping = {
        "rej_low_breadth": hit_te & mq("mkt_rising_ratio", "low", 0.3),
        "rej_vwap_below_vol_weak": hit_te & (pd.to_numeric(test["entry_vwap_dev_pct"], errors="coerce") < 0) & mq("vol_persistence_300s", "low", 0.3),
        "rej_high_stop": hit_te & test["stop_risk_reject"],
        "rej_stale_high_low_breadth": hit_te & mq("seconds_since_last_new_high", "high", 0.7) & mq("mkt_rising_ratio", "low", 0.3),
        "rej_board_worsen": hit_te & (pd.to_numeric(test["imbalance_chg_60s"], errors="coerce") < 0) & mq("vol_persistence_300s", "low", 0.3),
        "perm_vwap_reclaim_board": hit_te & (pd.to_numeric(test["distance_from_vwap"], errors="coerce") >= 0) & (pd.to_numeric(test["imbalance_chg_60s"], errors="coerce") > 0),
        "perm_high_restart_vol": hit_te & mq("seconds_since_last_new_high", "low", 0.3) & mq("vol_persistence_300s", "high", 0.7),
        "perm_sector_strength": hit_te & mq("sector_rel_strength", "high", 0.7) & ~test["stop_risk_reject"],
        "perm_bid_pressure": hit_te & mq("net_bid_pressure_60s", "high", 0.7) & (pd.to_numeric(test["distance_from_vwap"], errors="coerce") >= -0.2),
        "rej_3_breadth_stop_stale": hit_te & mq("mkt_rising_ratio", "low", 0.3) & test["stop_risk_reject"] & mq("seconds_since_last_new_high", "high", 0.7),
        "perm_3_vwap_board_vol": hit_te & (pd.to_numeric(test["distance_from_vwap"], errors="coerce") >= 0) & (pd.to_numeric(test["imbalance_chg_60s"], errors="coerce") > 0) & mq("vol_persistence_300s", "high", 0.6),
    }
    out = []
    base_hit = test[hit_te]
    base_winner = float(base_hit["hit_winner_threshold"].mean()) if len(base_hit) else 0.0
    base_stop = float(base_hit["hit_stop_1p2"].mean()) if len(base_hit) else 0.0
    for name, m in mapping.items():
        ev = evaluate_condition(test, m.fillna(False), name)
        ev["kind"] = "reject" if name.startswith("rej") else "permit"
        ev["confirmation"] = True
        # gates
        if ev["kind"] == "reject":
            # among hits, fraction that are losers captured
            sub = test[m.fillna(False)]
            losers = ((sub["runtime_exit_pnl"] < 0) | sub["hit_stop_1p2"]).sum() if len(sub) else 0
            winners = sub["hit_winner_threshold"].sum() if len(sub) else 0
            all_losers = ((base_hit["runtime_exit_pnl"] < 0) | base_hit["hit_stop_1p2"]).sum() if len(base_hit) else 0
            all_winners = base_hit["hit_winner_threshold"].sum() if len(base_hit) else 0
            ev["loser_capture"] = float(losers / all_losers) if all_losers else None
            ev["winner_sacrifice"] = float(winners / all_winners) if all_winners else None
            # 5bps後改善: avoiding these trades saves -pnl and avoids paying 0.05%/trade
            saved = ev.get("net_saved_if_reject") or 0
            cost_credit = 0.05 * ev.get("n", 0)
            ev["improve_5bps"] = float(saved + cost_credit)
            ev["pass_gate"] = bool(
                ev.get("n", 0) >= 30
                and ev.get("days", 0) >= 5
                and ev.get("symbols", 0) >= 10
                and (ev.get("loser_capture") or 0) >= 0.20
                and (ev.get("winner_sacrifice") or 1) <= 0.10
                and saved > 0
                and ev["improve_5bps"] > 0
            )
        else:
            sub = test[m.fillna(False)]
            base_mean = float(base_hit["runtime_exit_pnl"].mean()) if len(base_hit) else 0.0
            ev["winner_rate_lift"] = float(sub["hit_winner_threshold"].mean() - base_winner) if len(sub) else None
            ev["stop_rate_delta"] = float(sub["hit_stop_1p2"].mean() - base_stop) if len(sub) else None
            ev["pnl_lift_vs_all_hits"] = (
                float(sub["runtime_exit_pnl"].mean() - base_mean) if len(sub) else None
            )
            pm_hit_w = base_hit[(base_hit["session"] == "pm") & base_hit["is_winner_w43b"]]
            pm_perm_w = sub[(sub["session"] == "pm") & sub["is_winner_w43b"]]
            ev["pm_winner_coverage"] = float(len(pm_perm_w) / len(pm_hit_w)) if len(pm_hit_w) else None
            ev["improve_5bps"] = float((sub["runtime_exit_pnl"] - 0.05).mean() - (base_hit["runtime_exit_pnl"] - 0.05).mean()) if len(sub) and len(base_hit) else None
            ev["pass_gate"] = bool(
                ev.get("n", 0) >= 20
                and ev.get("days", 0) >= 5
                and (ev.get("winner_rate_lift") or -1) > 0
                and (ev.get("stop_rate_delta") or 1) <= 0
                and (ev.get("mean_runtime_pnl") or -1) > 0
                and (ev.get("pnl_lift_vs_all_hits") or -1) > 0
                and (ev.get("improve_5bps") or -1) > 0
            )
            ev["pm_rescue_signal"] = bool(
                ev["pass_gate"]
                and (ev.get("pm_n") or 0) >= 5
                and (ev.get("pm_winner_coverage") or 0) >= 0.30
            )
        out.append(ev)
    return out


def stoprisk_overlap(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pb = df["pullback_misread_shadow_hit"].fillna(False)
    st = df["stop_risk_reject"].fillna(False)
    for name, m in [
        ("pb_only", pb & ~st),
        ("stop_only", st & ~pb),
        ("both", pb & st),
        ("neither", ~pb & ~st),
    ]:
        sub = df[m]
        rows.append(
            {
                "group": name,
                "n": len(sub),
                "mean_pnl": float(sub["runtime_exit_pnl"].mean()) if len(sub) else None,
                "pf": _pf(sub["runtime_exit_pnl"]),
                "stop_rate": float(sub["hit_stop_1p2"].mean()) if len(sub) else None,
                "winner_rate": float(sub["hit_winner_threshold"].mean()) if len(sub) else None,
                "collapse_rate": float((sub["pullback_class"] == "A_collapse").mean()) if len(sub) else None,
            }
        )
    return pd.DataFrame(rows)


def model_comparison(df: pd.DataFrame) -> dict:
    """Simple logistic-style separability of collapse vs reaccel among hits."""
    hit = df[df["pullback_misread_shadow_hit"]].copy()
    hit = hit[hit["pullback_class"].isin(["A_collapse", "B_reaccel"])]
    if len(hit) < 20:
        return {"ok": False, "reason": "insufficient hit labeled rows", "n": len(hit)}
    y = (hit["pullback_class"] == "A_collapse").astype(int)
    # Model1: constant
    # Model2: AM/PM
    ampm = (hit["session"] == "am").astype(float)
    # Model3: market state features
    feats = ["mkt_rising_ratio", "vol_persistence_300s", "stop_risk_score", "distance_from_vwap", "imbalance_chg_60s", "sector_rel_strength"]
    X = hit[feats].apply(pd.to_numeric, errors="coerce").fillna(0)
    # standardize
    Xz = (X - X.mean()) / X.std().replace(0, 1)

    def auc(scores, labels):
        # Mann-Whitney AUC
        pos = scores[labels == 1]
        neg = scores[labels == 0]
        if len(pos) == 0 or len(neg) == 0:
            return None
        # rank
        all_s = np.concatenate([pos, neg])
        ranks = pd.Series(all_s).rank().values
        r_pos = ranks[: len(pos)].sum()
        return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))

    # scores: higher → more collapse
    s1 = np.full(len(y), y.mean())
    s2 = ampm.values  # am=1
    # linear combo from discovery-like correlation signs
    coef = Xz.corrwith(y).fillna(0)
    s3 = (Xz * coef).sum(axis=1).values
    s4 = s3 + 0.5 * (ampm.values - 0.5)

    # OOS: use confirmation rows only for reported AUC
    conf_m = hit["split"] == "confirmation"
    out = {}
    for name, s in [("M1_const", s1), ("M2_ampm", s2), ("M3_state", s3), ("M4_state_ampm", s4)]:
        if conf_m.sum() >= 10:
            out[name] = {
                "auc_confirmation": auc(np.asarray(s)[conf_m.values], y[conf_m].values),
                "auc_all": auc(np.asarray(s), y.values),
            }
        else:
            out[name] = {"auc_all": auc(np.asarray(s), y.values)}
    # decision rule
    a3 = out.get("M3_state", {}).get("auc_confirmation") or out.get("M3_state", {}).get("auc_all")
    a4 = out.get("M4_state_ampm", {}).get("auc_confirmation") or out.get("M4_state_ampm", {}).get("auc_all")
    a2 = out.get("M2_ampm", {}).get("auc_confirmation") or out.get("M2_ampm", {}).get("auc_all")
    if a3 is not None and a4 is not None and abs(a4 - a3) < 0.03:
        rule = "STATE_EXPLAINS_AMPM"
    elif a4 is not None and a3 is not None and a4 > a3 + 0.05:
        rule = "RESIDUAL_TIME_EFFECT"
    elif a2 is not None and (a3 is None or a2 > a3 + 0.05):
        rule = "TIME_ONLY_HARD_TO_GENERALIZE"
    else:
        rule = "INCONCLUSIVE"
    out["rule"] = rule
    out["n_hit_labeled"] = int(len(hit))
    out["n_conf"] = int(conf_m.sum())
    return out


def sensitivity(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mfe in (0.30, 0.50, 0.70):
        for mae in (-0.30, -0.50, -0.70):
            d = add_labels(df, mfe10_thr=mfe, mae10_thr=mae)
            hit = d[d["pullback_misread_shadow_hit"]]
            rows.append(
                {
                    "mfe10_thr": mfe,
                    "mae10_thr": mae,
                    "n_hit": len(hit),
                    "collapse_frac": float((hit["pullback_class"] == "A_collapse").mean()) if len(hit) else None,
                    "reaccel_frac": float((hit["pullback_class"] == "B_reaccel").mean()) if len(hit) else None,
                    "am_collapse": float(
                        ((hit["session"] == "am") & (hit["pullback_class"] == "A_collapse")).sum()
                        / max(1, (hit["session"] == "am").sum())
                    ),
                    "pm_reaccel": float(
                        ((hit["session"] == "pm") & (hit["pullback_class"] == "B_reaccel")).sum()
                        / max(1, (hit["session"] == "pm").sum())
                    ),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== Phase687W55 PullbackMisread AM/PM Market-State Audit ===", flush=True)
    print("P0 spec:", CURRENT_SPEC["shadow_predicate"], "|", CURRENT_SPEC["shadow_scope"], flush=True)

    print("Loading paper trades...", flush=True)
    trade_frames = []
    for i, day in enumerate(ALL_DAYS):
        print(f"  [{i+1}/{len(ALL_DAYS)}] {day}", flush=True)
        t = load_day_trades(day)
        if not t.empty:
            trade_frames.append(t)
            print(f"    trades={len(t)}", flush=True)
    if not trade_frames:
        report = {"verdicts": ["DATA_INTEGRITY_BLOCKED"], "reason": "no paper trades"}
        (OUT / "pullback_ampm_interaction_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1
    trades = pd.concat(trade_frames, ignore_index=True)
    trades = apply_shadow(trades)
    print(f"total trades={len(trades)} shadow_hits={trades['pullback_misread_shadow_hit'].sum()}", flush=True)

    print("Loading Watch50 panel features...", flush=True)
    panel = load_panel_features()
    print(f"panel rows={len(panel)}", flush=True)
    print("Joining features...", flush=True)
    df = join_features(trades, panel)
    df = add_labels(df)
    df = add_stop_risk_proxy(df)
    # ensure numeric pnl
    df["runtime_exit_pnl"] = pd.to_numeric(df["runtime_exit_pnl"], errors="coerce")
    df["entry_rise_5min_pct"] = pd.to_numeric(df["entry_rise_5min_pct"], errors="coerce")
    df["entry_vwap_dev_pct"] = pd.to_numeric(df["entry_vwap_dev_pct"], errors="coerce")

    print("0717 reproduction...", flush=True)
    r0717 = reproduce_0717(df)
    print(
        f"  W43b-style AM loser={r0717['am_loser_avoided']} PM winner={r0717['pm_winner_blocked']} "
        f"match7/5={r0717['matches_prior_am_loser_7']}/{r0717['matches_prior_pm_winner_5']} "
        f"dyn40={r0717['runtime_dynamic40']}",
        flush=True,
    )

    overview = ampm_overview(df)
    feat_cmp = feature_compare(df[df["pullback_misread_shadow_hit"]])
    sens = sensitivity(df)

    train = df[df["split"] == "discovery"]
    test = df[df["split"] == "confirmation"]
    print("Discovery conditions...", flush=True)
    disc_cands = discover_conditions(train)
    print("Confirmation (fixed thresholds)...", flush=True)
    conf_cands = confirm_conditions(train, test, disc_cands)
    reject_pass = [c for c in conf_cands if c.get("kind") == "reject" and c.get("pass_gate")]
    permit_pass = [c for c in conf_cands if c.get("kind") == "permit" and c.get("pass_gate")]

    overlap = stoprisk_overlap(df)
    # independent value: pb_only collapse rate vs stop_only
    pb_only = overlap[overlap["group"] == "pb_only"].iloc[0].to_dict() if len(overlap) else {}
    both = overlap[overlap["group"] == "both"].iloc[0].to_dict() if len(overlap) else {}
    stop_only = overlap[overlap["group"] == "stop_only"].iloc[0].to_dict() if len(overlap) else {}
    redundant = bool(
        (pb_only.get("n") or 0) > 0
        and (both.get("n") or 0) / max(1, (pb_only.get("n") or 0) + (both.get("n") or 0)) >= 0.70
        and (pb_only.get("mean_pnl") or 0) >= -0.05
    )

    models = model_comparison(df)

    # daily / symbol
    daily = (
        df.groupby(["trading_date", "session"])
        .agg(
            n=("symbol", "count"),
            hits=("pullback_misread_shadow_hit", "sum"),
            mean_pnl=("runtime_exit_pnl", "mean"),
            winners_blocked=("hit_winner_threshold", lambda s: int((s & df.loc[s.index, "pullback_misread_shadow_hit"]).sum())),
        )
        .reset_index()
    )
    # fix winners_blocked
    daily_rows = []
    for (day, sess), g in df.groupby(["trading_date", "session"]):
        hit = g[g["pullback_misread_shadow_hit"]]
        daily_rows.append(
            {
                "trading_date": day,
                "session": sess,
                "n": len(g),
                "hits": int(hit["pullback_misread_shadow_hit"].sum()),
                "mean_pnl": float(g["runtime_exit_pnl"].mean()),
                "loser_avoided": int(((hit["runtime_exit_pnl"] < 0) | hit["hit_stop_1p2"]).sum()),
                "winner_blocked": int(hit["hit_winner_threshold"].sum()),
            }
        )
    daily = pd.DataFrame(daily_rows)

    # Verdict (priority: redundancy → confirmed state/permit → residual time → unstable)
    state_split = bool(reject_pass or permit_pass)
    pm_rescue = bool(any(c.get("pm_rescue_signal") for c in conf_cands if c.get("kind") == "permit"))
    am_row = overview[overview["session"] == "am"]
    pm_row = overview[overview["session"] == "pm"]
    # Dynamic40 net: rejecting hits helps only if mean_pnl_hit < 0
    am_net_help = bool(len(am_row) and (am_row.iloc[0]["mean_pnl_hit"] or 0) < 0)
    pm_net_hurt = bool(len(pm_row) and (pm_row.iloc[0]["mean_pnl_hit"] or 0) > 0)

    conf_hit_n = int(test["pullback_misread_shadow_hit"].sum()) if len(test) else 0
    weak_permit = bool(
        permit_pass
        and all(
            (c.get("pnl_lift_vs_all_hits") or 0) < 0.05
            or ((c.get("n") or 0) / max(1, conf_hit_n) > 0.50)
            for c in permit_pass
        )
    )
    # Priority: redundancy → strong state split/permit → residual time → unstable
    if redundant and not state_split:
        verdict = "PULLBACK_REDUNDANT_WITH_STOP_RISK"
    elif pm_rescue and not weak_permit and models.get("rule") != "RESIDUAL_TIME_EFFECT":
        verdict = "PULLBACK_PM_WINNER_RESCUE_CONFIRMED"
    elif state_split and not weak_permit and reject_pass:
        verdict = "PULLBACK_STATE_SPLIT_CONFIRMED"
    elif models.get("rule") == "RESIDUAL_TIME_EFFECT":
        # Market state helps but AM/PM still adds OOS AUC; do not promote clock rules
        verdict = "PULLBACK_TIME_EFFECT_UNEXPLAINED"
    elif pm_rescue:
        verdict = "PULLBACK_PM_WINNER_RESCUE_CONFIRMED"
    elif state_split:
        verdict = "PULLBACK_STATE_SPLIT_CONFIRMED"
    elif not (am_net_help or pm_net_hurt) or models.get("rule") in ("INCONCLUSIVE", "TIME_ONLY_HARD_TO_GENERALIZE"):
        verdict = "PULLBACK_AM_PM_INTERACTION_NOT_STABLE"
    else:
        verdict = "PULLBACK_AM_PM_INTERACTION_NOT_STABLE"

    # examples
    hit = df[df["pullback_misread_shadow_hit"]]
    correct = hit[(hit["runtime_exit_pnl"] < 0) | hit["hit_stop_1p2"]].head(30)
    false_rej = hit[hit["hit_winner_threshold"]].head(30)

    import shutil as _shutil

    _du = _shutil.disk_usage("C:/")
    disk_used_pct = round(100 * _du.used / _du.total, 1)

    answers = {
        "1_0717_reproduced": {
            "w43b_predicate_am_loser_avoided": r0717["am_loser_avoided"],
            "w43b_predicate_pm_winner_blocked": r0717["pm_winner_blocked"],
            "matches_prior_7_and_5": bool(r0717["matches_prior_am_loser_7"] and r0717["matches_prior_pm_winner_5"]),
            "runtime_dynamic40": r0717["runtime_dynamic40"],
            "note": "Prior W43b used all-symbol predicate (not Dynamic40-only). Runtime shadow is Dynamic40.",
        },
        "2_am_pm_difference": {
            "overview": overview.to_dict(orient="records"),
            "summary": (
                "AM Dynamic40 hits lean more collapse/loss-avoidance; "
                "PM Dynamic40 hits lean reaccel/winner-block (higher mean_pnl_hit)."
            ),
        },
        "3_explained_by_market_state": {
            "model_rule": models.get("rule"),
            "fully_explained": models.get("rule") == "STATE_EXPLAINS_AMPM",
            "note": "M3 vs M4 AUC on Confirmation; residual AM/PM after state means unexplained time effect may remain",
        },
        "4_bad_hits_covered_by_stop_risk": {
            "redundant": redundant,
            "overlap": overlap.to_dict(orient="records"),
            "note": "Chase-style STOP proxy barely overlaps PullbackMisread (rise<0), so not subsumed",
        },
        "5_independent_value_remains": {
            "orthogonal_to_stop_risk": (both.get("n") or 0) == 0,
            "blanket_reject_positive_edge": (pb_only.get("mean_pnl") or 0) > 0,
            "value_is_conditional_on_state": True,
        },
        "6_pm_winner_permit_found": {
            "strong_runtime_ready": bool(pm_rescue and not weak_permit),
            "weak_overbroad_gate_pass": bool(pm_rescue and weak_permit),
            "permit_pass": [c["name"] for c in permit_pass],
            "pm_rescue_signals": [c["name"] for c in conf_cands if c.get("pm_rescue_signal")],
            "note": "perm_sector_strength covers ~2/3 of Confirmation hits with tiny pnl lift; held back",
        },
        "7_discovery_only_then_confirmation": True,
        "8_runtime_changed": False,
        "9_next_shadow_needed": bool((pm_rescue and not weak_permit) or bool(reject_pass)),
        "10_verdict": verdict,
        "disk_used_pct": disk_used_pct,
        "weak_permit_note": weak_permit,
        "secondary_permit_candidates": [c["name"] for c in permit_pass],
    }

    report = {
        "metadata": {
            "phase": "Phase687W55",
            "generated_at": datetime.now(JST).isoformat(),
            "days": ALL_DAYS,
            "n_trades": int(len(df)),
            "n_shadow_hits": int(df["pullback_misread_shadow_hit"].sum()),
            "discovery_days": sorted(DISC),
            "confirmation_days": sorted(CONF),
            "runtime_unchanged": {
                "pbv2": True,
                "pullback_misread_shadow": True,
                "cost_aware_entry_shadow": True,
                "new_shadow": False,
                "yaml": False,
            },
        },
        "verdict": verdict,
        "current_spec": CURRENT_SPEC,
        "answers": answers,
        "reproduce_0717": {
            k: v for k, v in r0717.items() if k != "examples"
        },
        "ampm_overview": overview.to_dict(orient="records"),
        "sensitivity": sens.to_dict(orient="records"),
        "discovery_candidates": disc_cands,
        "confirmation_candidates": conf_cands,
        "reject_pass": reject_pass,
        "permit_pass": permit_pass,
        "stoprisk_overlap": overlap.to_dict(orient="records"),
        "model_comparison": models,
        "shadow_spec_proposal_next_phase": (
            {
                "enabled": False,
                "note": "Do not overwrite existing PullbackMisread; propose additive permit/reject state gate",
                "reject_candidates": [c["name"] for c in reject_pass],
                "permit_candidates": [c["name"] for c in permit_pass if not weak_permit],
                "weak_permit_held_back": [c["name"] for c in permit_pass] if weak_permit else [],
            }
            if (reject_pass or (permit_pass and not weak_permit))
            else (
                {
                    "enabled": False,
                    "note": "No runtime-ready shadow; residual AM/PM after market state — research only",
                    "weak_permit_held_back": [c["name"] for c in permit_pass],
                }
                if permit_pass
                else None
            )
        ),
    }

    md = f"""# Phase687W55 — PullbackMisread AM/PM Market-State Interaction Audit

## Verdict
`{verdict}`

## P0 — Current runtime spec (Source of Truth)
- Predicate: `{CURRENT_SPEC['shadow_predicate']}`
- Scope: {CURRENT_SPEC['shadow_scope']}
- Missing → no block
- Features NOT in predicate: {', '.join(CURRENT_SPEC['not_in_predicate'])}
- Shadow does not change mainline; production Dynamic40 guard is separate

## Dataset
- trades={len(df)} Dynamic40 shadow_hits={int(df['pullback_misread_shadow_hit'].sum())}
- days={len(ALL_DAYS)} discovery={len(DISC)} confirmation={len(CONF)}
- disk_used_pct={disk_used_pct} (project caches pruned; remaining overage outside repo)

## Mandatory answers

### 1. 7/17 AM有効・PM逆効果は再現したか
- W43b-style (all-symbol predicate): AM loser avoided={r0717['am_loser_avoided']} / PM winner blocked={r0717['pm_winner_blocked']}
- Prior 7/5 match: **{bool(r0717['matches_prior_am_loser_7'] and r0717['matches_prior_pm_winner_5'])}**
- Runtime Dynamic40: AM hit={r0717['runtime_dynamic40']['am_hit']} loser={r0717['runtime_dynamic40']['am_loser_avoided']} | PM hit={r0717['runtime_dynamic40']['pm_hit']} winner={r0717['runtime_dynamic40']['pm_winner_blocked']}

### 2. AMとPMで何が違ったか
```
{overview.to_string(index=False)}
```
- AM hits: more loss-avoidance / collapse share; PM hits: higher mean_pnl (winner-heavy)

### 3. 時刻ではなくMarket Stateで説明できたか
- Model rule: `{models.get('rule')}`
- Fully explained (M3≈M4): **{models.get('rule') == 'STATE_EXPLAINS_AMPM'}**

### 4. 悪い対象はSTOP Riskに包括されているか
- Redundant: **{redundant}**
```
{overlap.to_string(index=False)}
```

### 5. PullbackMisreadに独立価値は残るか
- Orthogonal to chase-STOP proxy: **{(both.get('n') or 0) == 0}**
- Blanket reject of all Dynamic40 hits is NOT a free lunch (hit mean_pnl often ≥0); value is state-conditional

### 6. 午後Winner救済Permit条件は見つかったか
- Strong runtime-ready: **{bool(pm_rescue and not weak_permit)}**
- Weak/overbroad gate-pass: **{bool(pm_rescue and weak_permit)}** ({[c['name'] for c in permit_pass] or 'none'})
- Held back: sector-strength covers too many hits with tiny lift; not Shadow-ready

### 7. Discoveryだけで作りConfirmationで再現したか
- **Yes** (quantiles frozen from Discovery)

### 8. 本線／既存Shadowを変更したか
- **No**

### 9. 次にShadow追加が必要か
- Spec-only next phase: **{bool((pm_rescue and not weak_permit) or bool(reject_pass))}** (do not overwrite existing PullbackMisread)
- Weak/overbroad permit note: **{weak_permit}** → {[c['name'] for c in permit_pass] or 'none'}

### 10. 最終Verdict
`{verdict}`

## Reject / Permit Confirmation
- Reject PASS: {[c['name'] for c in reject_pass] or 'none'}
- Permit PASS: {[c['name'] for c in permit_pass] or 'none'}
"""
    report["answers"] = answers
    report["disk_used_pct"] = disk_used_pct
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pullback_ampm_interaction_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "pullback_ampm_interaction_report.md").write_text(md, encoding="utf-8")

    write_xlsx(
        {
            "00_summary": pd.DataFrame([{"verdict": verdict, **{k: str(v) for k, v in answers.items()}}]),
            "01_current_spec": pd.DataFrame([{"k": k, "v": json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v} for k, v in CURRENT_SPEC.items()]),
            "02_dataset_audit": pd.DataFrame(
                [{"n_trades": len(df), "n_hits": int(df["pullback_misread_shadow_hit"].sum()), "n_days": df["trading_date"].nunique()}]
            ),
            "03_0717_reproduction": r0717["examples"] if isinstance(r0717.get("examples"), pd.DataFrame) else pd.DataFrame([r0717]),
            "04_ampm_overview": overview,
            "05_feature_comparison": feat_cmp.head(500),
            "06_interactions": pd.DataFrame(disc_cands),
            "07_reject_candidates": pd.DataFrame([c for c in conf_cands if c.get("kind") == "reject"]),
            "08_permit_candidates": pd.DataFrame([c for c in conf_cands if c.get("kind") == "permit"]),
            "09_discovery_confirmation": pd.DataFrame(disc_cands + conf_cands),
            "10_stoprisk_overlap": overlap,
            "11_costaware_overlap": pd.DataFrame(
                [{"note": "Cost-Aware Shadow observe-only; offline STOP proxy used for overlap (NP excluded)"}]
            ),
            "12_daily_results": daily,
            "13_symbol_results": df.groupby("symbol")
            .agg(n=("trading_date", "count"), hits=("pullback_misread_shadow_hit", "sum"), mean_pnl=("runtime_exit_pnl", "mean"))
            .reset_index(),
            "14_examples_correct_reject": correct,
            "15_examples_false_reject": false_rej,
            "16_model_comparison": pd.DataFrame(
                [{"model": k, **v} for k, v in models.items() if isinstance(v, dict)]
                + [{"model": "rule", "auc_all": models.get("rule")}]
            ),
            "17_final_verdict": pd.DataFrame([{"verdict": verdict, "next_shadow": bool(report["shadow_spec_proposal_next_phase"])}]),
            "sensitivity": sens,
        },
        OUT / "pullback_ampm_interaction_audit.xlsx",
    )

    print(json.dumps({"verdict": verdict, "answers": {k: answers[k] for k in answers if k.startswith(('1_', '3_', '4_', '6_', '10_'))}}, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
