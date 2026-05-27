"""
Phase 141: Fade-exit cross-symbol switch block shadow (review / replay only).

After momentum_fade_exit / price_momentum_fade_exit, block cross-symbol entries until
session end. No release, no priority exceptions (Phase141 simplicity).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1

POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_SWITCH_BLOCK_SHADOW = (
    "combined_structural_exit_v1_fade_switch_block_shadow"
)

FADE_SWITCH_BLOCK_TRIGGER_REASONS = frozenset(
    {"momentum_fade_exit", "price_momentum_fade_exit"}
)
BLOCK_REASON = "fade_cross_symbol_switch_block"


def uses_fade_switch_block_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_SWITCH_BLOCK_SHADOW


def cfg_for_v1_exits(cfg: Any) -> Any:
    """Keep combined_structural_exit_v1 exits; shadow only gates post-fade switches."""
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    if not uses_fade_switch_block_shadow(policy):
        return cfg

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    return _Proxy()


@dataclass
class FadeSwitchBlockState:
    """Permanent post-fade cross-symbol block for one symbol (no release in Phase141)."""

    old_symbol: str
    fade_exit_time: str
    fade_exit_reason: str
    blocked_count: int = 0

    @classmethod
    def enter(
        cls,
        *,
        old_symbol: str,
        fade_exit_time: str,
        fade_exit_reason: str,
    ) -> FadeSwitchBlockState:
        return cls(
            old_symbol=old_symbol,
            fade_exit_time=fade_exit_time,
            fade_exit_reason=fade_exit_reason,
        )


def block_log_fields(state: FadeSwitchBlockState) -> dict[str, Any]:
    return {
        "old_symbol": state.old_symbol,
        "old_exit_reason": state.fade_exit_reason,
        "fade_exit_time": state.fade_exit_time,
        "blocked_count": state.blocked_count,
        "block_reason": BLOCK_REASON,
    }


def cross_symbol_fade_switch_blocked(
    blocks: Mapping[str, FadeSwitchBlockState],
    *,
    new_symbol: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    True if any faded symbol (different from new_symbol) is still blocking cross-symbol entry.
    Returns (blocked, blocking_old_symbol, old_exit_reason).
    """
    for sym, state in blocks.items():
        if sym != new_symbol:
            return True, sym, state.fade_exit_reason
    return False, None, None
