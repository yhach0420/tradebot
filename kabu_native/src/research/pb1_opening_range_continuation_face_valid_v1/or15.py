"""OR15 freeze from completed 09:00–09:14 1-minute bars. No later modification."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.pb1_opening_range_continuation_face_valid_v1 import OR_END, OR_START, SESSION_FLAT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def session_idx_of(times: list[str]) -> list[int]:
    out: list[int] = []
    for i, t in enumerate(times):
        ts = str(t)
        if in_lunch(ts) or ts >= SESSION_FLAT:
            continue
        out.append(i)
    return out


def freeze_or15(times: list[str], high: Any, low: Any, session_idx: list[int]) -> dict[str, Any]:
    hs: list[float] = []
    ls: list[float] = []
    bars: list[str] = []
    for i in session_idx:
        t = str(times[i])
        if t < OR_START or t > OR_END:
            continue
        h = high[i]
        l = low[i]
        if not (_finite(h) and _finite(l)):
            continue
        hs.append(float(h))
        ls.append(float(l))
        bars.append(t)
    complete = len(bars) >= 15 and bars[0] == OR_START and bars[-1] == OR_END
    if not complete or not hs or not ls:
        return {
            "ok": False,
            "or_high": float("nan"),
            "or_low": float("nan"),
            "bar_n": len(bars),
            "bars": bars,
            "known_from": None,
        }
    return {
        "ok": True,
        "or_high": float(max(hs)),
        "or_low": float(min(ls)),
        "bar_n": len(bars),
        "bars": bars,
        "known_from": "09:15",
    }


def window_tv(times: list[str], va: Any, session_idx: list[int], t0: str, t1: str) -> float:
    s = 0.0
    n = 0
    for i in session_idx:
        t = str(times[i])
        if t0 <= t <= t1 and _finite(va[i]):
            s += float(va[i])
            n += 1
    return s if n else float("nan")
