"""One first-interaction episode per selected symbol × zone × day. True retest requires CLEAR."""
from __future__ import annotations

from typing import Any


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def new_episode(zone: dict[str, Any], *, date: str, symbol: str) -> dict[str, Any]:
    role = str(zone.get("selection_label") or zone.get("role") or "")
    as_resistance = role in ("ACTIVE_RESISTANCE", "BROKEN_SUPPORT_CANDIDATE")
    return {
        "symbol": symbol,
        "date": date,
        "zone_id": zone.get("zone_id"),
        "selection_slot": zone.get("selection_slot"),
        "selection_label": role,
        "role": zone.get("role"),
        "lo": float(zone["lo"]),
        "hi": float(zone["hi"]),
        "center": float(zone["center"]),
        "as_resistance": as_resistance,
        "first_test": False,
        "first_test_time": None,
        "rejection": False,
        "rejection_time": None,
        "break": False,
        "break_time": None,
        "accept2": False,
        "accept2_time": None,
        "accept_count": 0,
        "clear": False,
        "clear_time": None,
        "retest": False,
        "retest_time": None,
        "retest_hold": False,
        "retest_hold_time": None,
        "failed_retest": False,
        "failed_retest_time": None,
        "resolved": False,
        "resolution": None,
        "raw_overlap_bars": 0,
        "emissions": 0,
        "retest_without_clear": 0,
        "retroactive_timestamp": 0,
        "duplicate": False,
        "finished": False,
    }


def _overlap(h: float, l: float, lo: float, hi: float) -> bool:
    return l <= hi and h >= lo


def step_episode(ep: dict[str, Any], *, t: str, o: float, h: float, l: float, c: float) -> None:
    if ep.get("finished") or not _finite(c) or not _finite(h) or not _finite(l):
        return
    lo, hi = float(ep["lo"]), float(ep["hi"])
    res = bool(ep["as_resistance"])
    hit = _overlap(h, l, lo, hi)
    if hit:
        ep["raw_overlap_bars"] += 1

    if not ep["first_test"]:
        if hit:
            ep["first_test"] = True
            ep["first_test_time"] = t
            ep["emissions"] += 1
        else:
            return

    if ep["resolved"] and ep["resolution"] == "REJECT":
        ep["finished"] = True
        return

    if not ep["break"] and not ep["rejection"]:
        if res:
            if c > hi:
                ep["break"] = True
                ep["break_time"] = t
                ep["resolution"] = "BREAK"
                ep["emissions"] += 1
            elif c < lo:
                ep["rejection"] = True
                ep["rejection_time"] = t
                ep["resolution"] = "REJECT"
                ep["resolved"] = True
                ep["emissions"] += 1
                ep["finished"] = True
                return
        else:
            if c < lo:
                ep["break"] = True
                ep["break_time"] = t
                ep["resolution"] = "BREAK"
                ep["emissions"] += 1
            elif c > hi:
                ep["rejection"] = True
                ep["rejection_time"] = t
                ep["resolution"] = "REJECT"
                ep["resolved"] = True
                ep["emissions"] += 1
                ep["finished"] = True
                return
        return

    if not ep["break"]:
        return

    # ACCEPT2: two consecutive closes beyond the far side AFTER the break bar.
    if t != ep.get("break_time") and not ep["accept2"]:
        beyond = (c > hi) if res else (c < lo)
        if beyond:
            ep["accept_count"] = int(ep.get("accept_count") or 0) + 1
            if ep["accept_count"] == 2:
                ep["accept2"] = True
                ep["accept2_time"] = t
                ep["emissions"] += 1
                if ep["accept2_time"] == ep["break_time"]:
                    ep["retroactive_timestamp"] += 1
        else:
            ep["accept_count"] = 0

    cleared_now = (l > hi) if res else (h < lo)
    if cleared_now and not ep["clear"]:
        ep["clear"] = True
        ep["clear_time"] = t
        ep["emissions"] += 1

    if hit and not ep["retest"]:
        if ep["clear"]:
            ep["retest"] = True
            ep["retest_time"] = t
            ep["emissions"] += 1
        elif ep["break"]:
            # Lingering inside the band after break is not a retest.
            pass

    if ep["retest"] and not ep["retest_hold"] and not ep["failed_retest"]:
        fail = (c < lo) if res else (c > hi)
        hold_print = (l > hi) if res else (h < lo)
        if fail:
            ep["failed_retest"] = True
            ep["failed_retest_time"] = t
            ep["resolved"] = True
            ep["emissions"] += 1
            ep["finished"] = True
            return
        if hold_print and t != ep.get("retest_time"):
            ep["retest_hold"] = True
            ep["retest_hold_time"] = t
            ep["resolved"] = True
            ep["emissions"] += 1
            ep["finished"] = True


def close_episode(ep: dict[str, Any]) -> dict[str, Any]:
    if ep["retest"] and not ep["clear"]:
        ep["retest_without_clear"] = 1
    else:
        ep["retest_without_clear"] = int(ep.get("retest_without_clear") or 0)
    if ep["retest_hold"] and ep["failed_retest"]:
        ep["hold_fail_overlap"] = 1
    else:
        ep["hold_fail_overlap"] = 0
    if not ep.get("resolution"):
        if ep["first_test"]:
            ep["resolution"] = "UNRESOLVED"
        else:
            ep["resolution"] = "NO_TEST"
    ep["canonical"] = True
    return ep
