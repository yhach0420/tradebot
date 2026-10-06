"""V17 G3 two-bar confirmed BE-reloss. Frozen V13 entry. Last BE-family test. No threshold search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE as V12_FILL_EVIDENCE
from research.simple_tech_exit_family import FAMILY_ID, RESEARCH_PARALLELISM, TRUE_OOS
from research.simple_tech_exit_family.v14_spec import DEVELOPMENT_ENTRY_STACK as V14_STACK

ANALYSIS_ID = "SIMPLE_TECH_V17_TWO_BAR_CONFIRMED_BE_RELOSS"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V14_SPEC_SHA256_EXPECTED = "2cdd7fa85490bed2a4b6d0a935b6801cb6638c4033bbff82eb53ee5fdae04588"
V15_SPEC_SHA256_EXPECTED = "1322dd79ed08dc9afb442dd9f09c473a0a862e61c2de1ffdfcd1bf0a174362ce"
V16_SPEC_SHA256_EXPECTED = "6a59625cbbec189786fe1c6c32c2bd03b59b95bde40b96f68fb8374b5d9c4051"
V14_VERDICT_EXPECTED = "SIMPLE_TECH_V14_EXIT_PROBLEM_FOUND_MECHANISM_UNRESOLVED"
V15_VERDICT_EXPECTED = "SIMPLE_TECH_V15_BE_RELOSS_WINNER_HARM"
V16_VERDICT_EXPECTED = "SIMPLE_TECH_V16_BAR_CONFIRMED_WINNER_HARM"
DEVELOPMENT_ENTRY_STACK = V14_STACK
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
FILL_EVIDENCE = V12_FILL_EVIDENCE
SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
ELIGIBLE_SET_HASH_EXPECTED = "03d085b0d9402546e5f631d07073e2f89793b43b715e1e0ba5a23d160051be40"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
E4_WAIT_BUDGET_SEC = 5.0
PATH_WINDOW_SEC = 300.0
HORIZONS_SEC = (60.0, 180.0, 300.0)
MECHANISM_ID = "G3_PROFIT_ARMED_TWO_BAR_BE_RELOSS"
PROFIT_THRESHOLD_BPS = 0.0
P1_180_EXPECTED = 4
P2_180_EXPECTED = 11
P3_180_EXPECTED = 23
P1_300_EXPECTED = 4
P2_300_EXPECTED = 14
P3_300_EXPECTED = 19
G2_EVENT_N_180_V16 = 19
G2_EVENT_N_300_V16 = 20
P2_G2_EVENT_N_180_V16 = 11
P2_G2_EVENT_N_300_V16 = 13
P3_G2_EVENT_N_180_V16 = 8
P3_G2_EVENT_N_300_V16 = 6
MECH_MIN_EVENT_N = 10
MECH_MIN_DISTINCT_DAYS = 6
P2_CAPTURE_MIN = 0.70
EXIT_SPEC_FROZEN = False
EXIT_CERTIFIED = False
ENTRY_CERTIFIED = False
RUNTIME_CANDIDATE = False
RUNTIME_ADOPTION_ALLOWED = False
ENTRY_RULE_CHANGED = False
EMA_CHANGED = False
BB_CHANGED = False
RCI_CHANGED = False
PA_RESTORED = False
VOLUME_RESTORED = False
BOARD_RESTORED = False
THRESHOLD_SEARCH = False
GRID_SEARCH = False
ML_USED = False
C14_USED = False
TIME_EXIT_POLICY = False
TRAILING = False
THREE_BARS_CONFIRMATION = False
FOUR_BARS_CONFIRMATION = False
UNFILLED_VIRTUAL_POSITION = False
NEW_INDICATOR = False
PLUS_1BPS_ARMED = False
PLUS_3BPS_ARMED = False
PLUS_5BPS_ARMED = False
MFE10_ARMED = False
TRAIL_50 = False
TRAIL_80 = False


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


def canonical_v17_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
            "V10_SPEC_SHA256": V10_SPEC_SHA256_EXPECTED,
            "V12_SPEC_SHA256": V12_SPEC_SHA256_EXPECTED,
            "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
            "V14_SPEC_SHA256": V14_SPEC_SHA256_EXPECTED,
            "V15_SPEC_SHA256": V15_SPEC_SHA256_EXPECTED,
            "V16_SPEC_SHA256": V16_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "eligible_days": list(ELIGIBLE_DAYS),
            "development_entry_stack": DEVELOPMENT_ENTRY_STACK,
            "challenger": DEVELOPMENT_CHALLENGER,
            "signal_set_hash": SIGNAL_SET_HASH_EXPECTED,
            "eligible_set_hash": ELIGIBLE_SET_HASH_EXPECTED,
            "e4_fill_set_hash": E4_FILL_SET_HASH_EXPECTED,
            "filled_n": E4_FILLED_N_EXPECTED,
            "unfilled_n": E4_UNFILLED_N_EXPECTED,
            "exit_clock": "fill_time",
            "path_window_sec": PATH_WINDOW_SEC,
            "horizons_sec": list(HORIZONS_SEC),
            "horizons_are_rulers_not_time_exit": True,
            "fill_evidence": FILL_EVIDENCE,
            "wait_budget_sec": E4_WAIT_BUDGET_SEC,
            "mechanism": MECHANISM_ID,
            "arm": "first Bid1 > FillPrice",
            "event": "first two consecutive completed 1m Close <= FillPrice after arm",
            "decision_time": "second bar finalize_t",
            "confirmation_bars": 2,
            "profit_threshold_bps": float(PROFIT_THRESHOLD_BPS),
            "extra_bps_threshold": False,
            "mfe_threshold": False,
            "time_threshold": False,
            "three_bars_confirmation": False,
            "four_bars_confirmation": False,
            "rci_ema_bb_volume_board": False,
            "exit_spec_frozen": False,
            "exit_certified": False,
            "threshold_search": False,
            "grid_search": False,
            "ml": False,
            "c14": False,
            "time_exit_policy": False,
            "trailing": False,
            "plus_1bps_armed": False,
            "plus_3bps_armed": False,
            "plus_5bps_armed": False,
            "mfe10_armed": False,
            "trail_50": False,
            "trail_80": False,
            "passive_ask_sell": False,
            "inside_sell": False,
            "unfilled_virtual_position": False,
            "new_indicator": False,
            "entry_co_adaptation": False,
            "g1_rearm": False,
            "g2_rearm": False,
            "be_family_last_test": True,
            "research_parallelism": int(RESEARCH_PARALLELISM),
            "true_oos": bool(TRUE_OOS),
        }
    )


def spec_sha256_v17(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v17_spec()
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
