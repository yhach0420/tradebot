"""Freeze the range-break trigger. The later research run is not executed here."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.simple_tech_entry_family import PULLBACK_LOOKBACK
from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_one_mechanism_repair_precommit import (
    BASELINE_PRICE_ACTION_PASS_N,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    PRIMARY_DEFICIENCY,
    PRIMARY_HORIZON_SEC,
    REPAIR_ID,
    SUPPORT_MIN_N,
    VOLUME_PASS_N,
)
from research.symbol_setup_one_mechanism_repair_precommit.isolation import NATIVE


def _sha(body: Any) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _load(rel: str) -> dict[str, Any]:
    return json.loads((NATIVE / rel).read_text(encoding="utf-8"))


def repair_spec() -> dict[str, Any]:
    """Canonical repair object. Its hash is the repair spec identity."""
    return {
        "REPAIR_ID": REPAIR_ID,
        "ROLE": "ENTRY_TIMING_ONLY",
        "STAGE": "PRICE_ACTION_TRIGGER",
        "SETUP_TIMEFRAME": "1min_completed",
        "TRIGGER_TIMEFRAME": "1min_completed",
        "LOCAL_RESISTANCE": {
            "definition": "max(high[t-3], high[t-2], high[t-1])",
            "lookback_bars": 3,
            "current_bar_high_included": False,
            "bars_must_be_completed_before_t": True,
            "non_finite": "STRUCTURE_NOT_AVAILABLE",
            "inherited_horizon": "PULLBACK_LOOKBACK",
            "inherited_horizon_bars": 3,
        },
        "BASELINE_PRICE_ACTION": "close[t] > EMA9[t] AND close[t] > high[t-1] AND close[t] <= BB_upper[t]",
        "REPAIRED_PRICE_ACTION": "close[t] > EMA9[t] AND close[t] > max(high[t-3], high[t-2], high[t-1]) AND close[t] <= BB_upper[t]",
        "comparison": "strict_greater",
        "tolerance": None,
        "tick_buffer": None,
        "atr_adjustment": None,
        "percentage_buffer": None,
        "unchanged": {
            "MA": "EMA9[t] > EMA21[t] AND EMA21[t] > EMA21[t-3]",
            "BB": "frozen V1",
            "RCI": "frozen V1 reversal",
            "VOLUME": "volume[t] > 0 AND volume[t] >= 1.5 * median(prior 5 completed-bar volumes)",
            "BOARD": "BOARD_SUPPORT_VETO after repaired price action",
            "ENTRY_EXECUTION": "SIMPLE_TECH_V1_PASSIVE_BID_W5",
            "EXIT": "SYMBOL_SETUP_V1_LITERAL_MA_THESIS_LOSS_EXIT",
            "PORTFOLIO": "CAP 5, same-symbol, slot release",
            "UNIVERSE": "frozen membership",
            "POSITION_SIZE": "100 shares",
            "COST": "gross execution-price yen",
        },
        "stage_order": [
            "MA_TREND",
            "BB_LOCATION",
            "RCI_REVERSAL",
            "VOLUME_PARTICIPATION",
            "PRICE_ACTION_TRIGGER",
            "BOARD_SUPPORT_VETO",
        ],
        "research_contract": {
            "horizons_sec": [30, 60, 180, 300],
            "primary_horizon_sec": PRIMARY_HORIZON_SEC,
            "metrics": ["RAW_MID_MOVE", "BID_ANCHOR_TO_FUTURE_BID", "ASK_TO_BID"],
            "denominator": "ask0",
            "quote_clock": "first fresh two-sided quote at or after signal finalize; future quote at or after that quote plus the horizon",
            "ask_to_bid_is_primary_gate": False,
            "primary_population": "repaired pre-board signals",
        },
    }


def build() -> dict[str, Any]:
    ident = identity()
    failed = _load("results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json")
    gap = _load("results/research/symbol_setup_failure_evidence_gap_v1/report.json")
    stages = {row["stage"]: row for row in gap["analysis"]["stages"]}
    volume = stages["VOLUME_PARTICIPATION"]
    price = stages["PRICE_ACTION_TRIGGER"]
    baseline_180 = next(
        row
        for row in gap["analysis"]["tables"]["ALL_TECHNICAL_SIGNALS"]
        if int(row["horizon_sec"]) == int(PRIMARY_HORIZON_SEC)
    )
    primary = failed["primary"]
    checks = {
        "identity_ok": bool(ident["ok"]),
        "strategy_sha": failed["complete_strategy_sha256"] == COMPLETE_STRATEGY_SHA256,
        "trade_n": primary["trade_n"] == 7,
        "net_pnl_yen": primary["net_pnl_yen"] == -31500.0,
        "pf": primary["PF"] == 0.46153846153846156,
        "volume_pass_n": int(volume["pass_n"]) == int(VOLUME_PASS_N),
        "price_action_input_n": int(price["input_n"]) == int(VOLUME_PASS_N),
        "baseline_price_action_pass_n": int(price["pass_n"]) == int(BASELINE_PRICE_ACTION_PASS_N),
        "pullback_lookback_is_3": int(PULLBACK_LOOKBACK) == 3,
        "no_prospective": all(str(d) <= "20260910" for d in ident.get("dates") or []),
    }
    spec = repair_spec()
    spec_sha = _sha(spec)
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "repair_id": REPAIR_ID,
        "repair_spec_sha256": spec_sha,
        "repair_spec": spec,
        "primary_deficiency": PRIMARY_DEFICIENCY,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "population": {
            "volume_pass_n": int(volume["pass_n"]),
            "baseline_price_action_pass_n": int(price["pass_n"]),
            "source": "symbol_setup_failure_evidence_gap_v1 canonical stage order",
        },
        "baseline_180": {
            "raw_mid_median": baseline_180["raw_mid_median"],
            "bid_anchor_median": baseline_180["bid_anchor_median"],
            "ask_to_bid_median": baseline_180["ask_to_bid_median"],
            "n": baseline_180["n"],
            "population_n": baseline_180["population_n"],
        },
        "gates": {
            "support": {
                "repaired_signal_n_min": SUPPORT_MIN_N,
                "present_in_all_3_folds": True,
                "fail": "MECHANISM_SUPPORT_INSUFFICIENT",
            },
            "actionability_180": {
                "raw_mid_median": "> 0",
                "bid_anchor_median": "> 0",
                "population": "repaired pre-board full development surface",
            },
            "robustness": {
                "ORIGINAL18_raw_mid_180_median": ">= 0",
                "EXTENSION17_raw_mid_180_median": ">= 0",
                "folds_raw_mid_180_median_positive": "at least 2 of 3",
                "single_day_monopoly": False,
                "single_symbol_monopoly": False,
            },
            "baseline_improvement_180": {
                "repaired_raw_mid_median_gt_baseline": baseline_180["raw_mid_median"],
                "repaired_bid_anchor_median_gt_baseline": baseline_180["bid_anchor_median"],
            },
            "board_after_repair": "same frozen veto, diagnostic only, not the success condition",
        },
    }
