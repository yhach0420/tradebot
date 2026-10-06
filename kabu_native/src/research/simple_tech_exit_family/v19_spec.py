"""V19 EXIT structure verification of V18 FIXED180_FIRST_CAUSAL_BID. No new EXIT search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE as V12_FILL_EVIDENCE
from research.simple_tech_exit_family import FAMILY_ID, RESEARCH_PARALLELISM, TRUE_OOS
from research.simple_tech_exit_family.v14_spec import DEVELOPMENT_ENTRY_STACK as V14_STACK
from research.simple_tech_exit_family.v18_spec import EXIT_POLICY_NAME as V18_EXIT_POLICY

ANALYSIS_ID = "SIMPLE_TECH_V19_EXIT_STRUCTURE_VERIFICATION"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V18_SPEC_SHA256_EXPECTED = "3f88204a66249c9657a99506643488500b2a5e1781ac43563c51aa2f9781d0a9"
V18_VERDICT_EXPECTED = "SIMPLE_TECH_V18_FIXED180_EXIT_DEVELOPMENT_SUPPORTED"
V18_CASE_EXPECTED = "A"
DEVELOPMENT_ENTRY_STACK = V14_STACK
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
DEVELOPMENT_EXIT_POLICY = V18_EXIT_POLICY
EXIT_POLICY_NAME = V18_EXIT_POLICY
DEVELOPMENT_STRATEGY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5__FIXED180_FIRST_CAUSAL_BID"
FILL_EVIDENCE = V12_FILL_EVIDENCE
SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
ELIGIBLE_SET_HASH_EXPECTED = "03d085b0d9402546e5f631d07073e2f89793b43b715e1e0ba5a23d160051be40"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
E4_WAIT_BUDGET_SEC = 5.0
HOLD_SEC = 180.0
POLICY_ID = "X1_FIXED180"
PARITY_ABS_TOL = 1e-9
PARITY_YEN_TOL = 1e-2
PARITY_PF_TOL = 1e-4
PARITY_TS_TOL = 1e-6
V18_TRADE_N_EXPECTED = 38
V18_WIN_N_EXPECTED = 23
V18_LOSS_N_EXPECTED = 15
V18_FLAT_N_EXPECTED = 0
V18_WIN_RATE_EXPECTED = 0.6052631578947368
V18_MEAN_BPS_EXPECTED = 17.959453245414498
V18_MEDIAN_BPS_EXPECTED = 11.906878301793933
V18_TOTAL_PNL_YEN_100_EXPECTED = 18400.0
V18_AVG_PNL_YEN_100_EXPECTED = 484.21
V18_PROFIT_FACTOR_YEN_100_EXPECTED = 1.2548
V18_MAX_DRAWDOWN_YEN_100_EXPECTED = -53400.0
V18_POS_DAY_EXPECTED = 11
V18_NEG_DAY_EXPECTED = 5
V18_EX_BEST_DAY_EXPECTED = 14.548017703856496
V18_EX_TOP3_DAY_EXPECTED = 11.239592944165151
V18_DROP_TOP_SYMBOL_EXPECTED = 20.337625152472146
V18_DROP_TOP3_SYMBOL_EXPECTED = 25.32040512502691
V18_LAT_MEAN_EXPECTED = 0.35476312511845637
V18_LAT_MEDIAN_EXPECTED = 0.11799991130828857
V18_LAT_P75_EXPECTED = 0.28649991750717163
V18_LAT_MAX_EXPECTED = 2.7260000705718994
V18_SESSION_CLAMP_N_EXPECTED = 0
TRUE_OOS = False
EXIT_SPEC_FROZEN = False
EXIT_CERTIFIED = False
ENTRY_CERTIFIED = False
STRATEGY_CERTIFIED = False
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
G1_ARMED = False
G2_ARMED = False
G3_ARMED = False
D1_ARMED = False
D2_ARMED = False
D3_ARMED = False
STOP_LOSS_ARMED = False
BREAK_EVEN_ARMED = False
PASSIVE_ASK_SELL = False
INSIDE_SELL = False
WAIT_REPRICE_CHASE = False
ALT_HOLD_SEARCH = False
HOLD_60_POLICY = False
HOLD_300_POLICY = False
NEW_PERFORMANCE_GATE = False
V18_CACHE_COPY = False


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


def canonical_v19_spec() -> dict[str, Any]:
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
            "V18_SPEC_SHA256": V18_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "eligible_days": list(ELIGIBLE_DAYS),
            "development_entry_stack": DEVELOPMENT_ENTRY_STACK,
            "challenger": DEVELOPMENT_CHALLENGER,
            "exit_policy": EXIT_POLICY_NAME,
            "hold_sec": float(HOLD_SEC),
            "signal_set_hash": SIGNAL_SET_HASH_EXPECTED,
            "eligible_set_hash": ELIGIBLE_SET_HASH_EXPECTED,
            "e4_fill_set_hash": E4_FILL_SET_HASH_EXPECTED,
            "filled_n": E4_FILLED_N_EXPECTED,
            "verification_only": True,
            "v18_cache_copy": False,
            "independent_capture_replay": True,
            "alt_hold_search": False,
            "g1": False,
            "g2": False,
            "g3": False,
            "d1": False,
            "d2": False,
            "d3": False,
            "stop_loss": False,
            "break_even": False,
            "trailing": False,
            "passive_ask_sell": False,
            "inside_sell": False,
            "wait_reprice_chase": False,
            "new_performance_gate": False,
            "parity_abs_tol": float(PARITY_ABS_TOL),
            "parity_yen_tol": float(PARITY_YEN_TOL),
            "parity_pf_tol": float(PARITY_PF_TOL),
            "parity_ts_tol": float(PARITY_TS_TOL),
            "true_oos": bool(TRUE_OOS),
            "exit_certified": False,
            "strategy_certified": False,
            "research_parallelism": int(RESEARCH_PARALLELISM),
        }
    )


def spec_sha256_v19(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v19_spec()
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
