"""Causal feature catalog from Capture board state at t<=t0."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34b_entry_execution.features import FEATURE_SPECS, preentry_from_board
from research.e1_x10_risk_universe.tick import jpx_tick_size_yen

# formula / family for inventory. Live FEATURE_ORDER is a subset; this catalog is research-only.
CATALOG: tuple[tuple[str, str, str], ...] = (
    ("spread_bps", "board", "(ask-bid)/mid*1e4 at last continuous board t<=t0"),
    ("tick_spread", "board", "(ask-bid)/JPX_tick(bid)"),
    ("imbalance", "board", "(bid_qty-ask_qty)/(bid_qty+ask_qty)"),
    ("log_bid_qty", "board", "log1p(bid_qty)"),
    ("log_ask_qty", "board", "log1p(ask_qty)"),
    ("fresh_sec", "board", "event_t - quote_time"),
    ("mid_ret_60s", "price/path", "(mid_t0/mid_t0-60)-1 in bps*1e4 via preentry"),
    ("mid_ret_180s", "price/path", "same 180s"),
    ("mid_ret_300s", "price/path", "same 300s"),
    ("mid_abs_ret_60s", "price/path", "abs(mid_ret_60s)"),
    ("mid_range_180s_bps", "price/path", "(max-min)mid_180s/mid*1e4"),
    ("event_rate_60s", "event rate", "board events in 60s / 60"),
    ("event_rate_180s", "event rate", "board events in 180s / 180"),
    ("rebound_180s", "rebound", "(mid - min_mid_180)/mid"),
    ("drawdown_180s", "rebound", "(mid - max_mid_180)/mid"),
    ("volume_rate_60s", "volume rate", "max(0, vol_t0-vol_t0-60)/60"),
    ("log_trading_volume", "volume", "log1p(TradingVolume) at t<=t0"),
    ("trading_value_rate_60s", "trading value", "mid * volume_rate_60s"),
    ("vwap_dist_bps", "VWAP", "(mid-session_vwap)/mid*1e4 using volume increments t<=t0"),
    ("flow_imbalance_60s", "flow", "mean imbalance over boards in 60s"),
    ("executable_flag", "board", "1 if last board is_executable_continuous else 0"),
    ("xs_mid_ret_60s_z", "cross-sectional", "z-score of mid_ret_60s within anchor"),
    ("xs_imbalance_z", "cross-sectional", "z-score of imbalance within anchor"),
    ("xs_spread_z", "cross-sectional", "z-score of spread_bps within anchor"),
    ("xs_volume_rate_z", "cross-sectional", "z-score of volume_rate_60s within anchor"),
    ("xs_event_rate_z", "cross-sectional", "z-score of event_rate_60s within anchor"),
)

RAW_FEATURES = tuple(
    n for n, _fam, _f in CATALOG if not n.startswith("xs_")
)
XS_FEATURES = tuple(n for n, _fam, _f in CATALOG if n.startswith("xs_"))


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _mid_at(board: dict[str, np.ndarray], t0: float) -> Optional[float]:
    t = board["t"]
    if t.size == 0:
        return None
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    for j in range(i, -1, -1):
        if board["special"][j]:
            continue
        a = float(board["ask"][j])
        b = float(board["bid"][j])
        if np.isfinite(a) and np.isfinite(b) and a > 0 and b > 0:
            return (a + b) / 2.0
    return None


def extra_from_board(
    board: dict[str, np.ndarray],
    t0: float,
    *,
    rows: list[dict[str, Any]] | None = None,
    series: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {n: None for n in RAW_FEATURES}
    base = preentry_from_board(board, t0)
    for n, _fam in FEATURE_SPECS:
        if n in out:
            out[n] = base.get(n)
    t = board["t"]
    if t.size == 0:
        return out
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    if i < 0:
        return out
    exe = board.get("executable")
    if exe is not None and i < int(getattr(exe, "size", 0) or 0):
        out["executable_flag"] = 1.0 if bool(exe[i]) else 0.0
    mid0 = _mid_at(board, t0)
    if mid0 and mid0 > 0:
        t_lo = float(t0) - 180.0
        mask = (t <= float(t0) + 1e-12) & (t >= t_lo - 1e-12)
        if "special" in board:
            mask = mask & (~board["special"].astype(bool))
        ask = board["ask"].astype(float)
        bid = board["bid"].astype(float)
        ok = mask & np.isfinite(ask) & np.isfinite(bid) & (ask > 0) & (bid > 0)
        if ok.any():
            mids = (ask[ok] + bid[ok]) / 2.0
            out["rebound_180s"] = float((mid0 - float(np.min(mids))) / mid0)
            out["drawdown_180s"] = float((mid0 - float(np.max(mids))) / mid0)
        t_lo60 = float(t0) - 60.0
        mask60 = (t <= float(t0) + 1e-12) & (t >= t_lo60 - 1e-12)
        imb = []
        for j in np.where(mask60)[0]:
            if board["special"][j]:
                continue
            aq = float(board["ask_qty"][j]) if np.isfinite(board["ask_qty"][j]) else 0.0
            bq = float(board["bid_qty"][j]) if np.isfinite(board["bid_qty"][j]) else 0.0
            den = aq + bq
            if den > 0:
                imb.append((bq - aq) / den)
        if imb:
            out["flow_imbalance_60s"] = float(np.mean(imb))
    if series is not None:
        out.update(volume_features_at(series, t0, mid0))
    elif rows:
        out.update(volume_features_at(source_series(rows), t0, mid0))
    return out


def source_series(rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    ts: list[float] = []
    vol: list[float] = []
    mid: list[float] = []
    for rec in rows:
        ti = _f(rec.get("t"))
        if ti is None:
            continue
        ts.append(ti)
        vv = _f(rec.get("TradingVolume"))
        vol.append(vv if vv is not None else float("nan"))
        a = _f(rec.get("ask"))
        b = _f(rec.get("bid"))
        if a is None and isinstance(rec.get("Sell1"), dict):
            a = _f(rec.get("Sell1").get("Price"))
        if b is None and isinstance(rec.get("Buy1"), dict):
            b = _f(rec.get("Buy1").get("Price"))
        mid.append((a + b) / 2.0 if a and b and a > 0 and b > 0 else float("nan"))
    t = np.asarray(ts, dtype=float)
    v = np.asarray(vol, dtype=float)
    m = np.asarray(mid, dtype=float)
    dvol = np.zeros_like(v)
    if v.size:
        prev = np.roll(v, 1)
        prev[0] = np.nan
        d = v - prev
        dvol = np.where(np.isfinite(v) & np.isfinite(prev) & (d >= 0), d, 0.0)
        px = np.where(np.isfinite(m), m, np.nan)
        contrib = np.where(np.isfinite(px) & (dvol > 0), px * dvol, 0.0)
        vwap_n = np.cumsum(contrib)
        vwap_d = np.cumsum(np.where(dvol > 0, dvol, 0.0))
    else:
        vwap_n = v
        vwap_d = v
    return {"t": t, "vol": v, "mid": m, "vwap_n": vwap_n, "vwap_d": vwap_d}


def volume_features_at(series: dict[str, np.ndarray], t0: float, mid0: Optional[float]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "volume_rate_60s": None,
        "log_trading_volume": None,
        "trading_value_rate_60s": None,
        "vwap_dist_bps": None,
    }
    t = series.get("t")
    if t is None or t.size == 0:
        return out
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    if i < 0:
        return out
    vol = series["vol"]
    now = float(vol[i]) if np.isfinite(vol[i]) else None
    if now is None:
        for k in range(i, -1, -1):
            if np.isfinite(vol[k]):
                now = float(vol[k])
                break
    if now is not None:
        out["log_trading_volume"] = float(np.log1p(max(now, 0.0)))
    j = int(np.searchsorted(t, float(t0) - 60.0, side="right") - 1)
    past = None
    if j >= 0:
        for k in range(j, -1, -1):
            if np.isfinite(vol[k]):
                past = float(vol[k])
                break
    if now is not None and past is not None:
        out["volume_rate_60s"] = float(max(0.0, now - past) / 60.0)
    if out.get("volume_rate_60s") is not None and mid0:
        out["trading_value_rate_60s"] = float(mid0 * float(out["volume_rate_60s"]))
    vd = float(series["vwap_d"][i]) if np.isfinite(series["vwap_d"][i]) else 0.0
    if vd > 0 and mid0:
        vwap = float(series["vwap_n"][i]) / vd
        out["vwap_dist_bps"] = float((mid0 - vwap) / mid0 * 10000.0)
    return out


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


def labels_from_board(board: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    out = {
        "FORWARD_MID_RETURN_600S": None,
        "fwd_ret_60s": None,
        "fwd_ret_180s": None,
        "fwd_ret_300s": None,
        "fwd_ret_750s": None,
        "MFE600": None,
        "MAE600": None,
        "UP_FIRST": None,
        "DOWN_FIRST": None,
    }
    mid0 = _mid_at(board, t0)
    if not mid0 or mid0 <= 0:
        return out
    t = board["t"]
    ask = board["ask"].astype(float)
    bid = board["bid"].astype(float)
    special = board["special"].astype(bool) if "special" in board else np.zeros(t.shape, dtype=bool)
    ok = (~special) & np.isfinite(ask) & np.isfinite(bid) & (ask > 0) & (bid > 0)
    mid = np.where(ok, (ask + bid) / 2.0, np.nan)

    def ret_at(sec: float) -> Optional[float]:
        tgt = float(t0) + sec
        j = int(np.searchsorted(t, tgt, side="right") - 1)
        for k in range(j, -1, -1):
            if t[k] < float(t0) - 1e-12:
                break
            if np.isfinite(mid[k]):
                return float(mid[k] / mid0 - 1.0)
        return None

    out["fwd_ret_60s"] = ret_at(60.0)
    out["fwd_ret_180s"] = ret_at(180.0)
    out["fwd_ret_300s"] = ret_at(300.0)
    out["FORWARD_MID_RETURN_600S"] = ret_at(600.0)
    out["fwd_ret_750s"] = ret_at(750.0)

    t1 = float(t0) + 600.0
    mask = (t >= float(t0) - 1e-12) & (t <= t1 + 1e-12) & np.isfinite(mid)
    if mask.any():
        rel = mid[mask] / mid0 - 1.0
        out["MFE600"] = float(np.nanmax(rel))
        out["MAE600"] = float(np.nanmin(rel))
        tick = float(jpx_tick_size_yen(mid0)) / mid0 if mid0 else 0.0
        up = down = False
        times = t[mask]
        rels = rel
        for ti, r in zip(times, rels):
            if (not up) and r >= tick:
                up = True
                if out["UP_FIRST"] is None:
                    out["UP_FIRST"] = 1.0 if not down else 0.0
            if (not down) and r <= -tick:
                down = True
                if out["DOWN_FIRST"] is None:
                    out["DOWN_FIRST"] = 1.0 if not up else 0.0
            if up and down:
                break
        if out["UP_FIRST"] is None:
            out["UP_FIRST"] = 0.0
        if out["DOWN_FIRST"] is None:
            out["DOWN_FIRST"] = 0.0
    return out
