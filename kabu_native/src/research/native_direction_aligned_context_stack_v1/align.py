"""Direction-aligned feature formulas. No raw unsigned directional returns in the model."""
from __future__ import annotations

from typing import Any

from research.native_participation_x_sr_context_discovery_v1.native import close_location
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import is_primary


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def dir_of(direction: str) -> int:
    return 1 if str(direction) == "BULLISH" else -1


def aligned_ret(raw: Any, direction: str) -> float:
    if not _finite(raw):
        return float("nan")
    return float(dir_of(direction)) * float(raw)


def aligned_vwap_bps(close: Any, vwap: Any, direction: str) -> float:
    if not _finite(close) or not _finite(vwap) or float(vwap) == 0:
        return float("nan")
    return float(dir_of(direction)) * (float(close) - float(vwap)) / float(vwap) * 10_000.0


def event_range_rel(h: float, l: float, med20: float) -> float:
    if not _finite(h) or not _finite(l) or not _finite(med20) or float(med20) <= 0:
        return float("nan")
    return (float(h) - float(l)) / float(med20)


def break_distance_bps(close: float, mx: float, mn: float, direction: str) -> float:
    if not _finite(close) or float(close) == 0:
        return float("nan")
    ref = mx if str(direction) == "BULLISH" else mn
    if not _finite(ref):
        return float("nan")
    return float(dir_of(direction)) * (float(close) - float(ref)) / float(close) * 10_000.0


def close_location_strength(h: float, l: float, c: float, direction: str) -> float:
    loc = close_location(h, l, c)
    if loc is None:
        return float("nan")
    return float(loc) if str(direction) == "BULLISH" else float(1.0 - loc)


def sr_aligned(snap: dict[str, Any], *, close: float, atr: float, direction: str) -> dict[str, Any]:
    sups = list(snap.get("support_active") or [])
    ress = list(snap.get("resistance_active") or [])
    ahead = float("nan")
    behind = float("nan")
    if str(direction) == "BULLISH":
        cands = []
        for z in ress:
            lo, hi = float(z["lo"]), float(z["hi"])
            if lo >= close or (lo <= close <= hi):
                cands.append(max(0.0, lo - close))
        if cands and _finite(atr) and float(atr) > 0:
            ahead = min(cands) / float(atr)
        back = []
        for z in sups:
            lo, hi = float(z["lo"]), float(z["hi"])
            if hi <= close or (lo <= close <= hi):
                back.append(max(0.0, close - hi))
        if back and _finite(atr) and float(atr) > 0:
            behind = min(back) / float(atr)
        breaking = 1.0 if any(_finite(close) and close > float(z["hi"]) for z in ress) else 0.0
    else:
        cands = []
        for z in sups:
            lo, hi = float(z["lo"]), float(z["hi"])
            if hi <= close or (lo <= close <= hi):
                cands.append(max(0.0, close - hi))
        if cands and _finite(atr) and float(atr) > 0:
            ahead = min(cands) / float(atr)
        back = []
        for z in ress:
            lo, hi = float(z["lo"]), float(z["hi"])
            if lo >= close or (lo <= close <= hi):
                back.append(max(0.0, lo - close))
        if back and _finite(atr) and float(atr) > 0:
            behind = min(back) / float(atr)
        breaking = 1.0 if any(_finite(close) and close < float(z["lo"]) for z in sups) else 0.0
    return {
        "ahead_sr_distance_atr": ahead,
        "ahead_sr_missing": 0.0 if _finite(ahead) else 1.0,
        "behind_sr_distance_atr": behind,
        "behind_sr_missing": 0.0 if _finite(behind) else 1.0,
        "breaking_ahead_sr": breaking,
    }


def post_break_retest_aligned(eps: list[dict[str, Any]], *, t: str, direction: str) -> float:
    want_res = str(direction) == "BULLISH"
    for ep in eps:
        if not is_primary(str(ep.get("selection_slot") or "")):
            continue
        if bool(ep.get("as_resistance")) != want_res:
            continue
        if not ep.get("break"):
            continue
        if ep.get("retest") or ep.get("retest_hold"):
            ht = str(ep.get("retest_time") or ep.get("retest_hold_time") or "")
            if ht and ht <= t:
                return 1.0
    return 0.0


FEATURE_DEFINITIONS = {
    "DIR": "+1 if BULLISH displacement, -1 if BEARISH",
    "aligned_r5": "DIR * raw 5-minute clock return through completed bar T",
    "aligned_r15": "DIR * raw 15-minute clock return through completed bar T",
    "aligned_prior5_ret": "DIR * (close[T] / close[T-5 session bars] - 1)",
    "tv_clock_pctl": "same-minute TradingValue percentile vs prior 20 completed days, min 10; continuous; no 0.80 gate",
    "event_range_rel": "event bar range / median range of prior 20 completed session bars",
    "break_distance_bps": "DIR * (close - prior5 extreme) / close * 10000; extreme is prior5 high if bullish else prior5 low",
    "close_location_strength": "bullish = close_location; bearish = 1 - close_location",
    "prior5_range_rel": "unsigned prior-5 high-low / prior-20 median bar range (non-directional structure)",
    "aligned_vwap_bps": "DIR * (close - causal session VWAP) / causal session VWAP * 10000",
    "ahead_sr_distance_atr": "distance/ATR20 to nearest active obstacle in event direction; missing if none (no invented level)",
    "ahead_sr_missing": "1 if no ahead active S/R exists",
    "behind_sr_distance_atr": "distance/ATR20 to nearest active backstop behind the move; missing if none",
    "behind_sr_missing": "1 if no behind active S/R exists",
    "breaking_ahead_sr": "1 if bullish close through active resistance far bound, or bearish close through active support far bound",
    "post_break_retest_aligned": "1 if frozen break→retest sequence on the DIR-aligned salient zone is already known",
    "aligned_market_rel": "DIR * (symbol session return - market median), prior completed bar",
    "aligned_sector_rel": "DIR * (symbol session return - sector median), prior completed bar",
    "pullback_from_extreme_audit": "already DIR-like in the parent helper (negative = extension). NOT included: not in the compact set; not blindly multiplied by DIR.",
}

DIR_FORMULAS = {
    "aligned_r5": "DIR * raw_r5",
    "aligned_r15": "DIR * raw_r15",
    "aligned_prior5_ret": "DIR * raw_prior5_ret",
    "aligned_vwap_bps": "DIR * (close - vwap) / vwap * 10000",
    "aligned_market_rel": "DIR * raw_market_relative_return",
    "aligned_sector_rel": "DIR * raw_sector_relative_return",
    "ahead_sr": "BULLISH: nearest active resistance above/at price. BEARISH: nearest active support below/at price.",
    "behind_sr": "BULLISH: nearest active support below/at price. BEARISH: nearest active resistance above/at price.",
}
