"""OPENING_DRIVE_ACTIVE. Seed is not permanent. No clock expiry."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2 import (
    FAILED_ATTEMPT_N,
    LAST_BODY_MIN,
    OR_RECROSS_CLOSES,
    PROGRESS_HEAVY_OVERLAP,
    PROGRESS_TINY_EXTREME_FRAC,
    REPEATED_NO_EXPANSION_N,
    UNWIND_FRAC,
)
from research.pb1_v4_clarified_machine_correction_v2.seed import opposite_drive_established

PROGRESS_RESET_CLASSES = frozenset(
    {
        "REAL_BREAKOUT_EXTENSION",
        "MEANINGFUL_DIRECTIONAL_EXTENSION",
    }
)


def classify_progress(
    *,
    sign: int,
    bar: dict[str, Any],
    prev_bar: dict[str, Any] | None,
    prev_ext: Any,
) -> dict[str, Any]:
    """One completed-5m progress class. Wick nick / leftover drift do not reset stall."""
    ext = bar.get("h") if int(sign) > 0 else bar.get("l")
    rng = bar.get("range")
    body = bar.get("body_over_range")
    close = bar.get("c")
    if not _finite(ext) or not _finite(prev_ext):
        return {
            "class": "NO_DIRECTIONAL_PROGRESS",
            "resets_stall": False,
            "renewed_directional_auction": False,
        }
    progressed_ext = (int(sign) > 0 and float(ext) > float(prev_ext)) or (
        int(sign) < 0 and float(ext) < float(prev_ext)
    )
    if not progressed_ext:
        return {
            "class": "NO_DIRECTIONAL_PROGRESS",
            "resets_stall": False,
            "renewed_directional_auction": False,
            "delta": abs(float(ext) - float(prev_ext)),
        }
    delta = abs(float(ext) - float(prev_ext))
    tiny = _finite(rng) and float(rng) > 0 and delta <= float(PROGRESS_TINY_EXTREME_FRAC) * float(rng)
    close_prog = None
    overlap = None
    range_vs_prior = None
    if prev_bar:
        prev_c = prev_bar.get("c")
        if _finite(close) and _finite(prev_c):
            close_prog = (float(close) - float(prev_c)) * float(sign)
        ph, pl = prev_bar.get("h"), prev_bar.get("l")
        h, l = bar.get("h"), bar.get("l")
        if _finite(ph) and _finite(pl) and _finite(h) and _finite(l):
            lo = max(float(pl), float(l))
            hi = min(float(ph), float(h))
            cur_rng = float(h) - float(l)
            if cur_rng > 0:
                overlap = max(0.0, hi - lo) / cur_rng
        prev_rng = prev_bar.get("range")
        if _finite(rng) and _finite(prev_rng) and float(prev_rng) > 0:
            range_vs_prior = float(rng) / float(prev_rng)
    close_stalled = close_prog is None or float(close_prog) <= 0 or (
        delta > 0 and abs(float(close_prog)) < abs(delta) * 0.25
    )
    heavy = overlap is not None and float(overlap) >= float(PROGRESS_HEAVY_OVERLAP)
    contracted = range_vs_prior is not None and float(range_vs_prior) < 1.0
    weak_body = _finite(body) and float(body) < float(LAST_BODY_MIN)
    directional_close = close_prog is not None and float(close_prog) > 0 and not close_stalled
    genuine_extension = (not tiny) and (not heavy) and (not contracted)
    if directional_close and genuine_extension:
        cls = (
            "REAL_BREAKOUT_EXTENSION"
            if range_vs_prior is not None and float(range_vs_prior) > 1.0
            else "MEANINGFUL_DIRECTIONAL_EXTENSION"
        )
        return {
            "class": cls,
            "resets_stall": True,
            "renewed_directional_auction": True,
            "delta": delta,
            "close_prog": close_prog,
            "overlap": overlap,
            "range_vs_prior": range_vs_prior,
            "tiny_extreme": tiny,
        }
    if heavy and (tiny or close_stalled):
        cls = "RANGE_DRIFT_EXTREME"
    elif tiny or (close_stalled and weak_body):
        cls = "MARGINAL_EXTREME_ONLY"
    elif heavy:
        cls = "RANGE_DRIFT_EXTREME"
    else:
        cls = "MARGINAL_EXTREME_ONLY"
    return {
        "class": cls,
        "resets_stall": False,
        "renewed_directional_auction": False,
        "delta": delta,
        "close_prog": close_prog,
        "overlap": overlap,
        "range_vs_prior": range_vs_prior,
        "tiny_extreme": tiny,
    }


def opening_net(*, sign: int, open_0900: Any, close_now: Any) -> float | None:
    if not (_finite(open_0900) and _finite(close_now)):
        return None
    return (float(close_now) - float(open_0900)) * float(sign)


def classify_active_loss(
    *,
    sign: int,
    close: float,
    or_high: float,
    or_low: float,
    open_0900: Any,
    peak_disp: Any,
    wick_only_n: int,
    micro_break_n: int,
    recross_closes: int,
    left: bool,
    five_m_no_expansion_n: int,
    both_or_extremes_revisited: bool,
    location_identified: bool = False,
    had_renewed_auction: bool = False,
) -> dict[str, Any]:
    flags: list[str] = []
    disp_now = opening_net(sign=sign, open_0900=open_0900, close_now=close)
    if _finite(peak_disp) and float(peak_disp) > 0 and disp_now is not None:
        given = float(peak_disp) - float(disp_now)
        if given >= float(UNWIND_FRAC) * float(peak_disp) and float(disp_now) < 0.25 * float(peak_disp):
            flags.append("DISPLACEMENT_UNWOUND")
    failed_n = int(wick_only_n) + int(micro_break_n)
    if failed_n >= int(FAILED_ATTEMPT_N) and int(five_m_no_expansion_n) >= 2:
        flags.append("REPEATED_FAILED_PROGRESS")
    if left and int(recross_closes) >= int(OR_RECROSS_CLOSES):
        flags.append("REPEATED_OR_RECROSS")
    if left and both_or_extremes_revisited:
        flags.append("TWO_SIDED_BALANCE_REESTABLISHED")
    if int(five_m_no_expansion_n) >= int(REPEATED_NO_EXPANSION_N):
        # Leftover drift with no renewed auction dies. A proven drive waiting
        # for location identification is not stale merely from a pause.
        if location_identified or not had_renewed_auction:
            flags.append("STALE_RANGE_RESOLUTION")
    return {
        "lost": bool(flags),
        "flags": flags,
        "reason": flags[0] if flags else None,
        "clock_cutoff": False,
        "age_5m_gate": False,
        "disp_now": disp_now,
    }


def maybe_establish_opposite(*, bars: list[dict[str, Any]], seed_row: dict[str, Any], scale: Any) -> dict[str, Any]:
    fail = dict(seed_row.get("failed_open") or {})
    return opposite_drive_established(bars, seed=fail, scale=scale)
