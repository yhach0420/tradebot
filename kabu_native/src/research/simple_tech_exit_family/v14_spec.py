"""V14 EXIT state-path RCA. Frozen V13 entry. No EXIT rule. No threshold search. No C14."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, BB_PERIOD, BB_SIGMA, RCI_CROSS_LEVEL, RCI_PERIOD
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE as V12_FILL_EVIDENCE
from research.simple_tech_exit_family import ANALYSIS_ID, FAMILY_ID, RESEARCH_PARALLELISM, TRUE_OOS

PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V13_VERDICT_EXPECTED = "SIMPLE_TECH_V13_ENTRY_DEVELOPMENT_FREEZE_READY"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
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
PRIMARY_HORIZONS = (180, 300)
EVENT_IDS = ("D1_T3_CONTEXT_LOST", "D2_RCI_RELAPSE_MINUS80", "D3_BB_LOWER_CLOSE_BREACH")
MECH_MIN_EVENT_N = 10
MECH_MIN_DISTINCT_DAYS = 6
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
SPREAD_THRESHOLD_GATE = False
THRESHOLD_SEARCH = False
GRID_SEARCH = False
ML_USED = False
C14_USED = False
TIME_EXIT_POLICY = False
TRAILING = False
PROFIT_PCT_STOP = False
PASSIVE_ASK_SELL = False
INSIDE_SELL = False
EXIT_WAIT = False
UNFILLED_VIRTUAL_POSITION = False
NEW_INDICATOR = False


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


def canonical_v14_spec() -> dict[str, Any]:
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
            "events": list(EVENT_IDS),
            "ema_short": int(EMA_SHORT),
            "ema_long": int(EMA_LONG),
            "ema_slope_bars": int(EMA_SLOPE_BARS),
            "bb_period": int(BB_PERIOD),
            "bb_sigma": float(BB_SIGMA),
            "rci_period": int(RCI_PERIOD),
            "rci_cross_level": float(RCI_CROSS_LEVEL),
            "exit_spec_frozen": False,
            "exit_certified": False,
            "threshold_search": False,
            "grid_search": False,
            "ml": False,
            "c14": False,
            "time_exit_policy": False,
            "trailing": False,
            "passive_ask_sell": False,
            "inside_sell": False,
            "unfilled_virtual_position": False,
            "new_indicator": False,
            "entry_co_adaptation": False,
            "research_parallelism": int(RESEARCH_PARALLELISM),
            "true_oos": bool(TRUE_OOS),
        }
    )


def spec_sha256_v14(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v14_spec()
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
