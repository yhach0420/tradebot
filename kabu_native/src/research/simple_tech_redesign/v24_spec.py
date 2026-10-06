"""V24 board freshness semantics correction + frozen E4 replay. No ENTRY/E4/threshold/EXIT/sizing change."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID

ANALYSIS_ID = "SIMPLE_TECH_V24_BOARD_FRESHNESS_SEMANTICS_CORRECTION_REPLAY"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V22_SPEC_SHA256_EXPECTED = "9dcd0febaab41ca7e1222d0e598921cdb52db87fcaa16908d601259a01ad810c"
V22_VERDICT_EXPECTED = "SIMPLE_TECH_V22_NO_SAFE_COVERAGE_EXPANSION_FOUND"
V23_SPEC_SHA256_EXPECTED = "943abf02194bc03511e3ed0a596b4db6c9c664caf203f58c7ab6306db3bc8a83"
V23_VERDICT_EXPECTED = "SIMPLE_TECH_V23_AVOIDABLE_STALE_COVERAGE_FOUND"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"

SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
ELIGIBLE_SET_HASH_EXPECTED = "03d085b0d9402546e5f631d07073e2f89793b43b715e1e0ba5a23d160051be40"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
STALE_N_EXPECTED = 149
CANONICAL_FRESHNESS_SEC = float(BOARD_FRESHNESS_SEC)
E4_WAIT_BUDGET_SEC = 5.0
PARITY_ABS_TOL = 1e-6

BOARD_FRESHNESS_CLOCK_SOURCES = ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT")
FORBIDDEN_BOARD_CLOCK = "CurrentPriceTime"

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
ENTRY_CHANGED = False
E4_CHANGED = False
FRESHNESS_THRESHOLD_CHANGED = False
EXIT_CHANGED = False
SIZING_CHANGED = False
THRESHOLD_SEARCH = False
VIRTUAL_FILL = False
FILL_REPLAY_ON_FUTURE_QUOTE = False
PNL_EVAL = False
FIXED180_PNL_USED = False
HORIZON_SELECTION = False
FUTURE_QUOTE_CARRY_BACK = False
FUTURE_TIMESTAMP_CARRY_BACK = False
CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS = False

CASE_A_RECOVERED_FRAC = 0.70
CASE_A_NEW_FILL_MIN_N = 10
CASE_A_NEW_FILL_MIN_DAYS = 3
CASE_A_NEW_FILL_MIN_SYMBOLS = 5
CASE_D_UNRESOLVED_FRAC = 0.50


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


def canonical_v24_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V22_SPEC_SHA256": V22_SPEC_SHA256_EXPECTED,
            "V23_SPEC_SHA256": V23_SPEC_SHA256_EXPECTED,
            "V23_VERDICT": V23_VERDICT_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
            "E4_FILL": "ASK_CROSS_CONSERVATIVE",
            "FRESHNESS_THRESHOLD_CHANGED": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "EXIT_CHANGED": False,
            "SIZING_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "VIRTUAL_FILL": False,
            "FILL_REPLAY_ON_FUTURE_QUOTE": False,
            "PNL_EVAL": False,
            "FIXED180_PNL_USED": False,
            "HORIZON_SELECTION": False,
            "FUTURE_QUOTE_CARRY_BACK": False,
            "FUTURE_TIMESTAMP_CARRY_BACK": False,
            "CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS": False,
            "TRUE_OOS": False,
            "board_freshness": {
                "preferred": "BOARD_EVENT_TIME_BidTime_AskTime",
                "fallback": "INGRESS_RECEIVED_AT",
                "forbidden": "CurrentPriceTime",
                "price_freshness": "separate_CurrentPriceTime_not_used_for_execution_gate",
            },
            "legacy_locks": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "LEGACY_EVALUABLE_N": int(B1_EXECUTABLE_N_EXPECTED),
                "LEGACY_E4_FILLED_N": int(E4_FILLED_N_EXPECTED),
                "LEGACY_E4_NONFILLED_N": int(E4_UNFILLED_N_EXPECTED),
                "STALE_N": int(STALE_N_EXPECTED),
            },
            "decision": {
                "CASE_A_RECOVERED_FRAC": float(CASE_A_RECOVERED_FRAC),
                "CASE_A_NEW_FILL_MIN_N": int(CASE_A_NEW_FILL_MIN_N),
                "CASE_A_NEW_FILL_MIN_DAYS": int(CASE_A_NEW_FILL_MIN_DAYS),
                "CASE_A_NEW_FILL_MIN_SYMBOLS": int(CASE_A_NEW_FILL_MIN_SYMBOLS),
                "CASE_D_UNRESOLVED_FRAC": float(CASE_D_UNRESOLVED_FRAC),
            },
            "research_parallelism": 1,
        }
    )


def spec_sha256_v24(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v24_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert ENTRY_CHANGED is False
assert E4_CHANGED is False
assert FRESHNESS_THRESHOLD_CHANGED is False
assert CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS is False
assert VIRTUAL_FILL is False
assert PNL_EVAL is False
assert int(RESEARCH_PARALLELISM) == 1
