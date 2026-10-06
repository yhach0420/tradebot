"""V4 Volume quality RCA spec. Not an ENTRY implementation. Parent V1 SHA is identity."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import (
    FAMILY_ID,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
)
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC

ANALYSIS_ID = "SIMPLE_TECH_V4_VOLUME_QUALITY_RCA"
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
EPSILON_EFFICIENCY = 1e-6
VQ2_SOURCE = "e1_x14_board_independent_signal.volume_persistence_300s"
PRE_VOLUME_STAGE = "S4_PRICE_ACTION"


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


def canonical_v4_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "entry_rule_changed": False,
            "threshold_search": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "realized_pnl_forbidden": True,
            "SESSION": SESSION,
            "pre_volume_population": "TREND AND PULLBACK AND RCI AND PRICE_ACTION (V1 nested s4)",
            "evaluation": "exit_neutral_ask_markout",
            "horizons_sec": list(HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "vq_axes": ["VQ1_MAGNITUDE", "VQ2_PERSISTENCE", "VQ3_DIRECTION", "VQ4_PRICE_RESPONSE"],
            "vq1": "VOLUME[t] / median(prior5 1min volume)  # V1 volume_accel",
            "vq2": {
                "available_if": VQ2_SOURCE,
                "window_sec": 300,
                "rule": "fraction of 10s steps with positive cumulative TradingVolume delta",
            },
            "vq3": {
                "window_sec": 60,
                "increments": "positive TradingVolume cumulative delta only",
                "classify": "px>=Ask1 BUY; px<=Bid1 SELL; inside spread tick rule",
            },
            "vq4": {
                "ret": "causal CurrentPrice return over prior 60s in bps",
                "norm_vol": "volume_60 / median(prior5 60s volume)",
                "efficiency": "RET_60_BPS / max(NORMALIZED_VOLUME_60, epsilon)",
                "epsilon": float(EPSILON_EFFICIENCY),
            },
            "volume_mult_frozen": float(VOLUME_MULT),
            "volume_median_bars_frozen": int(VOLUME_MEDIAN_BARS),
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
        }
    )


def spec_sha256_v4(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v4_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert ENTRY_RULE_CHANGED is False
assert THRESHOLD_SEARCH is False
assert EXIT_IMPLEMENTED is False
assert C14_USED_FOR_SELECTION is False
assert abs(float(EPSILON_EFFICIENCY) - 1e-6) < 1e-18
