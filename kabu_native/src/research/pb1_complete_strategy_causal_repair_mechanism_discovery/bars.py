"""Causal 1m/3m/5m bars and session features. Completed bars only."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.fixed_universe_historical_foundation_v1.technical import ema, session_vwap, sma
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of
from research.pb1_v4_machine_implementation.bars5 import agg_window


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def hhmm_add_min(hhmm: str, add: int) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    tot = h * 60 + m + int(add)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def session_windows(*, width: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    m = 9 * 60
    while m + width - 1 < 11 * 60 + 30:
        t0 = f"{m // 60:02d}:{m % 60:02d}"
        t1m = m + width - 1
        t1 = f"{t1m // 60:02d}:{t1m % 60:02d}"
        out.append((t0, t1))
        m += width
    m = 12 * 60 + 30
    while m + width - 1 <= 15 * 60 + 20:
        t0 = f"{m // 60:02d}:{m % 60:02d}"
        t1m = m + width - 1
        t1 = f"{t1m // 60:02d}:{t1m % 60:02d}"
        out.append((t0, t1))
        m += width
    return out


def build_nm(rec: dict[str, Any], *, width: int) -> list[dict[str, Any]]:
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    sidx = session_idx_of(times)
    bars: list[dict[str, Any]] = []
    for t0, t1 in session_windows(width=int(width)):
        bar = agg_window(rec, sidx, t0, t1)
        if bar is None:
            continue
        bars.append(bar)
    return bars


def enrich_1m(rec: dict[str, Any]) -> dict[str, Any]:
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    n = len(times)
    o = np.asarray(rec.get("o") or [], dtype=float)
    h = np.asarray(rec.get("h") or [], dtype=float)
    l = np.asarray(rec.get("l") or [], dtype=float)
    c = np.asarray(rec.get("c") or [], dtype=float)
    v = np.asarray(rec.get("v") or [], dtype=float)
    va = np.asarray(rec.get("va") or [], dtype=float)
    if int(c.size) != n:
        return {"t": times, "o": o, "h": h, "l": l, "c": c, "vwap": np.array([]), "ema9": np.array([]), "sma5": np.array([])}
    vw = session_vwap(h, l, c, v, va if va.size == n else None)
    e9 = ema(c, 9)
    s5 = sma(c, 5)
    return {"t": times, "o": o, "h": h, "l": l, "c": c, "vwap": vw, "ema9": e9, "sma5": s5}


def or_levels(rec: dict[str, Any]) -> dict[str, Any] | None:
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    frozen = freeze_or15(times, rec.get("h") or [], rec.get("l") or [], session_idx_of(times))
    if not frozen or not _finite(frozen.get("or_high")) or not _finite(frozen.get("or_low")):
        return None
    return frozen


def open_0900(rec: dict[str, Any]) -> float | None:
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    for i, t in enumerate(times):
        if t == "09:00" and _finite((rec.get("o") or [None])[i] if i < len(rec.get("o") or []) else None):
            return float(rec["o"][i])
    for i, t in enumerate(times):
        if t >= "09:00" and not in_lunch(t) and _finite(rec["o"][i]):
            return float(rec["o"][i])
    return None


def ema_on_closes(closes: list[float], period: int) -> list[float]:
    arr = ema(np.asarray(closes, dtype=float), int(period))
    return [float(x) if x == x else float("nan") for x in arr]
