"""V20 frozen ENTRY+EXIT portfolio economics. No strategy search. No parameter retune."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE as V12_FILL_EVIDENCE
from research.simple_tech_strategy import FAMILY_ID, RESEARCH_PARALLELISM, TRUE_OOS

ANALYSIS_ID = "SIMPLE_TECH_V20_FROZEN_PORTFOLIO_ECONOMICS"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V10_SPEC_SHA256_EXPECTED = "8b59860a5e16eceb7f357600226e52e26f2dc3750c20b3d1f7f467b8efa1406d"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V18_SPEC_SHA256_EXPECTED = "3f88204a66249c9657a99506643488500b2a5e1781ac43563c51aa2f9781d0a9"
V19_SPEC_SHA256_EXPECTED = "b0ac5ef646c1faed6f79b223b2d72e0fc356bfd068dca3fe6a80e8119a494484"
V19_VERDICT_EXPECTED = "SIMPLE_TECH_V19_ENTRY_EXIT_DEVELOPMENT_FREEZE_READY"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_EXIT_POLICY = "FIXED180_FIRST_CAUSAL_BID"
DEVELOPMENT_STRATEGY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5__FIXED180_FIRST_CAUSAL_BID"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
FILL_EVIDENCE = V12_FILL_EVIDENCE
SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
ELIGIBLE_SET_HASH_EXPECTED = "03d085b0d9402546e5f631d07073e2f89793b43b715e1e0ba5a23d160051be40"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
ACTUAL_EXIT_SET_HASH_EXPECTED = "1825523e9bc1107213cb2bb8f390e767fa7b0221484d85042001eeaae58d4348"
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
E4_WAIT_BUDGET_SEC = 5.0
HOLD_SEC = 180.0
SHARES = 100
ELIGIBLE_DAY_N = 18
PARITY_ABS_TOL = 1e-9
PARITY_YEN_TOL = 1e-2
PARITY_PF_TOL = 1e-4
PARITY_TS_TOL = 1e-6
V19_TRADE_N_EXPECTED = 38
V19_WIN_N_EXPECTED = 23
V19_LOSS_N_EXPECTED = 15
V19_TOTAL_PNL_YEN_100_EXPECTED = 18400.0
V19_PROFIT_FACTOR_YEN_100_EXPECTED = 1.2548
V19_REALIZED_MAX_DD_YEN_EXPECTED = -53400.0
MIN_QTY = 100.0
SAME_SYMBOL_SEMANTICS_IN_FROZEN_STACK = False
POSITION_CAP_IN_FROZEN_STACK = False
FEE_MODEL_NAME = None
PRE_FEE_PNL = True
ANNUALIZE = False
TRUE_OOS = False
ENTRY_CERTIFIED = False
EXIT_CERTIFIED = False
STRATEGY_CERTIFIED = False
RUNTIME_CANDIDATE = False
RUNTIME_ADOPTION_ALLOWED = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
HOLD_SEARCH = False
EMA_RETUNE = False
RCI_RETUNE = False
BB_RETUNE = False
BOARD_ADDED = False
VOLUME_ADDED = False
PA_ADDED = False
WAIT_CHANGED = False
SYMBOL_EXCLUSION = False
TOD_EXCLUSION = False
SPREAD_GATE = False
STOP_TRAILING = False
PROFIT_TARGET = False
NEW_PROFIT_TARGET_GATE = False
ANNUALIZED = False
V18_CACHE_COPY = False
C14_USED = False
ML_USED = False
GRID_SEARCH = False
THRESHOLD_SEARCH = False
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


def canonical_v20_spec() -> dict[str, Any]:
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
            "V19_SPEC_SHA256": V19_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "eligible_days": list(ELIGIBLE_DAYS),
            "development_strategy_stack": DEVELOPMENT_STRATEGY_STACK,
            "entry_stack": DEVELOPMENT_ENTRY_STACK,
            "exit_policy": DEVELOPMENT_EXIT_POLICY,
            "hold_sec": float(HOLD_SEC),
            "shares": int(SHARES),
            "filled_n": E4_FILLED_N_EXPECTED,
            "e4_fill_set_hash": E4_FILL_SET_HASH_EXPECTED,
            "actual_exit_set_hash": ACTUAL_EXIT_SET_HASH_EXPECTED,
            "same_symbol_semantics": False,
            "position_cap_added": False,
            "strategy_search": False,
            "parameter_tuning": False,
            "hold_search": False,
            "symbol_exclusion": False,
            "tod_exclusion": False,
            "spread_gate": False,
            "stop_trailing": False,
            "profit_target": False,
            "new_profit_target_gate": False,
            "annualize": False,
            "fee_model": None,
            "pre_fee_pnl": True,
            "v18_cache_copy": False,
            "true_oos": bool(TRUE_OOS),
            "strategy_certified": False,
            "research_parallelism": int(RESEARCH_PARALLELISM),
        }
    )


def spec_sha256_v20(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v20_spec()
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
