"""Frozen complete-strategy contract. Economic replay is not run here."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.sector_state_alpha_complete_precommit import ANALYSIS_ID
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.isolation import NATIVE
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.runner import (
    FINAL_ALPHA_CLOCK,
    LAST_ENTRY_ADMISSION_CLOCK,
)
from research.causal_driver_pb1.sector_state_alpha_shadow import (
    M3_TARGET_SHA256,
    M3_TARGETS,
    PRECOMMIT_SHA256,
    Q60_OFF,
    Q80_ON,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_shadow_registration import M3_SHA256
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import (
    DISCOVERY_DATE_SHA256,
    DISCOVERY_FOLD_SHA256,
    FV_ELIGIBLE_DAY_SHA256,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    CAP,
    COST_MODEL_ID,
    EXPECTED_MACHINE_SHA256,
    FROZEN_ENTRY_IDENTITY,
    HISTORICAL_FILL_ID,
    LUNCH_POLICY,
    OPS_EXIT_ID,
    SESSION_FLAT,
    SHARES,
    TECHNICAL_EXIT_ID,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import COMPLETE_STRATEGY_IDENTITY
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate


def alpha_invalidation_family() -> dict[str, Any]:
    """One mechanism. Q60 is adopted explicitly as position-thesis death, not as the signal episode."""
    body = {
        "id": "ALPHA_POSITION_THESIS_INVALIDATION_V1",
        "admitted": [{
            "candidate_id": "Q60_STATE_LOSS",
            "class": "Q60_STATE_LOSS",
            "threshold": Q60_OFF,
            "threshold_source": "PARENT_DEV_FROZEN_QUINTILES",
            "persistence_observations": 1,
            "requires_valid_driver": True,
            "active_data_gap_is_thesis_death": False,
            "research_clock_end_is_thesis_death": False,
            "signal_episode_event": "ALPHA_OFF",
            "position_event": "ALPHA_THESIS_INVALIDATION",
            "same_event": False,
            "exit_fill": "next_executable_bar_open_after_invalidation_clock",
        }],
        "not_admitted": [
            "CONSECUTIVE_Q60_PERSISTENCE_GRID",
            "LOSS_OF_POSITIVE_BREADTH",
            "FRESHNESS_FAILURE_AS_EXIT",
            "RESEARCH_WINDOW_END_AS_THESIS_DEATH",
        ],
        "grid_search": False,
    }
    out = dict(body)
    out["family_sha256"] = sha256_obj(body)
    return out


SUPERSEDED_STRATEGY_SHA256 = "3788d1e9201e3754d40de7386dffa52d1cdabce18260aa2ccfe09518b45a71d6"
PRIOR_FAMILY_SHA256 = "2829a8be6441d4b437d46edb36a27c455ac21c352248e548a9b35d4070329424"
V4_ROOT = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
OCCUPANCY_FILE = NATIVE / "src" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation" / "portfolio.py"
RUNNER_FILE = NATIVE / "src" / "research" / "causal_driver_pb1" / "sector_state_alpha_complete_precommit" / "runner.py"


def _file_sha(path) -> str:
    return sha256_bytes(path.read_bytes())


def component_identities() -> dict[str, Any]:
    from research.pb1_v4_clarified_machine_correction_v4 import (
        E1_BODY_N1M,
        E1_NET_N1M,
        E1_RANGE_N1M,
        EXEC_1M_CONFIRMED,
        EXEC_5M_DIRECT,
    )

    walk_sha = _file_sha(V4_ROOT / "walk.py")
    continuation_sha = _file_sha(V4_ROOT / "continuation.py")
    execution_sha = _file_sha(V4_ROOT / "execution.py")
    e0 = {
        "E0_ID": EXEC_5M_DIRECT,
        "source_file": "src/research/pb1_v4_clarified_machine_correction_v4/continuation.py",
        "emitter_file": "src/research/pb1_v4_clarified_machine_correction_v4/walk.py",
        "continuation_sha256": continuation_sha,
        "emitter_sha256": walk_sha,
        "machine_sha256": EXPECTED_MACHINE_SHA256,
        "rule": "from THESIS_READY, completed 5m hold plus continuation, then next 1m open",
    }
    e1 = {
        "E1_ID": EXEC_1M_CONFIRMED,
        "source_file": "src/research/pb1_v4_clarified_machine_correction_v4/execution.py",
        "emitter_file": "src/research/pb1_v4_clarified_machine_correction_v4/walk.py",
        "execution_sha256": execution_sha,
        "emitter_sha256": walk_sha,
        "machine_sha256": EXPECTED_MACHINE_SHA256,
        "E1_NET_N1M": E1_NET_N1M,
        "E1_RANGE_N1M": E1_RANGE_N1M,
        "E1_BODY_N1M": E1_BODY_N1M,
        "rule": "from the same THESIS_READY, 1m at the pre-identified level, then next 1m open",
    }
    occupancy = {
        "OCCUPANCY_ENGINE_ID": "PB1_V4_REPLAY_OCCUPANCY_V1",
        "source_file": "src/research/pb1_v4_complete_strategy_build_and_economic_validation/portfolio.py",
        "source_sha256": _file_sha(OCCUPANCY_FILE),
        "cap": int(CAP),
        "same_symbol": True,
        "slot_release": "EXIT_FILL",
        "reentry": "after_slot_release_only",
        "event_priority": ["EXIT", "FILL", "ADMIT"],
    }
    occupancy["OCCUPANCY_ENGINE_SHA256"] = sha256_obj(occupancy)
    bundle = {
        "namespace": "ECONOMIC_REPLAY_RUNNER_V1",
        "files": {
            "runner.py": _file_sha(RUNNER_FILE),
            "portfolio.py": _file_sha(OCCUPANCY_FILE),
            "fill.py": _file_sha(OCCUPANCY_FILE.parent / "fill.py"),
            "exits.py": _file_sha(OCCUPANCY_FILE.parent / "exits.py"),
            "clocks.py": _file_sha(OCCUPANCY_FILE.parent / "clocks.py"),
            "continuation.py": continuation_sha,
            "execution.py": execution_sha,
        },
    }
    return {
        "e0": e0,
        "e1": e1,
        "E0_SHA256": sha256_obj(e0),
        "E1_SHA256": sha256_obj(e1),
        "occupancy": occupancy,
        "ECONOMIC_REPLAY_RUNNER_SHA256": sha256_obj(bundle),
    }


def contract() -> dict[str, Any]:
    gate = identity_gate()
    inv = alpha_invalidation_family()
    ids = component_identities()
    missing: list[str] = []
    if inv.get("family_sha256") != PRIOR_FAMILY_SHA256:
        missing.append("ALPHA_INVALIDATION_FAMILY_DRIFT")
    if not gate.get("ok"):
        missing.append("PB1_V4_IDENTITY")
    if TECHNICAL_EXIT_ID != "PB1_V4_THESIS_LOST_NEXT_OPEN":
        missing.append("PB1_STOCK_THESIS_INVALIDATION")
    if OPS_EXIT_ID != "SESSION_FLAT_1520" or SESSION_FLAT != "15:20":
        missing.append("SESSION_CLOSE")
    if HISTORICAL_FILL_ID != "HISTORICAL_NEXT_BAR_OPEN_EXECUTION" or COST_MODEL_ID != "X1_8BPS_EXECUTION_STRESS":
        missing.append("EXECUTION_CONTRACT")
    if int(SHARES) != 100 or int(CAP) != 5:
        missing.append("SIZE_OR_CAP")
    if list(M3_TARGETS) != ["6590", "6787", "6861", "6941", "6961"]:
        missing.append("TARGET_SET")
    body = {
        "analysis_id": ANALYSIS_ID,
        "supersedes": SUPERSEDED_STRATEGY_SHA256,
        "superseded_before_economic_replay": True,
        "cancelled_next": "ACTIVATE_SECTOR_STATE_ALPHA_SIGNAL_SHADOW_PROSPECTIVE_V1",
        "alpha": {
            "parent": "M3",
            "test_id": "BREADTH|SECTOR_3650|w1|h3",
            "family": "SECTOR3650_BREADTH_W1_UP",
            "direction": "LONG",
            "driver_n": 30,
            "driver_sha256": M3_SHA256,
            "target_sha256": M3_TARGET_SHA256,
            "targets": list(M3_TARGETS),
            "q80_on": Q80_ON,
            "q60_off": Q60_OFF,
            "clock": "09:10-11:25",
            "precommit_sha256": PRECOMMIT_SHA256,
            "validated_transmission_sha256": VALIDATED_TRANSMISSION_SHA256,
            "q60_signal_off_is_not_position_exit": True,
        },
        "alpha_position_invalidation": inv,
        "pb1": {
            "identity": FROZEN_ENTRY_IDENTITY,
            "machine_sha256": EXPECTED_MACHINE_SHA256,
            "role": "STATE_FILTER_CONFIRMATION",
            "generates_alpha": False,
            "thesis_invalidation_id": TECHNICAL_EXIT_ID,
            "thesis_lost_absorbing": True,
            "exit_fill": "next causal open after THESIS_LOST, lunch skipped, PM allowed until 15:20",
            "v4_changed": False,
            "v5_created": False,
            "parent_complete_strategy_identity": COMPLETE_STRATEGY_IDENTITY,
        },
        "adapter": {
            "order": "ALPHA_THEN_PB1_THEN_E0_E1",
            "thesis_ready_without_alpha": "NO_TRADE",
            "direction_mismatch": "ALPHA_DIRECTION_MISMATCH",
            "stale_pb1_before_alpha_on": "NOT_CONSUMED",
            "E0_ID": ids["e0"]["E0_ID"],
            "E0_SHA256": ids["E0_SHA256"],
            "E1_ID": ids["e1"]["E1_ID"],
            "E1_SHA256": ids["E1_SHA256"],
            "e0": ids["e0"],
            "e1": ids["e1"],
            "new_timing_indicator": False,
            "both_e0_and_e1_remain": True,
        },
        "observability": {
            "final_alpha_clock": FINAL_ALPHA_CLOCK,
            "last_entry_admission_clock": LAST_ENTRY_ADMISSION_CLOCK,
            "window_end_event": "ALPHA_OBSERVABILITY_WINDOW_END",
            "window_end_classification": "FAIL_CLOSE",
            "window_end_is_economic_thesis_loss": False,
            "alpha_position_pm_carry": False,
            "gap_during_window_kills_thesis": False,
            "gap_blocks_new_entry": True,
            "gap_through_final_clock": "ALPHA_OBSERVABILITY_WINDOW_END",
            "qualification_at_1125": "REJECT_ALPHA_OBSERVABILITY_END_BEFORE_FILL",
            "fill_admitted_at_1124_is_valid": True,
            "final_clock_order": [
                "FAIL_CLOSE_INVALID_DATA",
                "FINAL_ALPHA_DRIVER",
                "Q60_STATE_LOSS_IF_APPLICABLE",
                "THESIS_INVALIDATION",
                "EXIT_SCHEDULING",
                "FILLS_ADMITTED_AT_OR_BEFORE_1124",
                "BIND_THOSE_FILLS_TO_1125_STATE",
                "ALPHA_OBSERVABILITY_WINDOW_END_IF_THESIS_ALIVE",
                "SLOT_RELEASE_ON_EXIT_FILL",
                "NO_NEW_ADMIT_AFTER_1124",
            ],
        },
        "execution": {
            "historical": HISTORICAL_FILL_ID,
            "classification": "RESEARCH_EXECUTION_APPROXIMATION",
            "cost_bps_primary": 8,
            "cost_bps_diagnostic": [8, 12, 16],
            "best_cost_selection": False,
            "same_bar_fill": False,
            "mid_fill": False,
            "shares": int(SHARES),
            "lunch": LUNCH_POLICY,
        },
        "position": {
            "theses": ["ALPHA_THESIS", "PB1_STOCK_THESIS"],
            "exit_on": "FIRST_CAUSAL_INVALIDATION",
            "same_clock_dual_invalidation": "ONE_EXIT_BOTH_REASONS_RECORDED",
            "session_flat": OPS_EXIT_ID,
            "session_flat_clock": SESSION_FLAT,
            "overnight": False,
            "alpha_position_pm_carry": False,
            "session_flat_is_fallback_not_normal_m3_exit": True,
        },
        "portfolio": {
            "OCCUPANCY_ENGINE_ID": ids["occupancy"]["OCCUPANCY_ENGINE_ID"],
            "OCCUPANCY_ENGINE_SHA256": ids["occupancy"]["OCCUPANCY_ENGINE_SHA256"],
            "engine": ids["occupancy"],
            "cap": int(CAP),
            "same_symbol": True,
            "slot_release": "EXIT_FILL",
            "reentry": "after_slot_release_only",
            "independent_trade_sum": False,
            "existing_event_priority": ["EXIT", "FILL", "ADMIT"],
        },
        "event_priority": [
            "FAIL_CLOSE_INVALID_DATA",
            "THESIS_INVALIDATION",
            "EXIT",
            "SLOT_RELEASE",
            "ENTRY_FILL",
            "ALPHA_UPDATE",
            "ADMIT",
        ],
        "chronology": {
            "label": "RETROSPECTIVE_DEVELOPMENT_ECONOMIC_FEASIBILITY",
            "not": ["HOLDOUT", "FROZEN_VALIDATION", "PROSPECTIVE", "FINAL_CONFIRMATION"],
            "exposed_discovery_date_sha256": DISCOVERY_DATE_SHA256,
            "discovery_fold_sha256": DISCOVERY_FOLD_SHA256,
            "late_date_sha256": FV_ELIGIBLE_DAY_SHA256,
            "folds": {
                "EARLY": "20240917-20251126",
                "MIDDLE": "20251127-20260421",
                "LATE": "20260422-20260911",
            },
            "not_confined_to_one_fold": "at_least_two_folds_net_pnl_gt_0",
        },
        "economic_gate": {
            "frozen_before_pnl": True,
            "net_pnl_gt_0": True,
            "profit_factor_gt_1": True,
            "mean_net_trade_gt_0": True,
            "median_net_trade_gte_0": True,
            "max_drawdown_finite_and_reported": True,
            "chronology_not_one_fold": True,
            "cost_12bps_net_pnl_gt_0": True,
            "primary_cost_bps": 8,
            "lowered_after_results": False,
        },
        "economic_replay": "NOT_RUN",
        "ECONOMIC_REPLAY_RUNNER_SHA256": ids["ECONOMIC_REPLAY_RUNNER_SHA256"],
        "shadow_registration": "FUTURE_RUNTIME_INFRASTRUCTURE_ONLY",
        "prospective_data_opened": False,
    }
    if inv["admitted"][0]["position_event"] == inv["admitted"][0]["signal_episode_event"]:
        missing.append("ALPHA_THESIS_INVALIDATION")
    out = dict(body)
    out["missing_contracts"] = missing
    out["ready"] = not missing
    out["NEW_ALPHA_COMPLETE_STRATEGY_SHA256"] = sha256_obj(body) if not missing else None
    return out
