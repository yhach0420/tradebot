"""V8 TF1 architecture role RCA. Hard-gate ablation only. No retune. No mixed TF. No EXIT."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family import (
    BB_PERIOD,
    BB_SIGMA,
    BOARD_ASK_BID_QTY_MAX_RATIO,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    FAMILY_ID,
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC, V1_LOCKED
from research.simple_tech_entry_family.v4_persistence_spec import V3_LOCKED_CONTROL

ANALYSIS_ID = "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V6_RCA_SPEC_SHA256_EXPECTED = "dca616aa938fe6290dbe1caf15712d0d7827fd75871927d7a480227cc03c5ae0"
V7_SPEC_SHA256_EXPECTED = "5244e3a18aa733cd49ba52bf7e5598b13450c4a24995e865650d164470d723ae"
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
MIXED_TF_STRATEGY = False
NEW_TIMEFRAME = False
PQ3_HARD_GATE = False
PERSISTENCE_ADDED = False
PULLBACK_REMOVED = False
A0_SIGNAL_N_EXPECTED = 29
ROLE_MIN_EXECUTABLE_N = 20
ARM_ORDER = (
    "A0_V1",
    "A1_NO_PRICE_ACTION",
    "A2_NO_VOLUME",
    "A3_NO_PA_NO_VOLUME",
    "A4_CORE",
    "A5_CORE_NO_RCI",
    "A6_CORE_NO_BOARD",
)
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


def canonical_v8_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V6_RCA_SPEC_SHA256": V6_RCA_SPEC_SHA256_EXPECTED,
            "V7_SPEC_SHA256": V7_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "tf": "TF1_only",
            "new_timeframe": False,
            "mixed_tf_strategy_this_run": False,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "realized_pnl_forbidden": True,
            "threshold_search": False,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "pq3_hard_gate": False,
            "persistence_added": False,
            "pullback_removed": False,
            "join_only_v7_tf1_plus_v6_diagnostics": True,
            "recapture": False,
            "arms": list(ARM_ORDER),
            "no_other_combinations": True,
            "period_counts_frozen": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "ema_slope_bars": int(EMA_SLOPE_BARS),
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "pullback_lookback": int(PULLBACK_LOOKBACK),
                "rci_period": int(RCI_PERIOD),
                "rci_cross_level": float(RCI_CROSS_LEVEL),
                "volume_median_bars": int(VOLUME_MEDIAN_BARS),
                "volume_mult": float(VOLUME_MULT),
                "warmup_bars": int(WARMUP_BARS),
            },
            "evaluation": "exit_neutral_ask_markout",
            "horizons_sec": list(MARKOUT_HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "a0_signal_n_expected": int(A0_SIGNAL_N_EXPECTED),
            "role_min_executable_n": int(ROLE_MIN_EXECUTABLE_N),
            "min_qty": float(MIN_QTY),
            "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
            "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
            "v3_locked_control": dict(V3_LOCKED_CONTROL),
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v8(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v8_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert MIXED_TF_STRATEGY is False
assert NEW_TIMEFRAME is False
assert PQ3_HARD_GATE is False
assert PERSISTENCE_ADDED is False
assert PULLBACK_REMOVED is False
assert tuple(ARM_ORDER) == (
    "A0_V1",
    "A1_NO_PRICE_ACTION",
    "A2_NO_VOLUME",
    "A3_NO_PA_NO_VOLUME",
    "A4_CORE",
    "A5_CORE_NO_RCI",
    "A6_CORE_NO_BOARD",
)
assert tuple(MARKOUT_HORIZONS_SEC) == (60.0, 180.0, 300.0)
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert tuple(HORIZONS_SEC)[:1] == (30.0,)
assert int(WARMUP_BARS) == 24
