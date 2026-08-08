"""Candidate Registry: max 24 JointStrategyPackages, frozen before any economics.

ID: X6R3_<CONT|PULL|BREAK>_<STANDARD|STRICT>_<REG_STANDARD|REG_STRICT>_<EXIT_A|EXIT_B>
Candidates whose required features lack proven coverage are disabled BEFORE any
results exist. No duplicates / filler strategies to pad the count.
"""
from __future__ import annotations

from typing import Any

from .exits import (
    EXIT_EVALUATION_ORDER,
    EXIT_PACKAGES,
    INVALIDATION_DEFINITIONS,
    MAX_STRUCTURAL_RISK_BPS,
    REENTRY_RULE,
)
from .features import FEATURE_FORMULAS, SYMBOL_FEATURES_CORE
from .regime import REGIME_DEFINITIONS
from .setups import CHASE_REJECT, CONFIRMATION_LEVELS, SETUP_DEFINITIONS

MAX_CANDIDATES = 24

SETUPS = ("CONT", "PULL", "BREAK")
CONFIRMATIONS = ("STANDARD", "STRICT")
REGIMES = ("REG_STANDARD", "REG_STRICT")
EXITS = ("EXIT_A", "EXIT_B")

STATE_MACHINE_ORDER = "IDLE -> SETUP -> TRIGGERED -> CONFIRM -> OPEN (post-trigger confirmation)"

DIAGNOSTIC_ONLY_NOTE = (
    "volume / board10 field groups are DIAGNOSTIC-ONLY for these 24 candidates "
    "even when their as-of coverage passes: no new candidate axes or post-hoc "
    "thresholds are added during the R1 repair"
)

# Features each setup requires beyond the always-required core.
REQUIRED_FEATURES: dict[str, list[str]] = {
    "CONT": ["ret_300s_bps", "range_pos_300s", "dir_eff_300s", "breakout_dev_bps",
             "rv_300s_bps", "up_persist_60s", "high_60s"],
    "PULL": ["ret_30s_bps", "ret_60s_bps", "rv_300s_bps", "up_persist_60s"],
    "BREAK": ["range_ratio_60_300", "vol_ratio_60_300", "rv_60s_bps", "rv_300s_bps",
              "up_persist_60s", "spread_bps"],
}
REQUIRED_MARKET_FEATURES = [
    "mkt_up_ratio_60s", "mkt_ret_60s_med_bps", "mkt_ret_300s_med_bps",
    "mkt_vol_expansion", "mkt_spread_worse_ratio", "mkt_evaluable_n",
]
# Optional (used only if coverage proven; never required):
OPTIONAL_FEATURES = ["vwap_dev_bps", "vol_rate_60s", "board_imbalance10"]


def strategy_id(setup: str, conf: str, reg: str, exit_id: str) -> str:
    return f"X6R3_{setup}_{conf}_{reg}_{exit_id}"


def build_candidate_registry(
    *,
    core_feature_coverage_ok: bool,
    market_feature_coverage_ok: bool,
    vwap_available: bool,
    volume_available: bool,
    board_available: bool,
) -> list[dict[str, Any]]:
    """Enumerate all 24; disable (never silently drop) candidates lacking coverage."""
    exits_by_id = {("EXIT_A" if "A" in x["exit_id"].split("_")[1] else "EXIT_B"): x
                   for x in EXIT_PACKAGES}
    rows: list[dict[str, Any]] = []
    for setup in SETUPS:
        for conf in CONFIRMATIONS:
            for reg in REGIMES:
                for ex in EXITS:
                    sid = strategy_id(setup, conf, reg, ex)
                    required = sorted(set(REQUIRED_FEATURES[setup]) | {"mid", "spread_bps"})
                    enabled = bool(core_feature_coverage_ok and market_feature_coverage_ok)
                    disable_reason = "" if enabled else "REQUIRED_FEATURE_COVERAGE_MISSING"
                    xp = exits_by_id[ex]
                    rows.append(
                        {
                            "strategy_id": sid,
                            "enabled": enabled,
                            "disable_reason": disable_reason,
                            "setup": setup,
                            "state_machine_order": STATE_MACHINE_ORDER,
                            "frozen_at_triggered": [
                                "trigger_level", "structural stop reference",
                                "pullback_low / compression high-low", "tick",
                                "trigger timestamp", "episode_id",
                            ],
                            "tick_rule": "dynamic JPX resolver per symbol class (no fixed 0.1)",
                            "diagnostic_only_fields": DIAGNOSTIC_ONLY_NOTE,
                            "setup_state_machine": SETUP_DEFINITIONS[setup],
                            "confirmation": conf,
                            "confirmation_spec": CONFIRMATION_LEVELS[conf],
                            "regime_mode": "strict" if reg == "REG_STRICT" else "standard",
                            "regime_definitions": REGIME_DEFINITIONS["strict" if reg == "REG_STRICT" else "standard"],
                            "features_used": required + REQUIRED_MARKET_FEATURES,
                            "optional_features_in_use": [
                                f for f, ok in (
                                    ("vwap_dev_bps", vwap_available and setup == "PULL"),
                                ) if ok
                            ],
                            "feature_formulas": {
                                k: FEATURE_FORMULAS[k]
                                for k in required + REQUIRED_MARKET_FEATURES
                                if k in FEATURE_FORMULAS
                            },
                            "trigger": SETUP_DEFINITIONS[setup]["trigger"],
                            "reject_conditions": [
                                CHASE_REJECT["formula"],
                                f"structural risk > {MAX_STRUCTURAL_RISK_BPS}bps at entry",
                                "regime RISK_OFF_UNSTABLE",
                                "last 10 minutes of session",
                                "same-episode re-entry",
                                "spread unhealthy / quote stale (NOT_EVALUABLE)",
                            ],
                            "invalidation": INVALIDATION_DEFINITIONS[setup],
                            "stop": xp["initial_stop"],
                            "no_progress": xp["no_progress"],
                            "trailing": xp["trailing"],
                            "max_hold_sec": xp["max_hold_sec"],
                            "session_close": xp["session_close"],
                            "exit_evaluation_order": EXIT_EVALUATION_ORDER,
                            "reentry_rule": REENTRY_RULE,
                            "required_coverage": {
                                "core_price_quote": True,
                                "market_loo": True,
                                "vwap": False,
                                "volume": False,
                                "board": False,
                            },
                            "missing_data_behavior": (
                                "NaN feature => predicate fails => no ENTRY; grid point "
                                "recorded NOT_EVALUABLE; never filled or interpolated"
                            ),
                        }
                    )
    ids = [r["strategy_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("FAIL: duplicate strategy ids")
    if len(rows) > MAX_CANDIDATES:
        raise SystemExit(f"FAIL: registry {len(rows)} exceeds cap {MAX_CANDIDATES}")
    rows.sort(key=lambda r: r["strategy_id"])
    return rows
