"""Causal paper fills for the V20 paper primary.

ENTRY and EXIT decisions stay on the inherited C3 book. This mode only
separates the signal from the fill, and it keeps the morning book open for
exits until 11:30. The live launcher arms it. Order delay was not measured.
"""
from __future__ import annotations

import os

from research.anchor_timing_robustness.grid import hm_epoch
from research.execution_realism_layer_v1.model import ExecutionRealismBook
from small_paper.fixed_support_x1_session import _EPS

CAUSAL_ENV = "TRADEBOT_PAPER_EXECUTION_CAUSAL"
FILL_CLASS = "ASSUMED_CAUSAL_QUOTE"


def causal_mode_requested() -> bool:
    """Isolated switch. Unset means the sealed Paper owner stays in place."""
    return str(os.environ.get(CAUSAL_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


class PaperExecutionCausalBook(ExecutionRealismBook):
    """C3 decisions with fills only on a later observed quote."""

    execution_mode = "PAPER_EXECUTION_CAUSAL_V1"

    def __init__(self) -> None:
        super().__init__(0.0)
        self.default_runtime = False
        self.proves_broker_fill = False
        self.entry_stop_t: float | None = None
        self.exit_request_t: float | None = None
        self.settlement_end_t: float | None = None
        self.exchange_end_t: float | None = None
        self.entries_after_stop = 0
        self.post_stop_signals: list[dict] = []

    def bind_causal_schedule(self, *, day: str, kind: str) -> None:
        if str(kind).lower() == "pm":
            self.bind_safe_pm_flatten(day=str(day))
            self.entry_stop_t = float(hm_epoch(str(day), 15, 20))
            self.exit_request_t = float(hm_epoch(str(day), 15, 20))
            self.settlement_end_t = float(hm_epoch(str(day), 15, 25))
            self.exchange_end_t = float(hm_epoch(str(day), 15, 30))
            return
        start = float(hm_epoch(str(day), 9, 0))
        entry_stop = float(hm_epoch(str(day), 11, 20))
        exit_request = float(hm_epoch(str(day), 11, 25))
        settlement = float(hm_epoch(str(day), 11, 30))
        self.start_session(day=str(day), session="AM", sess_start=start, sess_end=settlement)
        self._manual = True
        self.flatten_start = exit_request
        self.flatten_deadline = settlement
        self.entry_stop_t = entry_stop
        self.exit_request_t = exit_request
        self.settlement_end_t = settlement
        self.exchange_end_t = settlement
        self.executor_session_boundary = settlement
        self.runtime_session_boundary = settlement
        self._assert_boundary()

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        stopped = self.entry_stop_t is not None and float(event_t) + _EPS >= float(self.entry_stop_t)
        if stopped:
            self._entries_closed = True
        seen = int(self.signal_seen_n)
        before = len(self.open_pos)
        cap_before = int(self.counts.get("cap") or 0)
        same_before = int(self.counts.get("same_symbol") or 0)
        super()._on_signal(sym, index, event_t, heap, flush_t)
        if int(self.signal_seen_n) == seen or not stopped:
            return
        opened = len(self.open_pos) > before
        pos = self.open_pos.get(sym) if opened else None
        self.entries_after_stop += 1
        self.post_stop_signals.append(
            {
                "symbol": sym,
                "signal_uid": f"{self.day}|{self.session}|{sym}|{int(index)}",
                "signal_t": float(event_t),
                "entry_signal_t": float(event_t),
                "buy_pending": bool(pos and str(pos.get("exec_state")) == "BUY_PENDING"),
                "buy_filled": bool(pos and float(pos.get("filled_qty") or 0.0) > 0.0),
                "opened": opened,
                "reason": "OPENED_AFTER_ENTRY_STOP" if opened else "ENTRY_STOP",
                "cap_delta": int(self.counts.get("cap") or 0) - cap_before,
                "same_symbol_delta": int(self.counts.get("same_symbol") or 0) - same_before,
                "session": self.session,
            }
        )
        if opened:
            self.violations.append(f"entry_after_stop {sym} {index}")

    def hold_for_settlement(self) -> None:
        """Stop new entries. An open SELL_PENDING position stays occupied."""
        self._entries_closed = True

    def _complete(self, sym: str, pos: dict) -> None:
        super()._complete(sym, pos)
        self._stamp(self.closed[-1] if self.closed else None)

    def _fail_close(self, sym: str) -> None:
        before = len(self.residuals)
        super()._fail_close(sym)
        if len(self.residuals) > before:
            self._stamp(self.residuals[-1])

    def _stamp(self, row: dict | None) -> None:
        if row is None:
            return
        row["fill_class"] = FILL_CLASS
        row["measured_order_latency_sec"] = None
        row["proves_broker_fill"] = False
        row["assumed_order_delay_sec"] = 0.0
