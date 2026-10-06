"""Economic Confirmation 1 contract. Hashed before any OC PnL is computed."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.pb1_v4_complete_strategy_economic_confirmation1 import (
    CASE_FAIL,
    CASE_INVALID,
    CASE_PASS,
    EVAL_FIRST,
    EVAL_LAST,
    EXPECTED_COMPLETE_STRATEGY_ID,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_ENTRY_ID,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_OC_E0,
    EXPECTED_OC_E1,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    FV_FIRST,
    FV_LAST,
    NEXT_CONF2,
    NEXT_DECOMP,
    PRECOMMIT_ID,
    PROSPECTIVE_FROM,
)


def conf1_precommit(
    *,
    machine_sha: str,
    source_inventory_sha: str,
    complete_strategy_sha256: str,
    confirmation_n: int,
    confirmation_first: str | None,
    confirmation_last: str | None,
    lookback_n: int,
    symbol_n: int,
) -> dict[str, Any]:
    body = {
        "precommit_id": PRECOMMIT_ID,
        "purpose": "COMPLETE_STRATEGY_ECONOMIC_CONFIRMATION1",
        "not_purpose": ["entry_retune", "exit_retune", "symbol_exclusion", "best_exit_search"],
        "entry_identity": EXPECTED_ENTRY_ID,
        "machine_sha": machine_sha,
        "EXPECTED_V4_MACHINE_SHA256": EXPECTED_MACHINE_SHA256,
        "source_inventory_sha": source_inventory_sha,
        "EXPECTED_SOURCE_INVENTORY_SHA256": EXPECTED_SOURCE_INVENTORY_SHA256,
        "complete_strategy_identity": EXPECTED_COMPLETE_STRATEGY_ID,
        "COMPLETE_STRATEGY_SHA256": complete_strategy_sha256,
        "EXPECTED_COMPLETE_STRATEGY_SHA256": EXPECTED_COMPLETE_STRATEGY_SHA256,
        "date_range": {
            "economic_confirmation_1_first": EVAL_FIRST,
            "economic_confirmation_1_last": EVAL_LAST,
            "confirmation_n": int(confirmation_n),
            "confirmation_first": confirmation_first,
            "confirmation_last": confirmation_last,
            "opened_this_task": ["OLD_CONFIRMATION_ECONOMIC"],
            "sealed_this_task": ["FROZEN_VALIDATION_ECONOMIC", "PROSPECTIVE"],
        },
        "lookback": {
            "role": "FEATURE_HISTORY_ONLY_FOR_FROZEN_V4_WALK",
            "lookback_n": int(lookback_n),
            "not_scored": True,
        },
        "symbol_n": int(symbol_n),
        "expected_frozen_v4_confirmation_emits": {"E0": int(EXPECTED_OC_E0), "E1": int(EXPECTED_OC_E1)},
        "execution": {
            "historical_fill": "HISTORICAL_NEXT_BAR_OPEN_EXECUTION",
            "RESEARCH_EXECUTION_APPROXIMATION": True,
            "live_paper_sot": "X1_IMMEDIATE_ASK",
            "shares": 100,
            "cost_model": "X1_8BPS_EXECUTION_STRESS",
            "CAP": 5,
            "forbidden": ["mid fill", "same-bar fill", "future quote", "best-price fill"],
        },
        "exit": {
            "technical": "PB1_V4_THESIS_LOST_NEXT_OPEN",
            "ops": "SESSION_FLAT_1520",
            "ops_is_not_thesis_death": True,
        },
        "portfolio": {
            "same_symbol_prohibition": True,
            "occupancy_increment": "FILL",
            "slot_release": "EXIT_FILL",
            "event_priority": ["EXIT", "FILL", "ADMIT"],
            "reentry_pnl_filter": False,
        },
        "primary_economic_gate": {
            "net_pnl_yen": "> 0",
            "profit_factor": "> 1.0",
            "mean_net_pnl_per_trade": "> 0",
            "all_required": True,
        },
        "robustness_audit_required": [
            "net_pnl_ex_top1_trade",
            "net_pnl_ex_top1_day",
            "net_pnl_ex_top1_symbol",
            "top1_trade_pnl_share",
            "top3_trade_pnl_share",
            "top1_day_pnl_share",
            "top3_day_pnl_share",
            "top1_symbol_pnl_share",
            "top3_symbol_pnl_share",
        ],
        "concentration_rule": {
            "ECONOMIC_EDGE_CONCENTRATED": "true if any ex_top1 net_pnl <= 0",
            "do_not_drop_symbol_day_or_trade": True,
        },
        "pass_fail_rules": {
            "PASS_VERDICT": CASE_PASS,
            "FAIL_VERDICT": CASE_FAIL,
            "INVALID_VERDICT": CASE_INVALID,
            "PASS_NEXT": NEXT_CONF2,
            "FAIL_NEXT": NEXT_DECOMP,
            "invariants_fail_blocks_economic_verdict": True,
        },
        "sealed": {
            "frozen_validation": {"first": FV_FIRST, "last": FV_LAST, "economic_opened": False},
            "prospective": {"from": PROSPECTIVE_FROM, "opened": False},
        },
        "forbidden_after_seeing_results": [
            "drop E0 or E1",
            "drop high-price symbols",
            "drop time windows",
            "drop EXIT reasons",
            "drop losing symbols",
            "lower cost bps",
            "rescore Confirmation 1 after a change",
        ],
        "V4_CHANGED": False,
        "COMPLETE_STRATEGY_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "once_only": True,
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    body["PRECOMMIT_SHA256"] = hashlib.sha256(raw).hexdigest()
    return body
