"""Pinned C4 Full Strategy V2 contract. Fold-local eligibility parity before economics."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    execution_contract,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.c4_portfolio_crowding_full_strategy_v2 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_E,
    CONTROL_ARM_N,
    DEV_CLASSIFICATION,
    DEVELOPMENT_DAYS,
    ELIGIBLE_ENTRY_N,
    EXECUTION_ID,
    EXIT_N,
    FROZEN_ARM_IDS,
    FROZEN_ELIGIBLE_IDS,
    MATCHED_STRATEGY_N,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NEXT_IF_CANARY,
    NEXT_IF_E,
    POSITION_CAP,
    SHARES,
    TOTAL_ARM_N,
    TREATMENT_ARM_N,
)
from research.c4_portfolio_crowding_precommit_v1 import KEPT_EXIT_IDS
from research.c4_portfolio_crowding_precommit_v2 import CONTROL_POLICY_ID, TREATMENT_POLICY_ID
from research.c4_portfolio_crowding_precommit_v2.spec import (
    arm_id,
    attribution_stability_gates,
    fold_c4_contract,
    incremental_gate,
)
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID, CANARY_SOURCE

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "prior.py",
    "fold_parity.py",
    "harvest.py",
    "streams.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_arm_id(cid: str) -> dict[str, Any]:
    ctrl = f"__{CONTROL_POLICY_ID}__{EXECUTION_ID}__"
    treat = f"__{TREATMENT_POLICY_ID}__{EXECUTION_ID}__"
    if ctrl in cid:
        entry_id, exit_id = cid.split(ctrl, 1)
        return {
            "ENTRY_ID": entry_id,
            "POLICY_ID": CONTROL_POLICY_ID,
            "EXECUTION": EXECUTION_ID,
            "EXIT_ID": exit_id,
            "ARM": "ARM_CONTROL",
            "WINNER_ELIGIBLE": False,
        }
    if treat in cid:
        entry_id, exit_id = cid.split(treat, 1)
        return {
            "ENTRY_ID": entry_id,
            "POLICY_ID": TREATMENT_POLICY_ID,
            "EXECUTION": EXECUTION_ID,
            "EXIT_ID": exit_id,
            "ARM": "ARM_C4_TREATMENT",
            "WINNER_ELIGIBLE": True,
        }
    raise RuntimeError(f"UNPARSEABLE_ARM:{cid}")


def matched_control_id(treatment_id: str) -> str:
    mark_t = f"__{TREATMENT_POLICY_ID}__"
    mark_c = f"__{CONTROL_POLICY_ID}__"
    if mark_t not in treatment_id:
        raise RuntimeError(f"NOT_TREATMENT:{treatment_id}")
    return treatment_id.replace(mark_t, mark_c, 1)


def treatment_ids() -> list[str]:
    return [arm_id(eid, TREATMENT_POLICY_ID, z) for eid in FROZEN_ELIGIBLE_IDS for z in KEPT_EXIT_IDS]


def control_ids() -> list[str]:
    return [arm_id(eid, CONTROL_POLICY_ID, z) for eid in FROZEN_ELIGIBLE_IDS for z in KEPT_EXIT_IDS]


def candidate_ids() -> list[str]:
    return list(FROZEN_ARM_IDS)


def fold_local_eligibility_contract() -> dict[str, Any]:
    return {
        "NAME": "FOLD_LOCAL_STRUCTURAL_ELIGIBILITY_PARITY",
        "RUN_BEFORE_ECONOMICS": True,
        "TRAINING_BLOCKS_ONLY": True,
        "HELD_OUT_INTERVENTION_FORBIDDEN": True,
        "PNL_FORBIDDEN": True,
        "PF_FORBIDDEN": True,
        "FILLS_FORBIDDEN": True,
        "TRADES_FORBIDDEN": True,
        "EXIT_RESULT_FORBIDDEN": True,
        "CONTROL_RESULT_FORBIDDEN": True,
        "TREATMENT_RESULT_FORBIDDEN": True,
        "RULE": "TRAIN_C4_ATTRIBUTION_ELIGIBLE iff TRAIN_C4_INFORMATIVE_BLOCK_N >= 3",
        "INFORMATIVE_BLOCK": "C4_REJECT_N_BY_BLOCK > 0",
        "EXPECTED_TRAIN_ELIGIBLE_IDS": list(FROZEN_ELIGIBLE_IDS),
        "REQUIRED_ELIGIBLE_N_EACH_FOLD": 5,
        "ANY_HANDOFF_TRAIN_ELIGIBLE": False,
        "NEW_THRESHOLD": False,
        "NEW_C4_POLICY": False,
    }


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "DEV_CLASSIFICATION": DEV_CLASSIFICATION,
        "PRIMARY_DECISION_UNIT": "ENTRY+C4 admission+X1_IMMEDIATE_ASK+EXIT+CAP5+same-symbol+occupancy+slot release+reentry",
        "PRE_ECONOMICS": fold_local_eligibility_contract(),
        "C4_IS_ADMISSION_OVERLAY": True,
        "ELIGIBLE_ENTRY_N": int(ELIGIBLE_ENTRY_N),
        "FROZEN_ELIGIBLE_IDS": list(FROZEN_ELIGIBLE_IDS),
        "EXIT_N": int(EXIT_N),
        "EXIT_IDS": list(KEPT_EXIT_IDS),
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "EXIT6_CREATED": False,
        "EXECUTION_ID": EXECUTION_ID,
        "CONTROL_POLICY_ID": CONTROL_POLICY_ID,
        "TREATMENT_POLICY_ID": TREATMENT_POLICY_ID,
        "SAME_T0_BACKFILL": False,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
        "OCCUPANCY_K_SEARCH": False,
        "CAP": int(POSITION_CAP),
        "SHARES": int(SHARES),
        "MATCHED_STRATEGY_N": int(MATCHED_STRATEGY_N),
        "CONTROL_ARM_N": int(CONTROL_ARM_N),
        "TREATMENT_ARM_N": int(TREATMENT_ARM_N),
        "TOTAL_ARM_N": int(TOTAL_ARM_N),
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "UNIFORM_40_ARM_RERUN": True,
        "EXECUTION": execution_contract(),
        "PORTFOLIO": portfolio_contract(),
        "COVERAGE_GATES": coverage_gates(),
        "FOLD_COVERAGE_GATES": fold_coverage_gates(),
        "ECONOMIC_GATES": economic_gates(),
        "STABILITY_GATES": stability_gates(),
        "INCREMENTAL_GATE": incremental_gate(),
        "ATTRIBUTION_STABILITY": attribution_stability_gates(),
        "FOLDS": fold_c4_contract(),
        "CANARY": {**canary_spec(), "RUN_THIS_PRECOMMIT": False, "RUN_THIS_FULL_STRATEGY": True},
        "CANARY_ID": CANARY_ID,
        "CANARY_SOURCE": CANARY_SOURCE,
        "CANARY_EXPECTED": dict(CANARY_EXPECTED),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_E": CASE_E,
        "CASE_CANARY": CASE_CANARY,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "NEXT_IF_C": NEXT_IF_C,
        "NEXT_IF_E": NEXT_IF_E,
        "NEXT_IF_CANARY": NEXT_IF_CANARY,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "SIZING": False,
        "STRESS_OPENED": False,
        "NEW_THRESHOLD": False,
        "NEW_C4_POLICY": False,
        "ENTRY_PARAMETER_RETUNE": False,
        "EXIT_PARAMETER_RETUNE": False,
    }


def spec_sha256() -> str:
    return dumps_sha256(canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes() if (root / name).is_file() else b"MISSING")
    return h.hexdigest()
