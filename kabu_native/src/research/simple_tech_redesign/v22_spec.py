"""V22 ENTRY coverage RCA. Frozen T3_PULLBACK_RCI + E4. No ENTRY/EXIT/sizing change. No threshold search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import ANALYSIS_ID, FAMILY_ID

PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V7_SPEC_SHA256_EXPECTED = "5244e3a18aa733cd49ba52bf7e5598b13450c4a24995e865650d164470d723ae"
V6_SPEC_SHA256_EXPECTED = "dca616aa938fe6290dbe1caf15712d0d7827fd75871927d7a480227cc03c5ae0"

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
FIXED180_STATUS = "HISTORICAL_BENCHMARK_ONLY_NOT_FINAL_EXIT"
FIXED180_SELECTION_METRIC = False

SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
ELIGIBLE_SET_HASH_EXPECTED = "03d085b0d9402546e5f631d07073e2f89793b43b715e1e0ba5a23d160051be40"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
E4_WAIT_BUDGET_SEC = 5.0
B1_MARKOUT_60_EXPECTED = -10.341459816473584
B1_MARKOUT_180_EXPECTED = -5.799542722666464
B1_MARKOUT_300_EXPECTED = -7.205716906744924
PARITY_ABS_TOL = 1e-6

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
RUNTIME_ADOPTION_ALLOWED = False
POSITION_SIZING_SPEC_FROZEN = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
SIZING_CHANGED = False
THRESHOLD_SEARCH = False
EMA_CHANGED = False
BB_CHANGED = False
RCI_CHANGED = False
WAIT_SEARCH = False
VOLUME_THRESHOLD_SEARCH = False
BOARD_THRESHOLD_SEARCH = False
NEW_INDICATOR = False
C14_USED = False
VIRTUAL_FILL_COHORT_C = False
SINGLE_HORIZON_SELECTION = False
FIXED180_PNL_USED_FOR_ENTRY = False

PATH_HORIZONS_SEC = (60.0, 180.0, 300.0, 600.0)
PATH_KINDS = ("mid_mid", "ask_mid", "ask_bid")
COHORT_MIN_N = 20
SHAPE_HORIZON_MAJORITY = 3
SHAPE_KIND_MAJORITY = 2
V7_SHAPE_HORIZON_MAJORITY = 2
PATH_COVERAGE_MIN_FRAC = 0.80

UNEVAL_CLASSES = ("missing_quote", "missing_qty", "special_quote", "itayose", "stale", "other")

FROZEN_ARCHETYPES = (
    "B1_CURRENT_T3_PULLBACK_RCI",
    "TREND_RCI_NO_PULLBACK",
    "TREND_PULLBACK_NO_RCI",
    "PULLBACK_RCI_NO_TREND",
    "TREND_PA_NO_PULLBACK",
)

DEFICIENCY_ORDER = (
    "DATA_EXECUTION_EVIDENCE_LIMITED",
    "EXECUTION_COVERAGE_DEFICIENCY",
    "CURRENT_PULLBACK_TOO_NARROW",
    "COMPLEMENTARY_ENTRY_ARCHETYPE_NEEDED",
    "NO_SAFE_COVERAGE_EXPANSION_FOUND",
)

SIZING_DIRECTION_LOCK = {
    "UNIVERSE_INDEPENDENT_OF_MARGIN": True,
    "MARGIN_USES": ("position_quantity", "capital_feasibility", "concurrent_capital_usage"),
    "CAPITAL_SHORT": "CAPITAL_REJECT",
    "UNIVERSE_EXCLUSION_FORBIDDEN": True,
    "SIZING_ECONOMICS_AFTER_ENTRY_EXIT_CANDIDATES": True,
}

EXIT_DIRECTION_LOCK = {
    "ELAPSED_SECONDS_EXIT_FORBIDDEN_AFTER_V22": True,
    "STATE_DETERIORATION_TFS": ("1m", "3m", "5m"),
    "STATE_FAMILIES": ("MA", "BB", "RCI", "Volume"),
    "TIME_HORIZON_IS_DIAGNOSTIC_RULER_ONLY": True,
}


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


def canonical_v22_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
            "V12_SPEC_SHA256": V12_SPEC_SHA256_EXPECTED,
            "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
            "V7_SPEC_SHA256": V7_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "DEVELOPMENT_CHALLENGER": DEVELOPMENT_CHALLENGER,
            "FIXED180_STATUS": FIXED180_STATUS,
            "FIXED180_SELECTION_METRIC": False,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "SIZING_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "EMA_CHANGED": False,
            "BB_CHANGED": False,
            "RCI_CHANGED": False,
            "WAIT_SEARCH": False,
            "VOLUME_THRESHOLD_SEARCH": False,
            "BOARD_THRESHOLD_SEARCH": False,
            "NEW_INDICATOR": False,
            "C14_USED": False,
            "VIRTUAL_FILL_COHORT_C": False,
            "SINGLE_HORIZON_SELECTION": False,
            "FIXED180_PNL_USED_FOR_ENTRY": False,
            "TRUE_OOS": False,
            "RUNTIME_CANDIDATE": False,
            "POSITION_SIZING_SPEC_FROZEN": False,
            "path_horizons_sec": list(PATH_HORIZONS_SEC),
            "path_kinds": list(PATH_KINDS),
            "path_anchor": "signal_t0_not_fill_t_not_exit",
            "mfe_mae": "per_horizon_ask_bid_and_mid_mid_and_ask_mid_on_causal_quotes",
            "cohorts": {
                "A": "E4_FILLED",
                "B": "SIGNAL_AND_EXECUTION_EVALUABLE_AND_E4_NONFILL",
                "C": "SIGNAL_AND_EXECUTION_UNEVALUABLE",
            },
            "uneval_classes": list(UNEVAL_CLASSES),
            "technical_state": {
                "tfs": ("1m", "3m", "5m"),
                "completed_bars_only": True,
                "families": ("EMA9", "EMA21", "BB20_2SIGMA", "RCI9", "Volume"),
                "board": "support_diagnostic_only",
            },
            "decision": {
                "cohort_min_n": int(COHORT_MIN_N),
                "shape_horizon_majority": int(SHAPE_HORIZON_MAJORITY),
                "shape_kind_majority": int(SHAPE_KIND_MAJORITY),
                "v7_shape_horizon_majority": int(V7_SHAPE_HORIZON_MAJORITY),
                "path_coverage_min_frac": float(PATH_COVERAGE_MIN_FRAC),
                "q1": "cohort_B_positive_path_shape_on_majority_kinds_without_using_180_exit_pnl",
                "q2": "current_signal_n_is_primary_only_if_q1_false_and_no_execution_miss",
                "q3": "TREND_RCI_NO_PULLBACK_is_frozen_near_miss_of_current_pullback",
                "q4": "TREND_PA_NO_PULLBACK_or_PULLBACK_RCI_NO_TREND_if_not_q3",
                "rci_fail_is_not_case_b": True,
                "deficiency_order": list(DEFICIENCY_ORDER),
            },
            "frozen_archetypes": list(FROZEN_ARCHETYPES),
            "sizing_direction_lock": SIZING_DIRECTION_LOCK,
            "exit_direction_lock": EXIT_DIRECTION_LOCK,
            "research_parallelism": 1,
        }
    )


def spec_sha256_v22(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v22_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert ENTRY_CHANGED is False
assert EXIT_CHANGED is False
assert THRESHOLD_SEARCH is False
assert VIRTUAL_FILL_COHORT_C is False
assert SINGLE_HORIZON_SELECTION is False
assert FIXED180_PNL_USED_FOR_ENTRY is False
assert tuple(PATH_HORIZONS_SEC) == (60.0, 180.0, 300.0, 600.0)
