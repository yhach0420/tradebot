"""OPENING_DRIVE_SEED taxonomy by auction path. Not TRUE-first. Continued intent is a state."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation import (
    ATR_SANITY_FRAC,
    FAIL_DRIVE_BODY_MIN,
    FAIL_DRIVE_DISP_MIN,
    FAIL_DRIVE_N_SAME_MIN,
    FAIL_VISIBLE_RANGE_MIN,
    FAIL_COUNTER_BODY_FRAC,
    FLAT_DISP_MAX,
    FLAT_RANGE_MAX,
    LAST_BODY_MIN,
    ONE_BAR_SHARE_MAX,
    TRUE_BODY_FRAC_MIN,
    TRUE_COUNTER_FRAC,
    TRUE_DISP_MIN,
    TRUE_N_SAME_MIN,
    TRUE_RANGE_MIN,
    WEAK_BAR_BODY_MAX,
    WIDE_REJECTION_BODY_MAX,
)


def _ratio(num: Any, den: Any) -> float | None:
    if not (_finite(num) and _finite(den) and float(den) > 0):
        return None
    return float(num) / float(den)


def displacement_scale(*, mean_clock_median: Any, atr20: Any) -> float | None:
    parts: list[float] = []
    if _finite(mean_clock_median) and float(mean_clock_median) > 0:
        parts.append(float(mean_clock_median))
    if _finite(atr20) and float(atr20) > 0:
        parts.append(float(ATR_SANITY_FRAC) * float(atr20))
    if not parts:
        return None
    return max(parts)


def opening_close_loc(bars: list[dict[str, Any]]) -> float | None:
    hs = [float(b["h"]) for b in bars if _finite(b.get("h"))]
    ls = [float(b["l"]) for b in bars if _finite(b.get("l"))]
    if not hs or not ls:
        return None
    hi, lo = max(hs), min(ls)
    last = bars[-1].get("c")
    if not _finite(last) or hi <= lo:
        return None
    return (float(last) - lo) / (hi - lo)


def continued_intent_state(bars: list[dict[str, Any]], *, sign: int) -> dict[str, Any]:
    dirs = [int(b.get("direction") or 0) for b in bars]
    bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in bars]
    ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
    first_o = bars[0].get("o") if bars else None
    last_c = bars[-1].get("c") if bars else None
    net = (float(last_c) - float(first_o)) * float(sign) if _finite(first_o) and _finite(last_c) else None
    gross = sum(ranges) if ranges else None
    net_vs_gross = _ratio(net, gross)
    one_bar_share = (max(ranges) / sum(ranges)) if ranges and sum(ranges) > 0 else None
    n_strong = int(sum(1 for x in bodies if x >= float(TRUE_BODY_FRAC_MIN)))
    last_body = bodies[-1] if bodies else None
    last_dir = dirs[-1] if dirs else 0
    first_dir = dirs[0] if dirs else 0
    n_opp = int(sum(1 for d in dirs if d == -int(sign) and d != 0))
    n_same = int(sum(1 for d in dirs if d == int(sign)))
    close_to_close = _finite(net) and float(net) > 0
    same_dir_progression = last_dir == int(sign)
    one_bar_domination = bool(
        _finite(one_bar_share)
        and float(one_bar_share) >= float(ONE_BAR_SHARE_MAX)
        and (n_strong < 2 or min(bodies) < float(WEAK_BAR_BODY_MAX))
    )
    loss_of_progress = (last_dir == -int(sign)) or (
        last_body is not None and float(last_body) < float(LAST_BODY_MIN)
    )
    middle_only_pullback = (
        len(dirs) == 3
        and first_dir == int(sign)
        and last_dir == int(sign)
        and dirs[1] == -int(sign)
        and bodies[1] < float(TRUE_BODY_FRAC_MIN)
    )
    two_sided_internal = n_opp >= 1 and not middle_only_pullback
    loc = opening_close_loc(bars)
    uncommitted = loc is not None and 0.35 <= float(loc) <= 0.65
    substantial_opp = any(
        int(b.get("direction") or 0) == -int(sign) and _finite(b.get("body_over_range")) and float(b["body_over_range"]) >= float(TRUE_BODY_FRAC_MIN)
        for b in bars
    )
    two_sided_balance = bool(n_opp >= 1 and uncommitted and substantial_opp)
    ok = bool(
        close_to_close
        and same_dir_progression
        and (not one_bar_domination)
        and (not loss_of_progress)
        and (not two_sided_balance)
    )
    return {
        "ok": ok,
        "close_to_close_progression": close_to_close,
        "net_vs_gross": net_vs_gross,
        "one_bar_share": one_bar_share,
        "one_bar_domination": one_bar_domination,
        "same_direction_progression": same_dir_progression,
        "loss_of_directional_progress": loss_of_progress,
        "two_sided_internal_path": two_sided_internal,
        "two_sided_balance": two_sided_balance,
        "middle_only_pullback": middle_only_pullback,
        "opening_close_loc": loc,
        "last_body_over_range": last_body,
        "n_same": n_same,
        "n_opp": n_opp,
        "n_strong_bodies": n_strong,
        "is_state_not_magic_threshold": True,
    }


def _clock_range_ratio(bar: dict[str, Any], clock: dict[str, Any] | None) -> float | None:
    med = (clock or {}).get("median")
    rng = bar.get("range")
    return _ratio(rng, med)


def failed_open_seed_visible(bars: list[dict[str, Any]], clocks: list[dict[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {"ok": False, "reason": "no_bars"}
    first = bars[0]
    first_dir = int(first.get("direction") or 0)
    first_body = first.get("body_over_range")
    first_rn = _clock_range_ratio(first, clocks[0] if clocks else None)
    last_dir = int(bars[-1].get("direction") or 0)
    visible_range = _finite(first_rn) and float(first_rn) >= float(FAIL_VISIBLE_RANGE_MIN)
    directional_fail = (
        first_dir != 0
        and _finite(first_body)
        and float(first_body) >= float(FAIL_COUNTER_BODY_FRAC)
        and visible_range
    )
    wide_rejection = visible_range and ((not _finite(first_body)) or float(first_body) <= float(WIDE_REJECTION_BODY_MAX))
    if not (directional_fail or wide_rejection):
        return {"ok": False, "reason": "first_bar_not_visible_failed_attempt"}
    later_reverse = last_dir != 0 and last_dir != first_dir if first_dir != 0 else last_dir != 0
    if first_dir == 0:
        forming = last_dir
        later_reverse = last_dir != 0
    else:
        forming = -first_dir
    if not later_reverse or forming not in (1, -1):
        return {"ok": False, "reason": "no_path_reversal_after_failed_attempt", "first_dir": first_dir}
    # Directional fail: opposite auction starts by trading through the failed print close.
    # Wide doji/range rejection: must reclaim the failed bar's far extreme.
    if directional_fail:
        reclaim = float(first["c"])
    else:
        reclaim = float(first["h"]) if int(forming) > 0 else float(first["l"])
    return {
        "ok": True,
        "forming_sign": int(forming),
        "failed_dir": first_dir if first_dir != 0 else -int(forming),
        "wide_rejection": bool(wide_rejection and not directional_fail),
        "directional_failed_attempt": bool(directional_fail),
        "first_range_over_clock": first_rn,
        "first_body_over_range": float(first_body) if _finite(first_body) else None,
        "failed_extreme": float(first["h"]) if forming < 0 else float(first["l"]),
        "failed_reclaim_level": reclaim,
    }


def opposite_drive_established(
    bars: list[dict[str, Any]],
    *,
    seed: dict[str, Any],
    scale: Any,
) -> dict[str, Any]:
    sign = int(seed.get("forming_sign") or 0)
    reclaim = seed.get("failed_reclaim_level")
    if sign not in (1, -1) or not _finite(reclaim) or len(bars) < 2:
        return {"ok": False, "reason": "invalid_failed_open_seed"}
    last_c = bars[-1].get("c")
    if not _finite(last_c):
        return {"ok": False, "reason": "no_last_close"}
    reclaimed = float(last_c) > float(reclaim) if sign > 0 else float(last_c) < float(reclaim)
    after = bars[1:]
    same = [b for b in after if int(b.get("direction") or 0) == sign]
    n_same = len(same)
    bodies = [float(b["body_over_range"]) for b in same if _finite(b.get("body_over_range"))]
    mean_body = (sum(bodies) / len(bodies)) if bodies else None
    last_dir = int(bars[-1].get("direction") or 0)
    last_continues = last_dir == sign
    ext = seed.get("failed_extreme")
    disp = None
    if _finite(ext) and _finite(last_c) and _finite(scale) and float(scale) > 0:
        raw = (float(last_c) - float(ext)) * float(sign)
        disp = float(raw) / float(scale)
    two_sided = (
        any(int(b.get("direction") or 0) == -sign for b in after)
        and n_same >= 1
        and last_dir == -sign
    )
    ok = bool(
        reclaimed
        and n_same >= int(FAIL_DRIVE_N_SAME_MIN)
        and _finite(mean_body)
        and float(mean_body) >= float(FAIL_DRIVE_BODY_MIN)
        and last_continues
        and _finite(disp)
        and float(disp) >= float(FAIL_DRIVE_DISP_MIN)
        and not two_sided
    )
    return {
        "ok": ok,
        "reclaimed_failed_bar": reclaimed,
        "n_same_after_fail": n_same,
        "mean_body_after_fail": mean_body,
        "last_continues": last_continues,
        "disp_over_scale": disp,
        "two_sided_reset": two_sided,
        "reason": "opposite_completed_5m_directional_auction" if ok else "opposite_drive_not_established",
        "fixed_bar_expiry": False,
    }


def classify_seed(
    bars: list[dict[str, Any]],
    *,
    clock_snap: dict[str, Any],
    atr20: Any,
) -> dict[str, Any]:
    open_bars = list(bars[:3])
    clocks = list(clock_snap.get("same_clock") or [])
    scale = displacement_scale(mean_clock_median=clock_snap.get("mean_clock_median"), atr20=atr20)
    if len(open_bars) < 3 or scale is None:
        return {
            "ok": False,
            "seed": "NO_VALID_DRIVE_SEED",
            "subtype": "FLAT_OR_CRAWL",
            "DIR": 0,
            "reason": "incomplete_5m_or_no_same_clock_or_atr_scale",
            "true_first": False,
        }
    ratios = []
    for i, b in enumerate(open_bars):
        r = _clock_range_ratio(b, clocks[i] if i < len(clocks) else None)
        if _finite(r):
            ratios.append(float(r))
    max_own = max(ratios) if ratios else None
    fail = failed_open_seed_visible(open_bars, clocks)
    # Path precedence: failed-open reversal is classified before TRUE.
    if fail.get("ok"):
        opp = opposite_drive_established(open_bars, seed=fail, scale=scale)
        return {
            "ok": False,
            "seed": "FAILED_OPEN_SEED",
            "subtype": "FAILED_OPEN_SEED",
            "DIR": int(fail["forming_sign"]),
            "reason": "auction_path_failed_open_not_true_first",
            "true_first": False,
            "forming": not bool(opp.get("ok")),
            "opposite_established_at_seed": bool(opp.get("ok")),
            "failed_open": fail,
            "opposite": opp,
            "scale": scale,
            "max_range_over_own_clock": max_own,
            "intent": continued_intent_state(open_bars, sign=int(fail["forming_sign"])),
        }

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for sign in (1, -1):
        first_o = open_bars[0].get("o")
        last_c = open_bars[-1].get("c")
        net = (float(last_c) - float(first_o)) * float(sign) if _finite(first_o) and _finite(last_c) else 0.0
        scored.append((float(net) / float(scale), int(sign), continued_intent_state(open_bars, sign=int(sign))))
    scored.sort(key=lambda x: -x[0])
    best_disp, best_sign, intent = scored[0]
    dirs = [int(b.get("direction") or 0) for b in open_bars]
    n_same = int(sum(1 for d in dirs if d == int(best_sign)))
    n_opp = int(sum(1 for d in dirs if d == -int(best_sign) and d != 0))
    bodies = [float(b["body"]) for b in open_bars if _finite(b.get("body"))]
    ranges = [float(b["range"]) for b in open_bars if _finite(b.get("range"))]
    mean_body = _ratio(sum(bodies), sum(ranges)) if ranges else None
    opp_mags = [abs(float(b["net"])) for b in open_bars if int(b.get("direction") or 0) == -int(best_sign)]
    largest_counter = max(opp_mags) if opp_mags else 0.0
    counter_ok = float(largest_counter) <= float(TRUE_COUNTER_FRAC) * max(float(best_disp) * float(scale), 1e-9)

    true_ok = bool(
        intent.get("ok")
        and float(best_disp) >= float(TRUE_DISP_MIN)
        and _finite(max_own)
        and float(max_own) >= float(TRUE_RANGE_MIN)
        and n_same >= int(TRUE_N_SAME_MIN)
        and _finite(mean_body)
        and float(mean_body) >= float(TRUE_BODY_FRAC_MIN)
        and counter_ok
        and not intent.get("two_sided_balance")
        and not intent.get("one_bar_domination")
        and not intent.get("loss_of_directional_progress")
    )
    if true_ok:
        return {
            "ok": True,
            "seed": "TRUE_OPENING_DRIVE_SEED",
            "subtype": "TRUE_OPENING_DRIVE_SEED",
            "DIR": int(best_sign),
            "reason": "one_sided_or_pullback_path_with_continued_intent",
            "true_first": False,
            "forming": False,
            "intent": intent,
            "disp_over_scale": best_disp,
            "max_range_over_own_clock": max_own,
            "mean_body_over_range": mean_body,
            "scale": scale,
            "atr_sanity_frac": float(ATR_SANITY_FRAC),
        }

    if intent.get("two_sided_balance") or (n_same >= 1 and n_opp >= 1 and not intent.get("middle_only_pullback")):
        subtype = "TWO_SIDED_OPEN"
        reason = "two_sided_opening_auction"
    elif (not _finite(max_own) or float(max_own) < float(FLAT_RANGE_MAX)) or abs(float(best_disp)) < float(FLAT_DISP_MAX):
        subtype = "FLAT_OR_CRAWL"
        reason = "crawl_or_flat_opening"
    else:
        subtype = "MICRO_OR_LEAK"
        reason = "ordinary_or_one_bar_or_lost_intent"
    return {
        "ok": False,
        "seed": "NO_VALID_DRIVE_SEED",
        "subtype": subtype,
        "DIR": 0,
        "reason": reason,
        "true_first": False,
        "intent": intent,
        "disp_over_scale": best_disp,
        "max_range_over_own_clock": max_own,
        "scale": scale,
    }
