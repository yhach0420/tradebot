"""V25 corrected execution baseline reconciliation. V24 official verdict frozen. No economic selection."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID

ANALYSIS_ID = "SIMPLE_TECH_V25_CORRECTED_EXECUTION_BASELINE_RECONCILIATION"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V22_SPEC_SHA256_EXPECTED = "9dcd0febaab41ca7e1222d0e598921cdb52db87fcaa16908d601259a01ad810c"
V23_SPEC_SHA256_EXPECTED = "943abf02194bc03511e3ed0a596b4db6c9c664caf203f58c7ab6306db3bc8a83"
V24_SPEC_SHA256_EXPECTED = "4a91218ca164f3a3cf0e86eaa75b8efb103ebc7d8b82e7b38a4d3ab134c90889"
V24_VERDICT_EXPECTED = "SIMPLE_TECH_V24_LEGACY_PARITY_FAILED"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"

SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
CORRECTED_FILL_HASH_EXPECTED = "7e783f4d580d79ab137ce437d63ed5591c50b22fc387271c22eec6398cfe806c"
B1_SIGNAL_N_EXPECTED = 275
CORRECTED_EVALUABLE_N_EXPECTED = 273
CORRECTED_UNEVALUABLE_N_EXPECTED = 2
CORRECTED_E4_FILLED_N_EXPECTED = 52
CORRECTED_E4_NONFILLED_N_EXPECTED = 221
CANONICAL_FRESHNESS_SEC = float(BOARD_FRESHNESS_SEC)
E4_WAIT_BUDGET_SEC = 5.0

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
ENTRY_CHANGED = False
E4_CHANGED = False
FRESHNESS_THRESHOLD_CHANGED = False
EXIT_CHANGED = False
SIZING_CHANGED = False
PNL_EVAL = False
FIXED180_PNL_USED = False
LEGACY_126_38_88_REQUIRED = False
CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS = False

SPECIAL_CASES = (
    {"id": "SPECIAL_CASE_5985", "date": "20260722", "symbol": "5985", "kind": "BOARD_STALE_PRICE_FRESH"},
    {"id": "SPECIAL_CASE_3696", "date": "20260817", "symbol": "3696", "kind": "BOARD_STALE_PRICE_FRESH"},
    {"id": "SPECIAL_CASE_6890", "date": "20260731", "symbol": "6890", "kind": "LEGACY_NONFILL_TO_CORRECTED_FILL"},
    {"id": "SPECIAL_CASE_6703", "date": "20260803", "symbol": "6703", "kind": "LEGACY_NONFILL_TO_CORRECTED_FILL"},
    {"id": "SPECIAL_CASE_4401", "date": "20260810", "symbol": "4401", "kind": "LEGACY_NONFILL_TO_CORRECTED_FILL"},
    {"id": "SPECIAL_CASE_581A", "date": "20260825", "symbol": "581A", "kind": "SAME_PRICE_EARLIER_FILL"},
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


def canonical_v25_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V24_SPEC_SHA256": V24_SPEC_SHA256_EXPECTED,
            "V24_VERDICT_FROZEN": V24_VERDICT_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
            "FRESHNESS_THRESHOLD_CHANGED": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "EXIT_CHANGED": False,
            "SIZING_CHANGED": False,
            "PNL_EVAL": False,
            "FIXED180_PNL_USED": False,
            "LEGACY_126_38_88_REQUIRED": False,
            "CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS": False,
            "TRUE_OOS": False,
            "board_freshness": {
                "preferred": "BOARD_EVENT_TIME_BidTime_AskTime",
                "fallback": "INGRESS_RECEIVED_AT",
                "forbidden": "CurrentPriceTime",
            },
            "expected": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "CORRECTED_EXECUTION_EVALUABLE_N": int(CORRECTED_EVALUABLE_N_EXPECTED),
                "CORRECTED_EXECUTION_UNEVALUABLE_N": int(CORRECTED_UNEVALUABLE_N_EXPECTED),
                "CORRECTED_E4_FILLED_N": int(CORRECTED_E4_FILLED_N_EXPECTED),
                "CORRECTED_E4_NONFILLED_N": int(CORRECTED_E4_NONFILLED_N_EXPECTED),
                "CORRECTED_FILL_HASH": CORRECTED_FILL_HASH_EXPECTED,
            },
            "special_cases": [dict(x) for x in SPECIAL_CASES],
            "research_parallelism": 1,
        }
    )


def spec_sha256_v25(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v25_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert ENTRY_CHANGED is False
assert E4_CHANGED is False
assert FRESHNESS_THRESHOLD_CHANGED is False
assert LEGACY_126_38_88_REQUIRED is False
assert PNL_EVAL is False
assert int(RESEARCH_PARALLELISM) == 1
