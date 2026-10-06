"""V12 ENTRY execution architecture. Frozen B1 signal. No EXIT. No C14. No extra waits."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, FAMILY_ID, RCI_CROSS_LEVEL, RCI_PERIOD, WARMUP_BARS
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import PATH_SEC, V1_LOCKED

ANALYSIS_ID = "SIMPLE_TECH_V12_ENTRY_EXECUTION_ARCHITECTURE"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
V11_SPEC_SHA256_EXPECTED = "32803ea9186a385ad51de3995e42d66455782995e290c7e1a587e2cc3bf6bd38"
V11_VERDICT_EXPECTED = "SIMPLE_TECH_V11_GROSS_SIGNAL_EDGE_EXECUTION_COST_DOMINANT"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
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
REPRICE_ALLOWED = False
CHASE_ALLOWED = False
FALLBACK_MARKET = False
OPTIMISTIC_TOUCH_FILL = False
ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH = True
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
B1_MARKOUT_60_EXPECTED = -10.341459816473584
B1_MARKOUT_180_EXPECTED = -5.799542722666464
B1_MARKOUT_300_EXPECTED = -7.205716906744924
ROLE_MIN_FILL_N = 20
MARKOUT_HORIZONS_SEC = (60.0, 180.0, 300.0)
WAIT_BUDGETS_SEC = (5.0, 15.0, 30.0)
CONTROL_ARM = "E0_CROSS_NOW"
POLICY_ARMS = (
    ("E1_BID_W5", "PASSIVE_BID", 5.0),
    ("E2_BID_W15", "PASSIVE_BID", 15.0),
    ("E3_BID_W30", "PASSIVE_BID", 30.0),
    ("E4_INSIDE1_W5", "IMPROVE_1TICK", 5.0),
    ("E5_INSIDE1_W15", "IMPROVE_1TICK", 15.0),
    ("E6_INSIDE1_W30", "IMPROVE_1TICK", 30.0),
)
FAMILY_PAIRS = {
    "PASSIVE_BID": (("E1_BID_W5", "E2_BID_W15"), ("E2_BID_W15", "E3_BID_W30")),
    "IMPROVE_1TICK": (("E4_INSIDE1_W5", "E5_INSIDE1_W15"), ("E5_INSIDE1_W15", "E6_INSIDE1_W30")),
}
FILL_EVIDENCE = "ASK_CROSS_CONSERVATIVE"
PRIMARY_MARKOUT = "FILL_TO_MID_SIGNAL_ANCHORED"
SECONDARY_MARKOUT = "FILL_TO_BID_SIGNAL_ANCHORED"
ADVERSE_BPS_GAP = 5.0
ADVERSE_UNFILLED_MIN_N = 10


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


def canonical_v12_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
            "V10_SPEC_SHA256": V10_SPEC_SHA256_EXPECTED,
            "V11_SPEC_SHA256": V11_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "tf": "TF1_only",
            "frozen_stack": "T3_AND_PULLBACK_AND_RCI",
            "t3_definition": "EMA9>EMA21 AND EMA21[t]>EMA21[t-3]",
            "pullback": "V1",
            "rci": "RCI9_-80_cross",
            "board_hard_veto": False,
            "inverse_board_gate": False,
            "price_action": False,
            "volume_magnitude": False,
            "persistence_gate": False,
            "pq3_gate": False,
            "new_indicator": False,
            "threshold_search": False,
            "signal_rule_changed": False,
            "spread_threshold_gate": False,
            "extra_wait_search": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "pa_restored": False,
            "volume_restored": False,
            "new_timeframe": False,
            "entry_signal_stack_frozen": True,
            "control_arm": CONTROL_ARM,
            "price_families": ["PASSIVE_BID", "IMPROVE_1TICK"],
            "wait_budgets_sec": list(WAIT_BUDGETS_SEC),
            "policy_arms": [list(x) for x in POLICY_ARMS],
            "no_reprice": True,
            "no_chase": True,
            "no_fallback_market": True,
            "no_optimistic_touch_fill": True,
            "fill_evidence": FILL_EVIDENCE,
            "primary_markout": PRIMARY_MARKOUT,
            "secondary_markout": SECONDARY_MARKOUT,
            "evaluation_anchor": "signal_t0",
            "unfilled_contribute_zero": True,
            "unconditional_denominator": int(B1_EXECUTABLE_N_EXPECTED),
            "quote_replay_sealed_capture": True,
            "signal_recapture": False,
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
            "b1_markout_60_expected": float(B1_MARKOUT_60_EXPECTED),
            "b1_markout_180_expected": float(B1_MARKOUT_180_EXPECTED),
            "b1_markout_300_expected": float(B1_MARKOUT_300_EXPECTED),
            "role_min_fill_n": int(ROLE_MIN_FILL_N),
            "tod_diagnostic_only": True,
            "spread_diagnostic_only": True,
            "r1b0_not_a_gate": True,
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v12(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v12_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert TRUE_OOS is False
assert SIGNAL_RULE_CHANGED is False
assert BOARD_HARD_VETO is False
assert INVERSE_BOARD_GATE is False
assert PA_RESTORED is False
assert VOLUME_RESTORED is False
assert EXIT_IMPLEMENTED is False
assert EXTRA_WAIT_SEARCH is False
assert OPTIMISTIC_TOUCH_FILL is False
assert ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH is True
assert int(B1_EXECUTABLE_N_EXPECTED) == 126
assert int(B1_SIGNAL_N_EXPECTED) == 275
assert int(WARMUP_BARS) == 24
assert abs(float(RCI_CROSS_LEVEL) + 80.0) < 1e-12
assert tuple(MARKOUT_HORIZONS_SEC) == (60.0, 180.0, 300.0)
assert tuple(WAIT_BUDGETS_SEC) == (5.0, 15.0, 30.0)
assert len(POLICY_ARMS) == 6
