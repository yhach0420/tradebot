"""TRAIL10 DynamicEngine: CLOCK_GRID off; Current _run_anchor/fill/EXIT on. Frozen spec."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.dynamic_anchor_p2_3.engine import DecompEngine
from research.trailing10_full_history_p2_4b.clock import IncrementalTrail10

JST = ZoneInfo("Asia/Tokyo")


class Trail10Engine(DecompEngine):
    """P2-2/P2-3 fire path with TRAIL10 FALSE→TRUE at g. No independent T1. No extra wait."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.clock: Optional[IncrementalTrail10] = None
        self.decision_snapshot_future_leak = 0

    def bind_trail10(self, clock: IncrementalTrail10) -> None:
        self.clock = clock
        self.trading_date = str(clock.day)

    def process_market_push(
        self,
        *,
        symbol: str,
        payload: dict[str, Any],
        event_t: Optional[float] = None,
    ) -> dict[str, Any]:
        t = float(event_t if event_t is not None else 0.0)
        t_ing = time.perf_counter()
        ing = self.ingest_push(symbol=symbol, payload=payload, event_t=t)
        self.last_native_ingest_us = (time.perf_counter() - t_ing) * 1_000_000.0
        if not ing.get("ingested") and ing.get("reason") == "duplicate_sequence":
            ing["fill_checked"] = False
            ing["anchor_fired"] = False
            self.last_fill_check_us = 0.0
            return ing
        if self.clock is not None:
            self.clock.append_tick(symbol=symbol, et=t, pay=payload)
        fired = self.maybe_fire_dynamic(now_t=t)
        t_fill = time.perf_counter()
        fills = self.on_tick_fill_check(event_t=t, payload=payload, symbol=symbol)
        self.last_fill_check_us = (time.perf_counter() - t_fill) * 1_000_000.0
        self._note_occupancy(t)
        ing["fill_checked"] = True
        ing["anchor_fired"] = bool(fired)
        ing["fill_n"] = len(fills or [])
        return ing

    def maybe_fire_dynamic(self, *, now_t: float) -> list[dict[str, Any]]:
        if self.clock is None:
            return []
        self.clock.evaluate_grids_until(float(now_t))
        due = self.clock.due_anchors(float(now_t))
        if not due:
            return []
        by_g: dict[float, list[dict[str, Any]]] = {}
        for c in due:
            if float(c["decision_fire_time"]) <= float(c["g"]) + 1e-12:
                self.decision_snapshot_future_leak += 1
            by_g.setdefault(round(float(c["g"]), 6), []).append(c)
        out: list[dict[str, Any]] = []
        for gk in sorted(by_g):
            cohort = by_g[gk]
            g = float(cohort[0]["g"])
            hour = datetime.fromtimestamp(g, JST).hour
            session = str(cohort[0].get("session") or ("AM" if hour < 12 else "PM"))
            out.extend(
                self._fire_confirmed_cohort(
                    t1=g, session=session, cohort=cohort, decision_time=float(now_t)
                )
            )
        return out
