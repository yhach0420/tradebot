"""
Phase 143: Fade-exit first cross-symbol switch block shadow (review / replay only).

After momentum_fade_exit / price_momentum_fade_exit, block only the first
cross-symbol accepted entry for that fade; then allow subsequent entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.switch_old_vs_new_review import MAX_PAIR_SEC

POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_FIRST_SWITCH_BLOCK_SHADOW = (
    "combined_structural_exit_v1_fade_first_switch_block_shadow"
)

FADE_FIRST_SWITCH_TRIGGER_REASONS = frozenset(
    {"momentum_fade_exit", "price_momentum_fade_exit"}
)
BLOCK_REASON = "fade_first_cross_symbol_switch"


def uses_fade_first_switch_block_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_FIRST_SWITCH_BLOCK_SHADOW


def cfg_for_v1_exits(cfg: Any) -> Any:
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    if not uses_fade_first_switch_block_shadow(policy):
        return cfg

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    return _Proxy()


def fade_state_key(old_symbol: str, fade_exit_time: str) -> str:
    return f"{old_symbol}|{fade_exit_time}"


@dataclass
class FadeFirstSwitchBlockState:
    """One fade exit: block at most one cross-symbol accepted entry."""

    old_symbol: str
    fade_exit_time: str
    fade_exit_ts: float
    fade_exit_reason: str
    first_cross_consumed: bool = False
    block_consumed: bool = False
    blocked_new_symbol: str = ""
    blocked_new_entry_time: str = ""

    @classmethod
    def enter(
        cls,
        *,
        old_symbol: str,
        fade_exit_time: str,
        fade_exit_ts: float,
        fade_exit_reason: str,
    ) -> FadeFirstSwitchBlockState:
        return cls(
            old_symbol=old_symbol,
            fade_exit_time=fade_exit_time,
            fade_exit_ts=fade_exit_ts,
            fade_exit_reason=fade_exit_reason,
        )


def enter_log_fields(state: FadeFirstSwitchBlockState) -> dict[str, Any]:
    return {
        "old_symbol": state.old_symbol,
        "old_exit_reason": state.fade_exit_reason,
        "fade_exit_time": state.fade_exit_time,
        "block_reason": BLOCK_REASON,
        "block_consumed": state.block_consumed,
    }


def block_log_fields(
    state: FadeFirstSwitchBlockState,
    *,
    blocked_new_symbol: str,
    blocked_new_entry_time: str,
) -> dict[str, Any]:
    return {
        "old_symbol": state.old_symbol,
        "old_exit_reason": state.fade_exit_reason,
        "fade_exit_time": state.fade_exit_time,
        "blocked_new_symbol": blocked_new_symbol,
        "blocked_new_entry_time": blocked_new_entry_time,
        "block_reason": BLOCK_REASON,
        "block_consumed": True,
    }


def try_block_first_cross_symbol(
    states: Mapping[str, FadeFirstSwitchBlockState],
    *,
    new_symbol: str,
    new_entry_time: str,
    new_entry_ts: float,
) -> tuple[bool, Optional[FadeFirstSwitchBlockState]]:
    """
    Block if this accepted entry is the first cross-symbol attempt within MAX_PAIR_SEC
    after the earliest pending fade (by fade_exit_ts).
    """
    pending = [
        s
        for s in states.values()
        if s.old_symbol != new_symbol and not s.first_cross_consumed
    ]
    if not pending:
        return False, None
    pending.sort(key=lambda s: s.fade_exit_ts)
    for st in pending:
        if new_entry_ts - st.fade_exit_ts > MAX_PAIR_SEC:
            st.first_cross_consumed = True
            continue
        st.first_cross_consumed = True
        st.block_consumed = True
        st.blocked_new_symbol = new_symbol
        st.blocked_new_entry_time = new_entry_time
        return True, st
    return False, None
