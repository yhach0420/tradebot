"""OPENING_DRIVE_ACTIVE. Seed is not permanent. No clock expiry."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v4 import (
    FAILED_ATTEMPT_N,
    FAILED_BREAK_REACCEPTED,
    LAST_BODY_MIN,
    OR_RECROSS_CLOSES,
    PROGRESS_HEAVY_OVERLAP,
    PROGRESS_TINY_EXTREME_FRAC,
    UNWIND_FRAC,
)
from research.pb1_v4_clarified_machine_correction_v4.encoding import RCA_COMPARABLE_OPPOSITE_BODY
from research.pb1_v4_clarified_machine_correction_v4.seed import opposite_drive_established

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


def _bar_overlap(prev_bar: dict[str, Any] | None, bar: dict[str, Any] | None) -> float | None:
    if not prev_bar or not bar:
        return None
    ph, pl = prev_bar.get("h"), prev_bar.get("l")
    h, l = bar.get("h"), bar.get("l")
    if not (_finite(ph) and _finite(pl) and _finite(h) and _finite(l)):
        return None
    lo = max(float(pl), float(l))
    hi = min(float(ph), float(h))
    cur_rng = float(h) - float(l)
    if cur_rng <= 0:
        return None
    return max(0.0, hi - lo) / cur_rng


def note_failed_probe_pending(extra: dict[str, Any], *, sign: int, bar: dict[str, Any], prog: dict[str, Any]) -> None:
    """Non-terminal diagnostic. Geometric probe is not death. REAL_RENEWED_AUCTION clears."""
    cls = str(prog.get("class") or "")
    bdir = int(bar.get("direction") or 0)
    if bool(prog.get("renewed_directional_auction")) or cls in PROGRESS_RESET_CLASSES:
        extra.pop("failed_probe_pending", None)
        extra.pop("failed_probe_bar", None)
        extra.pop("failed_probe_opposite_bar", None)
        extra["failed_probe_state"] = None
        return
    if cls in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME") and bdir == int(sign):
        extra["failed_probe_pending"] = True
        extra["failed_probe_bar"] = dict(bar)
        extra["failed_probe_state"] = "FAILED_PROBE_PENDING"
        extra.pop("failed_probe_opposite_bar", None)
        return
    if extra.get("failed_probe_pending") and bdir == -int(sign):
        extra["failed_probe_opposite_bar"] = dict(bar)
        extra["failed_probe_state"] = "FAILED_PROBE_PENDING_OPPOSITE_SEEN"


def classify_failed_break_reaccepted(
    *,
    sign: int,
    bar: dict[str, Any] | None,
    prev_bar: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """Confirmation-bar leftover reacceptance. Not any-overlap. Not N-bar. Not backdated to the probe."""
    extra = extra or {}
    if not extra.get("failed_probe_pending") or not bar:
        return {"reaccepted": False, "reason": None, "family": None}
    probe = dict(extra.get("failed_probe_bar") or {})
    opp = dict(extra.get("failed_probe_opposite_bar") or {})
    if not probe or not opp:
        return {"reaccepted": False, "reason": None, "family": None}
    if str(bar.get("t1") or "") == str(opp.get("t1") or ""):
        return {"reaccepted": False, "reason": None, "family": None}
    if str(bar.get("t1") or "") == str(probe.get("t1") or ""):
        return {"reaccepted": False, "reason": None, "family": None}
    hs = [probe.get("h"), opp.get("h"), bar.get("h")]
    ls = [probe.get("l"), opp.get("l"), bar.get("l")]
    if not all(_finite(x) for x in hs + ls):
        return {"reaccepted": False, "reason": None, "family": None}
    env_hi = max(float(probe["h"]), float(opp["h"]))
    env_lo = min(float(probe["l"]), float(opp["l"]))
    contained = float(bar["h"]) <= env_hi + 1e-12 and float(bar["l"]) >= env_lo - 1e-12
    overlap = _bar_overlap(prev_bar, bar)
    heavy = overlap is not None and float(overlap) >= float(PROGRESS_HEAVY_OVERLAP)
    close = bar.get("c")
    probe_not_accepted = False
    if _finite(close):
        if int(sign) > 0 and _finite(probe.get("h")):
            probe_not_accepted = float(close) < float(probe["h"])
        elif int(sign) < 0 and _finite(probe.get("l")):
            probe_not_accepted = float(close) > float(probe["l"])
    if contained and heavy and probe_not_accepted:
        return {
            "reaccepted": True,
            "reason": FAILED_BREAK_REACCEPTED,
            "family": "AUCTION_ENDED_AS_RANGE",
            "route": "FAILED_BREAK_REACCEPTED",
            "n_bar_expiry": False,
            "overlap_is_not_death_alone": True,
        }
    return {"reaccepted": False, "reason": None, "family": None}
    if not (_finite(open_0900) and _finite(close_now)):
        return None
    return (float(close_now) - float(open_0900)) * float(sign)


def opening_net(*, sign: int, open_0900: Any, close_now: Any) -> float | None:
    if not (_finite(open_0900) and _finite(close_now)):
        return None
    return (float(close_now) - float(open_0900)) * float(sign)


def classify_stale_range_resolution(
    *,
    sign: int,
    bar: dict[str, Any] | None,
    prev_bar: dict[str, Any] | None,
) -> dict[str, Any]:
    """Auction-end event, not N-bar expiry.

    A pause (NO_DIRECTIONAL_PROGRESS repeating) is not death.
    STALE is a committed opposite 5m that does not contract vs the previous 5m.
    """
    if not bar or not prev_bar:
        return {"stale": False, "reason": None}
    bdir = int(bar.get("direction") or 0)
    if bdir != -int(sign):
        return {"stale": False, "reason": None}
    body = bar.get("body_over_range")
    rng = bar.get("range")
    prev_rng = prev_bar.get("range")
    if not (_finite(body) and float(body) >= float(RCA_COMPARABLE_OPPOSITE_BODY)):
        return {"stale": False, "reason": None}
    if not (_finite(rng) and _finite(prev_rng) and float(rng) >= float(prev_rng)):
        return {"stale": False, "reason": None}
    return {
        "stale": True,
        "reason": "STALE_RANGE_RESOLUTION",
        "n_bar_expiry": False,
        "location_gated": False,
    }


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
    bar: dict[str, Any] | None = None,
    prev_bar: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = location_identified, had_renewed_auction, or_high, or_low
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
    stale = classify_stale_range_resolution(sign=sign, bar=bar, prev_bar=prev_bar)
    if stale.get("stale"):
        flags.append("STALE_RANGE_RESOLUTION")
    reacc = classify_failed_break_reaccepted(sign=sign, bar=bar, prev_bar=prev_bar, extra=extra)
    if reacc.get("reaccepted"):
        flags.append(FAILED_BREAK_REACCEPTED)
    if FAILED_BREAK_REACCEPTED in flags:
        reason = FAILED_BREAK_REACCEPTED
    elif "STALE_RANGE_RESOLUTION" in flags:
        reason = "STALE_RANGE_RESOLUTION"
    else:
        reason = flags[0] if flags else None
    family = None
    if FAILED_BREAK_REACCEPTED in flags or "STALE_RANGE_RESOLUTION" in flags:
        family = "AUCTION_ENDED_AS_RANGE"
    return {
        "lost": bool(flags),
        "flags": flags,
        "reason": reason,
        "auction_end_family": family,
        "clock_cutoff": False,
        "age_5m_gate": False,
        "n_bar_expiry": False,
        "location_gated": False,
        "disp_now": disp_now,
        "five_m_no_expansion_n_diagnostic": int(five_m_no_expansion_n),
        "stale_event": stale,
        "failed_break_reaccepted": reacc,
    }


def maybe_establish_opposite(*, bars: list[dict[str, Any]], seed_row: dict[str, Any], scale: Any) -> dict[str, Any]:
    fail = dict(seed_row.get("failed_open") or {})
    return opposite_drive_established(bars, seed=fail, scale=scale)
