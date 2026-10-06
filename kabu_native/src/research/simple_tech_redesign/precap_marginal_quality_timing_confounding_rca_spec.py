"""Pre-CAP marginal quality timing/arrival-order confounding RCA. No new filter."""
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
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_TIMING_CONFOUNDING_RCA"
PARENT_RCA_ID = "SIMPLE_TECH_SLOT_RELEASE_MARGINAL_ADMISSION_QUALITY_RCA"
PARENT_VERDICT = "SIMPLE_TECH_MARGINAL_ENTRY_QUALITY_BOTTLENECK_SUPPORTED"
OBSERVED_ECONOMIC_BOTTLENECK = "MARGINAL_ENTRY_QUALITY"
INTRINSIC_MECHANISM_CONFIRMED = False
EARLIEST_POSSIBLE_IF_CASE_A = "20260907"
PRIMARY_POPULATION = "CONTROL_ADMITTED_AND_CAP_ONLY_BLOCKED_EXECUTABLE"
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
CAP_CHANGED = False
ENTRY_CHANGED = False
TIME_FILTER = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False
FAMILY_CLOSED = True
PROSPECTIVE_ARMED = False

DEV_HYP_N = 126
FWD_HYP_N = 16
DEV_CAP_ONLY_N = 144
FWD_CAP_ONLY_N = 20
DEV_ADMITTED_FROM_OPEN_MEDIAN = 2670.799500107765
DEV_BLOCKED_FROM_OPEN_MEDIAN = 6392.305999994278
FWD_ADMITTED_FROM_OPEN_MEDIAN = 2580.111999988556
FWD_BLOCKED_FROM_OPEN_MEDIAN = 6210.199999928474
CLOCK_TOL_SEC = 1.0
FIRST5_N = int(POSITION_CAP)
FWD_OUTLIER_DAY = "20260828"
FWD_OUTLIER_SYMBOL = "285A"
DECOMP_TOL = 0.01

T3_INVENTORY_FIELDS = (
    "t3_ema9",
    "t3_ema21",
    "t3_rci9",
    "t3_bb_lower",
    "t3_close",
    "t3_ema_gap_bps",
    "t3_close_ema9_bps",
    "p2_setup_low",
    "p2_setup_high",
    "p2_signal_bar_low",
    "p2_signal_bar_close",
    "p2_bb_lower_at_signal",
)
T3_MISSING_NOT_COMPUTED = (
    "t3_rci9_prev",
    "t3_ema21_slope_input",
)

SOURCE_FILES = (
    "precap_marginal_quality_timing_confounding_rca_spec.py",
    "precap_marginal_quality_timing_confounding_rca_harvest.py",
    "precap_marginal_quality_timing_confounding_rca_analyze.py",
    "precap_marginal_quality_timing_confounding_rca_publish.py",
    "precap_marginal_quality_timing_confounding_rca.py",
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


def canonical_timing_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PARENT_RCA_ID": PARENT_RCA_ID,
            "PARENT_VERDICT": PARENT_VERDICT,
            "OBSERVED_ECONOMIC_BOTTLENECK": OBSERVED_ECONOMIC_BOTTLENECK,
            "INTRINSIC_MECHANISM_CONFIRMED": False,
            "FAMILY_CLOSED": True,
            "PROSPECTIVE_ARMED": False,
            "first_eligible_prospective_date": None,
            "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SHARES": int(SHARES),
            "POSITION_CAP": int(POSITION_CAP),
            "FIRST5_N": int(FIRST5_N),
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
            "TIME_FILTER": False,
            "THRESHOLD_SEARCH": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "DEV_HYP_N": int(DEV_HYP_N),
            "FWD_HYP_N": int(FWD_HYP_N),
        }
    )


def spec_sha256_timing(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_timing_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_timing() -> str:
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
assert int(DEV_FILL_N) == 84 and int(FWD_FILL_N) == 20
assert abs(float(DEV_PNL) - 146680.0) <= YEN_PARITY_TOL
assert abs(float(FWD_PNL) + 45100.0) <= YEN_PARITY_TOL
assert int(DEV_CORE_N) == 18 and int(DEV_ADDED_N) == 66
assert int(FWD_CORE_N) == 9 and int(FWD_ADDED_N) == 11
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
assert PROSPECTIVE_ARMED is False
assert TIME_FILTER is False
assert NEW_ENTRY_FILTER is False
