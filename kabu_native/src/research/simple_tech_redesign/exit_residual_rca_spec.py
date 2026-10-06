"""EXIT residual-loss architecture RCA. Occupancy Control only. No new EXIT. No 20260903."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import DEVELOPMENT_ENTRY_STACK, PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_false_break_rca_spec import spec_sha256_false_break
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.branch_u_holdout_spec import CAUSAL_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS
from research.simple_tech_redesign.v26_spec import PATH_TYPES
from research.simple_tech_redesign.v28_spec import SHARES
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_EXIT_RESIDUAL_LOSS_ARCHITECTURE_RCA"
PRIMARY_POPULATION = "OCCUPANCY_CONTROL_FILLS_ONLY"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"
OCCUPANCY_SOT = "simple_tech_entry_family.portfolio.portfolio_replay"
BREAK_EVEN_DEF = "NET_EXECUTABLE_PNL_YEN_100_GE_0"
BOARD_FAMILY_STATUS = "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"
BRANCH_U_STATUS = "SIMPLE_TECH_BRANCH_U_EXIT_PATH_CLOSED"
EXIT_BOARD_CONFIRMATION_SUPPORTED = False

FALSE_BREAK_VERDICT_EXPECTED = "SIMPLE_TECH_BRANCH_U_EXIT_PATH_CLOSED"
FALSE_BREAK_SPEC_SHA256_EXPECTED = "f442d57e9a7db68452f0656856f6245d315cd3eff27c244e6c69954d5daf1fe4"
HARD_REJECT_VERDICT_EXPECTED = "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"

PATH_LABELS = tuple(PATH_TYPES)
RESIDUAL_CLASSES = (
    "U_EARLY_NEVER_BE",
    "P_EARLY_AFTER_BE",
    "P_PROFIT_THEN_FAILURE",
    "PROTECTED_DIP",
    "PROTECTED_GOOD",
    "OTHER",
)
FAILURE_CLASSES = (
    "U_EARLY_NEVER_BE",
    "P_EARLY_AFTER_BE",
    "P_PROFIT_THEN_FAILURE",
)
PROVEN_FAILURE_CLASSES = ("P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")
PROTECTED_CLASSES = ("PROTECTED_GOOD", "PROTECTED_DIP")
RANK_METRICS = (
    "GROSS_TERMINAL_LOSS",
    "PEAK_TO_CLOSE_GIVEBACK",
    "BELOW_BE_TERMINAL_LOSS",
)

DEV_CONTROL_FILL_N_EXPECTED = 84
DEV_CONTROL_CORE_N_EXPECTED = 18
DEV_CONTROL_ADDED_N_EXPECTED = 66
DEV_CONTROL_PNL_EXPECTED = 146680.0
FWD_CONTROL_FILL_N_EXPECTED = 20
FWD_CONTROL_CORE_N_EXPECTED = 9
FWD_CONTROL_ADDED_N_EXPECTED = 11
FWD_CONTROL_PNL_EXPECTED = -45100.0
YEN_PARITY_TOL = 0.01
CONCENTRATION_WARN = 0.50
DOMINANCE_RATIO = 1.25
MATERIAL_VS_WINNER_FRAC = 0.25
NOT_MATERIAL_FRAC = 0.10
FWD_CLASS_MIN_N = 3

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
K_SEARCH = False
FIXED_WAIT_USED = False
BPS_THRESHOLD_USED = False
TRAILING_STOP_USED = False
BB_VARIANT_USED = False
EMA_ADDED = False
RCI_ADDED = False
BOARD_USED = False
ML_USED = False
ENTRY_CHANGED = False
CAP_CHANGED = False
SIZING_CHANGED = False
TRUE_OOS = False
CERTIFIED = False
RUNTIME_CANDIDATE = False
RESEARCH_PARALLELISM = 1

SOURCE_FILES = (
    "exit_residual_rca_spec.py",
    "exit_residual_rca_harvest.py",
    "exit_residual_rca_analyze.py",
    "exit_residual_rca_publish.py",
    "exit_residual_rca.py",
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


def canonical_residual_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "PRIMARY_EXIT": PRIMARY_EXIT,
            "OCCUPANCY_SOT": OCCUPANCY_SOT,
            "BREAK_EVEN_DEF": BREAK_EVEN_DEF,
            "BOARD_FAMILY_STATUS": BOARD_FAMILY_STATUS,
            "BRANCH_U_STATUS": BRANCH_U_STATUS,
            "EXIT_BOARD_CONFIRMATION_SUPPORTED": False,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "CAUSAL_SPEC_SHA256": CAUSAL_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "POSITION_CAP": int(POSITION_CAP),
            "SHARES": int(SHARES),
            "PATH_LABELS": list(PATH_LABELS),
            "RESIDUAL_CLASSES": list(RESIDUAL_CLASSES),
            "FAILURE_CLASSES": list(FAILURE_CLASSES),
            "PROVEN_FAILURE_CLASSES": list(PROVEN_FAILURE_CLASSES),
            "RANK_METRICS": list(RANK_METRICS),
            "DEV_CONTROL_FILL_N_EXPECTED": int(DEV_CONTROL_FILL_N_EXPECTED),
            "FWD_CONTROL_FILL_N_EXPECTED": int(FWD_CONTROL_FILL_N_EXPECTED),
            "NEW_EXIT_RULE": False,
            "THRESHOLD_SEARCH": False,
            "K_SEARCH": False,
            "FIXED_WAIT_USED": False,
            "BPS_THRESHOLD_USED": False,
            "TRAILING_STOP_USED": False,
            "BB_VARIANT_USED": False,
            "BOARD_USED": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "TRUE_OOS": False,
            "research_parallelism": 1,
        }
    )


def spec_sha256_residual(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_residual_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_residual() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_false_break() == FALSE_BREAK_SPEC_SHA256_EXPECTED
assert NEW_EXIT_RULE is False
assert THRESHOLD_SEARCH is False
assert K_SEARCH is False
assert BB_VARIANT_USED is False
assert BOARD_USED is False
assert EXIT_BOARD_CONFIRMATION_SUPPORTED is False
assert int(POSITION_CAP) == 5
assert int(SHARES) == 100
assert "20260903" in FORBIDDEN_DAYS
assert list(LOCKED_SERIES_DAYS) == ["20260828", "20260831", "20260901", "20260902"]
assert int(DEV_CONTROL_FILL_N_EXPECTED) == 84
assert int(FWD_CONTROL_FILL_N_EXPECTED) == 20
