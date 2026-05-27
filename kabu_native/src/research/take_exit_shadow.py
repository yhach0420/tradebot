"""
Phase 151: Shadow-only take-as-exit on top of combined_structural_exit_v1.

Priority (enforced in replay + live observer):
  stop_hit > take_exit > session_close > combined structural exits
"""

from __future__ import annotations

from typing import Any

from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1

POLICY_COMBINED_STRUCTURAL_EXIT_V1_TAKE_EXIT_SHADOW = (
    "combined_structural_exit_v1_take_exit_shadow"
)

TAKE_EXIT_REASON = "take_exit"

REVIEW_EXIT_ALIASES = frozenset(
    {
        TAKE_EXIT_REASON,
        "morning_session_close",
        "afternoon_session_close",
        "session_end",
    }
)


def uses_take_exit_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_TAKE_EXIT_SHADOW


def is_take_exit_review_reason(reason: str) -> bool:
    return str(reason or "").strip() in REVIEW_EXIT_ALIASES


def observer_event_is_take_exit(oe: Any, *, exit_policy: str) -> bool:
    """True when observer emitted a take-as-exit (shadow) or legacy TAKE signal."""
    if not uses_take_exit_shadow(exit_policy):
        return False
    kind = str(getattr(oe, "kind", "") or "")
    if kind == "take":
        return True
    if kind == "exit":
        ctx = getattr(oe, "context", None) or {}
        return str(ctx.get("exit_reason") or "").strip() == TAKE_EXIT_REASON
    return False


def _cfg_for_v1_signal(cfg: Any) -> Any:
    """Structural fade checks use production combined v1 rules."""
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    if not uses_take_exit_shadow(policy):
        return cfg

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    return _Proxy()
