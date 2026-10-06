"""V10 TF1 RCI/Board incremental role RCA. T3 is reference context. No PA/Volume/Persistence. No EXIT."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, FAMILY_ID, WARMUP_BARS
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import PATH_SEC, V1_LOCKED

ANALYSIS_ID = "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V9_SPEC_SHA256_EXPECTED = "d6a226c5d80f25211f312df2c3766d9385dc5147221d85829ab2a56a680c65e4"
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
NEW_TIMEFRAME = False
MIXED_TF_STRATEGY = False
CROSS_INDEPENDENT_NECESSITY_UNCONFIRMED = True
T3_IS_REFERENCE_CONTEXT = True
V8_TREND_OFF_RCI_BOARD_REUSED = False
B2_EXECUTABLE_N_EXPECTED = 106
B2_MARKOUT_180_EXPECTED = -8.698079493377563
B2_MARKOUT_300_EXPECTED = -10.239582977190492
ROLE_MIN_EXECUTABLE_N = 20
ARM_ORDER = ("B0_T3_PULLBACK", "B1_RCI", "B2_RCI_BOARD")
STATE_ORDER = ("R1B1", "R1B0", "R0B1", "R0B0")
MARKOUT_HORIZONS_SEC = (60.0, 180.0, 300.0)


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


def canonical_v10_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
            "V9_SPEC_SHA256": V9_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "tf": "TF1_only",
            "base": "T3_AND_PULLBACK",
            "t3_definition": "EMA9>EMA21 AND EMA21[t]>EMA21[t-3]",
            "t3_is_reference_context": True,
            "cross_independent_necessity_unconfirmed": True,
            "price_action": False,
            "volume_magnitude": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "threshold_search": False,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "pa_restored": False,
            "volume_restored": False,
            "persistence_added": False,
            "pq3_hard_gate": False,
            "pq3_diagnostic_only": True,
            "new_timeframe": False,
            "join_only_v8_tf1": True,
            "recapture": False,
            "v8_trend_off_rci_board_reused": False,
            "arms": list(ARM_ORDER),
            "states": list(STATE_ORDER),
            "states_diagnostic_only": True,
            "b1_vs_b0": "RCI_incremental",
            "b2_vs_b1": "Board_incremental",
            "ema_short": int(EMA_SHORT),
            "ema_long": int(EMA_LONG),
            "ema_slope_bars": int(EMA_SLOPE_BARS),
            "warmup_bars": int(WARMUP_BARS),
            "horizons_sec": list(MARKOUT_HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "b2_executable_n_expected": int(B2_EXECUTABLE_N_EXPECTED),
            "b2_markout_180_expected": float(B2_MARKOUT_180_EXPECTED),
            "b2_markout_300_expected": float(B2_MARKOUT_300_EXPECTED),
            "role_min_executable_n": int(ROLE_MIN_EXECUTABLE_N),
            "tod_diagnostic_only": True,
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v10(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v10_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert TRUE_OOS is False
assert EMA_CHANGED is False
assert RCI_CHANGED is False
assert PA_RESTORED is False
assert VOLUME_RESTORED is False
assert PERSISTENCE_ADDED is False
assert PQ3_HARD_GATE is False
assert V8_TREND_OFF_RCI_BOARD_REUSED is False
assert CROSS_INDEPENDENT_NECESSITY_UNCONFIRMED is True
assert T3_IS_REFERENCE_CONTEXT is True
assert tuple(ARM_ORDER) == ("B0_T3_PULLBACK", "B1_RCI", "B2_RCI_BOARD")
assert int(B2_EXECUTABLE_N_EXPECTED) == 106
assert int(WARMUP_BARS) == 24
assert int(EMA_SLOPE_BARS) == 3
