"""PUSH / board event resolution audit (no interpolation)."""
from __future__ import annotations

import statistics
from typing import Any, Sequence

from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def _gaps(times: Sequence) -> list[float]:
    out = []
    for i in range(1, len(times)):
        dt = (times[i] - times[i - 1]).total_seconds()
        if dt >= 0:
            out.append(dt)
    return out


def _pct_le(xs: Sequence[float], thr: float) -> float | None:
    if not xs:
        return None
    return round(sum(1 for x in xs if x <= thr) / len(xs), 4)


def audit_resolution(push_by_day: dict[str, dict[str, list[PushTick]]], days: Sequence[str]) -> dict[str, Any]:
    push_gaps: list[float] = []
    price_gaps: list[float] = []
    board_gaps: list[float] = []
    same_quote_hold: list[float] = []
    n_sym = 0
    for day in days:
        by = push_by_day.get(day) or {}
        for sym, ticks in by.items():
            if len(ticks) < 20:
                continue
            n_sym += 1
            # sample first 500 gaps per symbol for speed
            ts = [t.event_time for t in ticks[:800]]
            push_gaps.extend(_gaps(ts)[:500])
            # price updates: when current_price changes
            pts = []
            last_px = None
            for t in ticks[:800]:
                if last_px is None or t.current_price != last_px:
                    pts.append(t.event_time)
                    last_px = t.current_price
            price_gaps.extend(_gaps(pts)[:400])
            # board updates: bid/ask change
            bts = []
            last_b = last_a = None
            hold0 = None
            for t in ticks[:800]:
                if t.bid != last_b or t.ask != last_a:
                    if hold0 is not None and last_b is not None:
                        same_quote_hold.append((t.event_time - hold0).total_seconds())
                    bts.append(t.event_time)
                    hold0 = t.event_time
                    last_b, last_a = t.bid, t.ask
            board_gaps.extend(_gaps(bts)[:400])

    def dist(xs: list[float]) -> dict[str, Any]:
        if not xs:
            return {"n": 0}
        s = sorted(xs)
        return {
            "n": len(xs),
            "median": round(statistics.median(s), 4),
            "p90": round(s[int(0.9 * (len(s) - 1))], 4),
            "mean": round(sum(s) / len(s), 4),
            "le_100ms": _pct_le(s, 0.1),
            "le_500ms": _pct_le(s, 0.5),
            "le_1s": _pct_le(s, 1.0),
        }

    push_d = dist(push_gaps)
    # R3 interpretation
    r3_note = (
        "R3 next-event Bid typically reflects next PUSH/1s-bar wait, not a controlled 100–500ms order delay. "
        f"PUSH median gap={push_d.get('median')}s; rate≤500ms={push_d.get('le_500ms')}."
    )
    insufficient = (push_d.get("le_500ms") or 0) < 0.05
    return {
        "symbols_sampled": n_sym,
        "push_interval": push_d,
        "price_update_interval": dist(price_gaps),
        "board_update_interval": dist(board_gaps),
        "same_quote_hold": dist(same_quote_hold),
        "r3_interpretation": r3_note,
        "r3_is_order_delay": False,
        "r3_is_next_push_wait": True,
        "insufficient_event_resolution": insufficient,
        "verdict": "INSUFFICIENT_EVENT_RESOLUTION" if insufficient else "RESOLUTION_AUDIT_READY",
        "note": "No exchange vs local received_at field in PushTick cache; local event_time only.",
    }
