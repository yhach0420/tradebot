"""V13 E4 structure verification. Do not change V12 official results. No EXIT. No new search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, FAMILY_ID, RCI_CROSS_LEVEL, RCI_PERIOD, WARMUP_BARS
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import PATH_SEC, V1_LOCKED
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE as V12_FILL_EVIDENCE

ANALYSIS_ID = "SIMPLE_TECH_V13_ENTRY_EXECUTION_STRUCTURE_VERIFICATION"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
V11_SPEC_SHA256_EXPECTED = "32803ea9186a385ad51de3995e42d66455782995e290c7e1a587e2cc3bf6bd38"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V12_VERDICT_EXPECTED = "SIMPLE_TECH_V12_EXECUTION_HELPFUL_EDGE_STILL_INSUFFICIENT"
V12_CASE_EXPECTED = "B"
V12_SELECTED_POLICY = "E1_BID_W5"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
ENTRY_CERTIFIED = False
RUNTIME_CANDIDATE = False
RUNTIME_ADOPTION_ALLOWED = False
ASK_RUNTIME_ADOPTION_ALLOWED = False
EXIT_IMPLEMENTED = False
ENTRY_RULE_CHANGED = False
C14_USED_FOR_SELECTION = False
THRESHOLD_SEARCH = False
EMA_CHANGED = False
BB_CHANGED = False
RCI_CHANGED = False
PA_RESTORED = False
VOLUME_RESTORED = False
PERSISTENCE_ADDED = False
PQ3_HARD_GATE = False
BOARD_HARD_VETO = False
INVERSE_BOARD_GATE = False
NEW_INDICATOR = False
NEW_TIMEFRAME = False
MIXED_TF_STRATEGY = False
SIGNAL_RULE_CHANGED = False
SPREAD_THRESHOLD_GATE = False
EXTRA_WAIT_SEARCH = False
PROFIT_RANKING = False
NEW_PERFORMANCE_GATE = False
REPRICE_ALLOWED = False
CHASE_ALLOWED = False
FALLBACK_MARKET = False
OPTIMISTIC_TOUCH_FILL = False
V12_RETROACTIVE_CASE_A_FORBIDDEN = True
V12_SELECTION_SEMANTICS_AMBIGUOUS = True
ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH = True
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
E4_FILL_RATE_EXPECTED = 38.0 / 126.0
E4_INSIDE_COLLAPSE_N_EXPECTED = 80
E4_WAIT_BUDGET_SEC = 5.0
PARITY_ABS_TOL = 1e-9
FILL_EVIDENCE = V12_FILL_EVIDENCE
MARKOUT_HORIZONS_SEC = (60.0, 180.0, 300.0)

E4_UNCOND_MID_EXPECTED = {
    "60": 4.843247525004207,
    "180": 6.275295903844826,
    "300": 3.7420009153922686,
}
E4_UNCOND_BID_EXPECTED = {
    "60": 3.7118258996081206,
    "180": 5.294920250387348,
    "300": 2.6669237088570106,
}
E4_FILLED_GROSS_EXPECTED = {
    "60": 14.2449270103132,
    "180": 18.99097204050863,
    "300": 10.594504176520035,
}
E4_UNFILLED_GROSS_EXPECTED = {
    "60": -1.0422384158467717,
    "180": 2.431929715893311,
    "300": 5.194731847076495,
}
E4_BID_POS_DAY_180_EXPECTED = 11
E4_BID_NEG_DAY_180_EXPECTED = 5
E4_BID_POS_DAY_300_EXPECTED = 10
E4_BID_NEG_DAY_300_EXPECTED = 6
E4_BID_EX_BEST_180_EXPECTED = 5.031879342279906
E4_BID_EX_BEST_300_EXPECTED = 0.7047337106056968


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


def canonical_v13_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
            "V10_SPEC_SHA256": V10_SPEC_SHA256_EXPECTED,
            "V11_SPEC_SHA256": V11_SPEC_SHA256_EXPECTED,
            "V12_SPEC_SHA256": V12_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "tf": "TF1_only",
            "frozen_stack": "T3_AND_PULLBACK_AND_RCI",
            "t3_definition": "EMA9>EMA21 AND EMA21[t]>EMA21[t-3]",
            "pullback": "V1",
            "rci": "RCI9_-80_cross",
            "development_challenger": DEVELOPMENT_CHALLENGER,
            "v12_selected_policy": V12_SELECTED_POLICY,
            "v12_case": V12_CASE_EXPECTED,
            "v12_verdict": V12_VERDICT_EXPECTED,
            "v12_edge_repaired": False,
            "v12_selection_semantics_ambiguous": True,
            "v12_retroactive_case_a_forbidden": True,
            "no_new_profit_ranking": True,
            "no_extra_wait": True,
            "no_new_performance_gate": True,
            "board_hard_veto": False,
            "inverse_board_gate": False,
            "price_action": False,
            "volume_magnitude": False,
            "persistence_gate": False,
            "pq3_gate": False,
            "threshold_search": False,
            "signal_rule_changed": False,
            "spread_threshold_gate": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "fill_evidence": FILL_EVIDENCE,
            "wait_budget_sec": float(E4_WAIT_BUDGET_SEC),
            "no_reprice": True,
            "no_chase": True,
            "no_fallback_market": True,
            "no_optimistic_touch_fill": True,
            "evaluation_anchor": "signal_t0",
            "unfilled_contribute_zero": True,
            "unconditional_denominator": int(B1_EXECUTABLE_N_EXPECTED),
            "parity_abs_tol": float(PARITY_ABS_TOL),
            "ema_short": int(EMA_SHORT),
            "ema_long": int(EMA_LONG),
            "ema_slope_bars": int(EMA_SLOPE_BARS),
            "warmup_bars": int(WARMUP_BARS),
            "rci_period": int(RCI_PERIOD),
            "rci_cross_level": float(RCI_CROSS_LEVEL),
            "horizons_sec": list(MARKOUT_HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "b1_signal_n_expected": int(B1_SIGNAL_N_EXPECTED),
            "b1_executable_n_expected": int(B1_EXECUTABLE_N_EXPECTED),
            "e4_filled_n_expected": int(E4_FILLED_N_EXPECTED),
            "e4_inside_collapse_n_expected": int(E4_INSIDE_COLLAPSE_N_EXPECTED),
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "entry_certified": False,
            "runtime_candidate": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v13(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v13_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert TRUE_OOS is False
assert ENTRY_CERTIFIED is False
assert PROFIT_RANKING is False
assert NEW_PERFORMANCE_GATE is False
assert EXIT_IMPLEMENTED is False
assert V12_RETROACTIVE_CASE_A_FORBIDDEN is True
assert V12_SELECTION_SEMANTICS_AMBIGUOUS is True
assert DEVELOPMENT_CHALLENGER == "E4_INSIDE1_W5"
assert V12_SELECTED_POLICY == "E1_BID_W5"
assert abs(float(E4_FILL_RATE_EXPECTED) - 38.0 / 126.0) < 1e-18
assert tuple(MARKOUT_HORIZONS_SEC) == (60.0, 180.0, 300.0)
assert float(E4_WAIT_BUDGET_SEC) == 5.0
assert FILL_EVIDENCE == "ASK_CROSS_CONSERVATIVE"
