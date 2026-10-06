"""Frozen prospective observation protocol for pre-CAP marginal candidate quality. No filter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
    SHARES,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_V1"
PARENT_RCA_ID = "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_TIMING_CONFOUNDING_RCA"
PARENT_VERDICT = "SIMPLE_TECH_PRECAP_FWD_OUTLIER_DOMINATED"
OBSERVED_ECONOMIC_BOTTLENECK = "MARGINAL_ENTRY_QUALITY"
INTRINSIC_MECHANISM_CONFIRMED = False
PRIMARY_MECHANISM_FROZEN = "OUTLIER_DOMINATED"
PROTOCOL_FREEZE_DATE = "20260904"
FIRST_ELIGIBLE_DATE = "20260907"
OBSERVATION_DAY1_LABEL = "MARGINAL_QUALITY_OBSERVATION_DAY1"
WINDOW_DAYS = ("20260907", "20260908", "20260909", "20260910", "20260911")
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
MIN_BLOCKED_HYP_N = 15
CONCENTRATION_WARN = 0.5
OUTLIER_SYMBOL = "285A"
OUTLIER_DAY_HISTORICAL = "20260828"
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
CAP_CHANGED = False
ENTRY_CHANGED = False
TIME_FILTER = False
RANK_FILTER = False
ROLE_FILTER = False
THRESHOLD_SEARCH = False
FEATURE_DISCOVERY = False
MATCHING_IN_PRIMARY_VERDICT = False
TRUE_OOS = False
CERTIFIED = False
FAMILY_CLOSED = True
CANDIDATE_FROZEN = False
PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN = True
PROSPECTIVE_ARMED = True
FLOOR_BREAK_FAMILY = "CLOSED"
BOARD_FAMILY = "CLOSED"

SOURCE_FILES = (
    "precap_marginal_quality_prospective_v1_spec.py",
    "precap_marginal_quality_prospective_v1_harvest.py",
    "precap_marginal_quality_prospective_v1_analyze.py",
    "precap_marginal_quality_prospective_v1_publish.py",
    "precap_marginal_quality_prospective_v1.py",
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


def canonical_prospective_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PARENT_RCA_ID": PARENT_RCA_ID,
            "PARENT_VERDICT": PARENT_VERDICT,
            "OBSERVED_ECONOMIC_BOTTLENECK": OBSERVED_ECONOMIC_BOTTLENECK,
            "INTRINSIC_MECHANISM_CONFIRMED": False,
            "PRIMARY_MECHANISM_FROZEN": PRIMARY_MECHANISM_FROZEN,
            "PROTOCOL_FREEZE_DATE": PROTOCOL_FREEZE_DATE,
            "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": True,
            "CANDIDATE_FROZEN": False,
            "PROSPECTIVE_ARMED": True,
            "first_eligible_prospective_date": FIRST_ELIGIBLE_DATE,
            "OBSERVATION_DAY1_LABEL": OBSERVATION_DAY1_LABEL,
            "WINDOW_DAYS": list(WINDOW_DAYS),
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "MIN_BLOCKED_HYP_N": int(MIN_BLOCKED_HYP_N),
            "CONCENTRATION_WARN": float(CONCENTRATION_WARN),
            "OUTLIER_SYMBOL_INCLUDED": OUTLIER_SYMBOL,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SHARES": int(SHARES),
            "POSITION_CAP": int(POSITION_CAP),
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "FAMILY_CLOSED": True,
            "FLOOR_BREAK_FAMILY": FLOOR_BREAK_FAMILY,
            "BOARD_FAMILY": BOARD_FAMILY,
            "NEW_EXIT_RULE": False,
            "NEW_ENTRY_FILTER": False,
            "CAP_CHANGED": False,
            "ENTRY_CHANGED": False,
            "TIME_FILTER": False,
            "RANK_FILTER": False,
            "ROLE_FILTER": False,
            "THRESHOLD_SEARCH": False,
            "FEATURE_DISCOVERY": False,
            "MATCHING_IN_PRIMARY_VERDICT": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CAP_ONLY_DEFINITION": "T3_VALID_AND_EXECUTABLE_AND_SAME_SYMBOL_PASSED_AND_CAP_IS_ONLY_REJECT",
            "HYPOTHETICAL": "ONE_SHOT_SESSION_CLOSE_HOLD_NO_CASCADE",
        }
    )


def spec_sha256_prospective(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_prospective_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_prospective() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(SHARES) == 100
assert int(POSITION_CAP) == 5
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
assert PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN is True
assert CANDIDATE_FROZEN is False
assert NEW_ENTRY_FILTER is False
assert CAP_CHANGED is False
assert NEW_EXIT_RULE is False
assert MATCHING_IN_PRIMARY_VERDICT is False
assert FIRST_ELIGIBLE_DATE == "20260907"
assert WINDOW_DAYS[0] == FIRST_ELIGIBLE_DATE
assert "20260903" in FORBIDDEN_INPUT_DAYS
assert "20260904" in FORBIDDEN_INPUT_DAYS
assert PROTOCOL_FREEZE_DATE == "20260904"
