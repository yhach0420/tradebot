"""EXIT failure composition shift RCA. Explains MIXED residual verdict. No new EXIT."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import DEVELOPMENT_ENTRY_STACK, PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS
from research.simple_tech_redesign.exit_residual_rca_spec import (
    DEV_CONTROL_ADDED_N_EXPECTED,
    DEV_CONTROL_CORE_N_EXPECTED,
    DEV_CONTROL_FILL_N_EXPECTED,
    DEV_CONTROL_PNL_EXPECTED,
    FWD_CONTROL_ADDED_N_EXPECTED,
    FWD_CONTROL_CORE_N_EXPECTED,
    FWD_CONTROL_FILL_N_EXPECTED,
    FWD_CONTROL_PNL_EXPECTED,
    YEN_PARITY_TOL,
)

RESIDUAL_VERDICT_EXPECTED = "SIMPLE_TECH_EXIT_RESIDUAL_MIXED_ARCHITECTURE"
RESIDUAL_RCA_SPEC_SHA256_EXPECTED = "7abc9f419bba5a81b942fc2c11d1cf99aae16b6498ec4872730e2013c02d16b2"

ANALYSIS_ID = "SIMPLE_TECH_EXIT_FAILURE_COMPOSITION_SHIFT_RCA"
SOURCE_ANALYSIS_ID = "SIMPLE_TECH_EXIT_RESIDUAL_LOSS_ARCHITECTURE_RCA"
SOURCE_REVISION = "R1_RANK_FIELD_MAPPING"
SOURCE_VERDICT = RESIDUAL_VERDICT_EXPECTED
PRIMARY_POPULATION = "OCCUPANCY_CONTROL_FILLS_ONLY"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"
BOARD_FAMILY_STATUS = "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"
BRANCH_U_STATUS = "SIMPLE_TECH_BRANCH_U_EXIT_PATH_CLOSED"

FAILURE_CLASSES = (
    "U_EARLY_NEVER_BE",
    "P_EARLY_AFTER_BE",
    "P_PROFIT_THEN_FAILURE",
)
P_FAMILY = "P_FAMILY"
PRIMARY_FAILURE_KEYS = FAILURE_CLASSES + (P_FAMILY,)

COMMON_ROLE_WEIGHT_CORE = 27.0 / 104.0
COMMON_ROLE_WEIGHT_ADDED = 77.0 / 104.0
CONCENTRATION_WARN = 0.50
DECOMP_TOL = 1e-6
CONTEXT_CLASSES = ("PROTECTED_GOOD", "PROTECTED_DIP", "OTHER")

SHIFT_DRIVERS = (
    "ROLE_MIX",
    "INCIDENCE_SHIFT",
    "SEVERITY_SHIFT",
    "ENTRY_HORIZON_SHIFT",
    "PRICE_SCALE_CONCENTRATION",
    "DAY_SYMBOL_CONCENTRATION",
    "WITHIN_ROLE_LIFECYCLE_SHIFT",
    "MIXED_OR_INSUFFICIENT",
)

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False
RESEARCH_PARALLELISM = 1

SOURCE_FILES = (
    "exit_composition_shift_rca_spec.py",
    "exit_composition_shift_rca_analyze.py",
    "exit_composition_shift_rca_publish.py",
    "exit_composition_shift_rca.py",
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


def canonical_composition_shift_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "SOURCE_ANALYSIS_ID": SOURCE_ANALYSIS_ID,
            "SOURCE_REVISION": SOURCE_REVISION,
            "SOURCE_VERDICT": SOURCE_VERDICT,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "PRIMARY_EXIT": PRIMARY_EXIT,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "RESIDUAL_RCA_SPEC_SHA256": RESIDUAL_RCA_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "FAILURE_CLASSES": list(FAILURE_CLASSES),
            "P_FAMILY": P_FAMILY,
            "COMMON_ROLE_WEIGHT_CORE": COMMON_ROLE_WEIGHT_CORE,
            "COMMON_ROLE_WEIGHT_ADDED": COMMON_ROLE_WEIGHT_ADDED,
            "CONCENTRATION_WARN": CONCENTRATION_WARN,
            "DECOMP_TOL": DECOMP_TOL,
            "DEV_CONTROL_FILL_N_EXPECTED": int(DEV_CONTROL_FILL_N_EXPECTED),
            "FWD_CONTROL_FILL_N_EXPECTED": int(FWD_CONTROL_FILL_N_EXPECTED),
            "NEW_EXIT_RULE": False,
            "THRESHOLD_SEARCH": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "TRUE_OOS": False,
            "research_parallelism": 1,
        }
    )


def spec_sha256_composition_shift(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_composition_shift_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_composition_shift() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert NEW_EXIT_RULE is False
assert THRESHOLD_SEARCH is False
assert "20260903" in FORBIDDEN_DAYS
assert int(DEV_CONTROL_FILL_N_EXPECTED) == 84
assert int(FWD_CONTROL_FILL_N_EXPECTED) == 20
