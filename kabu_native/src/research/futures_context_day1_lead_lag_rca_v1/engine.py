"""Lead/lag RCA engine. Reuses Day1 effect-check loaders. No threshold search. No sendorder."""
from __future__ import annotations

from typing import Any, Optional

from research.futures_context_day1_effect_check_v1.engine import (
    AsOf,
    _epoch,
    clock_dt,
    mean,
    median,
    ret_asof,
)
from research.futures_context_day1_lead_lag_rca_v1 import (
    OFFSET_LABELS,
    OFFSETS_SEC,
    PRIMARY_HORIZON,
    PRIMARY_HORIZON_SEC,
    PRIMARY_LOOKBACK_SEC,
)


def sgn(x: Optional[float]) -> int:
    if x is None:
        return 0
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def overlap_map(
    offset_sec: int,
    *,
    lookback_sec: int = PRIMARY_LOOKBACK_SEC,
    outcome_sec: int = PRIMARY_HORIZON_SEC,
) -> dict[str, Any]:
    """Time overlap of NK_RET_180S window at T+offset vs stock 10m outcome [T, T+10m]. T=0."""
    feat_a = int(offset_sec) - int(lookback_sec)
    feat_b = int(offset_sec)
    out_a = 0
    out_b = int(outcome_sec)
    ov_a = max(feat_a, out_a)
    ov_b = min(feat_b, out_b)
    ov = max(0, ov_b - ov_a)
    return {
        "offset_sec": int(offset_sec),
        "feature_window_sec_from_T": [feat_a, feat_b],
        "outcome_window_sec_from_T": [out_a, out_b],
        "overlap_sec": ov,
        "overlap_frac_of_feature": (ov / float(lookback_sec)) if lookback_sec else None,
        "overlap_frac_of_outcome": (ov / float(outcome_sec)) if outcome_sec else None,
        "feature_fully_inside_outcome": feat_a >= out_a and feat_b <= out_b,
        "noncausal_diagnostic": offset_sec > 0,
    }


def stock_mid_ret_asof(quotes: AsOf, t_epoch: float, lookback_sec: int) -> Optional[float]:
    now, _ = quotes.at(t_epoch)
    lag, _ = quotes.at(t_epoch - float(lookback_sec))
    if now is None or lag is None:
        return None
    mid_now = now[2]
    mid_lag = lag[2]
    if mid_now is None or mid_lag is None or mid_lag == 0:
        return None
    return (float(mid_now) - float(mid_lag)) / float(mid_lag)


def ew_stock_past_ret(stocks: dict[str, AsOf], symbols: list[str], t_epoch: float, lookback_sec: int) -> Optional[float]:
    xs = []
    for sym in symbols:
        series = stocks.get(sym)
        if series is None:
            continue
        r = stock_mid_ret_asof(series, t_epoch, lookback_sec)
        if r is not None:
            xs.append(r)
    return mean(xs)


def bucket_shift(clocks: list[dict[str, Any]], feature_key: str, outcome_key: str) -> dict[str, Any]:
    pos = [c for c in clocks if isinstance(c.get(feature_key), (int, float)) and c[feature_key] > 0]
    neg = [c for c in clocks if isinstance(c.get(feature_key), (int, float)) and c[feature_key] < 0]
    pos_xs = [float(c[outcome_key]) for c in pos if c.get(outcome_key) is not None]
    neg_xs = [float(c[outcome_key]) for c in neg if c.get(outcome_key) is not None]
    pos_m = mean(pos_xs)
    neg_m = mean(neg_xs)
    shift = None
    if pos_m is not None and neg_m is not None:
        shift = pos_m - neg_m
    elif pos_m is not None:
        shift = pos_m
    return {
        "pos_clock_n": len(pos_xs),
        "neg_clock_n": len(neg_xs),
        "pos_mean": pos_m,
        "neg_mean": neg_m,
        "pos_median": median(pos_xs),
        "neg_median": median(neg_xs),
        "mid_shift_bps": shift,
        "direction": sgn(shift),
    }


def peak_offset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = [r for r in rows if r.get("mid_shift_bps") is not None]
    if not ranked:
        return {"label": None, "offset_sec": None, "mid_shift_bps": None}
    best = max(ranked, key=lambda r: float(r["mid_shift_bps"]))
    return {
        "label": best.get("label"),
        "offset_sec": best.get("offset_sec"),
        "mid_shift_bps": best.get("mid_shift_bps"),
        "noncausal_diagnostic": bool(best.get("noncausal_diagnostic")),
    }


def causal_same_direction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    causal = [r for r in rows if not r.get("noncausal_diagnostic")]
    dirs = [int(r.get("direction") or 0) for r in causal]
    nz = [d for d in dirs if d != 0]
    same = bool(nz) and len(set(nz)) == 1
    return {
        "same": same,
        "directions": {r["label"]: r.get("direction") for r in causal},
        "zero_n": sum(1 for d in dirs if d == 0),
    }


def four_state(
    clocks: list[dict[str, Any]],
    nk_key: str,
    stk_key: str,
    outcome_key: str,
) -> dict[str, Any]:
    names = {
        (1, 1): "NK_up_STK_up",
        (1, -1): "NK_up_STK_down",
        (-1, 1): "NK_down_STK_up",
        (-1, -1): "NK_down_STK_down",
    }
    buckets: dict[str, list[float]] = {n: [] for n in names.values()}
    members: dict[str, list[str]] = {n: [] for n in names.values()}
    for c in clocks:
        a, b = sgn(c.get(nk_key)), sgn(c.get(stk_key))
        name = names.get((a, b))
        if name is None:
            continue
        v = c.get(outcome_key)
        if v is None:
            continue
        buckets[name].append(float(v))
        members[name].append(str(c.get("clock")))
    out = {}
    for name, xs in buckets.items():
        out[name] = {
            "n": len(xs),
            "clocks": members[name],
            "mean": mean(xs),
            "median": median(xs),
        }
    return out


def sign_agree_n(xs: list[tuple[Optional[float], Optional[float]]]) -> dict[str, Any]:
    agree = 0
    disagree = 0
    for a, b in xs:
        sa, sb = sgn(a), sgn(b)
        if sa == 0 or sb == 0:
            continue
        if sa == sb:
            agree += 1
        else:
            disagree += 1
    total = agree + disagree
    return {"agree_n": agree, "disagree_n": disagree, "compared_n": total}


def classify_type(
    *,
    causal_same: bool,
    peak_is_noncausal: bool,
    zero_shift: Optional[float],
    max_noncausal_shift: Optional[float],
    stock_prior_shift: Optional[float],
    stock_earlier_than_nk: bool,
    nk_adds: bool,
    overlap_placebo_feature_inside_outcome: bool,
) -> tuple[str, str]:
    """Return (A|B|C|D, reason). No threshold search; uses sign/order only."""
    if not causal_same:
        return "D", "causal offsets (-5/-3/-1/0) do not share one MID-shift sign"
    if peak_is_noncausal and overlap_placebo_feature_inside_outcome:
        if zero_shift is not None and max_noncausal_shift is not None and max_noncausal_shift > zero_shift:
            return "B", "+1/+3/+5 peak with NK 180s window inside the stock 10m outcome; contemporaneous common move"
        return "B", "noncausal positive offsets peak; contemporaneous common move"
    if stock_earlier_than_nk and not nk_adds:
        return "C", "stock equal-weight PAST_RET_180S shows the same state earlier and NK180 does not add on disagreement clocks"
    if peak_is_noncausal:
        return "B", "noncausal positive offsets stronger than T-as-of NK_RET_180S"
    if stock_earlier_than_nk:
        return "C", "stock prior state leads NK_RET_180S"
    return "A", "causal offsets same direction and peak at 0 or earlier"


def nk_adds_beyond_stock(states: dict[str, Any]) -> dict[str, Any]:
    """On sign-disagreement clocks, does future 10m follow NK or stock?"""
    up_down = states.get("NK_up_STK_down") or {}
    down_up = states.get("NK_down_STK_up") or {}
    a = up_down.get("mean")
    b = down_up.get("mean")
    n_ud = int(up_down.get("n") or 0)
    n_du = int(down_up.get("n") or 0)
    n = n_ud + n_du
    follows_nk = None
    follows_stock = None
    if a is not None and b is not None:
        # If NK adds, NK_up/STK_down should have higher future MID than NK_down/STK_up.
        follows_nk = a > b
        follows_stock = a < b
    elif a is not None:
        follows_nk = a > 0
        follows_stock = a < 0
    elif b is not None:
        follows_nk = b < 0
        follows_stock = b > 0
    insufficient = n_ud < 2 or n_du < 2
    adds = bool(follows_nk) and not follows_stock and not insufficient
    return {
        "disagreement_clock_n": n,
        "NK_up_STK_down_n": n_ud,
        "NK_down_STK_up_n": n_du,
        "NK_up_STK_down_mean": a,
        "NK_down_STK_up_mean": b,
        "future_follows_NK": follows_nk,
        "future_follows_stock": follows_stock,
        "nk_adds": adds,
        "insufficient_n": insufficient,
    }


def reversal_vs_persistence(clocks: list[dict[str, Any]]) -> dict[str, Any]:
    nk30_vs_180 = sign_agree_n([(c.get("NK_RET_30S"), c.get("NK_RET_180S")) for c in clocks])
    nk60_vs_180 = sign_agree_n([(c.get("NK_RET_60S"), c.get("NK_RET_180S")) for c in clocks])
    nk180_vs_10m = sign_agree_n([(c.get("NK_RET_180S"), c.get("mid_10m")) for c in clocks])
    nk30_vs_10m = sign_agree_n([(c.get("NK_RET_30S"), c.get("mid_10m")) for c in clocks])
    nk60_vs_10m = sign_agree_n([(c.get("NK_RET_60S"), c.get("mid_10m")) for c in clocks])
    stk180_vs_nk180 = sign_agree_n([(c.get("STK_PAST_RET_180S"), c.get("NK_RET_180S")) for c in clocks])
    stk180_vs_10m = sign_agree_n([(c.get("STK_PAST_RET_180S"), c.get("mid_10m")) for c in clocks])
    turning = 0
    persist = 0
    for c in clocks:
        s30, s180, s10 = sgn(c.get("NK_RET_30S")), sgn(c.get("NK_RET_180S")), sgn(c.get("mid_10m"))
        if s30 == 0 or s180 == 0 or s10 == 0:
            continue
        if s30 != s180 and s180 == s10:
            turning += 1
        if s180 == s10:
            persist += 1
    return {
        "NK30_vs_NK180": nk30_vs_180,
        "NK60_vs_NK180": nk60_vs_180,
        "NK180_vs_10m_MID": nk180_vs_10m,
        "NK30_vs_10m_MID": nk30_vs_10m,
        "NK60_vs_10m_MID": nk60_vs_10m,
        "STK180_vs_NK180": stk180_vs_nk180,
        "STK180_vs_10m_MID": stk180_vs_10m,
        "turning_point_clock_n": turning,
        "nk180_persists_into_10m_clock_n": persist,
        "short_reversal_explains_30_60_inversion": turning > 0 and nk30_vs_10m.get("disagree_n", 0) >= nk30_vs_10m.get("agree_n", 0),
        "trend_persistence_explains_180s": (nk180_vs_10m.get("agree_n", 0) > nk180_vs_10m.get("disagree_n", 0)),
    }


def attach_offset_features(
    clocks: list[dict[str, Any]],
    nk: AsOf,
    day: str,
) -> tuple[list[dict[str, Any]], int]:
    leak = 0
    out = []
    for src in clocks:
        hm = tuple(int(x) for x in str(src["clock"]).split(":"))
        te = _epoch(clock_dt(day, hm))
        row = dict(src)
        feats = dict(src.get("features") or {})
        row["NK_RET_30S"] = feats.get("NK_RET_30S")
        row["NK_RET_60S"] = feats.get("NK_RET_60S")
        row["NK_RET_180S"] = feats.get("NK_RET_180S")
        row["mid_10m"] = src.get(f"{PRIMARY_HORIZON}_MID_RETURN_BPS_mean")
        row["long_10m"] = src.get(f"{PRIMARY_HORIZON}_LONG_EXEC_MARKOUT_BPS_mean")
        row["short_10m"] = src.get(f"{PRIMARY_HORIZON}_SHORT_EXEC_MARKOUT_BPS_mean")
        row["long_10m_median"] = src.get(f"{PRIMARY_HORIZON}_LONG_EXEC_MARKOUT_BPS_median")
        row["short_10m_median"] = src.get(f"{PRIMARY_HORIZON}_SHORT_EXEC_MARKOUT_BPS_median")
        for off, lab in zip(OFFSETS_SEC, OFFSET_LABELS):
            r, lk = ret_asof(nk, te + float(off), PRIMARY_LOOKBACK_SEC)
            leak += lk
            row[f"NK180_at_{lab}"] = r
        out.append(row)
    return out, leak
