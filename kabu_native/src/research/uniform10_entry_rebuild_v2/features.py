"""C V2 causal feature catalog. executable_flag excluded. No future events."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.uniform10_entry_rebuild.features import extra_from_board, source_series

CATALOG: tuple[tuple[str, str, str, str, str], ...] = (
    ("spread_bps", "SPREAD", "(ask-bid)/mid*1e4", "last board t<=t0", "t0"),
    ("tick_spread", "SPREAD", "(ask-bid)/JPX_tick(bid)", "last board t<=t0", "t0"),
    ("imbalance", "IMBALANCE", "(bid_qty-ask_qty)/(bid_qty+ask_qty)", "last board t<=t0", "t0"),
    ("log_bid_qty", "BOARD", "log1p(bid_qty)", "last board t<=t0", "t0"),
    ("log_ask_qty", "BOARD", "log1p(ask_qty)", "last board t<=t0", "t0"),
    ("fresh_sec", "BOARD", "payload quote-clock vs recv of last PUSH", "last board t<=t0", "t0"),
    ("mark_age_t0", "EVENT ACTIVITY", "t0 - last valid M4 quote event_t", "M4 mark t<=t0", "t0"),
    ("mid_ret_60s", "PRICE / PATH", "(mid_t0/mid_t0-60)-1 in 1e4 bps units via preentry", "60s", "t0"),
    ("mid_ret_180s", "PRICE / PATH", "same 180s", "180s", "t0"),
    ("mid_ret_300s", "PRICE / PATH", "same 300s", "300s", "t0"),
    ("mid_abs_ret_60s", "PRICE / PATH", "abs(mid_ret_60s)", "60s", "t0"),
    ("mid_range_180s_bps", "PRICE / PATH", "(max-min)mid_180s/mid*1e4", "180s", "t0"),
    ("event_rate_60s", "EVENT ACTIVITY", "board events in 60s / 60", "60s", "t0"),
    ("event_rate_180s", "EVENT ACTIVITY", "board events in 180s / 180", "180s", "t0"),
    ("rebound_180s", "REBOUND / PULLBACK", "(mid - min_mid_180)/mid", "180s", "t0"),
    ("drawdown_180s", "RECENT HIGH/LOW", "(mid - max_mid_180)/mid", "180s", "t0"),
    ("volume_rate_60s", "VOLUME", "max(0, vol_t0-vol_t0-60)/60", "60s", "t0"),
    ("log_trading_volume", "VOLUME", "log1p(TradingVolume) t<=t0", "session-to-t0", "t0"),
    ("trading_value_rate_60s", "TRADING VALUE", "mid * volume_rate_60s", "60s", "t0"),
    ("vwap_dist_bps", "VWAP DISTANCE", "(mid-session_vwap)/mid*1e4", "session-to-t0", "t0"),
    ("flow_imbalance_60s", "FLOW", "mean imbalance over boards in 60s", "60s", "t0"),
    ("xs_mid_ret_60s_z", "CROSS-SECTIONAL RELATIVE FEATURES", "z of mid_ret_60s within date×anchor", "t0 CS", "t0"),
    ("xs_imbalance_z", "CROSS-SECTIONAL RELATIVE FEATURES", "z of imbalance within date×anchor", "t0 CS", "t0"),
    ("xs_spread_z", "CROSS-SECTIONAL RELATIVE FEATURES", "z of spread_bps within date×anchor", "t0 CS", "t0"),
    ("xs_volume_rate_z", "CROSS-SECTIONAL RELATIVE FEATURES", "z of volume_rate_60s within date×anchor", "t0 CS", "t0"),
    ("xs_event_rate_z", "CROSS-SECTIONAL RELATIVE FEATURES", "z of event_rate_60s within date×anchor", "t0 CS", "t0"),
)

RAW_FEATURES = tuple(n for n, *_r in CATALOG if not n.startswith("xs_"))
XS_FEATURES = tuple(n for n, *_r in CATALOG if n.startswith("xs_"))
PREDICTIVE_FEATURES = RAW_FEATURES + XS_FEATURES
LIVE_BASELINE = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def causal_features(board: dict[str, np.ndarray], t0: float, *, rows: list[dict[str, Any]] | None, series=None) -> dict[str, Any]:
    raw = extra_from_board(board, t0, rows=rows, series=series)
    raw.pop("executable_flag", None)
    return {k: raw.get(k) for k in RAW_FEATURES if k != "mark_age_t0"}


def attach_cross_section(rows: list[dict[str, Any]]) -> None:
    by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault((str(r.get("date")), str(r.get("anchor"))), []).append(r)

    def z(vals: list[Optional[float]]) -> list[Optional[float]]:
        xs = [v for v in vals if v is not None and v == v]
        if len(xs) < 3:
            return [None for _ in vals]
        mu = float(np.mean(xs))
        sd = float(np.std(xs))
        if sd <= 1e-12:
            return [0.0 if v is not None and v == v else None for v in vals]
        return [((v - mu) / sd) if v is not None and v == v else None for v in vals]

    mapping = {
        "xs_mid_ret_60s_z": "mid_ret_60s",
        "xs_imbalance_z": "imbalance",
        "xs_spread_z": "spread_bps",
        "xs_volume_rate_z": "volume_rate_60s",
        "xs_event_rate_z": "event_rate_60s",
    }
    for _k, grp in by.items():
        for xs_name, src in mapping.items():
            zs = z([_f(r.get(src)) for r in grp])
            for r, v in zip(grp, zs):
                r[xs_name] = v


def mfe_mae(board: dict[str, np.ndarray], t0: float, t1: float, mid0: float, t_lo: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or t.size == 0 or not (mid0 and mid0 > 0):
        return None, None
    ask = board["ask"].astype(float)
    bid = board["bid"].astype(float)
    spec = board["special"].astype(bool) if board.get("special") is not None else np.zeros(t.shape, dtype=bool)
    rels = []
    for j in range(int(t.size)):
        tj = float(t[j])
        if t_lo is not None and tj + 1e-12 < float(t_lo):
            continue
        if tj < float(t0) - 1e-12:
            continue
        if tj > float(t1) + 1e-12:
            break
        if spec[j]:
            continue
        a, b = float(ask[j]), float(bid[j])
        if not (np.isfinite(a) and np.isfinite(b) and b > 0 and a > b + 1e-12):
            continue
        rels.append(((a + b) / 2.0) / float(mid0) - 1.0)
    if not rels:
        return None, None
    raw_mfe = float(np.max(rels))
    mae = float(np.min(rels))
    return float(max(0.0, raw_mfe)), mae
