"""V6 Trend vs Pullback stage RCA. Not an ENTRY implementation. Parent V1 SHA is identity."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import (
    BB_PERIOD,
    BB_SIGMA,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    FAMILY_ID,
    PULLBACK_LOOKBACK,
)
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC
from research.simple_tech_entry_family.v4_persistence_spec import V4_RCA_SPEC_SHA256_EXPECTED

ANALYSIS_ID = "SIMPLE_TECH_V6_TREND_PULLBACK_STAGE_RCA"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V5_SPEC_SHA256_EXPECTED = "7525f62120dc0f1d7436af508fec045375be6db89a45b56a5790de32d71dc09e"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_ADOPTION_ALLOWED = False
ASK_RUNTIME_ADOPTION_ALLOWED = False
EXIT_IMPLEMENTED = False
ENTRY_RULE_CHANGED = False
C14_USED_FOR_SELECTION = False
THRESHOLD_SEARCH = False
PERSISTENCE_ADDED = False
RCI_CHANGED = False
EMA_CHANGED = False
BB_CHANGED = False
PRE_TREND_N_EXPECTED = 99427
TREND_PASS_N_EXPECTED = 43709
PULLBACK_PASS_N_EXPECTED = 25593
TQ_PICK_ORDER = ("TQ1", "TQ2", "TQ4", "TQ3")
PQ_PICK_ORDER = ("PQ1", "PQ2", "PQ3", "PQ4")


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


def canonical_v6_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V4_RCA_SPEC_SHA256": V4_RCA_SPEC_SHA256_EXPECTED,
            "V5_SPEC_SHA256": V5_SPEC_SHA256_EXPECTED,
            "entry_rule_changed": False,
            "threshold_search": False,
            "persistence_added": False,
            "rci_changed": False,
            "ema_changed": False,
            "bb_changed": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "SESSION": SESSION,
            "pre_trend_population": "all warmup-valid evaluable V1 nested s0 rows",
            "pre_pullback_population": "TREND_PASS (V1 nested s1) only",
            "pre_trend_n_expected": int(PRE_TREND_N_EXPECTED),
            "trend_pass_n_expected": int(TREND_PASS_N_EXPECTED),
            "pullback_pass_n_expected": int(PULLBACK_PASS_N_EXPECTED),
            "evaluation": "exit_neutral_ask_markout",
            "horizons_sec": list(HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "frozen_ma_bb": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "ema_slope_bars": int(EMA_SLOPE_BARS),
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "pullback_lookback": int(PULLBACK_LOOKBACK),
            },
            "tq_axes": {
                "TQ1": "(EMA9/EMA21 - 1) * 10000  # signed separation bps; higher_better",
                "TQ2": "(EMA21[t]/EMA21[t-3] - 1) * 10000  # signed 3-bar slope bps, not abs; higher_better",
                "TQ3": "(EMA9[t]/EMA9[t-3] - 1) * 10000  # signed 3-bar slope bps, not abs; higher_better",
                "TQ4": "TQ1[t] - TQ1[t-3]  # separation expansion; higher_better",
            },
            "pq_axes": {
                "PQ1": "min over last3 incl current of (Low/EMA9 - 1) * 10000; lower_better (deeper pullback)",
                "PQ2": "min over last3 incl current of Low Bollinger %B; lower_better (closer to lower band)",
                "PQ3": "Close %B[t] - min last3 Low %B; higher_better (recovery)",
                "PQ4": "bars since last Low<=EMA9 on evaluable same-symbol history; lower_better",
            },
            "axis_higher_better": {
                "TQ1": True,
                "TQ2": True,
                "TQ3": True,
                "TQ4": True,
                "PQ1": False,
                "PQ2": False,
                "PQ3": True,
                "PQ4": False,
            },
            "tq_population": "executable PRE_TREND",
            "pq_population": "executable PRE_PULLBACK (TREND_PASS)",
            "good6_diagnostic_only": True,
            "good6_not_mechanism_gate": True,
            "no_adx_macd_vwap": True,
            "tq_pick_order": list(TQ_PICK_ORDER),
            "pq_pick_order": list(PQ_PICK_ORDER),
            "both_supported_tiebreak": [
                "greater stage mean improvement on both 180 and 300",
                "else larger (d180+d300)",
                "else larger (DAY_POS - DAY_NEG) on stage 180",
                "else larger best-axis |rho180|+|rho300|",
                "else PULLBACK as downstream of TREND",
            ],
            "preserved": {
                "v3_entry_signal_edge_supported": False,
                "v4_supported_volume_mechanism": "PERSISTENCE",
                "v4_persistence_rule_mechanism_supported": False,
                "v5_supported_reversal_mechanism": "NONE",
            },
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
        }
    )


def spec_sha256_v6(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v6_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert ENTRY_RULE_CHANGED is False
assert THRESHOLD_SEARCH is False
assert PERSISTENCE_ADDED is False
assert RCI_CHANGED is False
assert EMA_CHANGED is False
assert BB_CHANGED is False
assert int(PRE_TREND_N_EXPECTED) == 99427
assert int(TREND_PASS_N_EXPECTED) == 43709
assert int(PULLBACK_PASS_N_EXPECTED) == 25593
assert int(EMA_SLOPE_BARS) == 3
assert int(PULLBACK_LOOKBACK) == 3
assert tuple(TQ_PICK_ORDER) == ("TQ1", "TQ2", "TQ4", "TQ3")
assert tuple(PQ_PICK_ORDER) == ("PQ1", "PQ2", "PQ3", "PQ4")
