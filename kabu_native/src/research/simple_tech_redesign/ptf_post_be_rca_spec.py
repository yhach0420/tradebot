"""PTF post-BE state transition RCA. Mechanism discovery only. No new EXIT."""
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

ANALYSIS_ID = "SIMPLE_TECH_PTF_POST_BE_STATE_TRANSITION_RCA"
SOURCE_COMPOSITION_VERDICT = "SIMPLE_TECH_EXIT_COMPOSITION_SHIFT_CONCENTRATION_DRIVEN"
PRIMARY_POPULATION = "BE_REACHED_OCCUPANCY_CONTROL_ONLY"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"
ANCHOR = "FIRST_ECONOMIC_BREAK_EVEN"
BOARD_FAMILY_STATUS = "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"
BRANCH_U_STATUS = "SIMPLE_TECH_BRANCH_U_EXIT_PATH_CLOSED"

DEV_BE_N_EXPECTED = 77
FWD_BE_N_EXPECTED = 19
DEV_CONTROL_FILL_N = 84
FWD_CONTROL_FILL_N = 20

PRIMARY_DIAGNOSTIC = "P_PROFIT_THEN_FAILURE"
COMPARE_PROTECTED = ("PROTECTED_GOOD", "PROTECTED_DIP")
COMPARE_PROVEN = "P_EARLY_AFTER_BE"
SECONDARY_CLASS = "OTHER"

PTF_TOP_DAY = {"DEVELOPMENT": "20260824", "FORWARD_BURNED": "20260831"}
PTF_TOP_SYMBOL = {"DEVELOPMENT": "6834", "FORWARD_BURNED": "6976"}

STRUCTURE_PRIMITIVES = (
    "A_EMA_STRUCTURE_LOSS_3M",
    "A_EMA_STRUCTURE_LOSS_5M",
    "D_BB_STRUCTURE_LOSS_3M",
    "E_RCI_ROLLOVER_3M",
    "TREND_LOST_1M",
)
PRIMARY_STRUCTURE = "A_EMA_STRUCTURE_LOSS_3M"

CLOSED_MECHANISMS = (
    "V27_PERSISTENCE_3M_EMA_K6",
    "V28_K6_PERSISTENCE_EXIT",
    "V29_TERMINAL_SEQUENCE",
    "BRANCH_U_BB_LOWER",
    "PRE_CAP_BOARD",
    "EXIT_BOARD_CONFIRMATION",
)

VERDICTS = (
    "SIMPLE_TECH_PTF_DISTINCT_POST_BE_MECHANISM_FOUND",
    "SIMPLE_TECH_PROVEN_FAILURE_SHARED_MECHANISM_FOUND",
    "SIMPLE_TECH_PTF_MECHANISM_ALREADY_CLOSED",
    "SIMPLE_TECH_PTF_POST_BE_MECHANISM_NOT_FOUND",
    "SIMPLE_TECH_PTF_POST_BE_INSUFFICIENT",
)

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False
RESEARCH_PARALLELISM = 1

SOURCE_FILES = (
    "ptf_post_be_rca_spec.py",
    "ptf_post_be_rca_harvest.py",
    "ptf_post_be_rca_analyze.py",
    "ptf_post_be_rca_publish.py",
    "ptf_post_be_rca.py",
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


def canonical_ptf_post_be_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "SOURCE_COMPOSITION_VERDICT": SOURCE_COMPOSITION_VERDICT,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "PRIMARY_EXIT": PRIMARY_EXIT,
            "ANCHOR": ANCHOR,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "DEV_BE_N_EXPECTED": int(DEV_BE_N_EXPECTED),
            "FWD_BE_N_EXPECTED": int(FWD_BE_N_EXPECTED),
            "PRIMARY_DIAGNOSTIC": PRIMARY_DIAGNOSTIC,
            "NEW_EXIT_RULE": False,
            "THRESHOLD_SEARCH": False,
            "TRUE_OOS": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
        }
    )


def spec_sha256_ptf_post_be(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_ptf_post_be_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_ptf_post_be() -> str:
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
assert "20260903" in FORBIDDEN_DAYS
