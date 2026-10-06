"""V7 native timeframe role RCA. Same period counts on 1m/3m/5m. Not an ENTRY. No mixed-TF strategy."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
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
from research.simple_tech_entry_family.v6_spec import PULLBACK_PASS_N_EXPECTED, PRE_TREND_N_EXPECTED, TREND_PASS_N_EXPECTED

ANALYSIS_ID = "SIMPLE_TECH_V7_TIMEFRAME_ROLE_RCA"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V6_RCA_SPEC_SHA256_EXPECTED = "dca616aa938fe6290dbe1caf15712d0d7827fd75871927d7a480227cc03c5ae0"
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
TF_IDS = ("TF1", "TF3", "TF5")
TF_WIDTH_SEC = {"TF1": 60.0, "TF3": 180.0, "TF5": 300.0}
MAJOR_ROLES = ("TREND", "PULLBACK", "RCI", "PRICE_ACTION")
ALL_ROLES = ("TREND", "PULLBACK", "RCI", "PRICE_ACTION", "VOLUME")
ROLE_MIN_EXE_PASS = 20
ROLE_MIN_EXE_FAIL = 20
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


def canonical_v7_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V6_RCA_SPEC_SHA256": V6_RCA_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "c14_forbidden": True,
            "exit_forbidden": True,
            "realized_pnl_forbidden": True,
            "threshold_search": False,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "mixed_tf_strategy_this_run": False,
            "native_timeframe_comparison": True,
            "same_real_time_lookback": False,
            "timeframes": {"TF1": 60, "TF3": 180, "TF5": 300},
            "forbidden_timeframes": ["2m", "4m", "10m", "15m", "tick", "volume_bars"],
            "bar_anchor": "09:00_JST",
            "completed_bar_only": True,
            "signal_timestamp": "bar_finalize_t_first_causal_availability",
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
            "dev_wait_sec_parity_with_v1": float(DEV_WAIT_SEC),
            "evaluation": "exit_neutral_ask_markout",
            "horizons_sec_fixed": list(MARKOUT_HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "no_horizon_rescale": True,
            "board": "event_level_support_veto_at_decision_time_not_a_tf_role",
            "volume_persistence_300s": "tf_clock_diagnostic_not_hard_gate",
            "good6_diagnostic_only": True,
            "role_min_exe_pass": int(ROLE_MIN_EXE_PASS),
            "role_min_exe_fail": int(ROLE_MIN_EXE_FAIL),
            "preferred_scale_order": ["day_consistency", "ex_best_robustness", "effect_180_300_consistency", "faster_scale"],
            "single_tf_if_pullback_rci_pa_agree": True,
            "min_qty": float(MIN_QTY),
            "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
            "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
            "pre_trend_n_expected_tf1": int(PRE_TREND_N_EXPECTED),
            "trend_pass_n_expected_tf1": int(TREND_PASS_N_EXPECTED),
            "pullback_pass_n_expected_tf1": int(PULLBACK_PASS_N_EXPECTED),
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "entry_signal_spec_frozen": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v7(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v7_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert MIXED_TF_STRATEGY is False
assert THRESHOLD_SEARCH is False
assert tuple(TF_IDS) == ("TF1", "TF3", "TF5")
assert abs(float(TF_WIDTH_SEC["TF3"]) - 180.0) < 1e-12
assert tuple(MARKOUT_HORIZONS_SEC) == (60.0, 180.0, 300.0)
assert 900.0 not in MARKOUT_HORIZONS_SEC
assert int(WARMUP_BARS) == 24
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert tuple(HORIZONS_SEC)[:1] == (30.0,)
