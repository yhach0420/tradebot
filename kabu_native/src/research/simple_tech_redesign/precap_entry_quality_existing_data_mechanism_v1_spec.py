"""Frozen existing-data ENTRY quality mechanism protocol. No prospective. No filter this run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
    SHARES,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_PRECAP_ENTRY_QUALITY_EXISTING_DATA_MECHANISM_V1"
PARENT_RCA_ID = "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_TIMING_CONFOUNDING_RCA"
PARENT_VERDICT = "SIMPLE_TECH_PRECAP_FWD_OUTLIER_DOMINATED"
OBSERVED_ECONOMIC_BOTTLENECK = "MARGINAL_ENTRY_QUALITY"
INTRINSIC_MECHANISM_CONFIRMED = False
PRIMARY_MECHANISM_FROZEN = "OUTLIER_DOMINATED"
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
PROSPECTIVE_HARVEST_SUSPENDED = True
PROSPECTIVE_ARMED = False
CANDIDATE_FROZEN = False
TRUE_OOS = False
CERTIFIED = False
BURNED_EXISTING_DATA = True
FUTURE_DATA_USED = False
INDEPENDENT_ALPHA_FAMILY = False
FEATURE_DISCOVERY = False
THRESHOLD_SEARCH = False
NEW_ENTRY_FILTER = False
NEW_EXIT_RULE = False
CAP_CHANGED = False
FAMILY_CLOSED = True

BLOCK_A_DISCOVERY = (
    "20260722",
    "20260728",
    "20260729",
    "20260730",
    "20260731",
    "20260803",
    "20260804",
    "20260805",
    "20260806",
    "20260807",
    "20260810",
    "20260817",
)
BLOCK_B_INTERNAL_STABILITY = (
    "20260819",
    "20260820",
    "20260824",
    "20260825",
    "20260826",
    "20260827",
)
BLOCK_C_BURNED_STRESS = (
    "20260828",
    "20260831",
    "20260901",
    "20260902",
)
BLOCK_A_USE = "mechanism discovery"
BLOCK_B_USE = "internal temporal stability"
BLOCK_C_USE = "burned stress diagnostic"
BLOCK_B_IS_VALIDATION = False
BLOCK_B_IS_HOLDOUT = False
BLOCK_B_IS_CONFIRMATION_OOS = False
BLOCK_C_IS_VALIDATION = False

FEATURES = (
    "volume_percentile_60s",
    "distance_from_vwap_bps",
    "rebound_from_recent_low_bps",
    "trading_value_percentile_180s",
)
FEATURE_IDS = {
    "volume_percentile_60s": "F1",
    "distance_from_vwap_bps": "F2",
    "rebound_from_recent_low_bps": "F3",
    "trading_value_percentile_180s": "F4",
}
REBOUND_SOURCE_KEY = "rebound_from_low_180s_bps"
AVAILABILITY_MIN = 0.80
MIN_SPLIT_N = 5
CONCENTRATION_WARN = 0.5
WORST_REJECT_PCT = 0.30
THRESHOLD_POLICY = "FIXED_WORST_30PCT_REJECTION_FROM_BLOCK_A_ONLY"
MISSING_POLICY_FROZEN = "FAIL_OPEN_TO_BASELINE"
MISSING_POLICY_THIS_RUN = "EXCLUDE_FROM_GRADIENT_NO_IMPUTE"
NEXT_CANDIDATE_ID_IF_CASE_A = "PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1"
NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED = False
PRIMARY_POPULATION = "PRE_CAP_EXECUTABLE_POOL"
CAP_ONLY_DEFINITION = "T3_VALID_AND_EXECUTABLE_AND_SAME_SYMBOL_PASSED_AND_CAP_IS_ONLY_REJECT"

SOURCE_FILES = (
    "precap_entry_quality_existing_data_mechanism_v1_spec.py",
    "precap_entry_quality_existing_data_mechanism_v1_harvest.py",
    "precap_entry_quality_existing_data_mechanism_v1_analyze.py",
    "precap_entry_quality_existing_data_mechanism_v1_publish.py",
    "precap_entry_quality_existing_data_mechanism_v1.py",
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
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PARENT_RCA_ID": PARENT_RCA_ID,
            "PARENT_VERDICT": PARENT_VERDICT,
            "OBSERVED_ECONOMIC_BOTTLENECK": OBSERVED_ECONOMIC_BOTTLENECK,
            "INTRINSIC_MECHANISM_CONFIRMED": False,
            "PRIMARY_MECHANISM_FROZEN": PRIMARY_MECHANISM_FROZEN,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "PROSPECTIVE_ARMED": False,
            "CANDIDATE_FROZEN": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "BURNED_EXISTING_DATA": True,
            "FUTURE_DATA_USED": False,
            "INDEPENDENT_ALPHA_FAMILY": False,
            "FEATURE_DISCOVERY": False,
            "THRESHOLD_SEARCH": False,
            "NEW_ENTRY_FILTER": False,
            "NEW_EXIT_RULE": False,
            "CAP_CHANGED": False,
            "BLOCK_A_DISCOVERY": list(BLOCK_A_DISCOVERY),
            "BLOCK_B_INTERNAL_STABILITY": list(BLOCK_B_INTERNAL_STABILITY),
            "BLOCK_C_BURNED_STRESS": list(BLOCK_C_BURNED_STRESS),
            "BLOCK_A_USE": BLOCK_A_USE,
            "BLOCK_B_USE": BLOCK_B_USE,
            "BLOCK_C_USE": BLOCK_C_USE,
            "BLOCK_B_IS_VALIDATION": False,
            "BLOCK_C_IS_VALIDATION": False,
            "FEATURES": list(FEATURES),
            "AVAILABILITY_MIN": float(AVAILABILITY_MIN),
            "MIN_SPLIT_N": int(MIN_SPLIT_N),
            "CONCENTRATION_WARN": float(CONCENTRATION_WARN),
            "WORST_REJECT_PCT": float(WORST_REJECT_PCT),
            "THRESHOLD_POLICY": THRESHOLD_POLICY,
            "MISSING_POLICY_FROZEN": MISSING_POLICY_FROZEN,
            "NEXT_CANDIDATE_ID_IF_CASE_A": NEXT_CANDIDATE_ID_IF_CASE_A,
            "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED": False,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "CAP_ONLY_DEFINITION": CAP_ONLY_DEFINITION,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SHARES": int(SHARES),
            "POSITION_CAP": int(POSITION_CAP),
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "TIE_BREAK": list(FEATURES),
        }
    )


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


def block_of(day: str) -> str | None:
    d = str(day)
    if d in BLOCK_A_DISCOVERY:
        return "BLOCK_A_DISCOVERY"
    if d in BLOCK_B_INTERNAL_STABILITY:
        return "BLOCK_B_INTERNAL_STABILITY"
    if d in BLOCK_C_BURNED_STRESS:
        return "BLOCK_C_BURNED_STRESS"
    return None


def all_research_days() -> tuple[str, ...]:
    return BLOCK_A_DISCOVERY + BLOCK_B_INTERNAL_STABILITY + BLOCK_C_BURNED_STRESS


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(SHARES) == 100
assert int(POSITION_CAP) == 5
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
assert MAX_RESEARCH_DATE == "20260902"
assert list(BLOCK_A_DISCOVERY) + list(BLOCK_B_INTERNAL_STABILITY) == list(ELIGIBLE_DAYS)
assert list(BLOCK_C_BURNED_STRESS) == list(LOCKED_SERIES_DAYS)
assert all(d <= MAX_RESEARCH_DATE for d in all_research_days())
assert all(d not in FORBIDDEN_INPUT_DAYS for d in all_research_days())
assert "20260907" not in all_research_days()
assert PROSPECTIVE_HARVEST_SUSPENDED is True
assert TRUE_OOS is False
assert INDEPENDENT_ALPHA_FAMILY is False
assert THRESHOLD_SEARCH is False
assert NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED is False
assert WORST_REJECT_PCT == 0.30
assert FEATURES[0] == "volume_percentile_60s"
assert FEATURES[1] == "distance_from_vwap_bps"
assert FEATURES[2] == "rebound_from_recent_low_bps"
assert FEATURES[3] == "trading_value_percentile_180s"
assert BLOCK_B_IS_VALIDATION is False
assert BLOCK_C_IS_VALIDATION is False
