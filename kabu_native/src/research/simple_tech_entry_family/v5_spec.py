"""V5 Reversal quality RCA spec. Not an ENTRY implementation. Parent V1 SHA is identity."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import FAMILY_ID, RCI_CROSS_LEVEL, RCI_PERIOD
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC
from research.simple_tech_entry_family.v4_persistence_spec import V4_RCA_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v4_spec import ANALYSIS_ID as V4_RCA_ANALYSIS_ID

ANALYSIS_ID = "SIMPLE_TECH_V5_REVERSAL_QUALITY_RCA"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_ADOPTION_ALLOWED = False
ASK_RUNTIME_ADOPTION_ALLOWED = False
EXIT_IMPLEMENTED = False
ENTRY_RULE_CHANGED = False
C14_USED_FOR_SELECTION = False
THRESHOLD_SEARCH = False
PERSISTENCE_ADDED = False
PRE_REVERSAL_N_EXPECTED = 25593
RCI_CROSS_PASS_N_EXPECTED = 275
RQ4_LOOKBACK_BARS = int(RCI_PERIOD)
R1_WEAK_CROSS_MAX = -70.0
R2_RECOVERY_FRACTION_MIN = 0.5
R3_FAST_DELTA_MIN = 20.0
PRE_REVERSAL_STAGE = "S2_PULLBACK"
MECHANISM_PICK_ORDER = ("RQ4", "RQ3", "RQ2", "RQ1")


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


def canonical_v5_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V4_RCA_ANALYSIS_ID": V4_RCA_ANALYSIS_ID,
            "V4_RCA_SPEC_SHA256": V4_RCA_SPEC_SHA256_EXPECTED,
            "entry_rule_changed": False,
            "threshold_search": False,
            "persistence_added": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "realized_pnl_forbidden": True,
            "SESSION": SESSION,
            "pre_reversal_population": "TREND_UP AND PULLBACK_SETUP (V1 nested s2). Price Action/Volume/Board not used for population.",
            "pre_reversal_n_expected": int(PRE_REVERSAL_N_EXPECTED),
            "existing_rci_cross": "RCI9[t-1] <= -80 AND RCI9[t] > -80",
            "rci_cross_level_frozen": float(RCI_CROSS_LEVEL),
            "rci_period_frozen": int(RCI_PERIOD),
            "evaluation": "exit_neutral_ask_markout",
            "horizons_sec": list(HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "rq_axes": ["RQ1_RCI_LEVEL", "RQ2_RCI_DELTA", "RQ3_RCI_MULTI_BAR_SLOPE", "RQ4_RCI_RECOVERY_FRACTION"],
            "rq1": "RCI9[t]",
            "rq2": "RCI9[t] - RCI9[t-1]",
            "rq3": "RCI9[t] - RCI9[t-2]",
            "rq4": {
                "lookback_bars": int(RQ4_LOOKBACK_BARS),
                "local_low": "most recent confirmed causal local min in lookback; else min of lookback window",
                "fraction": "(RCI9[t] - L) / (0 - L) when L < 0; else missing",
            },
            "taxonomy_descriptive_only": {
                "R1_WEAK_CROSS": "rci_cross AND -80 < RCI9[t] <= -70",
                "R2_STRONG_RCI_RECOVERY": "RQ4 >= 0.5",
                "R3_FAST_RCI_REVERSAL": "RQ2 >= 20  # 10 percent of RCI range [-100,100]",
                "R4_SINGLE_BAR_SPIKE": "RQ2 > 0 AND prior-bar delta = RQ3-RQ2 <= 0",
                "R5_MULTI_BAR_RCI_RECOVERY": "RQ2 > 0 AND prior-bar delta > 0",
                "R6_RCI_RECOVERED_BUT_PRICE_NOT_FOLLOWING": "(RQ4>=0.5 OR RQ2>0) AND NOT (close>ema9 AND close>high[t-1])",
            },
            "mechanism_pick_order": list(MECHANISM_PICK_ORDER),
            "preserved": {
                "v3_entry_signal_edge_supported": False,
                "v4_supported_volume_mechanism": "PERSISTENCE",
                "v4_persistence_rule_mechanism_supported": False,
                "persistence_not_added_this_run": True,
            },
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
        }
    )


def spec_sha256_v5(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v5_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert ENTRY_RULE_CHANGED is False
assert THRESHOLD_SEARCH is False
assert PERSISTENCE_ADDED is False
assert EXIT_IMPLEMENTED is False
assert C14_USED_FOR_SELECTION is False
assert abs(float(RCI_CROSS_LEVEL) + 80.0) < 1e-12
assert int(PRE_REVERSAL_N_EXPECTED) == 25593
assert int(RCI_CROSS_PASS_N_EXPECTED) == 275
assert int(RQ4_LOOKBACK_BARS) == 9
assert tuple(MECHANISM_PICK_ORDER) == ("RQ4", "RQ3", "RQ2", "RQ1")
assert abs(float(R3_FAST_DELTA_MIN) - 20.0) < 1e-12
assert abs(float(R2_RECOVERY_FRACTION_MIN) - 0.5) < 1e-12
assert abs(float(R1_WEAK_CROSS_MAX) + 70.0) < 1e-12
