"""V23 stale execution coverage RCA. Frozen V22 ENTRY/E4/freshness. No fill replay. No PnL."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID

ANALYSIS_ID = "SIMPLE_TECH_V23_STALE_EXECUTION_COVERAGE_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V22_SPEC_SHA256_EXPECTED = "9dcd0febaab41ca7e1222d0e598921cdb52db87fcaa16908d601259a01ad810c"
V22_VERDICT_EXPECTED = "SIMPLE_TECH_V22_NO_SAFE_COVERAGE_EXPANSION_FOUND"
V22_PRIMARY_EXPECTED = "NO_SAFE_COVERAGE_EXPANSION_FOUND"
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
CLOCK_DISAGREE_SEC = 0.5
INGRESS_FRESH_SEC = float(BOARD_FRESHNESS_SEC)

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
FILL_REPLAY = False
PNL_EVAL = False
FUTURE_QUOTE_CARRY_BACK = False
FUTURE_RECEIVED_CARRY_BACK = False

STALE_CLASSES = (
    "S1_REAL_NO_FRESH_MARKET_UPDATE",
    "S2_EVENT_TIME_STALE_BUT_INGRESS_FRESH",
    "S3_CAPTURE_JOIN_ALIGNMENT_FAILURE",
    "S4_TIMESTAMP_SEMANTIC_MISMATCH",
    "S5_OTHER_PROVEN",
    "S6_UNRESOLVED",
)
AVOIDABLE_CLASSES = (
    "S2_EVENT_TIME_STALE_BUT_INGRESS_FRESH",
    "S3_CAPTURE_JOIN_ALIGNMENT_FAILURE",
    "S4_TIMESTAMP_SEMANTIC_MISMATCH",
)
REAL_CLASS = "S1_REAL_NO_FRESH_MARKET_UPDATE"

CASE_A_REAL_FRAC = 0.70
CASE_A_AVOIDABLE_MAX = 0.20
CASE_B_AVOIDABLE_FRAC = 0.30
CASE_B_MIN_DAYS = 3
CASE_B_MIN_SYMBOLS = 5
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


def canonical_v23_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V22_SPEC_SHA256": V22_SPEC_SHA256_EXPECTED,
            "V22_VERDICT": V22_VERDICT_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "FRESHNESS_THRESHOLD_CHANGED": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "EXIT_CHANGED": False,
            "SIZING_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "VIRTUAL_FILL": False,
            "FILL_REPLAY": False,
            "PNL_EVAL": False,
            "FUTURE_QUOTE_CARRY_BACK": False,
            "FUTURE_RECEIVED_CARRY_BACK": False,
            "TRUE_OOS": False,
            "target_cohort": "V22_UNEVALUABLE_stale_N_149",
            "clocks": {
                "ingress": ("received_at", "received_at_jst", "persisted_at", "received_at_utc"),
                "capture_event_epoch": "V22_join_clock_prefers_ingress_then_quote",
                "price_event": "CurrentPriceTime",
                "board_event": ("BidTime", "AskTime"),
                "canonical_fresh_v22": "board_age_sec_else_event_t_minus_CurrentPriceTime_AskTime_BidTime",
            },
            "classes": list(STALE_CLASSES),
            "class_priority": [
                "S6_if_critical_timestamps_missing",
                "S3_if_ingress_join_would_not_be_stale",
                "S4_if_CurrentPriceTime_stale_but_BidAskTime_fresh_or_payload_age_disagrees",
                "S2_if_same_record_event_clock_disagrees_with_ingress",
                "S1_if_board_and_price_quote_clocks_stale_vs_ingress",
                "S5_other_proven",
                "S6_unresolved",
            ],
            "avoidable_classes": list(AVOIDABLE_CLASSES),
            "decision": {
                "CASE_A_REAL_FRAC": float(CASE_A_REAL_FRAC),
                "CASE_A_AVOIDABLE_MAX": float(CASE_A_AVOIDABLE_MAX),
                "CASE_B_AVOIDABLE_FRAC": float(CASE_B_AVOIDABLE_FRAC),
                "CASE_B_MIN_DAYS": int(CASE_B_MIN_DAYS),
                "CASE_B_MIN_SYMBOLS": int(CASE_B_MIN_SYMBOLS),
                "CASE_D_UNRESOLVED_FRAC": float(CASE_D_UNRESOLVED_FRAC),
            },
            "research_parallelism": 1,
        }
    )


def spec_sha256_v23(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v23_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert ENTRY_CHANGED is False
assert E4_CHANGED is False
assert FRESHNESS_THRESHOLD_CHANGED is False
assert VIRTUAL_FILL is False
assert FILL_REPLAY is False
assert PNL_EVAL is False
assert int(RESEARCH_PARALLELISM) == 1
