"""Bar-clock helpers. Lunch skip. Same-bar prohibition. No Bid/Ask invention."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch, parse_hhmm
from research.pb1_v4_complete_strategy_build_and_economic_validation import SESSION_FLAT


def hhmm_to_min(hhmm: str) -> int | None:
    parsed = parse_hhmm(hhmm)
    if parsed is None:
        return None
    return int(parsed[0]) * 60 + int(parsed[1])


def event_key(date: str, hhmm: str) -> tuple[str, int]:
    m = hhmm_to_min(hhmm)
    return (str(date), int(m if m is not None else 10**9))


def _finite_open(rec: dict[str, Any], i: int) -> bool:
    try:
        o = float(rec["o"][i])
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    return o == o and o > 0


def next_open_after(rec: dict[str, Any], *, after_t: str, allow_pm: bool, session_flat: str = SESSION_FLAT) -> dict[str, Any] | None:
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    after = str(after_t)[:5]
    after_m = hhmm_to_min(after)
    if after_m is None:
        return None
    for i, t in enumerate(times):
        tm = hhmm_to_min(t)
        if tm is None:
            continue
        if tm <= after_m:
            continue
        if in_lunch(t):
            continue
        if (not allow_pm) and t >= "11:30":
            continue
        if t > str(session_flat)[:5]:
            continue
        if not _finite_open(rec, i):
            continue
        return {"i": i, "t": t, "px": float(rec["o"][i]), "same_bar": False}
    return None


def flatten_open(rec: dict[str, Any], *, fill_t: str, session_flat: str = SESSION_FLAT) -> dict[str, Any] | None:
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    fill_m = hhmm_to_min(str(fill_t)[:5])
    flat = str(session_flat)[:5]
    hit = None
    for i, t in enumerate(times):
        if in_lunch(t):
            continue
        if t > flat:
            continue
        if fill_m is not None:
            tm = hhmm_to_min(t)
            if tm is None or tm <= fill_m:
                continue
        if not _finite_open(rec, i):
            continue
        if t == flat:
            return {"i": i, "t": t, "px": float(rec["o"][i]), "same_bar": False}
        hit = {"i": i, "t": t, "px": float(rec["o"][i]), "same_bar": False}
    return hit
