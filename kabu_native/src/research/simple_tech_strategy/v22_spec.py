"""V22 sizing execution capacity RCA. Frozen E4+FIXED180. No sizing policy. No PnL search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE as V12_FILL_EVIDENCE
from research.simple_tech_strategy import FAMILY_ID, RESEARCH_PARALLELISM, TRUE_OOS

ANALYSIS_ID = "SIMPLE_TECH_V22_SIZING_EXECUTION_CAPACITY_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V8_SPEC_SHA256_EXPECTED = "38610ed82179e5516a09a15ce0b4442dfbc5795f9e7543a738908e2854ee0c3d"
V12_SPEC_SHA256_EXPECTED = "0f157c1a1ac02141506f91d68768617e0934c2c657a15f8eff7e4f6341969799"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V18_SPEC_SHA256_EXPECTED = "3f88204a66249c9657a99506643488500b2a5e1781ac43563c51aa2f9781d0a9"
V19_SPEC_SHA256_EXPECTED = "b0ac5ef646c1faed6f79b223b2d72e0fc356bfd068dca3fe6a80e8119a494484"
V20_SPEC_SHA256_EXPECTED = "f016007235106a930614dc7c54fe5baa3fbf41e0b097f56054fff0545f5e7499"
V21_SPEC_SHA256_EXPECTED = "f99b8ad815b4c06269ba49e53651aea55026b92e48d0a9430df02df54c96186d"
V19_VERDICT_EXPECTED = "SIMPLE_TECH_V19_ENTRY_EXIT_DEVELOPMENT_FREEZE_READY"
V20_VERDICT_EXPECTED = "SIMPLE_TECH_V20_PORTFOLIO_ECONOMICS_FRAGILE"
V21_VERDICT_EXPECTED = "SIMPLE_TECH_V21_FIXED_SHARE_SIZING_DOMINANT_FRAGILITY"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_EXIT_POLICY = "FIXED180_FIRST_CAUSAL_BID"
DEVELOPMENT_STRATEGY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5__FIXED180_FIRST_CAUSAL_BID"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
FILL_EVIDENCE = V12_FILL_EVIDENCE
SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
ELIGIBLE_SET_HASH_EXPECTED = "03d085b0d9402546e5f631d07073e2f89793b43b715e1e0ba5a23d160051be40"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
EXIT_INPUT_FILL_SET_HASH_EXPECTED = "11482508f99d6b665f9d197d81bc1b026c57ba1b763f74b50f07013225d201ba"
SCHEDULED_EXIT_SET_HASH_EXPECTED = "802ffda78db528506c952255f7b110e2862086d23d3c2c9b30bce2de7165b1f5"
ACTUAL_EXIT_SET_HASH_EXPECTED = "1825523e9bc1107213cb2bb8f390e767fa7b0221484d85042001eeaae58d4348"
B1_SIGNAL_N_EXPECTED = 275
B1_EXECUTABLE_N_EXPECTED = 126
E4_FILLED_N_EXPECTED = 38
E4_UNFILLED_N_EXPECTED = 88
E4_WAIT_BUDGET_SEC = 5.0
HOLD_SEC = 180.0
LOT_SHARES = 100
SHARES_100 = 100
HYPOTHETICAL_NOTIONAL_YEN = 1000000.0
HYPOTHETICAL_NOTIONAL_IS_POLICY = False
MULTILOT_MIN_TRADE_N = 2
MULTILOT_MIN_DAY_N = 2
PARITY_ABS_TOL = 1e-9
PARITY_TS_TOL = 1e-6
TRUE_OOS = False
FORWARD_OOS_ELIGIBLE = False
STRATEGY_CERTIFIED = False
POSITION_SIZING_SPEC_FROZEN = False
POSITION_SIZING_CERTIFIED = False
RUNTIME_CANDIDATE = False
RUNTIME_ADOPTION_ALLOWED = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
EMA_CHANGED = False
RCI_CHANGED = False
PULLBACK_CHANGED = False
E4_CHANGED = False
WAIT_CHANGED = False
HOLD_CHANGED = False
EXIT_ADDED = False
SIZING_SEARCH = False
ADOPT_SIZING_POLICY = False
TARGET_NOTIONAL_CHOSEN = False
PNL_OPTIMIZATION = False
VOL_SIZING = False
RISK_SIZING = False
KELLY = False
PRICE_BUCKET_SIZING = False
SYMBOL_SIZING = False
QUARTILE_GATE = False
PARTIAL_FILL = False
QUEUE_FILL = False
VWAP_FILL = False
DEEPER_BOOK = False
SLIPPAGE_ASSUMPTION = False
EXIT_WAIT_EXTRA = False
REUSE_100SHARE_FILL_SET = False
V20_VERDICT_MUTATION = False
V21_VERDICT_MUTATION = False
ADOPT_1M_POLICY = False
C14_USED = False
ML_USED = False
GRID_SEARCH = False


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
            "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
            "V19_SPEC_SHA256": V19_SPEC_SHA256_EXPECTED,
            "V20_SPEC_SHA256": V20_SPEC_SHA256_EXPECTED,
            "V21_SPEC_SHA256": V21_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "eligible_days": list(ELIGIBLE_DAYS),
            "development_strategy_stack": DEVELOPMENT_STRATEGY_STACK,
            "fill_evidence": FILL_EVIDENCE,
            "e4_wait_budget_sec": float(E4_WAIT_BUDGET_SEC),
            "hold_sec": float(HOLD_SEC),
            "lot_shares": int(LOT_SHARES),
            "shares_100": int(SHARES_100),
            "signal_n": int(B1_SIGNAL_N_EXPECTED),
            "eligible_n": int(B1_EXECUTABLE_N_EXPECTED),
            "filled_n_100": int(E4_FILLED_N_EXPECTED),
            "e4_fill_set_hash": E4_FILL_SET_HASH_EXPECTED,
            "actual_exit_set_hash": ACTUAL_EXIT_SET_HASH_EXPECTED,
            "hypothetical_notional_yen": float(HYPOTHETICAL_NOTIONAL_YEN),
            "hypothetical_notional_is_policy": False,
            "sizing_search": False,
            "adopt_sizing_policy": False,
            "target_notional_chosen": False,
            "pnl_optimization": False,
            "reuse_100share_fill_set": False,
            "partial_fill": False,
            "queue_fill": False,
            "exit_wait_extra": False,
            "quartile_gate": False,
            "multilot_min_trade_n": int(MULTILOT_MIN_TRADE_N),
            "multilot_min_day_n": int(MULTILOT_MIN_DAY_N),
            "position_sizing_spec_frozen": False,
            "true_oos": bool(TRUE_OOS),
            "forward_oos_eligible": False,
            "research_parallelism": int(RESEARCH_PARALLELISM),
        }
    )


def spec_sha256_v22(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v22_spec()
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
