"""Disable Early Guard trigger only. Continuation Gate / 750 / ENTRY unchanged."""
from __future__ import annotations

from typing import Any

from research.v1r_exit_v2_asymmetric.policy import apply_architecture
from small_paper.v1r_exit_v2_contract import frozen_continuation
import small_paper.v1r_live_dual_lane as vdl


def apply_arch_e_guard_off(bundle: dict[str, Any]) -> dict[str, Any]:
    return apply_architecture(bundle, arch="E", guard=None, cont_rule=frozen_continuation())


def attach_early_guard_off() -> None:
    """Patch Dual Lane Arch E lookup for this process. Research wrap only."""
    vdl.apply_arch_e_to_bundle = apply_arch_e_guard_off  # type: ignore[method-assign]
