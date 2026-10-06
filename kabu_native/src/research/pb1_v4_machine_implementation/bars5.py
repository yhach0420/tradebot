"""Completed 5m bars from causal 1m. BAR_START clock. No future bar."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, close_loc

FIVE_WINDOWS_OPEN = (("09:00", "09:04"), ("09:05", "09:09"), ("09:10", "09:14"))


def _ratio(num: Any, den: Any) -> float | None:
    if not (_finite(num) and _finite(den) and float(den) > 0):
        return None
    return float(num) / float(den)


def agg_window(rec: dict[str, Any], session_idx: list[int], t0: str, t1: str) -> dict[str, Any] | None:
    o = h = l = c = None
    tv = 0.0
    n = 0
    last_i = None
    for i in session_idx:
        t = str(rec["t"][i])[:5]
        if t < t0 or t > t1:
            continue
        oi, hi, li, ci = rec["o"][i], rec["h"][i], rec["l"][i], rec["c"][i]
        if not (_finite(oi) and _finite(hi) and _finite(li) and _finite(ci)):
            continue
        if o is None:
            o = float(oi)
            h = float(hi)
            l = float(li)
        else:
            h = max(float(h), float(hi))
            l = min(float(l), float(li))
        c = float(ci)
        last_i = int(i)
        if _finite(rec["va"][i]):
            tv += float(rec["va"][i])
        n += 1
    if o is None or h is None or l is None or c is None or n <= 0 or last_i is None:
        return None
    rng = float(h) - float(l)
    body = abs(float(c) - float(o))
    direction = 1 if float(c) > float(o) else (-1 if float(c) < float(o) else 0)
    return {
        "t0": t0,
        "t1": t1,
        "o": float(o),
        "h": float(h),
        "l": float(l),
        "c": float(c),
        "range": rng,
        "body": body,
        "body_over_range": _ratio(body, rng),
        "close_loc": close_loc(h, l, c),
        "direction": direction,
        "net": float(c) - float(o),
        "tv": float(tv),
        "bar_n": n,
        "end_pos": last_i,
    }


def _plus_min(hhmm: str, minutes: int) -> str:
    h = int(hhmm[:2])
    m = int(hhmm[3:5])
    tot = h * 60 + m + int(minutes)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def five_m_windows_am() -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    t0 = "09:00"
    while t0 <= "11:15":
        t1 = _plus_min(t0, 4)
        out.append((t0, t1))
        t0 = _plus_min(t0, 5)
    return tuple(out)


def build_five_m(rec: dict[str, Any], session_idx: list[int], *, through: str | None = None) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for t0, t1 in five_m_windows_am():
        if through is not None and t1 > str(through)[:5]:
            break
        bar = agg_window(rec, session_idx, t0, t1)
        if bar is None:
            continue
        bars.append(bar)
    return bars


def is_five_m_close(t: str) -> bool:
    mm = str(t)[:5]
    if len(mm) < 5 or mm[2] != ":":
        return False
    try:
        minute = int(mm[3:5])
    except ValueError:
        return False
    return minute % 5 == 4


def sequence_for_sign(bars: list[dict[str, Any]], *, sign: int, normal_range: Any) -> dict[str, Any]:
    dirs = [int(b.get("direction") or 0) for b in bars]
    n_same = int(sum(1 for d in dirs if d == int(sign)))
    n_opp = int(sum(1 for d in dirs if d == -int(sign) and d != 0))
    first_o = bars[0]["o"] if bars and _finite(bars[0].get("o")) else None
    last_c = bars[-1]["c"] if bars and _finite(bars[-1].get("c")) else None
    net = (float(last_c) - float(first_o)) if first_o is not None and last_c is not None else None
    dir_disp = (float(net) * float(sign)) if net is not None else None
    opp_mags = [abs(float(b["net"])) for b in bars if int(b.get("direction") or 0) == -int(sign)]
    largest_counter = max(opp_mags) if opp_mags else 0.0
    if n_opp == 0 and n_same >= 2:
        seq = "same-direction"
    elif dirs and dirs[0] == -int(sign) and n_same >= 1 and int(sign) != 0:
        seq = "reversal"
    elif n_same and n_opp:
        seq = "two-sided"
    else:
        seq = "same-direction" if n_same else "two-sided"
    ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
    bodies = [float(b["body"]) for b in bars if _finite(b.get("body"))]
    return {
        "n_same_dir_5m": n_same,
        "n_counter_5m": n_opp,
        "net_directional_displacement": dir_disp,
        "net_displacement_over_normal_5m": _ratio(dir_disp, normal_range),
        "largest_counter_5m": largest_counter,
        "largest_counter_over_normal_5m": _ratio(largest_counter, normal_range),
        "sequence": seq,
        "mean_body_over_range": _ratio(sum(bodies), sum(ranges)) if ranges and sum(ranges) > 0 else None,
        "max_range_over_normal_5m": _ratio(max(ranges) if ranges else None, normal_range),
        "first_bar_direction": dirs[0] if dirs else 0,
        "first_bar_range_over_normal": _ratio(bars[0].get("range") if bars else None, normal_range),
        "first_bar_body_over_range": bars[0].get("body_over_range") if bars else None,
    }
