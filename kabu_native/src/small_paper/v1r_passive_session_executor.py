"""Thin Paper adapter around the existing V1R native push path.

Does not reimplement PENDING, the one-second wait, ask-cross, expiry, or dual-lane admission.
"""
from __future__ import annotations

from typing import Any, Mapping


class V1rPassiveSessionExecutor:
    """PASSIVE_FILL_ENTRY_V1 session executor. Delegates every push."""

    execution_family = "PASSIVE_FILL_ENTRY_V1"
    session_executor = "V1rPassiveSessionExecutor"

    def start_session(self, **_kwargs: Any) -> None:
        return None

    def on_market_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        ctx = event.get("pipeline_ctx")
        payload = event.get("payload") or {}
        symbol = str(event.get("symbol") or "")
        received = event.get("t0_push_received_at")
        message_index = event.get("message_index")
        if ctx is not None:
            from small_paper.pilot_runner import _apply_v1r_native_every_push

            return _apply_v1r_native_every_push(
                ctx,
                payload,
                symbol=symbol,
                t0_push_received_at=received,
                message_index=message_index,
            )
        from small_paper.v1r_native_entry_live import apply_v1r_native_every_push

        return apply_v1r_native_every_push(
            symbol=symbol,
            payload=dict(payload),
            t0_push_received_at=received,
            universe=event.get("universe"),
            trace_dir=event.get("trace_dir"),
            blocked=bool(event.get("blocked", False)),
        )

    def on_session_boundary(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def close_session(self) -> dict[str, Any]:
        return self.snapshot_status()

    def snapshot_status(self) -> dict[str, Any]:
        return {
            "execution_family": self.execution_family,
            "session_executor": self.session_executor,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
