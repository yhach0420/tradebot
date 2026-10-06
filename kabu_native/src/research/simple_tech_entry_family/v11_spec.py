"""V11 TF1 signal vs execution-cost RCA. Frozen B1 stack. Quote replay only. No EXIT. No gates."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, FAMILY_ID, RCI_CROSS_LEVEL, RCI_PERIOD, WARMUP_BARS
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import PATH_SEC, V1_LOCKED

ANALYSIS_ID = "SIMPLE_TECH_V11_SIGNAL_VS_EXECUTION_COST_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
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
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
B1_MARKOUT_60_EXPECTED = -10.341459816473584
B1_MARKOUT_180_EXPECTED = -5.799542722666464
B1_MARKOUT_300_EXPECTED = -7.205716906744924
ROLE_MIN_EXECUTABLE_N = 20
MARKOUT_HORIZONS_SEC = (60.0, 180.0, 300.0)
MARKOUT_KINDS = ("GROSS_MID", "ENTRY_CROSS", "FULL_EXECUTABLE")


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


def canonical_v11_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
            "V10_SPEC_SHA256": V10_SPEC_SHA256_EXPECTED,
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
            "c14_forbidden": True,
            "exit_forbidden": True,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "pa_restored": False,
            "volume_restored": False,
            "new_timeframe": False,
            "quote_replay_sealed_capture": True,
            "signal_recapture": False,
            "markout_kinds": list(MARKOUT_KINDS),
            "gross_mid": "Mid0->MidH",
            "entry_cross": "Ask0->MidH",
            "full_executable": "Ask0->BidH",
            "decomposition": "exact_quote_differences",
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
            "role_min_executable_n": int(ROLE_MIN_EXECUTABLE_N),
            "tod_diagnostic_only": True,
            "spread_diagnostic_only": True,
            "r1b0_not_a_gate": True,
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v11(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v11_spec()
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
assert int(B1_EXECUTABLE_N_EXPECTED) == 126
assert int(B1_SIGNAL_N_EXPECTED) == 275
assert int(WARMUP_BARS) == 24
assert abs(float(RCI_CROSS_LEVEL) + 80.0) < 1e-12
assert tuple(MARKOUT_HORIZONS_SEC) == (60.0, 180.0, 300.0)
