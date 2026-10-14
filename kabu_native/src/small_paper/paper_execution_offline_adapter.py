"""Offline switch for the causal paper book.

The sealed Paper resolver is not called from here when the switch is on.
An unset switch returns the sealed owner unchanged. The live runner does
not import this module.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook, causal_mode_requested

OFFLINE_ONLY = "CAUSAL_PAPER_EXECUTION_OFFLINE_ONLY"


def resolve_offline_paper_executor(
    activation: Mapping[str, Any],
    *,
    strategy_mode: Optional[str] = None,
    offline: bool = False,
) -> Any:
    """Replay helper. The live paper launcher does not call this.

    Offline without the explicit causal flag stays on the sealed owner.
    The paper flag alone does not enter this function.
    """
    if causal_mode_requested() and offline:
        book = PaperExecutionCausalBook()
        book.live_order_enabled = False
        book.activation_id = str(activation.get("activation_id") or "")
        return book
    from small_paper.paper_session_executor import resolve_sealed_session_executor

    return resolve_sealed_session_executor(activation, strategy_mode=strategy_mode)
