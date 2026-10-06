"""S1 5m opening drive. OR-half does not enter the definition."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_machine_implementation import (
    FAIL_COUNTER_BODY_FRAC,
    FAIL_COUNTER_MIN,
    FAIL_DRIVE_DISP_MIN,
    FAIL_EXTEND_MAX_BARS,
    FLAT_DISP_MAX,
    FLAT_RANGE_MAX,
    INVALID_OPENING_STATES,
    TRUE_BODY_FRAC_MIN,
    TRUE_COUNTER_FRAC,
    TRUE_DISP_MIN,
    TRUE_N_SAME_MIN,
    TRUE_RANGE_MIN,
    VALID_OPENING_STATES,
)
from research.pb1_v4_machine_implementation.bars5 import sequence_for_sign

PASS_STATES = VALID_OPENING_STATES
REJECT_STATES = INVALID_OPENING_STATES


def _failed_extreme(open_bars: list[dict[str, Any]], *, forming_sign: int) -> float | None:
    opp = [b for b in open_bars if int(b.get("direction") or 0) == -int(forming_sign)]
    if not opp:
        return None
    bar = opp[0]
    if int(forming_sign) < 0:
        return float(bar["h"]) if _finite(bar.get("h")) else None
    return float(bar["l"]) if _finite(bar.get("l")) else None


def _failed_drive_disp(bars: list[dict[str, Any]], *, forming_sign: int, normal: Any) -> float | None:
    ext = _failed_extreme(bars[:3], forming_sign=forming_sign)
    last_c = bars[-1].get("c") if bars else None
    if not (_finite(ext) and _finite(last_c) and _finite(normal) and float(normal) > 0):
        return None
    if int(forming_sign) < 0:
        disp = float(ext) - float(last_c)
    else:
        disp = float(last_c) - float(ext)
    return float(disp) / float(normal)


def _unit(normal_opening_5m: Any, atr20: Any) -> float | None:
    if _finite(normal_opening_5m) and float(normal_opening_5m) > 0:
        return float(normal_opening_5m)
    if _finite(atr20) and float(atr20) > 0:
        return 0.25 * float(atr20)
    return None


def _true_ok(seq: dict[str, Any]) -> bool:
    disp = seq.get("net_displacement_over_normal_5m")
    mx = seq.get("max_range_over_normal_5m")
    n_same = int(seq.get("n_same_dir_5m") or 0)
    ctr = seq.get("largest_counter_over_normal_5m")
    body = seq.get("mean_body_over_range")
    if not (_finite(disp) and float(disp) >= float(TRUE_DISP_MIN)):
        return False
    if not (_finite(mx) and float(mx) >= float(TRUE_RANGE_MIN)):
        return False
    if n_same < int(TRUE_N_SAME_MIN):
        return False
    if not (_finite(body) and float(body) >= float(TRUE_BODY_FRAC_MIN)):
        return False
    ctr_v = float(ctr) if _finite(ctr) else 0.0
    return ctr_v <= float(TRUE_COUNTER_FRAC) * float(disp)


def _failed_open_seed(seq: dict[str, Any], bars: list[dict[str, Any]]) -> bool:
    ctr = seq.get("largest_counter_over_normal_5m")
    if not (_finite(ctr) and float(ctr) >= float(FAIL_COUNTER_MIN)):
        return False
    if seq.get("sequence") not in ("reversal", "two-sided"):
        first_dir = int(seq.get("first_bar_direction") or 0)
        if first_dir == 0:
            return False
    first_br = seq.get("first_bar_body_over_range")
    first_rn = seq.get("first_bar_range_over_normal")
    visible_first = _finite(first_rn) and float(first_rn) >= float(FAIL_COUNTER_MIN)
    visible_body = _finite(first_br) and float(first_br) >= float(FAIL_COUNTER_BODY_FRAC)
    if visible_first and visible_body:
        return True
    if not bars:
        return False
    # Largest opposite-to-eventual-drive 5m bar must itself be a real body, not a wick dip.
    opp = [b for b in bars if int(b.get("direction") or 0) != 0]
    if not opp:
        return False
    biggest = max(opp, key=lambda b: abs(float(b.get("net") or 0.0)))
    br = biggest.get("body_over_range")
    return _finite(br) and float(br) >= float(FAIL_COUNTER_BODY_FRAC)


def classify_s1(bars: list[dict[str, Any]], *, normal_opening_5m: Any, atr20: Any = None) -> dict[str, Any]:
    """Classify opening drive from completed 5m bars. Never uses OR-half location."""
    n_open = min(3, len(bars))
    open_bars = list(bars[:n_open])
    extra = list(bars[3:])
    unit = _unit(normal_opening_5m, atr20)
    if len(open_bars) < 3 or unit is None:
        return {
            "ok": False,
            "state": "FLAT_OR_CRAWL",
            "DIR": 0,
            "reason": "incomplete_5m_or_no_prior_normal",
            "or_half_used": False,
        }

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for sign in (1, -1):
        seq = sequence_for_sign(open_bars, sign=sign, normal_range=unit)
        disp = float(seq.get("net_displacement_over_normal_5m") or 0.0)
        scored.append((disp, sign, seq))
    scored.sort(key=lambda x: -x[0])
    best_disp, best_sign, best_seq = scored[0]
    other_disp, other_sign, other_seq = scored[1]

    if _true_ok(best_seq):
        return {
            "ok": True,
            "state": "TRUE_OPENING_DRIVE",
            "DIR": int(best_sign),
            "reason": "directional_5m_displacement_and_bodies",
            "seq": best_seq,
            "or_half_used": False,
            "forming": False,
        }

    # Failed-open seed: a real counter auction, then opposite drive (may extend past 09:14).
    forming_sign = None
    forming_seq = None
    for disp, sign, seq in scored:
        if _failed_open_seed(seq, open_bars):
            # The failed attempt is against `sign`; we still need the later opposite auction
            # to establish `sign`. Seed on the sign whose counter is the first bar.
            first_dir = int(seq.get("first_bar_direction") or 0)
            if first_dir == -int(sign) or seq.get("sequence") == "reversal":
                forming_sign = int(sign)
                forming_seq = seq
                break
    if forming_sign is None:
        for sign in (1, -1):
            seq = sequence_for_sign(open_bars, sign=sign, normal_range=unit)
            first_dir = int(seq.get("first_bar_direction") or 0)
            first_rn = seq.get("first_bar_range_over_normal")
            first_br = seq.get("first_bar_body_over_range")
            if first_dir == -int(sign) and _finite(first_rn) and float(first_rn) >= float(FAIL_COUNTER_MIN):
                if _finite(first_br) and float(first_br) >= float(FAIL_COUNTER_BODY_FRAC):
                    forming_sign = int(sign)
                    forming_seq = seq
                    break

    if forming_sign is not None:
        use_bars = open_bars + extra[: int(FAIL_EXTEND_MAX_BARS)]
        ext_seq = sequence_for_sign(use_bars, sign=forming_sign, normal_range=unit)
        disp = _failed_drive_disp(use_bars, forming_sign=forming_sign, normal=unit)
        n_same = int(ext_seq.get("n_same_dir_5m") or 0)
        two_sided_locked = (
            int(best_seq.get("n_same_dir_5m") or 0) >= 1
            and int(best_seq.get("n_counter_5m") or 0) >= 1
            and (not _failed_open_seed(forming_seq or best_seq, open_bars))
            and abs(float(best_disp)) < float(TRUE_DISP_MIN)
        )
        if two_sided_locked and len(extra) == 0:
            return {
                "ok": False,
                "state": "TWO_SIDED_OPEN",
                "DIR": 0,
                "reason": "two_sided_open_not_failed_open",
                "seq": best_seq,
                "or_half_used": False,
                "forming": False,
            }
        if _finite(disp) and float(disp) >= float(FAIL_DRIVE_DISP_MIN) and n_same >= 1:
            return {
                "ok": True,
                "state": "FAILED_OPEN_THEN_REAL_DRIVE",
                "DIR": int(forming_sign),
                "reason": "visible_failed_open_then_opposite_5m_auction",
                "seq": ext_seq,
                "or_half_used": False,
                "forming": False,
                "failed_extreme": _failed_extreme(open_bars, forming_sign=int(forming_sign)),
            }
        if len(extra) < int(FAIL_EXTEND_MAX_BARS):
            return {
                "ok": False,
                "state": "FAILED_OPEN_THEN_REAL_DRIVE",
                "DIR": int(forming_sign),
                "reason": "forming_failed_open_awaiting_opposite_auction",
                "seq": ext_seq,
                "or_half_used": False,
                "forming": True,
                "failed_extreme": _failed_extreme(open_bars, forming_sign=int(forming_sign)),
            }
        return {
            "ok": False,
            "state": "LATE_RANGE_RESOLUTION",
            "DIR": 0,
            "reason": "failed_open_never_became_real_opposite_drive",
            "seq": ext_seq,
            "or_half_used": False,
            "forming": False,
        }

    mx = best_seq.get("max_range_over_normal_5m")
    body = best_seq.get("mean_body_over_range")
    n_same = int(best_seq.get("n_same_dir_5m") or 0)
    n_opp = int(best_seq.get("n_counter_5m") or 0)
    if (not _finite(mx) or float(mx) < float(FLAT_RANGE_MAX)) or (
        abs(float(best_disp)) < float(FLAT_DISP_MAX) and (not _finite(body) or float(body) < float(TRUE_BODY_FRAC_MIN))
    ):
        return {
            "ok": False,
            "state": "FLAT_OR_CRAWL",
            "DIR": 0,
            "reason": "no_visible_opening_drive_vs_normal",
            "seq": best_seq,
            "or_half_used": False,
            "forming": False,
        }
    if n_same >= 1 and n_opp >= 1 and abs(float(best_disp)) < float(TRUE_DISP_MIN):
        return {
            "ok": False,
            "state": "TWO_SIDED_OPEN",
            "DIR": 0,
            "reason": "two_sided_5m_auction",
            "seq": best_seq,
            "or_half_used": False,
            "forming": False,
        }
    if abs(float(best_disp)) < float(TRUE_DISP_MIN):
        return {
            "ok": False,
            "state": "MICRO_OR_LEAK",
            "DIR": 0,
            "reason": "micro_displacement_not_a_drive",
            "seq": best_seq,
            "or_half_used": False,
            "forming": False,
        }
    return {
        "ok": False,
        "state": "LATE_RANGE_RESOLUTION",
        "DIR": 0,
        "reason": "opening_not_a_locked_drive",
        "seq": best_seq,
        "or_half_used": False,
        "forming": False,
        "other_disp": other_disp,
        "other_sign": other_sign,
    }


def s1_pass(result: dict[str, Any]) -> bool:
    return bool(result.get("ok")) and str(result.get("state") or "") in PASS_STATES and not result.get("forming")
