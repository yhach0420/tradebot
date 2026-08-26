"""In-stream TRAIL10 on CAUSAL_10S_GRID. Frozen P2-4A contract. No spec change."""
from __future__ import annotations

import bisect
from typing import Any, Optional

from research.e1_x14_board_independent_signal.grid import session_grid_times
from research.trailing10_dynamic_anchor_p2_4a import (
    CHECKPOINT_STALE,
    EVALUABLE,
    INVALID_PRICE,
    NOT_EVALUABLE,
    SESSION_INVALID,
)
from research.trailing10_dynamic_anchor_p2_4a.contract import (
    TrailMachine,
    evaluate_trail,
    trail_checkpoints,
)
from small_paper.v1r_live_dual_lane import canonical_symbol_key


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


class IncrementalTrail10:
    """Causal TRAIL10 state + FALSE→TRUE edge on the ingest watermark."""

    def __init__(self, *, day: str, universe: list[str]) -> None:
        self.day = day
        self.universe = [canonical_symbol_key(s) for s in universe]
        self.grids_spec = session_grid_times(day)
        self.grid_i = 0
        self.times: dict[str, list[float]] = {s: [] for s in self.universe}
        self.prices: dict[str, list[float]] = {s: [] for s in self.universe}
        self.machines: dict[str, TrailMachine] = {s: TrailMachine(s) for s in self.universe}
        self.evals: list[dict[str, Any]] = []
        self.anchors: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.grid_n = 0
        self.evaluable_n = 0
        self.true_n = 0
        self.persist_refire = 0
        self.ne_created_edge = 0
        self.duplicate_edge = 0
        self.checkpoint_future_leak = 0
        self._seen_keys: set[tuple[str, float]] = set()
        self._last_status: dict[str, Optional[str]] = {s: None for s in self.universe}

    def append_tick(self, *, symbol: str, et: float, pay: dict[str, Any]) -> None:
        s = canonical_symbol_key(symbol)
        if s not in self.times:
            return
        px = _f(pay.get("CurrentPrice"))
        if px is None or px <= 0:
            return
        t = float(et)
        if self.times[s] and t + 1e-15 < self.times[s][-1]:
            return
        self.times[s].append(t)
        self.prices[s].append(float(px))

    def _asof(self, s: str, checkpoint: float) -> dict[str, Any]:
        times = self.times[s]
        if not times:
            return {"ok": False, "reason": "CHECKPOINT_MISSING", "event_time": None, "price": None}
        i = bisect.bisect_right(times, float(checkpoint) + 1e-12) - 1
        if i < 0:
            return {"ok": False, "reason": "CHECKPOINT_MISSING", "event_time": None, "price": None}
        src_t = float(times[i])
        if src_t > float(checkpoint) + 1e-12:
            self.checkpoint_future_leak += 1
            return {"ok": False, "reason": "CHECKPOINT_MISSING", "event_time": None, "price": None}
        age = float(checkpoint) - src_t
        if age > 60.0 + 1e-12:
            return {
                "ok": False,
                "reason": CHECKPOINT_STALE,
                "event_time": src_t,
                "price": None,
            }
        px = float(self.prices[s][i])
        if px <= 0:
            return {"ok": False, "reason": INVALID_PRICE, "event_time": src_t, "price": None}
        return {"ok": True, "reason": None, "event_time": src_t, "price": px}

    def _eval_symbol(self, s: str, g: float) -> dict[str, Any]:
        probe = evaluate_trail(symbol=s, g=g, day=self.day, events=[])
        reason = str(probe.get("reason") or "")
        if reason in {SESSION_INVALID, "GRID_MISALIGNED", "CHECKPOINT_MISALIGNED"}:
            return probe
        marks = trail_checkpoints(g)
        events: list[dict[str, Any]] = []
        for c in marks:
            hit = self._asof(s, c)
            if not hit["ok"]:
                return {
                    "status": NOT_EVALUABLE,
                    "reason": hit["reason"] or INVALID_PRICE,
                    "symbol": s,
                    "g": float(g),
                    "session": probe.get("session"),
                    "trail10_state": None,
                    "trend_slope": None,
                    "p0": None,
                    "p10": None,
                }
            events.append(
                {
                    "symbol": s,
                    "event_time": hit["event_time"],
                    "CurrentPrice": hit["price"],
                }
            )
        return evaluate_trail(symbol=s, g=g, day=self.day, events=events)

    def evaluate_grids_until(self, watermark: float) -> None:
        while self.grid_i < len(self.grids_spec):
            sess, gt = self.grids_spec[self.grid_i]
            g = gt.timestamp()
            if g + 1e-12 >= float(watermark):
                break
            self._eval_one_grid(str(sess), float(g))
            self.grid_i += 1

    def _eval_one_grid(self, sess: str, g: float) -> None:
        for s in self.universe:
            self.grid_n += 1
            ev = self._eval_symbol(s, g)
            status = str(ev.get("status") or NOT_EVALUABLE)
            state = ev.get("trail10_state")
            slim = {
                "symbol": s,
                "session": ev.get("session") or sess,
                "g": float(g),
                "status": status,
                "trail10_state": state,
            }
            self.evals.append(slim)
            if status == EVALUABLE:
                self.evaluable_n += 1
                if state is True:
                    self.true_n += 1
            sm = self.machines[s]
            prev_eval = sm.prev_eval
            prev_status = self._last_status[s]
            anc = sm.on_eval(ev, day=self.day)
            if status == EVALUABLE and state is True and prev_eval is True and anc is not None:
                self.persist_refire += 1
            if anc is not None:
                key = (s, round(float(anc.g), 6))
                if key in self._seen_keys:
                    self.duplicate_edge += 1
                self._seen_keys.add(key)
                if prev_status is not None and prev_status != EVALUABLE:
                    self.ne_created_edge += 1
                rec = {
                    "date": self.day,
                    "session": anc.session or sess,
                    "symbol": s,
                    "t0": float(anc.g),
                    "t1": float(anc.g),
                    "g": float(anc.g),
                    "status": "ANCHOR_FIRE",
                    "trend_slope": anc.trend_slope,
                    "p0": anc.p0,
                    "p10": anc.p10,
                    "endpoint_return": None
                    if anc.p0 is None or anc.p0 <= 0 or anc.p10 is None
                    else (float(anc.p10) / float(anc.p0) - 1.0),
                    "snapshot_cutoff": float(anc.g),
                }
                self.anchors.append(rec)
                self.pending.append(rec)
            self._last_status[s] = status

    def due_anchors(self, event_t: float) -> list[dict[str, Any]]:
        due: list[dict[str, Any]] = []
        still: list[dict[str, Any]] = []
        for c in self.pending:
            if float(c["g"]) + 1e-12 < float(event_t):
                c["decision_fire_time"] = float(event_t)
                due.append(c)
            else:
                still.append(c)
        self.pending = still
        return due
