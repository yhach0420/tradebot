"""R1: precommitted daily Spearman methodology. Same populations as V1. No new features."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_spec import (
    AVAILABILITY_MIN,
    BLOCK_A_DISCOVERY,
    BLOCK_A_USE,
    BLOCK_B_INTERNAL_STABILITY,
    BLOCK_B_IS_CONFIRMATION_OOS,
    BLOCK_B_IS_HOLDOUT,
    BLOCK_B_IS_VALIDATION,
    BLOCK_B_USE,
    BLOCK_C_BURNED_STRESS,
    BLOCK_C_IS_VALIDATION,
    BLOCK_C_USE,
    BURNED_EXISTING_DATA,
    CAP_CHANGED,
    CAP_ONLY_DEFINITION,
    CERTIFIED,
    CONCENTRATION_WARN,
    FEATURE_DISCOVERY,
    FEATURE_IDS,
    FEATURES,
    FORBIDDEN_INPUT_DAYS,
    FUTURE_DATA_USED,
    INDEPENDENT_ALPHA_FAMILY,
    MAX_RESEARCH_DATE,
    MISSING_POLICY_FROZEN,
    MISSING_POLICY_THIS_RUN,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    NEXT_CANDIDATE_ID_IF_CASE_A,
    NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED,
    OBSERVED_ECONOMIC_BOTTLENECK,
    PARENT_RCA_ID,
    PARENT_VERDICT,
    PRIMARY_MECHANISM_FROZEN,
    PRIMARY_POPULATION,
    PROSPECTIVE_ARMED,
    PROSPECTIVE_HARVEST_SUSPENDED,
    REBOUND_SOURCE_KEY,
    THRESHOLD_POLICY,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    WORST_REJECT_PCT,
    all_research_days,
    block_of,
    canonical_mechanism_spec as canonical_v1_spec,
)

ANALYSIS_ID = "SIMPLE_TECH_PRECAP_ENTRY_QUALITY_EXISTING_DATA_MECHANISM_V1_R1"
PRIOR_ANALYSIS_ID = "SIMPLE_TECH_PRECAP_ENTRY_QUALITY_EXISTING_DATA_MECHANISM_V1"
PRIOR_VERDICT = "SIMPLE_TECH_PRECAP_EXISTING_DATA_CONFOUNDED"
SUPERSEDED_FOR_DECISION = True
SUPERSEDED_REASON = "PRECOMMITTED_CONTINUOUS_DAILY_SPEARMAN_METHOD_NOT_IMPLEMENTED"
PRIMARY_METHOD = "CONTINUOUS_DAILY_SPEARMAN"
MEDIAN_SPLIT_PRIMARY = False
MEDIAN_SPLIT_ROLE = "SECONDARY_DIAGNOSTIC_ONLY"
POOLED_RHO_ROLE = "SECONDARY_ONLY"
MIN_DAY_N = 5
EXPECTED_POOL_N = {
    "BLOCK_A_DISCOVERY": 138,
    "BLOCK_B_INTERNAL_STABILITY": 90,
    "BLOCK_C_BURNED_STRESS": 40,
}
EXPECTED_OUTCOME_EVALUABLE_N = {
    "BLOCK_A_DISCOVERY": 129,
    "BLOCK_B_INTERNAL_STABILITY": 81,
    "BLOCK_C_BURNED_STRESS": 36,
}
EXPECTED_ATTRITION_N = {
    "BLOCK_A_DISCOVERY": 9,
    "BLOCK_B_INTERNAL_STABILITY": 9,
    "BLOCK_C_BURNED_STRESS": 4,
}
CAPTURE_STATES = ("ACTIVE_SAME_PID", "INACTIVE_EXPECTED", "UNKNOWN")
CANDIDATE_FROZEN = False
FAMILY_CLOSED = True
NEW_ENTRY_FILTER = False
SOURCE_FILES = (
    "precap_entry_quality_existing_data_mechanism_v1_r1_spec.py",
    "precap_entry_quality_existing_data_mechanism_v1_r1_harvest.py",
    "precap_entry_quality_existing_data_mechanism_v1_r1_analyze.py",
    "precap_entry_quality_existing_data_mechanism_v1_r1_publish.py",
    "precap_entry_quality_existing_data_mechanism_v1_r1.py",
)


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def canonical_mechanism_spec() -> dict[str, Any]:
    base = dict(canonical_v1_spec())
    base.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PRIOR_ANALYSIS_ID": PRIOR_ANALYSIS_ID,
            "PRIOR_VERDICT": PRIOR_VERDICT,
            "SUPERSEDED_FOR_DECISION": True,
            "SUPERSEDED_REASON": SUPERSEDED_REASON,
            "PRIMARY_METHOD": PRIMARY_METHOD,
            "MEDIAN_SPLIT_PRIMARY": False,
            "MEDIAN_SPLIT_ROLE": MEDIAN_SPLIT_ROLE,
            "POOLED_RHO_ROLE": POOLED_RHO_ROLE,
            "MIN_DAY_N": int(MIN_DAY_N),
            "EXPECTED_POOL_N": dict(EXPECTED_POOL_N),
            "EXPECTED_OUTCOME_EVALUABLE_N": dict(EXPECTED_OUTCOME_EVALUABLE_N),
            "EXPECTED_ATTRITION_N": dict(EXPECTED_ATTRITION_N),
            "CAPTURE_STATES": list(CAPTURE_STATES),
            "NEW_ENTRY_FILTER": False,
            "CANDIDATE_FROZEN": False,
        }
    )
    return _canon(base)


def spec_sha256_mechanism(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_mechanism_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_mechanism() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert ANALYSIS_ID.endswith("_V1_R1")
assert PRIMARY_METHOD == "CONTINUOUS_DAILY_SPEARMAN"
assert MEDIAN_SPLIT_PRIMARY is False
assert MIN_DAY_N == 5
assert MAX_RESEARCH_DATE == "20260902"
assert PROSPECTIVE_HARVEST_SUSPENDED is True
assert TRUE_OOS is False
assert THRESHOLD_SEARCH is False
assert NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED is False
assert WORST_REJECT_PCT == 0.30
assert FEATURES[0] == "volume_percentile_60s"
assert FEATURES[1] == "distance_from_vwap_bps"
assert FEATURES[2] == "rebound_from_recent_low_bps"
assert FEATURES[3] == "trading_value_percentile_180s"
assert BLOCK_B_IS_VALIDATION is False
assert BLOCK_C_IS_VALIDATION is False
assert SUPERSEDED_FOR_DECISION is True
assert NEW_ENTRY_FILTER is False
assert CANDIDATE_FROZEN is False
assert sum(EXPECTED_ATTRITION_N.values()) == 22
assert EXPECTED_POOL_N["BLOCK_A_DISCOVERY"] - EXPECTED_OUTCOME_EVALUABLE_N["BLOCK_A_DISCOVERY"] == EXPECTED_ATTRITION_N["BLOCK_A_DISCOVERY"]
assert EXPECTED_POOL_N["BLOCK_B_INTERNAL_STABILITY"] - EXPECTED_OUTCOME_EVALUABLE_N["BLOCK_B_INTERNAL_STABILITY"] == EXPECTED_ATTRITION_N["BLOCK_B_INTERNAL_STABILITY"]
assert EXPECTED_POOL_N["BLOCK_C_BURNED_STRESS"] - EXPECTED_OUTCOME_EVALUABLE_N["BLOCK_C_BURNED_STRESS"] == EXPECTED_ATTRITION_N["BLOCK_C_BURNED_STRESS"]

__all__ = [
    "ANALYSIS_ID",
    "AVAILABILITY_MIN",
    "BLOCK_A_DISCOVERY",
    "BLOCK_A_USE",
    "BLOCK_B_INTERNAL_STABILITY",
    "BLOCK_B_IS_CONFIRMATION_OOS",
    "BLOCK_B_IS_HOLDOUT",
    "BLOCK_B_IS_VALIDATION",
    "BLOCK_B_USE",
    "BLOCK_C_BURNED_STRESS",
    "BLOCK_C_IS_VALIDATION",
    "BLOCK_C_USE",
    "BURNED_EXISTING_DATA",
    "CAP_CHANGED",
    "CAP_ONLY_DEFINITION",
    "CAPTURE_STATES",
    "CANDIDATE_FROZEN",
    "CERTIFIED",
    "CONCENTRATION_WARN",
    "EXPECTED_ATTRITION_N",
    "EXPECTED_OUTCOME_EVALUABLE_N",
    "EXPECTED_POOL_N",
    "FEATURE_DISCOVERY",
    "FEATURE_IDS",
    "FEATURES",
    "FORBIDDEN_INPUT_DAYS",
    "FUTURE_DATA_USED",
    "INDEPENDENT_ALPHA_FAMILY",
    "MAX_RESEARCH_DATE",
    "MEDIAN_SPLIT_PRIMARY",
    "MEDIAN_SPLIT_ROLE",
    "MIN_DAY_N",
    "MISSING_POLICY_FROZEN",
    "MISSING_POLICY_THIS_RUN",
    "NEW_ENTRY_FILTER",
    "NEW_EXIT_RULE",
    "NEXT_CANDIDATE_ID_IF_CASE_A",
    "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED",
    "OBSERVED_ECONOMIC_BOTTLENECK",
    "PARENT_RCA_ID",
    "PARENT_VERDICT",
    "POOLED_RHO_ROLE",
    "PRIMARY_MECHANISM_FROZEN",
    "PRIMARY_METHOD",
    "PRIMARY_POPULATION",
    "PRIOR_ANALYSIS_ID",
    "PRIOR_VERDICT",
    "PROSPECTIVE_ARMED",
    "PROSPECTIVE_HARVEST_SUSPENDED",
    "REBOUND_SOURCE_KEY",
    "SOURCE_FILES",
    "SUPERSEDED_FOR_DECISION",
    "SUPERSEDED_REASON",
    "THRESHOLD_POLICY",
    "THRESHOLD_SEARCH",
    "TRUE_OOS",
    "WORST_REJECT_PCT",
    "all_research_days",
    "block_of",
    "canonical_mechanism_spec",
    "source_sha256_mechanism",
    "spec_sha256_mechanism",
]
