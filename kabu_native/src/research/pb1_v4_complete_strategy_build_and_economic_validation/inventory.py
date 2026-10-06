"""Existing execution and technical EXIT inventory. No new fill model. No PnL ranking."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    CAP,
    COST_MODEL_ID,
    HISTORICAL_FILL_ID,
    LIVE_FILL_SOT_ID,
    OPS_EXIT_ID,
    SHARES,
    TECHNICAL_EXIT_ID,
)
from research.systematic_state_transition_library_precommit_v1.spec import execution_contract
from small_paper.v1r_primary_runtime import BOARD_FRESHNESS_SEC_V1R, POSITION_CAP, WAIT_SEC


def execution_inventory() -> dict[str, Any]:
    x1 = execution_contract()
    return {
        "live_paper_sot": {
            "id": LIVE_FILL_SOT_ID,
            "contract": x1,
            "source": "src/research/participation_onset_full_strategy_v1/execution.py:evaluate_execution",
            "long_fill": "first fresh valid Ask1 at/after signal t0",
            "short_fill": "first fresh valid Bid1 at/after signal t0 (symmetric; live SoT is long Ask1)",
            "mid_forbidden": True,
            "bar_ohlc_fill_forbidden_on_board_path": True,
            "board_freshness_sec": float(BOARD_FRESHNESS_SEC_V1R),
            "min_qty": 100,
            "usable_on_discovery_parquet": False,
            "reason_unusable": "daytrade_historical_v2 minute parquet has no Bid1/Ask1 board",
        },
        "v1r_paper_exact_fast": {
            "id": "V1R_PASSIVE_BID_ASK_CROSS",
            "source": "src/small_paper/v1r_native_entry_live.py + e1_x34a_execution_policy.arms.find_ask_cross_fill",
            "entry": "PASSIVE Bid1 limit; fill when Ask crosses limit; fill_price=limit",
            "wait_sec": float(WAIT_SEC),
            "cap": int(POSITION_CAP),
            "not_pb1_entry": True,
            "reused_for_pb1_complete_strategy_fill": False,
            "reused_for_occupancy": True,
        },
        "historical_research_approximation": {
            "id": HISTORICAL_FILL_ID,
            "RESEARCH_EXECUTION_APPROXIMATION": True,
            "source": "PB1 V4 walk._next_open + SR support_resistance_mechanism_to_complete_strategy_v1.exits.next_avail_i",
            "fill_price": "next non-lunch 1m open after entry_allowed_at",
            "same_bar_forbidden": True,
            "mid_forbidden": True,
            "cost_model": COST_MODEL_ID,
            "X1_TAX_BPS": float(X1_TAX_BPS),
            "X1_TAX_ROLE": "research friction stress, not observed spread",
            "shares": int(SHARES),
            "used_on_development_replay": True,
        },
        "delta_vs_live_sot": [
            "Historical parquet cannot evaluate Ask1/Bid1 freshness or qty.",
            "Development replay therefore uses next-bar open (already the Frozen V4 entry_t/entry_px).",
            "8bps X1 is a documented stress tax, not Bid/Ask.",
            "When Bid/Ask boards exist (paper/prospective native capture), live SoT remains X1_IMMEDIATE_ASK / first causal Bid1.",
        ],
    }


def exit_inventory() -> dict[str, Any]:
    return {
        "selected_technical_exit": {
            "id": TECHNICAL_EXIT_ID,
            "rule": "Frozen V4 THESIS_LOST on the same candidate-day; fill next non-lunch open after THESIS_LOST_AT",
            "entry_aligned": True,
            "causal": True,
            "future_information_free": True,
            "parameter_search": False,
            "chosen_with_confirmation_pnl": False,
            "source": "src/research/pb1_v4_clarified_machine_correction_v4 (frozen THESIS_LOST)",
        },
        "selected_ops_exit": {
            "id": OPS_EXIT_ID,
            "rule": "If still OPEN after AM without THESIS_LOST, HOLD_THROUGH_LUNCH_RESUME_PM then flatten at 15:20 open",
            "not_thesis_death": True,
            "source": "PB1 SESSION_FLAT=15:20; V4 flatten_1520_is_session_ops_not_thesis_death",
        },
        "rejected_for_this_bind": [
            {
                "id": "Z1-Z5_SIMPLE_FULL",
                "why": "frozen generic VWAP/structure exits; not PB1-entry-aligned; winners ranked on other AM windows",
            },
            {
                "id": "AM_C0_INDICATOR_EXIT",
                "why": "wrong ENTRY parent (C0), not PB1 thesis",
            },
            {
                "id": "FIXED_HOLD_N_BAR",
                "why": "forbidden as ENTRY selection and as Complete Strategy technical EXIT",
            },
            {
                "id": "V1R_EXIT600",
                "why": "fixed 600s hold; V1R paper path, not PB1",
            },
        ],
        "portfolio": {
            "CAP": int(CAP),
            "CAP_SOURCE": "small_paper.v1r_primary_runtime.POSITION_CAP",
            "same_symbol": "reject SIGNAL if symbol already pending or open that session date",
            "occupancy": "increments at FILL, decrements at EXIT fill (SLOT_RELEASE)",
            "reentry": "allowed after slot release if Frozen V4 emits a later EXECUTION_READY; no winner/loser filter",
            "event_priority": "EXIT before FILL before EXPIRE before ADMIT",
        },
    }
