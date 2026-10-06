"""T3 P2 pullback-touch-age sequence mechanism. Closed absolute-feature family is not reopened."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.simple_tech_entry_family import PULLBACK_LOOKBACK
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign.branch_u_bb_spec import (
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
    SHARES,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import (
    BLOCK_A_DISCOVERY,
    BLOCK_A_USE,
    BLOCK_B_INTERNAL_STABILITY,
    BLOCK_B_IS_VALIDATION,
    BLOCK_B_USE,
    BLOCK_C_BURNED_STRESS,
    BLOCK_C_IS_VALIDATION,
    BLOCK_C_USE,
    CONCENTRATION_WARN,
    EXPECTED_ATTRITION_N,
    EXPECTED_OUTCOME_EVALUABLE_N,
    EXPECTED_POOL_N,
    FEATURES as CLOSED_ABSOLUTE_FEATURES,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_DAY_N,
    all_research_days,
    block_of,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_PRECAP_T3_SETUP_SEQUENCE_MECHANISM_V1"
PARENT_R1_ID = "SIMPLE_TECH_PRECAP_ENTRY_QUALITY_EXISTING_DATA_MECHANISM_V1_R1"
PARENT_EXISTING_DATA_VERDICT = "SIMPLE_TECH_PRECAP_EXISTING_DATA_CONFOUNDED"
PRIMARY_NEXT_MECHANISM_PRIOR = "CLOSE_SINGLE_FEATURE_ABSOLUTE_FILTER_FAMILY"
ABSOLUTE_FEATURE_FAMILY_CLOSED = True
PRIMARY_FIELD = "P2_TOUCH_AGE_BARS"
PRIMARY_HYPOTHESIS = "OLDER_PULLBACK_IS_WORSE"
HYPOTHESIS_DIRECTION = "lower_touch_age_is_better"
EXPECTED_SPEARMAN_SIGN = -1
FLIP_DIRECTION_FROM_RESULTS = False
PRIMARY_METHOD = "CONTINUOUS_DAILY_SPEARMAN"
PROSPECTIVE_HARVEST_SUSPENDED = True
PROSPECTIVE_ARMED = False
FUTURE_DATA_USED = False
TRUE_OOS = False
CERTIFIED = False
BURNED_EXISTING_DATA = True
NEW_ENTRY_FILTER = False
NEW_EXIT_RULE = False
CAP_CHANGED = False
THRESHOLD_SEARCH = False
FEATURE_DISCOVERY = False
INDEPENDENT_ALPHA_FAMILY = False
CANDIDATE_FROZEN = True
CANDIDATE_EVALUATED_THIS_RUN = False
NEXT_CANDIDATE_ID = "PRECAP_T3_PULLBACK_FRESHNESS_V1"
NEXT_CANDIDATE_RULE = "IF P2_TOUCH_AGE_BARS == 2: REJECT_ENTRY_QUALITY_STALE_PULLBACK ELSE baseline T3"
NEXT_CANDIDATE_ACCEPT_AGES = (0, 1)
NEXT_CANDIDATE_REJECT_AGE = 2
TOUCH_COUNT_ROLE = "SECONDARY_DIAGNOSTIC_ONLY"
SIGNAL_BAR_TOUCH_ROLE = "SECONDARY_DIAGNOSTIC_ONLY"
GEOMETRY_ROLE = "SECONDARY_DIAGNOSTIC_ONLY"
P2_WINDOW = "range(i - 3 + 1, i + 1)"
ALLOWED_AGES = (0, 1, 2)
ALLOWED_TOUCH_COUNTS = (1, 2, 3)

SOURCE_FILES = (
    "precap_t3_setup_sequence_mechanism_v1_spec.py",
    "precap_t3_setup_sequence_mechanism_v1_harvest.py",
    "precap_t3_setup_sequence_mechanism_v1_analyze.py",
    "precap_t3_setup_sequence_mechanism_v1_publish.py",
    "precap_t3_setup_sequence_mechanism_v1.py",
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


def canonical_sequence_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PARENT_R1_ID": PARENT_R1_ID,
            "PARENT_EXISTING_DATA_VERDICT": PARENT_EXISTING_DATA_VERDICT,
            "PRIMARY_NEXT_MECHANISM_PRIOR": PRIMARY_NEXT_MECHANISM_PRIOR,
            "ABSOLUTE_FEATURE_FAMILY_CLOSED": True,
            "CLOSED_ABSOLUTE_FEATURES": list(CLOSED_ABSOLUTE_FEATURES),
            "PRIMARY_FIELD": PRIMARY_FIELD,
            "PRIMARY_HYPOTHESIS": PRIMARY_HYPOTHESIS,
            "HYPOTHESIS_DIRECTION": HYPOTHESIS_DIRECTION,
            "EXPECTED_SPEARMAN_SIGN": int(EXPECTED_SPEARMAN_SIGN),
            "FLIP_DIRECTION_FROM_RESULTS": False,
            "PRIMARY_METHOD": PRIMARY_METHOD,
            "MIN_DAY_N": int(MIN_DAY_N),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "PROSPECTIVE_ARMED": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "BURNED_EXISTING_DATA": True,
            "FUTURE_DATA_USED": False,
            "NEW_ENTRY_FILTER": False,
            "NEW_EXIT_RULE": False,
            "CAP_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "FEATURE_DISCOVERY": False,
            "INDEPENDENT_ALPHA_FAMILY": False,
            "CANDIDATE_FROZEN": True,
            "CANDIDATE_EVALUATED_THIS_RUN": False,
            "NEXT_CANDIDATE_ID": NEXT_CANDIDATE_ID,
            "NEXT_CANDIDATE_RULE": NEXT_CANDIDATE_RULE,
            "NEXT_CANDIDATE_ACCEPT_AGES": list(NEXT_CANDIDATE_ACCEPT_AGES),
            "NEXT_CANDIDATE_REJECT_AGE": int(NEXT_CANDIDATE_REJECT_AGE),
            "P2_WINDOW": P2_WINDOW,
            "PULLBACK_LOOKBACK": int(PULLBACK_LOOKBACK),
            "ALLOWED_AGES": list(ALLOWED_AGES),
            "BLOCK_A_DISCOVERY": list(BLOCK_A_DISCOVERY),
            "BLOCK_B_INTERNAL_STABILITY": list(BLOCK_B_INTERNAL_STABILITY),
            "BLOCK_C_BURNED_STRESS": list(BLOCK_C_BURNED_STRESS),
            "BLOCK_B_IS_VALIDATION": False,
            "BLOCK_C_IS_VALIDATION": False,
            "EXPECTED_POOL_N": dict(EXPECTED_POOL_N),
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SHARES": int(SHARES),
            "POSITION_CAP": int(POSITION_CAP),
            "TOUCH_COUNT_ROLE": TOUCH_COUNT_ROLE,
            "SIGNAL_BAR_TOUCH_ROLE": SIGNAL_BAR_TOUCH_ROLE,
            "GEOMETRY_ROLE": GEOMETRY_ROLE,
        }
    )


def spec_sha256_sequence(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_sequence_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_sequence() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(PULLBACK_LOOKBACK) == 3
assert P2_WINDOW == "range(i - 3 + 1, i + 1)"
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
assert int(SHARES) == 100
assert int(POSITION_CAP) == 5
assert MAX_RESEARCH_DATE == "20260902"
assert PROSPECTIVE_HARVEST_SUSPENDED is True
assert TRUE_OOS is False
assert FLIP_DIRECTION_FROM_RESULTS is False
assert NEXT_CANDIDATE_REJECT_AGE == 2
assert NEXT_CANDIDATE_ACCEPT_AGES == (0, 1)
assert ABSOLUTE_FEATURE_FAMILY_CLOSED is True
assert list(CLOSED_ABSOLUTE_FEATURES) == [
    "volume_percentile_60s",
    "distance_from_vwap_bps",
    "rebound_from_recent_low_bps",
    "trading_value_percentile_180s",
]
assert BLOCK_B_IS_VALIDATION is False
assert BLOCK_C_IS_VALIDATION is False
assert CANDIDATE_EVALUATED_THIS_RUN is False
assert THRESHOLD_SEARCH is False
