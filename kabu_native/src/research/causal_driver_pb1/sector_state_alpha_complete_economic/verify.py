"""Bind the frozen V1.1 identities before any price row is read."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.sector_state_alpha_complete_economic import (
    EXPECTED_DRIVER_SHA256,
    EXPECTED_E0_ID,
    EXPECTED_E0_SHA256,
    EXPECTED_E1_ID,
    EXPECTED_E1_SHA256,
    EXPECTED_FAMILY_SHA256,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_OCCUPANCY_ID,
    EXPECTED_OCCUPANCY_SHA256,
    EXPECTED_RUNNER_SHA256,
    EXPECTED_STRATEGY_SHA256,
    EXPECTED_TARGET_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.contract import contract
from research.causal_driver_pb1.sector_state_alpha_shadow.identity import verify as verify_alpha
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate


def identity_check() -> dict[str, Any]:
    frozen = contract()
    alpha = verify_alpha()
    gate = identity_gate()
    checks = {
        "strategy_sha_exact": frozen.get("NEW_ALPHA_COMPLETE_STRATEGY_SHA256") == EXPECTED_STRATEGY_SHA256,
        "runner_sha_exact": frozen.get("ECONOMIC_REPLAY_RUNNER_SHA256") == EXPECTED_RUNNER_SHA256,
        "e0_id_exact": frozen.get("adapter", {}).get("E0_ID") == EXPECTED_E0_ID,
        "e0_sha_exact": frozen.get("adapter", {}).get("E0_SHA256") == EXPECTED_E0_SHA256,
        "e1_id_exact": frozen.get("adapter", {}).get("E1_ID") == EXPECTED_E1_ID,
        "e1_sha_exact": frozen.get("adapter", {}).get("E1_SHA256") == EXPECTED_E1_SHA256,
        "occupancy_id_exact": frozen.get("portfolio", {}).get("OCCUPANCY_ENGINE_ID") == EXPECTED_OCCUPANCY_ID,
        "occupancy_sha_exact": frozen.get("portfolio", {}).get("OCCUPANCY_ENGINE_SHA256") == EXPECTED_OCCUPANCY_SHA256,
        "family_sha_exact": frozen.get("alpha_position_invalidation", {}).get("family_sha256") == EXPECTED_FAMILY_SHA256,
        "machine_sha_exact": frozen.get("pb1", {}).get("machine_sha256") == EXPECTED_MACHINE_SHA256 and bool(gate.get("ok")),
        "target_sha_exact": frozen.get("alpha", {}).get("target_sha256") == EXPECTED_TARGET_SHA256 and bool(alpha.get("pass")),
        "m3_driver_sha_exact": frozen.get("alpha", {}).get("driver_sha256") == EXPECTED_DRIVER_SHA256
        and alpha.get("driver_constituent_set_sha256") == EXPECTED_DRIVER_SHA256,
        "q60_rule_unchanged": frozen.get("alpha_position_invalidation", {}).get("admitted", [{}])[0].get("candidate_id") == "Q60_STATE_LOSS",
        "economic_replay_flag_on_contract_is_not_run": frozen.get("economic_replay") == "NOT_RUN",
        "v4_changed": False,
        "v5_created": False,
    }
    return {
        "ok": all(bool(v) for k, v in checks.items() if k not in {"v4_changed", "v5_created"}),
        "checks": checks,
        "symbols": list(alpha.get("symbols") or []),
        "strategy_sha256": frozen.get("NEW_ALPHA_COMPLETE_STRATEGY_SHA256"),
        "runner_sha256": frozen.get("ECONOMIC_REPLAY_RUNNER_SHA256"),
    }
