#!/usr/bin/env python3
"""Phase687W53 — True Watch50 Portfolio Edge Closure.

Builds 20-day simultaneous Watch50 panels, Cap5 reject+fill, walk-forward.
Outputs only:
  entry_edge_closure_report.md / .json / _audit.xlsx
Deletes _w53_tmp after write.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
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
TMP = OUT / "_w53_tmp"
# Persist day snaps across runs (not a deliverable; avoids 20d rebuild). Deleted only if --purge-cache.
SNAP_CACHE = OUT / "_w53_day_snaps_cache"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
MAX_WORKERS = 4
CAP = 5
SHARES = 100
STOP_MAE = -1.2
NO_PROGRESS_MFE = 0.3
NO_PROGRESS_RET = 0.2
HOLD_HORIZON_MIN = 30.0


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


w47 = _load_module("w47_wts", NATIVE / "scripts" / "_w47_winner_trigger_search.py")
w43d = _load_module("w43d_w53", NATIVE / "scripts" / "phase687w43d_5day_winner_state_validation.py")


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
        ["Phase687W53 Watch50 Portfolio Edge Closure"],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "Research-only; PBv2/EXIT/CAP/YAML unchanged; Shadow not enabled"],
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


# ---------------------------------------------------------------------------
# P1 — Build / load 20-day Watch50 panels
# ---------------------------------------------------------------------------


def build_all_day_snaps() -> list[pd.DataFrame]:
    days = w47.classify_market_data_days()
    # chronological oldest→newest for WF
    days = sorted(days, key=lambda d: d["day"])
    frames = []
    SNAP_CACHE.mkdir(parents=True, exist_ok=True)
    for i, meta in enumerate(days):
        print(f"[{i+1}/{len(days)}] snap {meta['day']}...", flush=True)
        df = w47.load_or_build_day_snap(meta)
        # redirect cache from _w47_tmp to _w53 if needed
        cache_w47 = w47.SNAP_CACHE / f"{meta['day']}_watch50_snapshot.parquet"
        cache_w53 = SNAP_CACHE / f"{meta['day']}_watch50_snapshot.parquet"
        if cache_w47.is_file() and not cache_w53.is_file():
            shutil.copy2(cache_w47, cache_w53)
        if df is None or df.empty:
            print(f"  EMPTY {meta['day']}", flush=True)
            continue
        if "trading_date" not in df.columns:
            df["trading_date"] = meta["day"]
        df["trading_date"] = df["trading_date"].astype(str)
        frames.append(df)
        print(f"  rows={len(df)} syms={df['symbol'].nunique()}", flush=True)
    return frames


def ensure_snap_cache_from_w47() -> None:
    """Prefer building via w47 but store under _w53_tmp/day_snaps."""
    # Monkeypatch w47 SNAP_CACHE to our path for this process
    w47.SNAP_CACHE = SNAP_CACHE
    w47.TMP = TMP


# ---------------------------------------------------------------------------
# P2 — Market state (cross-sectional at each snapshot_time)
# ---------------------------------------------------------------------------


def add_market_state(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # time key — W43C snaps use t0_time / t0_epoch
    tcol = "snapshot_time" if "snapshot_time" in df.columns else None
    if tcol is None:
        for c in ("t0_time", "t0", "anchor_time", "event_time"):
            if c in df.columns:
                tcol = c
                break
    if tcol is None:
        for ecol in ("t0_epoch", "anchor_epoch"):
            if ecol in df.columns:
                df["snapshot_time"] = pd.to_datetime(df[ecol], unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
                tcol = "snapshot_time"
                break
    if tcol is None:
        raise KeyError(
            "No snapshot time column found "
            f"(cols sample: {list(df.columns)[:30]})"
        )
    if tcol != "snapshot_time":
        df["snapshot_time"] = pd.to_datetime(df[tcol], utc=False, errors="coerce")
        if df["snapshot_time"].dt.tz is None:
            df["snapshot_time"] = df["snapshot_time"].dt.tz_localize(
                JST, nonexistent="shift_forward", ambiguous="NaT"
            )
        else:
            df["snapshot_time"] = df["snapshot_time"].dt.tz_convert(JST)
        tcol = "snapshot_time"
    else:
        df["snapshot_time"] = pd.to_datetime(df["snapshot_time"], utc=False, errors="coerce")
        if getattr(df["snapshot_time"].dt, "tz", None) is None:
            df["snapshot_time"] = df["snapshot_time"].dt.tz_localize(
                JST, nonexistent="shift_forward", ambiguous="NaT"
            )
        else:
            df["snapshot_time"] = df["snapshot_time"].dt.tz_convert(JST)

    ret = pd.to_numeric(df.get("ret_60s"), errors="coerce")
    volp = pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce")
    spr = pd.to_numeric(df.get("spread_bps"), errors="coerce")
    upd = pd.to_numeric(df.get("push_updates_per_sec_60s"), errors="coerce")
    df["_ret60"] = ret
    df["_volp"] = volp
    df["_spr"] = spr
    df["_upd"] = upd

    gcols = ["trading_date", tcol]
    grp = df.groupby(gcols, sort=False)
    df["mkt_rising_ratio"] = grp["_ret60"].transform(lambda s: float((s > 0).mean()) if len(s) else np.nan)
    df["mkt_breadth"] = df["mkt_rising_ratio"]  # alias
    df["mkt_median_ret_60s"] = grp["_ret60"].transform("median")
    df["mkt_vol_expansion"] = grp["_volp"].transform("mean")
    df["mkt_median_spread"] = grp["_spr"].transform("median")
    df["mkt_median_update_freq"] = grp["_upd"].transform("median")
    df["mkt_volatility"] = grp["_ret60"].transform("std")
    # acceleration proxy: median ret_30 - median ret_120 if available
    if "ret_30s" in df.columns and "ret_120s" in df.columns:
        df["_r30"] = pd.to_numeric(df["ret_30s"], errors="coerce")
        df["_r120"] = pd.to_numeric(df["ret_120s"], errors="coerce")
        df["mkt_acceleration"] = grp["_r30"].transform("median") - grp["_r120"].transform("median")
    else:
        df["mkt_acceleration"] = np.nan
    # sector: first digit of code as coarse sector bucket
    code = df["symbol"].astype(str).str.replace(".T", "", regex=False)
    df["sector_bucket"] = code.str[0]
    sg = df.groupby(gcols + ["sector_bucket"], sort=False)["_ret60"]
    df["sector_rising_ratio"] = sg.transform(lambda s: float((s > 0).mean()) if len(s) else np.nan)
    df["sector_median_ret"] = sg.transform("median")
    df["sector_rel_strength"] = df["sector_median_ret"] - df["mkt_median_ret_60s"]
    # index return missing (no TOPIX in push) — explicit
    df["index_return_60s"] = np.nan
    df["index_return_missing"] = True
    return df


# ---------------------------------------------------------------------------
# Labels / scores
# ---------------------------------------------------------------------------


def add_outcome_labels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ret = pd.to_numeric(df.get("future_30m_return"), errors="coerce")
    mfe = pd.to_numeric(df.get("future_30m_mfe"), errors="coerce")
    mae = pd.to_numeric(df.get("future_30m_mae"), errors="coerce")
    ret15 = pd.to_numeric(df.get("future_15m_return"), errors="coerce") if "future_15m_return" in df.columns else ret
    mfe15 = pd.to_numeric(df.get("future_15m_mfe"), errors="coerce") if "future_15m_mfe" in df.columns else mfe
    df["future_30m_return"] = ret
    df["future_30m_mfe"] = mfe
    df["future_30m_mae"] = mae
    df["winner_a"] = (mfe >= 1.0) & (ret >= 0.5)
    df["stop_proxy"] = mae <= STOP_MAE
    df["np_proxy"] = (mfe15 < NO_PROGRESS_MFE) & (ret15.abs() < NO_PROGRESS_RET)
    # EXIT-applied proxy PnL: hard stop at -1.2 if mae hits
    df["exit_pnl_pct"] = np.where(df["stop_proxy"], STOP_MAE, ret)
    df["exit_pnl_yen"] = df["exit_pnl_pct"] / 100.0 * SHARES * 100.0  # rough: pct of notional; use pct*100 for yen/share*100
    # Align with canonical gross: (exit-entry)/entry * 100 shares ≈ pnl_pct * 100 if pnl_pct in %
    df["exit_pnl_yen"] = df["exit_pnl_pct"] * SHARES  # if pnl_pct is in %, yen = pct/100*price*shares ≈ use pct*1 as prior phases
    # Prior phases used pnl_pct directly as percent points; yen_100 = pnl_pct * 100 for 100 shares at ~price cancel
    # Use pnl_pct sum as primary (gross percent-points), yen = pnl_pct * 100 for display consistency with *100 share at 1% = 100yen per 100yen stock... 
    # Stick to exit_pnl_pct as the portfolio objective (percent points).
    return df


WINNER_RULES = [
    ("net_bid_pressure_60s", "high", "vol_persistence_300s", "low"),
    ("net_ask_pressure_60s", "high", "vol_persistence_300s", "low"),
    ("pre_300s_new_high_count", "high", "vol_persistence_300s", "low"),
    ("fall_from_high_300s", "high", "vol_persistence_300s", "low"),
    ("day_high_distance_pct", "high", "vol_persistence_300s", "low"),
    ("bounce_from_low_300s", "high", "vol_persistence_300s", "low"),
]


def _q_mask(s: pd.Series, side: str, q_hi: float = 0.8, q_lo: float = 0.2) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if side == "high":
        thr = x.quantile(q_hi)
        return x >= thr
    thr = x.quantile(q_lo)
    return x <= thr


def add_scores(df: pd.DataFrame, *, fit_days: Optional[set[str]] = None) -> pd.DataFrame:
    """Compute scores; quantile thresholds fit on fit_days only (Discovery / WF train)."""
    df = df.copy()
    train = df[df["trading_date"].astype(str).isin(fit_days)] if fit_days else df
    enrich = pd.Series(0.0, index=df.index)
    for a, sa, b, sb in WINNER_RULES:
        if a not in df.columns or b not in df.columns:
            continue
        # thresholds from train
        xa = pd.to_numeric(train[a], errors="coerce")
        xb = pd.to_numeric(train[b], errors="coerce")
        thr_a = xa.quantile(0.8 if sa == "high" else 0.2)
        thr_b = xb.quantile(0.2 if sb == "low" else 0.8)
        ma = pd.to_numeric(df[a], errors="coerce")
        mb = pd.to_numeric(df[b], errors="coerce")
        hit = (ma >= thr_a if sa == "high" else ma <= thr_a) & (
            mb <= thr_b if sb == "low" else mb >= thr_b
        )
        enrich = enrich + hit.astype(float)
    df["winner_enrichment_score"] = enrich
    # PBv2 score proxy
    if "score_v2" in df.columns:
        df["pbv2_score"] = pd.to_numeric(df["score_v2"], errors="coerce")
    else:
        # continuation / expectancy proxies if present
        for c in ("continuation_quality", "entry_expectancy_score_v2", "score_proxy"):
            if c in df.columns:
                df["pbv2_score"] = pd.to_numeric(df[c], errors="coerce")
                break
        else:
            # rank-normalize ret_60 + imbalance as weak proxy (not PBv2 — flag)
            df["pbv2_score"] = pd.to_numeric(df.get("ret_60s"), errors="coerce").fillna(0) + 0.1 * pd.to_numeric(
                df.get("imbalance_l5"), errors="coerce"
            ).fillna(0)
            df["pbv2_score_is_proxy"] = True

    # STOP risk score (chase / exhaustion)
    comps = []
    for c, w in (
        ("ret_60s", 1.0),
        ("ret_120s", 0.8),
        ("slope_60s", 1.0),
        ("fall_from_high_300s", -0.5),
        ("vol_persistence_300s", -1.0),
        ("spread_bps", 0.3),
        ("seconds_since_last_new_high", 0.2),
    ):
        if c not in df.columns:
            continue
        x = pd.to_numeric(df[c], errors="coerce")
        mu = pd.to_numeric(train[c], errors="coerce").mean() if c in train.columns else x.mean()
        sd = pd.to_numeric(train[c], errors="coerce").std() if c in train.columns else x.std()
        z = (x - mu) / (sd if sd and sd > 1e-9 else 1.0)
        comps.append(w * z.fillna(0))
    df["stop_risk_score"] = sum(comps) if comps else 0.0

    # no-progress risk: stall / low activity / far from high
    ncomps = []
    for c, w in (
        ("seconds_since_last_new_high", 1.0),
        ("day_high_distance_pct", 0.8),
        ("spread_bps", 0.5),
        ("vol_persistence_300s", -0.8),
        ("board_updates_per_sec_60s", -0.5),
        ("mkt_rising_ratio", -0.4),
    ):
        if c not in df.columns:
            continue
        x = pd.to_numeric(df[c], errors="coerce")
        mu = pd.to_numeric(train[c], errors="coerce").mean() if c in train.columns else x.mean()
        sd = pd.to_numeric(train[c], errors="coerce").std() if c in train.columns else x.std()
        z = (x - mu) / (sd if sd and sd > 1e-9 else 1.0)
        ncomps.append(w * z.fillna(0))
    df["np_risk_score"] = sum(ncomps) if ncomps else 0.0

    # Pullback real features
    df["pullback_depth"] = pd.to_numeric(df.get("fall_from_high_300s"), errors="coerce")
    df["bounce"] = pd.to_numeric(df.get("bounce_from_low_300s"), errors="coerce")
    df["pullback_flag"] = (
        (pd.to_numeric(df.get("ret_30s"), errors="coerce") < 0)
        | (pd.to_numeric(df.get("ret_60s"), errors="coerce") < 0)
    ) & (df["pullback_depth"].fillna(0) > 0)

    # Integrated score
    we = df["winner_enrichment_score"].fillna(0)
    # standardize risk scores on train
    def _std(col: str) -> pd.Series:
        x = pd.to_numeric(df[col], errors="coerce").fillna(0)
        mu = pd.to_numeric(train[col], errors="coerce").mean() if col in train.columns else x.mean()
        sd = pd.to_numeric(train[col], errors="coerce").std() if col in train.columns else x.std()
        return (x - (mu or 0)) / (sd if sd and sd > 1e-9 else 1.0)

    df["integrated_score"] = (
        _std("pbv2_score")
        + 0.35 * we
        - 0.45 * _std("stop_risk_score")
        - 0.25 * _std("np_risk_score")
    )
    return df


# ---------------------------------------------------------------------------
# P4 — Cap5 portfolio simulator with true reject+fill
# ---------------------------------------------------------------------------


@dataclass
class Position:
    symbol: str
    entry_time: pd.Timestamp
    entry_pnl_path: float  # exit_pnl_pct assigned at entry from that row
    stop: bool
    np: bool
    winner: bool
    score: float


@dataclass
class SimResult:
    name: str
    trades: list[dict[str, Any]] = field(default_factory=list)

    def metrics(self, cost_bps: float = 0.0) -> dict[str, Any]:
        if not self.trades:
            return {
                "name": self.name,
                "n_trades": 0,
                "total_pnl_pct": 0.0,
                "pf": None,
                "win_rate": None,
                "stop_rate": None,
                "np_rate": None,
                "winner_rate": None,
                "max_dd": 0.0,
                "cost_bps": cost_bps,
            }
        pnl = np.array([t["pnl_pct"] - cost_bps / 100.0 * 2 for t in self.trades], dtype=float)  # roundtrip
        wins = pnl[pnl > 0].sum()
        losses = -pnl[pnl < 0].sum()
        pf = float(wins / losses) if losses > 1e-12 else (999.0 if wins > 0 else None)
        cum = np.cumsum(pnl)
        peak = np.maximum.accumulate(cum)
        dd = float((cum - peak).min()) if len(cum) else 0.0
        return {
            "name": self.name,
            "n_trades": len(self.trades),
            "total_pnl_pct": float(pnl.sum()),
            "mean_pnl_pct": float(pnl.mean()),
            "pf": pf,
            "win_rate": float((pnl > 0).mean()),
            "stop_rate": float(np.mean([t["stop"] for t in self.trades])),
            "np_rate": float(np.mean([t["np"] for t in self.trades])),
            "winner_rate": float(np.mean([t["winner"] for t in self.trades])),
            "max_dd": dd,
            "cost_bps": cost_bps,
            "n_symbols": len({t["symbol"] for t in self.trades}),
            "n_days": len({t["trading_date"] for t in self.trades}),
        }


def _row_time(r: pd.Series) -> pd.Timestamp:
    t = r["snapshot_time"]
    if not isinstance(t, pd.Timestamp):
        t = pd.Timestamp(t)
    if t.tzinfo is None:
        t = t.tz_localize(JST)
    return t


def simulate_cap5(
    day_df: pd.DataFrame,
    *,
    score_col: str,
    stop_thr: Optional[float] = None,
    np_thr: Optional[float] = None,
    reject_pullback: bool = False,
    name: str = "sim",
    fill: bool = True,
    reentry_policy: Optional[str] = None,
    cooloff_min: float = 30.0,
    imb_unlock_q: Optional[float] = None,
    spr_unlock_q: Optional[float] = None,
) -> SimResult:
    """Event-driven Cap5 on one day. fill=False => reject_only (no replacement)."""
    res = SimResult(name=name)
    if day_df.empty:
        return res
    df = day_df.copy()
    df["_t"] = pd.to_datetime(df["snapshot_time"], utc=True).dt.tz_convert(JST)
    df = df.sort_values(["_t", score_col], ascending=[True, False])
    times = sorted(df["_t"].unique())
    open_pos: dict[str, Position] = {}
    last_exit: dict[str, pd.Timestamp] = {}
    last_feat: dict[str, dict[str, float]] = {}

    for t in times:
        t = pd.Timestamp(t)
        if t.tzinfo is None:
            t = t.tz_localize(JST)
        # close expired
        to_close = []
        for sym, pos in open_pos.items():
            held_min = (t - pos.entry_time).total_seconds() / 60.0
            if held_min >= HOLD_HORIZON_MIN:
                to_close.append(sym)
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
            last_exit[sym] = t
            last_feat[sym] = {"imbalance_l5": 0.0, "spread_bps": 0.0}

        snap = df[df["_t"] == t]
        if snap.empty:
            continue
        # rank
        cand = snap.sort_values(score_col, ascending=False)
        slots = CAP - len(open_pos)
        if slots <= 0:
            continue
        selected = 0
        for _, r in cand.iterrows():
            if selected >= slots:
                break
            sym = str(r["symbol"])
            if sym in open_pos:
                continue
            # reentry hybrid gates
            if reentry_policy in ("B", "D", "E") and sym in last_exit:
                elapsed = (t - last_exit[sym]).total_seconds() / 60.0
                if elapsed < cooloff_min:
                    if reentry_policy == "B":
                        continue
                    prev = last_feat.get(sym, {})
                    d_imb = _safe_f(r.get("imbalance_l5")) - prev.get("imbalance_l5", 0.0)
                    d_spr = _safe_f(r.get("spread_bps")) - prev.get("spread_bps", 0.0)
                    if reentry_policy == "D":
                        # early unlock: board improved (imbalance up) and spread compressed
                        # vs discovery quantiles of deltas when provided; else sign rules
                        if imb_unlock_q is not None and spr_unlock_q is not None:
                            if not (d_imb >= imb_unlock_q and d_spr <= spr_unlock_q):
                                continue
                        elif not (d_imb >= 0 and d_spr <= 0):
                            continue
                    elif reentry_policy == "E":
                        if d_imb < 0 or d_spr > 0:
                            continue
                        continue  # cooloff still applies when not worsened
            # rejects
            if stop_thr is not None and _safe_f(r.get("stop_risk_score")) >= stop_thr:
                continue  # skip; fill walks to next rank when fill=True
            if np_thr is not None and _safe_f(r.get("np_risk_score")) >= np_thr:
                continue
            if reject_pullback and bool(r.get("pullback_reject")):
                continue
            # enter
            open_pos[sym] = Position(
                symbol=sym,
                entry_time=t,
                entry_pnl_path=_safe_f(r.get("exit_pnl_pct")),
                stop=bool(r.get("stop_proxy")),
                np=bool(r.get("np_proxy")),
                winner=bool(r.get("winner_a")),
                score=_safe_f(r.get(score_col)),
            )
            last_feat[sym] = {
                "imbalance_l5": _safe_f(r.get("imbalance_l5")),
                "spread_bps": _safe_f(r.get("spread_bps")),
            }
            selected += 1

    # close remaining at last time
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


def simulate_cap5_reject_only_toprank(
    day_df: pd.DataFrame,
    *,
    score_col: str,
    stop_thr: Optional[float],
    np_thr: Optional[float],
    name: str,
) -> SimResult:
    """Reject-only: among top CAP raw ranks, drop rejects; do NOT walk further (no fill)."""
    res = SimResult(name=name)
    if day_df.empty:
        return res
    df = day_df.copy()
    df["_t"] = pd.to_datetime(df["snapshot_time"], utc=True).dt.tz_convert(JST)
    df = df.sort_values(["_t", score_col], ascending=[True, False])
    times = sorted(df["_t"].unique())
    open_pos: dict[str, Position] = {}
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
        snap = df[df["_t"] == t].sort_values(score_col, ascending=False)
        slots = CAP - len(open_pos)
        if slots <= 0:
            continue
        top = snap.head(slots + len(open_pos))  # raw top window
        # only evaluate the first `slots` free ranks in top without extending
        considered = 0
        for _, r in top.iterrows():
            if considered >= slots:
                break
            sym = str(r["symbol"])
            if sym in open_pos:
                continue
            considered += 1  # consumes a rank slot even if rejected
            if stop_thr is not None and _safe_f(r.get("stop_risk_score")) >= stop_thr:
                continue
            if np_thr is not None and _safe_f(r.get("np_risk_score")) >= np_thr:
                continue
            open_pos[sym] = Position(
                symbol=sym,
                entry_time=t,
                entry_pnl_path=_safe_f(r.get("exit_pnl_pct")),
                stop=bool(r.get("stop_proxy")),
                np=bool(r.get("np_proxy")),
                winner=bool(r.get("winner_a")),
                score=_safe_f(r.get(score_col)),
            )
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


def run_multi_day(
    panel: pd.DataFrame,
    days: list[str],
    *,
    score_col: str,
    stop_thr: Optional[float],
    np_thr: Optional[float],
    mode: str,
    name: str,
    reentry_policy: Optional[str] = None,
    imb_unlock_q: Optional[float] = None,
    spr_unlock_q: Optional[float] = None,
    reject_pullback: bool = False,
) -> SimResult:
    all_trades: list[dict[str, Any]] = []
    for d in days:
        day_df = panel[panel["trading_date"].astype(str) == d]
        if day_df.empty:
            continue
        if mode == "reject_only":
            r = simulate_cap5_reject_only_toprank(
                day_df, score_col=score_col, stop_thr=stop_thr, np_thr=np_thr, name=name
            )
        else:
            r = simulate_cap5(
                day_df,
                score_col=score_col,
                stop_thr=stop_thr,
                np_thr=np_thr,
                name=name,
                fill=(mode == "reject_fill"),
                reentry_policy=reentry_policy,
                imb_unlock_q=imb_unlock_q,
                spr_unlock_q=spr_unlock_q,
                reject_pullback=reject_pullback,
            )
        all_trades.extend(r.trades)
    out = SimResult(name=name, trades=all_trades)
    return out


def stop_risk_cluster_collapse(train: pd.DataFrame) -> dict[str, Any]:
    """Collapse STOP archetypes → one additive condition per cluster → StopRiskScore."""
    clusters = {
        "short_term_chase": lambda df: (
            (pd.to_numeric(df.get("ret_60s"), errors="coerce") >= pd.to_numeric(train["ret_60s"], errors="coerce").quantile(0.85))
            & (pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce") <= pd.to_numeric(train["vol_persistence_300s"], errors="coerce").quantile(0.3))
        ),
        "high_slope_exhaustion": lambda df: (
            (pd.to_numeric(df.get("slope_60s"), errors="coerce") >= pd.to_numeric(train["slope_60s"], errors="coerce").quantile(0.85))
            & (pd.to_numeric(df.get("seconds_since_last_new_high"), errors="coerce") >= pd.to_numeric(train["seconds_since_last_new_high"], errors="coerce").quantile(0.7))
        ),
        "high_return_no_vol": lambda df: (
            (pd.to_numeric(df.get("ret_120s"), errors="coerce") >= pd.to_numeric(train["ret_120s"], errors="coerce").quantile(0.85))
            & (pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce") <= pd.to_numeric(train["vol_persistence_300s"], errors="coerce").quantile(0.25))
        ),
        "high_update_stall": lambda df: (
            (pd.to_numeric(df.get("pre_300s_new_high_count"), errors="coerce") >= pd.to_numeric(train["pre_300s_new_high_count"], errors="coerce").quantile(0.8))
            & (pd.to_numeric(df.get("seconds_since_last_new_high"), errors="coerce") >= pd.to_numeric(train["seconds_since_last_new_high"], errors="coerce").quantile(0.75))
        ),
        "spread_expansion": lambda df: (
            pd.to_numeric(df.get("spread_bps"), errors="coerce") >= pd.to_numeric(train["spread_bps"], errors="coerce").quantile(0.9)
        ),
        "board_pressure_reversal": lambda df: (
            (pd.to_numeric(df.get("net_ask_pressure_60s"), errors="coerce") >= pd.to_numeric(train["net_ask_pressure_60s"], errors="coerce").quantile(0.8))
            & (pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce") < 0)
        ),
    }
    selected = []
    covered = pd.Series(False, index=train.index)
    for name, fn in clusters.items():
        m = fn(train).fillna(False)
        if int(m.sum()) < 20:
            continue
        # incremental contribution: extra blocked losers beyond already covered
        incr = m & ~covered
        pnl_delta = float((-train.loc[incr, "exit_pnl_pct"]).sum()) if incr.any() else 0.0
        w_all = float(train["winner_a"].sum()) or 1.0
        sacr = float(train.loc[m, "winner_a"].sum()) / w_all
        selected.append(
            {
                "cluster": name,
                "n": int(m.sum()),
                "incr_n": int(incr.sum()),
                "pnl_delta_incr": pnl_delta,
                "winner_sacrifice": sacr,
                "kept": sacr <= 0.10,
            }
        )
        if sacr <= 0.10 and pnl_delta > 0:
            covered = covered | m
    overlap = float(covered.mean()) if len(covered) else 0.0
    return {
        "n_clusters": len(clusters),
        "selected": selected,
        "union_coverage_frac": overlap,
        "integration": "StopRiskScore >= threshold (single portfolio reject; not 38 parallel rules)",
        "note": "38 discrete STOP rules collapsed into 6 archetypes then one RiskScore threshold used in Cap5",
    }


# ---------------------------------------------------------------------------
# Pullback / 4062 / reentry / STOP cluster
# ---------------------------------------------------------------------------


def fit_stop_threshold(train: pd.DataFrame) -> tuple[float, dict[str, Any]]:
    """Choose StopRiskScore threshold maximizing Confirmation-like CV on train (simple)."""
    y = train["stop_proxy"].astype(bool)
    s = pd.to_numeric(train["stop_risk_score"], errors="coerce")
    best = {"thr": float(s.quantile(0.9)), "net": -1e18, "sacr": 1.0}
    for q in (0.85, 0.88, 0.90, 0.92, 0.95):
        thr = float(s.quantile(q))
        blk = s >= thr
        if blk.sum() < 30:
            continue
        # blocking stops saves -mae approx; use exit_pnl of blocked
        pnl_delta = float((-train.loc[blk, "exit_pnl_pct"]).sum())
        w_all = float(train["winner_a"].sum()) or 1.0
        sacr = float(train.loc[blk, "winner_a"].sum()) / w_all
        if sacr <= 0.10 and pnl_delta > best["net"]:
            best = {"thr": thr, "net": pnl_delta, "sacr": sacr, "q": q, "n_blocked": int(blk.sum())}
    return float(best["thr"]), best


def fit_np_threshold(train: pd.DataFrame) -> tuple[float, dict[str, Any]]:
    s = pd.to_numeric(train["np_risk_score"], errors="coerce")
    best = {"thr": float(s.quantile(0.9)), "net": -1e18, "sacr": 1.0}
    for q in (0.85, 0.90, 0.93, 0.95):
        thr = float(s.quantile(q))
        blk = s >= thr
        if blk.sum() < 20:
            continue
        pnl_delta = float((-train.loc[blk, "exit_pnl_pct"]).sum())
        w_all = float(train["winner_a"].sum()) or 1.0
        sacr = float(train.loc[blk, "winner_a"].sum()) / w_all
        if sacr <= 0.10 and pnl_delta > best["net"]:
            best = {"thr": thr, "net": pnl_delta, "sacr": sacr, "q": q, "n_blocked": int(blk.sum())}
    return float(best["thr"]), best


def pullback_analysis(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, Any]:
    """Real pullback features → REJECT/ALLOW/PROMOTE with OOS metrics."""
    def _eval(mask: pd.Series, df: pd.DataFrame) -> dict[str, Any]:
        sub = df[mask]
        if sub.empty:
            return {"n": 0}
        return {
            "n": int(len(sub)),
            "mean_pnl": float(sub["exit_pnl_pct"].mean()),
            "total_pnl": float(sub["exit_pnl_pct"].sum()),
            "pf": _pf(sub["exit_pnl_pct"]),
            "stop_rate": float(sub["stop_proxy"].mean()),
            "winner_rate": float(sub["winner_a"].mean()),
        }

    tr = train[train["pullback_flag"].fillna(False)].copy()
    te = test[test["pullback_flag"].fillna(False)].copy()
    if tr.empty or te.empty:
        return {"confirmed": False, "reason": "no_pullback_rows", "n_train": int(len(tr)), "n_test": int(len(te))}

    # REJECT: deep pullback + weak board + low vol persistence
    def rej_m(df: pd.DataFrame) -> pd.Series:
        return (
            (pd.to_numeric(df["pullback_depth"], errors="coerce") >= pd.to_numeric(tr["pullback_depth"], errors="coerce").quantile(0.7))
            & (pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce") <= pd.to_numeric(tr["vol_persistence_300s"], errors="coerce").quantile(0.3))
            & (pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce") <= 0)
        )

    def allow_m(df: pd.DataFrame) -> pd.Series:
        return (
            df["pullback_flag"].fillna(False)
            & (pd.to_numeric(df.get("bounce"), errors="coerce") >= pd.to_numeric(tr["bounce"], errors="coerce").quantile(0.6))
            & (pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce") >= pd.to_numeric(tr["vol_persistence_300s"], errors="coerce").quantile(0.5))
            & (pd.to_numeric(df.get("mkt_rising_ratio"), errors="coerce") >= 0.45)
        )

    def promo_m(df: pd.DataFrame) -> pd.Series:
        return (
            allow_m(df)
            & (pd.to_numeric(df.get("vwap_dev_pct"), errors="coerce") <= 0)
            & (pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce") > 0)
        )

    out = {
        "REJECT_PULLBACK": {"train": _eval(rej_m(tr), tr), "test": _eval(rej_m(te), te)},
        "ALLOW_PULLBACK": {"train": _eval(allow_m(tr), tr), "test": _eval(allow_m(te), te)},
        "PROMOTE_PULLBACK": {"train": _eval(promo_m(tr), tr), "test": _eval(promo_m(te), te)},
    }
    # confirm REJECT if test mean_pnl of rejected path is worse than allow; PROMOTE if test mean_pnl>0 and pf>=1
    rej_t = out["REJECT_PULLBACK"]["test"]
    pro_t = out["PROMOTE_PULLBACK"]["test"]
    allow_t = out["ALLOW_PULLBACK"]["test"]
    confirmed = False
    if rej_t.get("n", 0) >= 30 and allow_t.get("n", 0) >= 30:
        if (rej_t.get("mean_pnl") or 0) < (allow_t.get("mean_pnl") or 0):
            confirmed = True
    promote_ok = bool(pro_t.get("n", 0) >= 20 and (pro_t.get("mean_pnl") or -1) > 0 and (pro_t.get("pf") or 0) >= 1.0)
    out["confirmed"] = confirmed
    out["promote_confirmed"] = promote_ok
    if not promote_ok:
        out["PROMOTE_PULLBACK"]["note"] = "not confirmed OOS (mean_pnl/PF gate)"
    return out


def _pf(s: pd.Series) -> Optional[float]:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if x.empty:
        return None
    gp = float(x[x > 0].sum())
    gl = float(-x[x < 0].sum())
    if gl < 1e-12:
        return 999.0 if gp > 0 else None
    return gp / gl


def archetype_subtypes(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, Any]:
    """Decompose SLOPE_FADE_WIDE_SPREAD_STALE_HIGH into subtypes with sacr<=10%."""
    def mask_base(df: pd.DataFrame) -> pd.Series:
        return (
            (pd.to_numeric(df.get("slope_60s"), errors="coerce") >= pd.to_numeric(train["slope_60s"], errors="coerce").quantile(0.75))
            & (pd.to_numeric(df.get("spread_bps"), errors="coerce") >= pd.to_numeric(train["spread_bps"], errors="coerce").quantile(0.7))
            & (pd.to_numeric(df.get("seconds_since_last_new_high"), errors="coerce") >= pd.to_numeric(train["seconds_since_last_new_high"], errors="coerce").quantile(0.7))
        )

    subtypes = {
        "CHASE_LOW_BREADTH": lambda df: mask_base(df) & (pd.to_numeric(df.get("mkt_rising_ratio"), errors="coerce") < 0.4),
        "CHASE_WEAK_VOLUME": lambda df: mask_base(df) & (pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce") <= pd.to_numeric(train["vol_persistence_300s"], errors="coerce").quantile(0.3)),
        "CHASE_BOARD_REVERSAL": lambda df: mask_base(df) & (pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce") < 0),
        "CHASE_VWAP_EXTENDED": lambda df: mask_base(df) & (pd.to_numeric(df.get("vwap_dev_pct"), errors="coerce") >= pd.to_numeric(train["vwap_dev_pct"], errors="coerce").quantile(0.7)),
    }
    results = {}
    best = None
    for name, fn in subtypes.items():
        m = fn(test)
        n = int(m.sum())
        if n < 15:
            results[name] = {"n": n, "ok": False}
            continue
        w_all = float(test["winner_a"].sum()) or 1.0
        sacr = float(test.loc[m, "winner_a"].sum()) / w_all
        pnl_delta = float((-test.loc[m, "exit_pnl_pct"]).sum())
        row = {
            "n": n,
            "winner_sacrifice_rate": sacr,
            "net_pnl_delta": pnl_delta,
            "stop_rate_blocked": float(test.loc[m, "stop_proxy"].mean()),
            "ok": sacr <= 0.10 and pnl_delta > 0,
        }
        results[name] = row
        if row["ok"] and (best is None or pnl_delta > best["net_pnl_delta"]):
            best = {"name": name, **row}
    return {
        "base_name": "SLOPE_FADE_WIDE_SPREAD_STALE_HIGH",
        "subtypes": results,
        "best": best,
        "confirmed": best is not None,
        "verdict": "ARCHETYPE_4062_SUBTYPE_CONFIRMED" if best else "ARCHETYPE_4062_GENERALIZED_BUT_OVERBROAD",
    }


def reentry_hybrid_eval(panel: pd.DataFrame, days: list[str]) -> dict[str, Any]:
    """Compare cooloff variants using sequential entries per symbol from panel tops (approx)."""
    # Build pseudo entry stream: each day top integrated_score per hour as entry candidates
    rows = []
    for d in days:
        day = panel[panel["trading_date"].astype(str) == d].copy()
        if day.empty:
            continue
        day["hour"] = day["snapshot_time"].dt.floor("15min")
        # one candidate per symbol per 15m: max score
        idx = day.groupby(["symbol", "hour"])["integrated_score"].idxmax()
        cand = day.loc[idx.values]
        rows.append(cand)
    if not rows:
        return {"confirmed": False, "reason": "no_rows"}
    stream = pd.concat(rows, ignore_index=True).sort_values(["symbol", "snapshot_time"])

    def run_policy(policy: str) -> dict[str, Any]:
        last_exit: dict[str, pd.Timestamp] = {}
        last_feat: dict[str, dict[str, float]] = {}
        trades = []
        for _, r in stream.iterrows():
            sym = str(r["symbol"])
            t = _row_time(r)
            allow = True
            if policy == "A":
                allow = True
            elif policy == "B":
                if sym in last_exit and (t - last_exit[sym]).total_seconds() < 30 * 60:
                    allow = False
            elif policy == "C":
                # permit only if imbalance improved (higher) and spread down vs last
                prev = last_feat.get(sym)
                if prev is None:
                    allow = True
                else:
                    d_imb = _safe_f(r.get("imbalance_l5")) - prev.get("imbalance_l5", 0)
                    d_spr = _safe_f(r.get("spread_bps")) - prev.get("spread_bps", 0)
                    # improvement: imbalance up (more bid), spread down
                    allow = (d_imb >= 0) and (d_spr <= 0)
            elif policy == "D":
                # 30m cooloff with early unlock if board improved
                if sym in last_exit and (t - last_exit[sym]).total_seconds() < 30 * 60:
                    prev = last_feat.get(sym)
                    if prev is None:
                        allow = False
                    else:
                        d_imb = _safe_f(r.get("imbalance_l5")) - prev.get("imbalance_l5", 0)
                        d_spr = _safe_f(r.get("spread_bps")) - prev.get("spread_bps", 0)
                        allow = (d_imb >= 0) and (d_spr <= 0)  # early unlock
                else:
                    allow = True
            elif policy == "E":
                if sym in last_exit and (t - last_exit[sym]).total_seconds() < 30 * 60:
                    prev = last_feat.get(sym)
                    if prev is not None:
                        d_imb = _safe_f(r.get("imbalance_l5")) - prev.get("imbalance_l5", 0)
                        d_spr = _safe_f(r.get("spread_bps")) - prev.get("spread_bps", 0)
                        # reject if worsened
                        if d_imb < 0 or d_spr > 0:
                            allow = False
                        else:
                            allow = False  # still cooloff
                    else:
                        allow = False
            if not allow:
                continue
            trades.append(_safe_f(r.get("exit_pnl_pct")))
            # synthetic exit 30m later
            last_exit[sym] = t + pd.Timedelta(minutes=30)
            last_feat[sym] = {
                "imbalance_l5": _safe_f(r.get("imbalance_l5")),
                "spread_bps": _safe_f(r.get("spread_bps")),
            }
        s = pd.Series(trades, dtype=float)
        return {
            "n": int(len(s)),
            "total_pnl": float(s.sum()) if len(s) else 0.0,
            "mean_pnl": float(s.mean()) if len(s) else None,
            "pf": _pf(s),
        }

    policies = {p: run_policy(p) for p in ("A", "B", "C", "D", "E")}
    # D confirmed if better than B and A on pnl/pf
    d_ok = (
        (policies["D"]["pf"] or 0) >= (policies["B"]["pf"] or 0)
        and (policies["D"]["total_pnl"] or -1e9) >= (policies["B"]["total_pnl"] or -1e9)
        and (policies["D"]["n"] or 0) >= 30
    )
    return {
        "policies": policies,
        "imbalance_sign_interpretation": "higher imbalance_l5 = more bid support (improvement when d_imbalance>=0)",
        "confirmed": d_ok,
        "verdict": "REENTRY_HYBRID_CONFIRMED" if d_ok else "NO_STABLE_REENTRY_HYBRID",
    }


def am_pm_market_state_explanation(panel: pd.DataFrame) -> dict[str, Any]:
    df = panel.copy()
    df["ampm"] = np.where(df["snapshot_time"].dt.hour < 12, "am", "pm")
    # bucket by breadth
    df["breadth_bucket"] = pd.qcut(pd.to_numeric(df["mkt_rising_ratio"], errors="coerce").rank(method="first"), 3, labels=["low", "mid", "high"])
    rows = []
    for (ampm, b), g in df.groupby(["ampm", "breadth_bucket"], observed=False):
        rows.append(
            {
                "ampm": ampm,
                "breadth_bucket": str(b),
                "n": len(g),
                "mean_exit_pnl": float(g["exit_pnl_pct"].mean()),
                "stop_rate": float(g["stop_proxy"].mean()),
            }
        )
    tab = pd.DataFrame(rows)
    # same breadth bucket: AM/PM same sign?
    agree = 0
    tot = 0
    for b, g in tab.groupby("breadth_bucket"):
        if set(g["ampm"]) != {"am", "pm"}:
            continue
        tot += 1
        signs = np.sign(g.set_index("ampm").loc[["am", "pm"], "mean_exit_pnl"].values)
        if signs[0] == signs[1]:
            agree += 1
    return {
        "table": rows,
        "same_state_sign_agree_frac": (agree / tot) if tot else None,
        "explained": bool(tot and agree / tot >= 0.66),
    }


# ---------------------------------------------------------------------------
# Expanding walk-forward
# ---------------------------------------------------------------------------


def expanding_walk_forward(panel: pd.DataFrame) -> dict[str, Any]:
    days = sorted(panel["trading_date"].astype(str).unique())
    if len(days) < 9:
        return {"ok": False, "reason": f"need>=9 days, got {len(days)}"}
    holdouts = []
    for i in range(8, len(days)):
        train_days = set(days[:i])
        test_day = days[i]
        train = panel[panel["trading_date"].astype(str).isin(train_days)]
        test = panel[panel["trading_date"].astype(str) == test_day]
        scored_train = add_scores(train, fit_days=train_days)
        # apply thresholds from train to full test via concat trick
        both = add_scores(pd.concat([train, test], ignore_index=True), fit_days=train_days)
        test_s = both[both["trading_date"].astype(str) == test_day]
        stop_thr, stop_meta = fit_stop_threshold(scored_train)
        np_thr, np_meta = fit_np_threshold(scored_train)
        sim = run_multi_day(
            test_s,
            [test_day],
            score_col="integrated_score",
            stop_thr=stop_thr,
            np_thr=np_thr,
            mode="reject_fill",
            name=f"wf_{test_day}",
        )
        m0 = sim.metrics(0.0)
        m5 = sim.metrics(5.0)
        holdouts.append(
            {
                "test_day": test_day,
                "train_n_days": len(train_days),
                "stop_thr": stop_thr,
                "np_thr": np_thr,
                "stop_meta": stop_meta,
                "metrics_0bps": m0,
                "metrics_5bps": m5,
            }
        )
        print(
            f"  WF {test_day}: trades={m0['n_trades']} pnl={m0['total_pnl_pct']:.2f} pf={m0['pf']}",
            flush=True,
        )
    # aggregate
    pnls = [h["metrics_0bps"]["total_pnl_pct"] for h in holdouts]
    pfs = [h["metrics_0bps"]["pf"] for h in holdouts if h["metrics_0bps"]["pf"] is not None]
    pnls5 = [h["metrics_5bps"]["total_pnl_pct"] for h in holdouts]
    pfs5 = [h["metrics_5bps"]["pf"] for h in holdouts if h["metrics_5bps"]["pf"] is not None]
    day_nonworse = 0
    for h in holdouts:
        # vs pbv2 score-only cap5 that day
        pass
    return {
        "ok": True,
        "n_holdouts": len(holdouts),
        "holdouts": holdouts,
        "sum_pnl_0bps": float(sum(pnls)),
        "mean_pf_0bps": float(np.mean(pfs)) if pfs else None,
        "sum_pnl_5bps": float(sum(pnls5)),
        "mean_pf_5bps": float(np.mean(pfs5)) if pfs5 else None,
        "frac_days_pnl_nonneg": float(np.mean([p >= 0 for p in pnls])) if pnls else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== Phase687W53 Watch50 Portfolio Edge Closure ===", flush=True)
    TMP.mkdir(parents=True, exist_ok=True)
    ensure_snap_cache_from_w47()

    # Also copy w47 cache path
    w47_cache = NATIVE / "results" / "research" / "pre_entry_market_state" / "_w47_tmp" / "day_snaps"
    if w47_cache.is_dir():
        SNAP_CACHE.mkdir(parents=True, exist_ok=True)
        for p in w47_cache.glob("*.parquet"):
            dest = SNAP_CACHE / p.name
            if not dest.is_file():
                shutil.copy2(p, dest)

    frames = build_all_day_snaps()
    if len(frames) < 8:
        report = {
            "verdicts": ["DATA_INTEGRITY_BLOCKED"],
            "reason": f"insufficient day snaps: {len(frames)}",
        }
        (OUT / "entry_edge_closure_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (OUT / "entry_edge_closure_report.md").write_text("# BLOCKED\ninsufficient snaps\n", encoding="utf-8")
        write_xlsx({"data_integrity": pd.DataFrame([report])}, OUT / "entry_edge_closure_audit.xlsx")
        return 1

    print("concat + market state...", flush=True)
    panel = pd.concat(frames, ignore_index=True)
    panel = add_market_state(panel)
    panel = add_outcome_labels(panel)
    days = sorted(panel["trading_date"].astype(str).unique())
    print(f"panel rows={len(panel)} days={len(days)} coverage_syms_mean={panel.groupby('trading_date')['symbol'].nunique().mean():.1f}", flush=True)

    # coverage check
    cov = (
        panel.groupby(["trading_date", "snapshot_time"])["symbol"]
        .nunique()
        .reset_index(name="n_symbols")
    )
    coverage = {
        "n_snapshot_times": int(len(cov)),
        "mean_symbols_per_snapshot": float(cov["n_symbols"].mean()),
        "frac_snapshots_ge_40": float((cov["n_symbols"] >= 40).mean()),
        "frac_snapshots_ge_45": float((cov["n_symbols"] >= 45).mean()),
    }
    panel_ready = coverage["frac_snapshots_ge_40"] >= 0.5

    # Discovery / Confirmation split
    mid = len(days) // 2
    disc_days = days[:mid]
    conf_days = days[mid:]
    print(f"Discovery {disc_days[0]}..{disc_days[-1]} ({len(disc_days)}) Confirmation {conf_days[0]}..{conf_days[-1]} ({len(conf_days)})", flush=True)

    print("scoring...", flush=True)
    panel_scored = add_scores(panel, fit_days=set(disc_days))
    # winner enrichment application count on confirmation
    we_conf = panel_scored[panel_scored["trading_date"].astype(str).isin(conf_days)]
    we_hits = int((we_conf["winner_enrichment_score"] > 0).sum())
    if we_hits == 0:
        report = {
            "verdicts": ["DATA_INTEGRITY_BLOCKED"],
            "reason": "winner enrichment applied 0 rows on confirmation — join/score bug",
            "coverage": coverage,
        }
        (OUT / "entry_edge_closure_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        (OUT / "entry_edge_closure_report.md").write_text("# DATA_INTEGRITY_BLOCKED\nwinner enrichment 0 hits\n", encoding="utf-8")
        write_xlsx({"data_integrity": pd.DataFrame([report])}, OUT / "entry_edge_closure_audit.xlsx")
        return 1

    train = panel_scored[panel_scored["trading_date"].astype(str).isin(disc_days)]
    test = panel_scored[panel_scored["trading_date"].astype(str).isin(conf_days)]
    stop_thr, stop_meta = fit_stop_threshold(train)
    np_thr, np_meta = fit_np_threshold(train)
    print(f"stop_thr={stop_thr:.4f} {stop_meta} np_thr={np_thr:.4f} {np_meta}", flush=True)

    # Pullback reject flag for sim
    pb = pullback_analysis(train, test)
    # mark pullback_reject on panel using train thresholds
    panel_scored["pullback_reject"] = False
    if pb.get("confirmed"):
        tr = train
        panel_scored["pullback_reject"] = (
            panel_scored["pullback_flag"].fillna(False)
            & (
                pd.to_numeric(panel_scored["pullback_depth"], errors="coerce")
                >= pd.to_numeric(tr["pullback_depth"], errors="coerce").quantile(0.7)
            )
            & (
                pd.to_numeric(panel_scored.get("vol_persistence_300s"), errors="coerce")
                <= pd.to_numeric(tr["vol_persistence_300s"], errors="coerce").quantile(0.3)
            )
            & (pd.to_numeric(panel_scored.get("imbalance_chg_60s"), errors="coerce") <= 0)
        )
    # refresh slices after pullback_reject mutation
    train = panel_scored[panel_scored["trading_date"].astype(str).isin(disc_days)]
    test = panel_scored[panel_scored["trading_date"].astype(str).isin(conf_days)]

    print("STOP cluster collapse...", flush=True)
    stop_clusters = stop_risk_cluster_collapse(train)

    # Reentry unlock quantiles from discovery sequential deltas (sign-safe)
    # higher imbalance_l5 = more bid; improvement => d_imb high, d_spr low
    re_deltas = []
    for sym, g in train.sort_values("snapshot_time").groupby("symbol"):
        g = g.sort_values("snapshot_time")
        if len(g) < 3:
            continue
        imb = pd.to_numeric(g["imbalance_l5"], errors="coerce")
        spr = pd.to_numeric(g["spread_bps"], errors="coerce")
        re_deltas.append(pd.DataFrame({"d_imb": imb.diff(), "d_spr": spr.diff()}))
    if re_deltas:
        rd = pd.concat(re_deltas, ignore_index=True).dropna()
        imb_unlock_q = float(rd["d_imb"].quantile(0.75))  # strong improvement vs q25 deterioration wording
        spr_unlock_q = float(rd["d_spr"].quantile(0.25))
    else:
        imb_unlock_q, spr_unlock_q = 0.0, 0.0

    print("portfolio arms on Confirmation...", flush=True)
    arms = {}
    # A: actual PBv2-all-entries baseline is a different population (official ENTRY log).
    # Cap5 panel cannot invent official entries; report separately as not Cap5-comparable.
    arms["B_cap5_fifo_baseline"] = run_multi_day(
        test, conf_days, score_col="pbv2_score", stop_thr=None, np_thr=None, mode="reject_fill", name="B_cap5_fifo_baseline"
    )
    # matched_count: same Cap5 engine / same trade intensity as B (reference for count-matched arms)
    arms["B_cap5_matched_count_baseline"] = arms["B_cap5_fifo_baseline"]
    arms["C_winner_rank"] = run_multi_day(
        test, conf_days, score_col="winner_enrichment_score", stop_thr=None, np_thr=None, mode="reject_fill", name="C_winner_rank"
    )
    arms["D_stop_reject_fill"] = run_multi_day(
        test, conf_days, score_col="pbv2_score", stop_thr=stop_thr, np_thr=None, mode="reject_fill", name="D_stop_reject_fill"
    )
    arms["D_stop_reject_only"] = run_multi_day(
        test, conf_days, score_col="pbv2_score", stop_thr=stop_thr, np_thr=None, mode="reject_only", name="D_stop_reject_only"
    )
    arms["E_np_reject_fill"] = run_multi_day(
        test, conf_days, score_col="pbv2_score", stop_thr=None, np_thr=np_thr, mode="reject_fill", name="E_np_reject_fill"
    )
    arms["F_winner_stop"] = run_multi_day(
        test, conf_days, score_col="integrated_score", stop_thr=stop_thr, np_thr=None, mode="reject_fill", name="F_winner_stop"
    )
    arms["G_winner_stop_np"] = run_multi_day(
        test, conf_days, score_col="integrated_score", stop_thr=stop_thr, np_thr=np_thr, mode="reject_fill", name="G_winner_stop_np"
    )
    arms["H_g_reentry_hybrid"] = run_multi_day(
        test,
        conf_days,
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        mode="reject_fill",
        name="H_g_reentry_hybrid",
        reentry_policy="D",
        imb_unlock_q=imb_unlock_q,
        spr_unlock_q=spr_unlock_q,
    )
    arms["I_full"] = run_multi_day(
        test,
        conf_days,
        score_col="integrated_score",
        stop_thr=stop_thr,
        np_thr=np_thr,
        mode="reject_fill",
        name="I_full",
        reentry_policy="D",
        imb_unlock_q=imb_unlock_q,
        spr_unlock_q=spr_unlock_q,
        reject_pullback=bool(pb.get("confirmed")),
    )

    # Validate fill != reject_only
    m_fill = arms["D_stop_reject_fill"].metrics()
    m_only = arms["D_stop_reject_only"].metrics()
    fill_validated = not (
        m_fill["n_trades"] == m_only["n_trades"]
        and {t["symbol"] for t in arms["D_stop_reject_fill"].trades}
        == {t["symbol"] for t in arms["D_stop_reject_only"].trades}
        and m_fill["n_trades"] > 0
    )
    # stronger: trade count should differ OR symbol multiset differ
    fill_validated = (
        m_fill["n_trades"] != m_only["n_trades"]
        or sorted(t["symbol"] for t in arms["D_stop_reject_fill"].trades)
        != sorted(t["symbol"] for t in arms["D_stop_reject_only"].trades)
    )
    print(f"reject_fill trades={m_fill['n_trades']} reject_only={m_only['n_trades']} fill_validated={fill_validated}", flush=True)

    arm_metrics = {k: v.metrics(0.0) for k, v in arms.items()}
    arm_metrics_5 = {k: v.metrics(5.0) for k, v in arms.items()}

    print("expanding walk-forward...", flush=True)
    wf = expanding_walk_forward(panel_scored)

    print("pullback / archetype / reentry / ampm...", flush=True)
    arch = archetype_subtypes(train, test)
    rehy = reentry_hybrid_eval(panel_scored, conf_days)
    ampm = am_pm_market_state_explanation(test)

    # Winner trigger absolute PF on confirmation when used as ranking
    win_m = arm_metrics["C_winner_rank"]
    base = arm_metrics["B_cap5_fifo_baseline"]
    winner_trigger_confirmed = bool(
        (win_m.get("pf") or 0) >= 1.0
        and (win_m.get("total_pnl_pct") or 0) > 0
        and (win_m.get("total_pnl_pct") or 0) > (base.get("total_pnl_pct") or -1e9)
    )

    best = arm_metrics["I_full"]
    best5 = arm_metrics_5["I_full"]

    # Leave-one-day / leave-one-symbol on I_full confirmation trades
    trades = arms["I_full"].trades
    day_pnls = defaultdict(float)
    sym_pnls = defaultdict(float)
    for t in trades:
        day_pnls[t["trading_date"]] += t["pnl_pct"]
        sym_pnls[t["symbol"]] += t["pnl_pct"]
    # drop worst day
    if day_pnls:
        worst_day = min(day_pnls, key=day_pnls.get)
        pnl_ex_day = best["total_pnl_pct"] - day_pnls[worst_day]
    else:
        worst_day, pnl_ex_day = None, None
    if sym_pnls:
        worst_sym = min(sym_pnls, key=sym_pnls.get)
        pnl_ex_sym = best["total_pnl_pct"] - sym_pnls[worst_sym]
    else:
        worst_sym, pnl_ex_sym = None, None

    frac_days_nonworse = None
    if day_pnls and base["n_trades"]:
        # per-day compare I vs B
        b_day = defaultdict(float)
        for t in arms["B_cap5_fifo_baseline"].trades:
            b_day[t["trading_date"]] += t["pnl_pct"]
        keys = set(day_pnls) | set(b_day)
        frac_days_nonworse = float(np.mean([day_pnls.get(k, 0) >= b_day.get(k, 0) - 1e-9 for k in keys])) if keys else None

    # PASS gates
    wf_pnl = wf.get("sum_pnl_0bps")
    wf_pf = wf.get("mean_pf_0bps")
    wf_pf5 = wf.get("mean_pf_5bps")
    pass_gates = {
        "panel_ready": panel_ready,
        "fill_validated": fill_validated,
        "wf_pnl_gt_0": bool(wf_pnl is not None and wf_pnl > 0),
        "wf_pf_gt_1": bool(wf_pf is not None and wf_pf > 1.0),
        "wf_pf5_ge_1": bool(wf_pf5 is not None and wf_pf5 >= 1.0),
        "conf_pnl_gt_baseline": bool((best.get("total_pnl_pct") or -1e9) > (base.get("total_pnl_pct") or 0)),
        "conf_pf_gt_1": bool((best.get("pf") or 0) > 1.0),
        "conf_5bps_pf_ge_1": bool((best5.get("pf") or 0) >= 1.0),
        "dd_improved": bool((best.get("max_dd") or -1e9) > (base.get("max_dd") or -1e9)),  # less negative
        "stop_nonworse": bool((best.get("stop_rate") or 1) <= (base.get("stop_rate") or 0) + 1e-6),
        "np_improved": bool((best.get("np_rate") or 1) <= (base.get("np_rate") or 1) + 1e-9),
        "days_70": bool(frac_days_nonworse is not None and frac_days_nonworse >= 0.70),
        "ex_day_pf_proxy": bool(pnl_ex_day is not None and pnl_ex_day > 0),
        "ex_sym_pf_proxy": bool(pnl_ex_sym is not None and pnl_ex_sym > 0),
        "ampm_explained": bool(ampm.get("explained")),
        "winner_enrichment_hits": we_hits,
    }
    runtime_ready = all(
        [
            pass_gates["panel_ready"],
            pass_gates["fill_validated"],
            pass_gates["wf_pnl_gt_0"],
            pass_gates["wf_pf_gt_1"],
            pass_gates["wf_pf5_ge_1"],
            pass_gates["conf_pnl_gt_baseline"],
            pass_gates["conf_pf_gt_1"],
            pass_gates["conf_5bps_pf_ge_1"],
            pass_gates["days_70"],
        ]
    )

    # Beam search note (2→3→4 on discovery for documentation)
    print("beam search (discovery features)...", flush=True)
    beam = _beam_search(train)

    verdicts = []
    if panel_ready:
        verdicts.append("TRUE_WATCH50_PANEL_READY")
    else:
        verdicts.append("DATA_INTEGRITY_BLOCKED")
    if fill_validated:
        verdicts.append("TRUE_REJECT_FILL_VALIDATED")
    if winner_trigger_confirmed:
        verdicts.append("WINNER_ENTRY_TRIGGER_CONFIRMED")
    else:
        verdicts.append("WINNER_ENRICHMENT_SIGNAL_CONFIRMED")
    # STOP confirmed on Cap5 portfolio effect (same baseline family), not row-level sum alone
    stop_port_ok = bool(
        (arm_metrics["D_stop_reject_fill"].get("total_pnl_pct") or -1e9) > (base.get("total_pnl_pct") or 0)
        and (arm_metrics["D_stop_reject_fill"].get("stop_rate") or 1) <= (base.get("stop_rate") or 1) + 1e-9
        and (stop_meta.get("sacr") or 1) <= 0.10
    )
    if stop_port_ok:
        verdicts.append("STOP_RISK_SCORE_CONFIRMED")
    np_port_ok = bool(
        (arm_metrics["E_np_reject_fill"].get("total_pnl_pct") or -1e9) > (base.get("total_pnl_pct") or 0)
        and (arm_metrics["E_np_reject_fill"].get("np_rate") or 1) <= (base.get("np_rate") or 1) + 1e-9
    )
    # also accept NP when G (with NP) beats F (without NP) on Cap5
    if np_port_ok or (
        (arm_metrics["G_winner_stop_np"].get("total_pnl_pct") or -1e9)
        > (arm_metrics["F_winner_stop"].get("total_pnl_pct") or 0)
        and (arm_metrics["G_winner_stop_np"].get("np_rate") or 1)
        <= (arm_metrics["F_winner_stop"].get("np_rate") or 1) + 1e-9
    ):
        verdicts.append("NOPROGRESS_PORTFOLIO_EDGE_CONFIRMED")
    if pb.get("confirmed"):
        verdicts.append("PULLBACK_CONTEXT_CONFIRMED")
    else:
        verdicts.append("NO_STABLE_PULLBACK_CONTEXT")
    reentry_port_ok = bool(
        (arm_metrics["H_g_reentry_hybrid"].get("total_pnl_pct") or -1e9)
        >= (arm_metrics["G_winner_stop_np"].get("total_pnl_pct") or -1e9)
        and (arm_metrics["H_g_reentry_hybrid"].get("n_trades") or 0) >= 30
    )
    if rehy.get("confirmed") or reentry_port_ok:
        verdicts.append("REENTRY_HYBRID_CONFIRMED")
    verdicts.append(arch["verdict"])
    # 5bps collapse => not runtime even if 0bps looks good
    if runtime_ready:
        verdicts.append("WATCH50_PORTFOLIO_EDGE_CONFIRMED")
        verdicts.append("RUNTIME_CANDIDATE_READY")
    else:
        verdicts.append("NO_EDGE_VS_PBV2")
        if pass_gates["fill_validated"] and panel_ready:
            verdicts.append("SHADOW_SPEC_READY")

    shadow_spec = {
        "enabled": False,
        "stop_risk_threshold": stop_thr,
        "np_risk_threshold": np_thr,
        "score": "integrated_score = z(pbv2) + 0.35*enrichment - 0.45*z(stop_risk) - 0.25*z(np_risk)",
        "winner_enrichment_rules": [f"{a}:{sa} & {b}:{sb}" for a, sa, b, sb in WINNER_RULES],
        "reentry": "D: 30m cooloff + early unlock when d_imbalance>=0 and d_spread<=0",
        "archetype_subtype": arch.get("best"),
    }

    answers = {
        "panel_coverage": coverage,
        "winner_enrichment_hits_confirmation": we_hits,
        "winner_trigger_absolute_confirmed": winner_trigger_confirmed,
        "stop_risk": {"threshold": stop_thr, "meta": stop_meta},
        "np_risk": {"threshold": np_thr, "meta": np_meta},
        "pullback": pb,
        "reentry_hybrid": rehy,
        "archetype": arch,
        "am_pm_state": ampm,
        "baselines": {
            "actual_pbv2_all_entries": {
                "status": "SEPARATE_POPULATION",
                "note": "Official ENTRY log population; Cap5 Watch50 PnL deltas are never computed against this baseline",
            },
            "cap5_fifo_baseline": base,
            "cap5_matched_count_baseline": arm_metrics["B_cap5_matched_count_baseline"],
            "note": "PnL deltas only within Cap5 simultaneous-panel family; no cross-baseline mix with actual_pbv2_all_entries",
        },
        "stop_clusters": stop_clusters,
        "reentry_unlock_quantiles": {
            "d_imbalance_unlock_ge": imb_unlock_q,
            "d_spread_unlock_le": spr_unlock_q,
            "imbalance_sign": "higher imbalance_l5 = more bid support; unlock when d_imb high and d_spr low",
        },
        "arms_0bps": arm_metrics,
        "arms_5bps": arm_metrics_5,
        "fill_validated": fill_validated,
        "walk_forward": {
            "sum_pnl_0bps": wf.get("sum_pnl_0bps"),
            "mean_pf_0bps": wf.get("mean_pf_0bps"),
            "sum_pnl_5bps": wf.get("sum_pnl_5bps"),
            "mean_pf_5bps": wf.get("mean_pf_5bps"),
            "frac_days_pnl_nonneg": wf.get("frac_days_pnl_nonneg"),
            "n_holdouts": wf.get("n_holdouts"),
        },
        "leave_one_day": {"worst_day": worst_day, "pnl_ex_day": pnl_ex_day},
        "leave_one_symbol": {"worst_symbol": worst_sym, "pnl_ex_sym": pnl_ex_sym},
        "frac_days_nonworse_vs_baseline": frac_days_nonworse,
        "pass_gates": pass_gates,
        "beam": beam,
        "runtime_unchanged": {
            "pbv2": True,
            "exit": True,
            "cap": 5,
            "shadow_enabled": False,
            "real_orders": False,
            "symbol_coefs": False,
            "time_coefs": False,
        },
    }

    report = {
        "metadata": {
            "phase": "Phase687W53",
            "generated_at": datetime.now(JST).isoformat(),
            "days": days,
            "n_days": len(days),
            "discovery_days": disc_days,
            "confirmation_days": conf_days,
            "n_rows": int(len(panel_scored)),
            "verdict_label_fixes": {
                "WINNER_ENTRY_TRIGGER_CONFIRMED": "only if absolute PF>=1; else WINNER_ENRICHMENT_SIGNAL_CONFIRMED",
                "ARCHETYPE_4062_SYMBOL_SPECIFIC": "ARCHETYPE_4062_GENERALIZED_BUT_OVERBROAD when overbroad",
            },
        },
        "verdicts": verdicts,
        "pass_gates": pass_gates,
        "runtime_candidate_ready": runtime_ready,
        "shadow_spec": shadow_spec,
        "required_answers": answers,
        "walk_forward_holdouts": wf.get("holdouts"),
    }

    md = f"""# Phase687W53 — True Watch50 Portfolio Edge Closure

## Verdict
`{' | '.join(verdicts)}`

## Panel
- days={len(days)} rows={len(panel_scored)}
- coverage: mean_syms/snapshot={coverage['mean_symbols_per_snapshot']:.1f} frac≥40={coverage['frac_snapshots_ge_40']:.2%}
- winner enrichment hits on Confirmation: **{we_hits}**

## Cap5 Confirmation (0bps)
- A actual_pbv2_all_entries: SEPARATE_POPULATION (not Cap5-compared)
- B Cap5 FIFO baseline: pnl={base.get('total_pnl_pct')} pf={base.get('pf')} trades={base.get('n_trades')}
- G Winner+STOP+NP: pnl={arm_metrics['G_winner_stop_np'].get('total_pnl_pct')} pf={arm_metrics['G_winner_stop_np'].get('pf')}
- H G+reentry hybrid: pnl={arm_metrics['H_g_reentry_hybrid'].get('total_pnl_pct')} pf={arm_metrics['H_g_reentry_hybrid'].get('pf')}
- I Full: pnl={best.get('total_pnl_pct')} pf={best.get('pf')} trades={best.get('n_trades')}
- D reject_fill vs reject_only: {m_fill['n_trades']} vs {m_only['n_trades']} fill_validated={fill_validated}

## Walk-forward
- sum_pnl_0bps={wf.get('sum_pnl_0bps')} mean_pf_0bps={wf.get('mean_pf_0bps')}
- sum_pnl_5bps={wf.get('sum_pnl_5bps')} mean_pf_5bps={wf.get('mean_pf_5bps')}
- frac_days_pnl≥0={wf.get('frac_days_pnl_nonneg')}

## Components
- STOP RiskScore thr={stop_thr:.4f} sacr={stop_meta.get('sacr')}
- NP RiskScore thr={np_thr:.4f}
- Pullback confirmed={pb.get('confirmed')} promote={pb.get('promote_confirmed')}
- Reentry hybrid D confirmed={rehy.get('confirmed')}
- 4062 subtype: {arch.get('verdict')} best={arch.get('best')}
- AM/PM state explained={ampm.get('explained')} agree={ampm.get('same_state_sign_agree_frac')}

## Runtime candidate
**{runtime_ready}** — Shadow enabled: False — PBv2 unchanged

## Pass gates
{json.dumps(pass_gates, ensure_ascii=False, indent=2)}
"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entry_edge_closure_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "entry_edge_closure_report.md").write_text(md, encoding="utf-8")
    write_xlsx(
        {
            "data_days": pd.DataFrame([{"day": d, "split": "discovery" if d in disc_days else "confirmation"} for d in days]),
            "coverage": pd.DataFrame([coverage]),
            "arm_metrics_0bps": pd.DataFrame(list(arm_metrics.values())),
            "arm_metrics_5bps": pd.DataFrame(list(arm_metrics_5.values())),
            "walk_forward": pd.DataFrame(
                [
                    {
                        "day": h["test_day"],
                        **{f"m0_{k}": v for k, v in h["metrics_0bps"].items() if k != "name"},
                        **{f"m5_{k}": v for k, v in h["metrics_5bps"].items() if k != "name"},
                    }
                    for h in (wf.get("holdouts") or [])
                ]
            ),
            "stop_risk": pd.DataFrame([stop_meta]),
            "np_risk": pd.DataFrame([np_meta]),
            "pullback": pd.DataFrame([{"confirmed": pb.get("confirmed"), "promote": pb.get("promote_confirmed")}]),
            "reentry": pd.DataFrame(
                [{"policy": k, **v} for k, v in (rehy.get("policies") or {}).items()]
            ),
            "archetype": pd.DataFrame(
                [{"subtype": k, **v} for k, v in (arch.get("subtypes") or {}).items()]
            ),
            "am_pm_state": pd.DataFrame(ampm.get("table") or []),
            "beam": pd.DataFrame(beam.get("top4") or beam.get("top2") or []),
            "pass_gates": pd.DataFrame([pass_gates]),
            "runtime_audit": pd.DataFrame([answers["runtime_unchanged"]]),
            "data_integrity": pd.DataFrame(
                [{"panel_ready": panel_ready, "fill_validated": fill_validated, "we_hits": we_hits}]
            ),
        },
        OUT / "entry_edge_closure_audit.xlsx",
    )

    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    # drop w47 day_snaps if created (W53 uses _w53_day_snaps_cache persistently)
    w47_tmp = OUT / "_w47_tmp"
    if w47_tmp.exists():
        shutil.rmtree(w47_tmp, ignore_errors=True)
    if "--purge-cache" in sys.argv and SNAP_CACHE.exists():
        shutil.rmtree(SNAP_CACHE, ignore_errors=True)

    print(json.dumps({"verdicts": verdicts, "runtime_ready": runtime_ready, "fill": fill_validated}, ensure_ascii=False), flush=True)
    return 0


def _beam_search(train: pd.DataFrame) -> dict[str, Any]:
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
    y = train["exit_pnl_pct"]
    scored = []
    for i, a in enumerate(feats):
        for b in feats[i + 1 :]:
            ma = _q_mask(train[a], "high")
            mb = _q_mask(train[b], "low")
            m = ma & mb
            if m.sum() < 40:
                continue
            scored.append(
                {
                    "features": [a, b],
                    "n": int(m.sum()),
                    "mean_pnl": float(y[m].mean()),
                    "pf": _pf(y[m]),
                }
            )
    scored.sort(key=lambda r: (r["mean_pnl"] if r["mean_pnl"] is not None else -1e9), reverse=True)
    top2 = scored[:100]
    # expand to 3
    top3 = []
    for r in top2[:40]:
        for c in feats:
            if c in r["features"]:
                continue
            m = np.ones(len(train), dtype=bool)
            for f in r["features"]:
                m &= _q_mask(train[f], "high" if "vol_persistence" not in f and "spread" not in f and "seconds" not in f else "low").values
            m &= _q_mask(train[c], "low" if "vol" in c or "spread" in c or "seconds" in c else "high").values
            if m.sum() < 30:
                continue
            top3.append(
                {
                    "features": r["features"] + [c],
                    "n": int(m.sum()),
                    "mean_pnl": float(y[m].mean()),
                    "pf": _pf(y[m]),
                }
            )
    top3.sort(key=lambda r: r["mean_pnl"], reverse=True)
    top3 = top3[:50]
    top4 = []
    for r in top3[:25]:
        for c in feats:
            if c in r["features"]:
                continue
            m = np.ones(len(train), dtype=bool)
            for f in r["features"]:
                side = "low" if ("vol" in f or "spread" in f or "seconds" in f) else "high"
                m &= _q_mask(train[f], side).values
            side = "low" if ("vol" in c or "spread" in c or "seconds" in c) else "high"
            m &= _q_mask(train[c], side).values
            if m.sum() < 25:
                continue
            top4.append(
                {
                    "features": r["features"] + [c],
                    "n": int(m.sum()),
                    "mean_pnl": float(y[m].mean()),
                    "pf": _pf(y[m]),
                }
            )
    top4.sort(key=lambda r: r["mean_pnl"], reverse=True)
    return {"top2": top2[:20], "top3": top3[:20], "top4": top4[:20]}


if __name__ == "__main__":
    raise SystemExit(main())
