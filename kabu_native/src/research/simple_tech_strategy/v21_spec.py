"""V21 sizing attribution RCA. Frozen V19/V20 trades. No sizing policy. No ENTRY/EXIT change."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_strategy import FAMILY_ID, RESEARCH_PARALLELISM, TRUE_OOS

ANALYSIS_ID = "SIMPLE_TECH_V21_SIZING_ATTRIBUTION_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V13_SPEC_SHA256_EXPECTED = "050b0bd65744404c708ba841c376127aa587872b5fa0e13f267b4d7ba58f20aa"
V18_SPEC_SHA256_EXPECTED = "3f88204a66249c9657a99506643488500b2a5e1781ac43563c51aa2f9781d0a9"
V19_SPEC_SHA256_EXPECTED = "b0ac5ef646c1faed6f79b223b2d72e0fc356bfd068dca3fe6a80e8119a494484"
V20_SPEC_SHA256_EXPECTED = "f016007235106a930614dc7c54fe5baa3fbf41e0b097f56054fff0545f5e7499"
V19_VERDICT_EXPECTED = "SIMPLE_TECH_V19_ENTRY_EXIT_DEVELOPMENT_FREEZE_READY"
V20_VERDICT_EXPECTED = "SIMPLE_TECH_V20_PORTFOLIO_ECONOMICS_FRAGILE"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_EXIT_POLICY = "FIXED180_FIRST_CAUSAL_BID"
DEVELOPMENT_STRATEGY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5__FIXED180_FIRST_CAUSAL_BID"
E4_FILL_SET_HASH_EXPECTED = "f58cfec4810a6f1247d3caa020c6a3d59f5d134518dbb463f0ae35d37f45eaf2"
EXIT_INPUT_FILL_SET_HASH_EXPECTED = "11482508f99d6b665f9d197d81bc1b026c57ba1b763f74b50f07013225d201ba"
SCHEDULED_EXIT_SET_HASH_EXPECTED = "802ffda78db528506c952255f7b110e2862086d23d3c2c9b30bce2de7165b1f5"
ACTUAL_EXIT_SET_HASH_EXPECTED = "1825523e9bc1107213cb2bb8f390e767fa7b0221484d85042001eeaae58d4348"
E4_FILLED_N_EXPECTED = 38
SHARES = 100
HOLD_SEC = 180.0
HYPOTHETICAL_NOTIONAL_YEN = 1000000.0
HYPOTHETICAL_NOTIONAL_IS_POLICY = False
PARITY_YEN_TOL = 1e-2
PARITY_PF_TOL = 1e-4
PARITY_ABS_TOL = 1e-9
V20_TOTAL_PNL_YEN_100_EXPECTED = 18400.0
V20_PF_EXPECTED = 1.2548
V20_EX_BEST_DAY_EXPECTED = -8600.0
V20_EX_TOP3_DAY_EXPECTED = -41400.0
V20_DD_EXPECTED = -53400.0
V18_MEAN_BPS_EXPECTED = 17.959453245414498
V18_MEDIAN_BPS_EXPECTED = 11.906878301793933
CV_NOTIONAL_MIN = 0.50
MAX_MEDIAN_RATIO_MIN = 2.0
SPEARMAN_ABS_PNL_MIN = 0.30
TRUE_OOS = False
FORWARD_OOS_ELIGIBLE = False
STRATEGY_CERTIFIED = False
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
VOL_SIZING = False
RISK_SIZING = False
KELLY = False
PRICE_BUCKET_SIZING = False
SYMBOL_SIZING = False
DYNAMIC_SIZING = False
CAPITAL_OPTIMIZATION = False
ADOPT_1M_POLICY = False
V20_VERDICT_MUTATION = False
QUARTILE_GATE = False
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


def canonical_v21_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
            "V18_SPEC_SHA256": V18_SPEC_SHA256_EXPECTED,
            "V19_SPEC_SHA256": V19_SPEC_SHA256_EXPECTED,
            "V20_SPEC_SHA256": V20_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "eligible_days": list(ELIGIBLE_DAYS),
            "development_strategy_stack": DEVELOPMENT_STRATEGY_STACK,
            "filled_n": E4_FILLED_N_EXPECTED,
            "e4_fill_set_hash": E4_FILL_SET_HASH_EXPECTED,
            "exit_input_fill_set_hash": EXIT_INPUT_FILL_SET_HASH_EXPECTED,
            "scheduled_exit_set_hash": SCHEDULED_EXIT_SET_HASH_EXPECTED,
            "actual_exit_set_hash": ACTUAL_EXIT_SET_HASH_EXPECTED,
            "shares": int(SHARES),
            "hold_sec": float(HOLD_SEC),
            "hypothetical_notional_yen": float(HYPOTHETICAL_NOTIONAL_YEN),
            "hypothetical_notional_is_policy": False,
            "sizing_search": False,
            "adopt_1m_policy": False,
            "quartile_gate": False,
            "entry_changed": False,
            "exit_changed": False,
            "v20_verdict_mutation": False,
            "cv_notional_min": float(CV_NOTIONAL_MIN),
            "max_median_ratio_min": float(MAX_MEDIAN_RATIO_MIN),
            "spearman_abs_pnl_min": float(SPEARMAN_ABS_PNL_MIN),
            "true_oos": bool(TRUE_OOS),
            "forward_oos_eligible": False,
            "strategy_certified": False,
            "research_parallelism": int(RESEARCH_PARALLELISM),
        }
    )


def spec_sha256_v21(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v21_spec()
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
