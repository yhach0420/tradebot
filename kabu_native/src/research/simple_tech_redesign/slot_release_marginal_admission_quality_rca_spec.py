"""Slot-release marginal admission quality RCA. No new EXIT/ENTRY/CAP rule."""
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
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import (
    CANDIDATE_EXIT_REASON,
    CANDIDATE_ID,
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    YEN_PARITY_TOL,
)

ANALYSIS_ID = "SIMPLE_TECH_SLOT_RELEASE_MARGINAL_ADMISSION_QUALITY_RCA"
FROZEN_VERDICT = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_ROBUSTNESS_FAILED"
FROZEN_CANDIDATE_ID = CANDIDATE_ID
FROZEN_SPEC_SHA256 = "de43059ee7376120d8467956b9d31aa1a44121e44ad4cac62c08428e905372f5"
EARLIEST_POSSIBLE_IF_CASE_A = "20260907"
PRIMARY_POPULATION = "CONTROL_CAP_ONLY_BLOCKED_AND_FAILED_TREATMENT_INCREMENTALS"
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
CAP_CHANGED = False
ENTRY_CHANGED = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False
CANDIDATE_FROZEN = False
FAMILY_CLOSED = True
PROSPECTIVE_ARMED = False

DEV_TREAT_FILL_N = 115
DEV_INCR_N = 31
DEV_DIRECT = 72990.0
DEV_SLOT = 76550.0
DEV_TOTAL = 149540.0
FWD_TREAT_FILL_N = 35
FWD_INCR_N = 15
FWD_DIRECT = -5000.0
FWD_SLOT = -82900.0
FWD_TOTAL = -87900.0

FWD_OUTLIER_DAY = "20260828"
FWD_OUTLIER_SYMBOL = "285A"
DEV_CONC_DAY = "20260806"
DECOMP_TOL = 0.01

SOURCE_FILES = (
    "slot_release_marginal_admission_quality_rca_spec.py",
    "slot_release_marginal_admission_quality_rca_harvest.py",
    "slot_release_marginal_admission_quality_rca_analyze.py",
    "slot_release_marginal_admission_quality_rca_publish.py",
    "slot_release_marginal_admission_quality_rca.py",
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


def canonical_marginal_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FROZEN_VERDICT": FROZEN_VERDICT,
            "FROZEN_CANDIDATE_ID": FROZEN_CANDIDATE_ID,
            "FROZEN_SPEC_SHA256": FROZEN_SPEC_SHA256,
            "CANDIDATE_FROZEN": False,
            "FAMILY_CLOSED": True,
            "PROSPECTIVE_ARMED": False,
            "first_eligible_prospective_date": None,
            "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SHARES": int(SHARES),
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "NEW_EXIT_RULE": False,
            "NEW_ENTRY_FILTER": False,
            "CAP_CHANGED": False,
            "ENTRY_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "CANDIDATE_EXIT_REASON": CANDIDATE_EXIT_REASON,
        }
    )


def spec_sha256_marginal(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_marginal_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_marginal() -> str:
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
assert int(DEV_FILL_N) == 84 and int(FWD_FILL_N) == 20
assert CANDIDATE_FROZEN is False
assert PROSPECTIVE_ARMED is False
assert abs(float(YEN_PARITY_TOL) - 0.01) <= 1e-12
assert int(DEV_CORE_N) == 18 and int(DEV_ADDED_N) == 66
assert int(FWD_CORE_N) == 9 and int(FWD_ADDED_N) == 11
assert abs(float(DEV_PNL) - 146680.0) <= YEN_PARITY_TOL
assert abs(float(FWD_PNL) + 45100.0) <= YEN_PARITY_TOL
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
