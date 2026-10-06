"""Pre-CAP hard-reject failure RCA. Diagnostic only. No new rule. No search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    CANONICAL_FRESHNESS_SEC,
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import (
    EVOLUTION_WINDOW_SEC,
    EVOLUTION_WINDOW_SOURCE,
    FORBIDDEN_DAYS,
    HOLDOUT_STATUS,
    TRUE_L1_ASK,
    TRUE_L1_BID,
    spec_sha256_board_rca,
)
from research.simple_tech_redesign.pre_cap_candidate_spec import (
    ADVERSE_COMPONENT_MIN,
    BOARD_RCA_SPEC_SHA256_EXPECTED,
    CANDIDATE_ID,
    COMPONENT_KEYS,
    COMPONENT_NAMES,
    spec_sha256_precap,
)
from research.simple_tech_redesign.v28_spec import SHARES
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_PRE_CAP_HARD_REJECT_FAILURE_RCA"
SOURCE_CANDIDATE_ID = CANDIDATE_ID
SOURCE_CANDIDATE_VERDICT = "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_ECONOMICS_FAILED"
SOURCE_PRECAP_SPEC_SHA256_EXPECTED = "43fc01358b2c6819e151ee76452e887aaf186c242b3a6c8812cec4713a9ce6b4"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"

NEW_ENTRY_RULE = False
THRESHOLD_SEARCH = False
K_OF_N_SEARCH = False
RANKING_RULE_CREATED = False
BOARD_FEATURE_ADDED = False
WINDOW_CHANGED = False
BRANCH_U_USED = False
BOARD_OK_REUSED = False
FORCE_TREATMENT_FILL_SET = False
TRUE_OOS = False
CERTIFIED = False
RUNTIME_CANDIDATE = False
RESEARCH_PARALLELISM = 1

DEV_COMMON_N_EXPECTED = 54
DEV_CONTROL_ONLY_N_EXPECTED = 30
DEV_INCREMENTAL_N_EXPECTED = 23

PROTECTED_PATHS = ("GOOD_CONTINUATION", "DIP_THEN_RECOVERY")
FAILURE_PATHS = ("EARLY_FAILURE",)
AM_OPEN_HOUR = 9
AM_OPEN_MINUTE = 0
DENSITY_LOOKBACK_SEC = 60.0
MIN_RELATIVE_PAIR_N_DEV = 8
MIN_RELATIVE_PAIR_N_FWD = 5

SOURCE_FILES = (
    "pre_cap_hard_reject_rca_spec.py",
    "pre_cap_hard_reject_rca_harvest.py",
    "pre_cap_hard_reject_rca_analyze.py",
    "pre_cap_hard_reject_rca_publish.py",
    "pre_cap_hard_reject_rca.py",
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


def canonical_hard_reject_rca_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "SOURCE_CANDIDATE_ID": SOURCE_CANDIDATE_ID,
            "SOURCE_CANDIDATE_VERDICT": SOURCE_CANDIDATE_VERDICT,
            "SOURCE_PRECAP_SPEC_SHA256": spec_sha256_precap(),
            "SOURCE_BOARD_RCA_SPEC_SHA256": spec_sha256_board_rca(),
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "PRIMARY_EXIT": PRIMARY_EXIT,
            "BRANCH_U_USED": False,
            "POSITION_CAP": int(POSITION_CAP),
            "SHARES": int(SHARES),
            "WAIT_SEC": float(DEV_WAIT_SEC),
            "EVOLUTION_WINDOW_SEC": float(EVOLUTION_WINDOW_SEC),
            "EVOLUTION_WINDOW_SOURCE": EVOLUTION_WINDOW_SOURCE,
            "TRUE_L1_BID": TRUE_L1_BID,
            "TRUE_L1_ASK": TRUE_L1_ASK,
            "COMPONENT_NAMES": list(COMPONENT_NAMES),
            "COMPONENT_KEYS": list(COMPONENT_KEYS),
            "ADVERSE_COMPONENT_MIN": int(ADVERSE_COMPONENT_MIN),
            "NEW_ENTRY_RULE": False,
            "THRESHOLD_SEARCH": False,
            "RANKING_RULE_CREATED": False,
            "BOARD_FEATURE_ADDED": False,
            "WINDOW_CHANGED": False,
            "BOARD_OK_REUSED": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "DEV_COMMON_N_EXPECTED": int(DEV_COMMON_N_EXPECTED),
            "DEV_CONTROL_ONLY_N_EXPECTED": int(DEV_CONTROL_ONLY_N_EXPECTED),
            "DEV_INCREMENTAL_N_EXPECTED": int(DEV_INCREMENTAL_N_EXPECTED),
            "DENSITY_LOOKBACK_SEC": float(DENSITY_LOOKBACK_SEC),
            "TRUE_OOS": False,
            "research_parallelism": 1,
        }
    )


def spec_sha256_hard_reject_rca(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_hard_reject_rca_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_hard_reject_rca() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_board_rca() == BOARD_RCA_SPEC_SHA256_EXPECTED
assert spec_sha256_precap() == SOURCE_PRECAP_SPEC_SHA256_EXPECTED
assert int(ADVERSE_COMPONENT_MIN) == 2
assert abs(float(EVOLUTION_WINDOW_SEC) - float(CANONICAL_FRESHNESS_SEC)) < 1e-12
assert NEW_ENTRY_RULE is False
assert THRESHOLD_SEARCH is False
assert RANKING_RULE_CREATED is False
assert BOARD_OK_REUSED is False
assert BRANCH_U_USED is False
assert int(POSITION_CAP) == 5
assert "20260903" in FORBIDDEN_DAYS
assert list(LOCKED_SERIES_DAYS) == ["20260828", "20260831", "20260901", "20260902"]
