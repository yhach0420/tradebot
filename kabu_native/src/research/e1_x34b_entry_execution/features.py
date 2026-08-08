"""Pre-entry board features (future-free) + limited routing catalog."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY

from . import DEC_AGG, DEC_PAS, DEC_SKIP, QUANTILE_NAMES, QUANTILE_RANKS


FEATURE_SPECS = (
    # Family A — Direction
    ("mid_ret_60s", "A_DIRECTION"),
    ("mid_ret_180s", "A_DIRECTION"),
    ("mid_ret_300s", "A_DIRECTION"),
    ("mid_range_180s_bps", "A_DIRECTION"),
    # Family B — Activity
    ("event_rate_60s", "B_ACTIVITY"),
    ("event_rate_180s", "B_ACTIVITY"),
    ("mid_abs_ret_60s", "B_ACTIVITY"),
    # Family C — Execution economics
    ("spread_bps", "C_EXEC"),
    ("tick_spread", "C_EXEC"),
    ("imbalance", "C_EXEC"),
    ("log_bid_qty", "C_EXEC"),
    ("log_ask_qty", "C_EXEC"),
    ("fresh_sec", "C_EXEC"),
    # Family D context (market)
    ("univ_med_mid_ret_60s", "D_MARKET"),
    ("tod_bucket", "D_MARKET"),
)


def _mid_series(board: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    t = board["t"].astype(float)
    ask = board["ask"].astype(float)
    bid = board["bid"].astype(float)
    ok = (
        np.isfinite(ask) & np.isfinite(bid) & (ask > 0) & (bid > 0)
        & (~board["special"].astype(bool))
    )
    mid = np.where(ok, (ask + bid) / 2.0, np.nan)
    return t, mid


def preentry_from_board(
    board: dict[str, np.ndarray],
    signal_t: float,
) -> dict[str, Any]:
    """Causal features using only board rows with t <= signal_t."""
    t, mid = _mid_series(board)
    out: dict[str, Any] = {name: None for name, _ in FEATURE_SPECS}
    if t.size == 0:
        return out
    i = int(np.searchsorted(t, signal_t, side="right") - 1)
    if i < 0:
        return out

    # walk back for valid ask/bid qty at i
    j = i
    while j >= 0:
        if board["special"][j]:
            j -= 1
            continue
        ask = float(board["ask"][j])
        bid = float(board["bid"][j])
        aq = board["ask_qty"][j]
        bq = board["bid_qty"][j]
        if not (np.isfinite(ask) and np.isfinite(bid) and ask > 0 and bid > 0):
            j -= 1
            continue
        fresh = float(board["fresh_sec"][j]) if np.isfinite(board["fresh_sec"][j]) else 0.0
        mid0 = (ask + bid) / 2.0
        out["spread_bps"] = (ask - bid) / mid0 * 10000.0
        tick = float(jpx_tick_size_yen(bid))
        out["tick_spread"] = (ask - bid) / tick if tick > 0 else None
        aq_f = float(aq) if np.isfinite(aq) else 0.0
        bq_f = float(bq) if np.isfinite(bq) else 0.0
        denom = aq_f + bq_f
        out["imbalance"] = (bq_f - aq_f) / denom if denom > 0 else None
        out["log_bid_qty"] = float(np.log1p(bq_f))
        out["log_ask_qty"] = float(np.log1p(aq_f))
        out["fresh_sec"] = fresh
        break
    if out["spread_bps"] is None:
        return out

    def _ret(sec: float) -> Optional[float]:
        t0 = signal_t - sec
        k = int(np.searchsorted(t, t0, side="left"))
        # last valid mid at/before signal and at/before t0
        m_now = mid[j] if j < mid.size and np.isfinite(mid[j]) else np.nan
        m_past = np.nan
        for kk in range(min(k, mid.size - 1), -1, -1):
            if t[kk] > signal_t + 1e-12:
                continue
            if np.isfinite(mid[kk]):
                m_past = mid[kk]
                if t[kk] <= t0 + 1e-9:
                    break
        if not (np.isfinite(m_now) and np.isfinite(m_past) and m_past > 0):
            return None
        return float((m_now / m_past - 1.0) * 10000.0)

    out["mid_ret_60s"] = _ret(60.0)
    out["mid_ret_180s"] = _ret(180.0)
    out["mid_ret_300s"] = _ret(300.0)
    r60 = out["mid_ret_60s"]
    out["mid_abs_ret_60s"] = abs(float(r60)) if r60 is not None else None

    # range 180s
    t0 = signal_t - 180.0
    mask = (t <= signal_t + 1e-12) & (t >= t0 - 1e-12) & np.isfinite(mid)
    if mask.any() and mid0 > 0:
        out["mid_range_180s_bps"] = float((np.nanmax(mid[mask]) - np.nanmin(mid[mask])) / mid0 * 10000.0)

    for sec, key in ((60.0, "event_rate_60s"), (180.0, "event_rate_180s")):
        t0 = signal_t - sec
        n = int(np.sum((t <= signal_t + 1e-12) & (t >= t0 - 1e-12)))
        out[key] = float(n / sec)

    # time-of-day bucket 0..5 (same as X33C TOD buckets roughly by hour)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    dt = datetime.fromtimestamp(float(signal_t), tz=ZoneInfo("Asia/Tokyo"))
    hm = dt.hour * 60 + dt.minute
    # encode as ordinal for quantile use
    if hm < 9 * 60 + 30:
        out["tod_bucket"] = 0.0
    elif hm < 10 * 60 + 30:
        out["tod_bucket"] = 1.0
    elif hm < 12 * 60:
        out["tod_bucket"] = 2.0
    elif hm < 13 * 60 + 30:
        out["tod_bucket"] = 3.0
    elif hm < 14 * 60 + 30:
        out["tod_bucket"] = 4.0
    else:
        out["tod_bucket"] = 5.0

    return out


def attach_universe_median(rows: list[dict[str, Any]]) -> None:
    """In-place: univ_med_mid_ret_60s by (date, signal_t) across symbols."""
    by: dict[tuple, list[float]] = {}
    for r in rows:
        v = r.get("mid_ret_60s")
        if v is None or not np.isfinite(v):
            continue
        key = (r["date"], float(r["signal_t"]))
        by.setdefault(key, []).append(float(v))
    med = {k: float(np.median(v)) for k, v in by.items() if len(v) >= 5}
    for r in rows:
        key = (r["date"], float(r["signal_t"]))
        r["univ_med_mid_ret_60s"] = med.get(key)


def fit_quantiles(rows: list[dict], feature: str) -> dict[float, float]:
    xs = [float(r[feature]) for r in rows if r.get(feature) is not None and np.isfinite(r[feature])]
    if len(xs) < 30:
        return {}
    a = np.asarray(xs, dtype=float)
    return {q: float(np.quantile(a, q)) for q in QUANTILE_RANKS}


def _cmp(v: float, op: str, thr: float) -> bool:
    if op == "GE":
        return v >= thr - 1e-15
    if op == "LE":
        return v <= thr + 1e-15
    raise ValueError(op)


def build_catalog() -> list[dict[str, Any]]:
    """
    Limited semantic families — not a large grid search.
    Each candidate defines routing to SKIP / AGGRESSIVE / PASSIVE.
    """
    cats: list[dict[str, Any]] = []

    # Family A: direction → AGGRESSIVE else SKIP
    for f in ("mid_ret_60s", "mid_ret_180s", "mid_ret_300s"):
        for q in (0.50, 0.70):
            cats.append({
                "id": f"A_AGG_{f}_ge_{QUANTILE_NAMES[q]}",
                "family": "A_DIRECTION",
                "kind": "agg_if_ge",
                "feature": f,
                "op": "GE",
                "quantile": q,
            })

    # Family B: activity → AGGRESSIVE else SKIP
    for f in ("event_rate_60s", "mid_abs_ret_60s"):
        for q in (0.70,):
            cats.append({
                "id": f"B_AGG_{f}_ge_{QUANTILE_NAMES[q]}",
                "family": "B_ACTIVITY",
                "kind": "agg_if_ge",
                "feature": f,
                "op": "GE",
                "quantile": q,
            })

    # Family C: wide spread → PASSIVE else SKIP
    for f in ("spread_bps", "tick_spread"):
        for q in (0.50, 0.70):
            cats.append({
                "id": f"C_PAS_{f}_ge_{QUANTILE_NAMES[q]}",
                "family": "C_EXEC",
                "kind": "pas_if_ge",
                "feature": f,
                "op": "GE",
                "quantile": q,
            })
    # deep bid → PASSIVE
    cats.append({
        "id": "C_PAS_imbalance_ge_q70",
        "family": "C_EXEC",
        "kind": "pas_if_ge",
        "feature": "imbalance",
        "op": "GE",
        "quantile": 0.70,
    })

    # Family D: dual routing
    for mq in (0.50, 0.70):
        for sq in (0.50, 0.70):
            cats.append({
                "id": f"D_DUAL_mom60_ge_{QUANTILE_NAMES[mq]}__spread_ge_{QUANTILE_NAMES[sq]}",
                "family": "D_DIRECTION_X_EXEC",
                "kind": "dual_mom_spread",
                "mom_feature": "mid_ret_60s",
                "mom_q": mq,
                "spread_feature": "spread_bps",
                "spread_q": sq,
                # if mom high & spread wide → PASSIVE; mom high & spread narrow → AGG;
                # mom low → SKIP
            })
            cats.append({
                "id": f"D_ROUTE_mom60_ge_{QUANTILE_NAMES[mq]}__else_pas_spread_ge_{QUANTILE_NAMES[sq]}",
                "family": "D_DIRECTION_X_EXEC",
                "kind": "agg_else_pas",
                "mom_feature": "mid_ret_60s",
                "mom_q": mq,
                "spread_feature": "spread_bps",
                "spread_q": sq,
                # mom high → AGG; elif spread high → PASSIVE; else SKIP
            })

    # narrow spread + mom → AGG
    cats.append({
        "id": "D_AGG_mom60_ge_q70_AND_spread_le_q30",
        "family": "D_DIRECTION_X_EXEC",
        "kind": "agg_and",
        "f1": "mid_ret_60s",
        "op1": "GE",
        "q1": 0.70,
        "f2": "spread_bps",
        "op2": "LE",
        "q2": 0.30,
    })

    return cats


def apply_rule(
    row: dict[str, Any],
    rule: dict[str, Any],
    thr: dict[str, dict[float, float]],
) -> str:
    """Return SKIP / AGGRESSIVE / PASSIVE using train-fitted thresholds only."""
    kind = rule["kind"]

    def thr_of(feat: str, q: float) -> Optional[float]:
        d = thr.get(feat) or {}
        return d.get(q)

    if kind == "agg_if_ge":
        f, q = rule["feature"], rule["quantile"]
        t = thr_of(f, q)
        v = row.get(f)
        if t is None or v is None or not np.isfinite(v):
            return DEC_SKIP
        return DEC_AGG if float(v) >= t else DEC_SKIP

    if kind == "pas_if_ge":
        f, q = rule["feature"], rule["quantile"]
        t = thr_of(f, q)
        v = row.get(f)
        if t is None or v is None or not np.isfinite(v):
            return DEC_SKIP
        return DEC_PAS if float(v) >= t else DEC_SKIP

    if kind == "dual_mom_spread":
        mt = thr_of(rule["mom_feature"], rule["mom_q"])
        st = thr_of(rule["spread_feature"], rule["spread_q"])
        mv, sv = row.get(rule["mom_feature"]), row.get(rule["spread_feature"])
        if None in (mt, st, mv, sv) or not (np.isfinite(mv) and np.isfinite(sv)):
            return DEC_SKIP
        if float(mv) < mt:
            return DEC_SKIP
        return DEC_PAS if float(sv) >= st else DEC_AGG

    if kind == "agg_else_pas":
        mt = thr_of(rule["mom_feature"], rule["mom_q"])
        st = thr_of(rule["spread_feature"], rule["spread_q"])
        mv, sv = row.get(rule["mom_feature"]), row.get(rule["spread_feature"])
        if mt is None or mv is None or not np.isfinite(mv):
            return DEC_SKIP
        if float(mv) >= mt:
            return DEC_AGG
        if st is None or sv is None or not np.isfinite(sv):
            return DEC_SKIP
        return DEC_PAS if float(sv) >= st else DEC_SKIP

    if kind == "agg_and":
        t1 = thr_of(rule["f1"], rule["q1"])
        t2 = thr_of(rule["f2"], rule["q2"])
        v1, v2 = row.get(rule["f1"]), row.get(rule["f2"])
        if None in (t1, t2, v1, v2) or not (np.isfinite(v1) and np.isfinite(v2)):
            return DEC_SKIP
        ok1 = _cmp(float(v1), rule["op1"], t1)
        ok2 = _cmp(float(v2), rule["op2"], t2)
        return DEC_AGG if ok1 and ok2 else DEC_SKIP

    return DEC_SKIP


def fit_all_thresholds(rows: list[dict]) -> dict[str, dict[float, float]]:
    feats = sorted({name for name, _ in FEATURE_SPECS})
    return {f: fit_quantiles(rows, f) for f in feats}
