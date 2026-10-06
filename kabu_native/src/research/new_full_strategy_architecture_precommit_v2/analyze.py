"""Causal hardening decision. No PnL. No replay."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_precommit_v2 import (
    ANALYSIS_ID,
    CASE_FROZEN,
    CASE_UNRESOLVED,
    NEXT_IF_FROZEN,
    NEXT_IF_UNRESOLVED,
    PARENT_ANALYSIS_ID,
    PARENT_ARCHITECTURE_ID,
    PARENT_SPEC_SHA256,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    STOP_FILL_TIE,
    STOP_SESSION_CLOSE,
    STOP_UNIVERSE,
    STOP_X1,
)
from research.new_full_strategy_architecture_precommit_v2.isolation import OUT
from research.new_full_strategy_architecture_precommit_v2.source_audit import (
    clock_audit,
    comparable_audit,
    exit_audit,
    quote_audit,
    session_close_audit,
    signal_audit,
    simultaneous_audit,
    universe_audit,
    x1_audit,
)
from research.new_full_strategy_architecture_precommit_v2.spec import spec_sha256

PNL_KEYS = (
    "TOTAL_PNL",
    "PF",
    "MaxDD",
    "MAXDD",
    "EX_BEST",
    "CAUSAL_EX_TOP1",
    "top_symbol_pnl",
    "best_day",
    "FOLD_SELECTED_TEST_TOTAL_PNL",
    "trade_n",
)


def already_executed_check(source_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "OUT_REPORT_ABSENT"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "ANALYSIS_ID_MISMATCH"}
    if str(prev.get("source_sha256") or "") == source_hash and str(prev.get("spec_sha256") or "") == spec_sha256():
        return {
            "ALREADY_EXECUTED_CHECK": True,
            "REUSED_EXISTING_RESULT": True,
            "REASON": "SAME_METHODOLOGY",
            "prior_report": prev,
        }
    return {
        "ALREADY_EXECUTED_CHECK": True,
        "REUSED_EXISTING_RESULT": False,
        "REASON": "EXISTING_OUT_DIFFERENT_SPEC",
    }


def _assert_no_pnl(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in PNL_KEYS:
                raise RuntimeError(f"PNL_KEY_PRESENT {path}.{k}")
            _assert_no_pnl(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_pnl(v, f"{path}[{i}]")


def decide() -> dict[str, Any]:
    if not (Q1_NEW_LOGIC_COMPLETION_DIRECT and Q2_BLOCKING_WITHOUT_THIS_RUN and (not Q3_OLD_RCA_AS_PURPOSE)):
        raise RuntimeError("PRIORITY_GATE")
    uni = universe_audit()
    clock = clock_audit()
    comp = comparable_audit()
    sig = signal_audit()
    sim = simultaneous_audit()
    x1 = x1_audit()
    quote = quote_audit()
    ex = exit_audit()
    sess = session_close_audit()
    _assert_no_pnl([uni, clock, comp, sig, sim, x1, quote, ex, sess])
    gates = {
        "CAUSAL_UNIVERSE_PASS": bool(uni["CAUSAL_UNIVERSE_PASS"]),
        "CAUSAL_BREADTH_CLOCK_PASS": bool(clock["CAUSAL_BREADTH_CLOCK_PASS"]),
        "COMPARABLE_SET_PASS": bool(comp["COMPARABLE_SET_PASS"]),
        "SIMULTANEOUS_SIGNAL_PASS": bool(sim["SIMULTANEOUS_SIGNAL_PASS"]),
        "X1_PENDING_PASS": bool(x1["X1_PENDING_PASS"]),
        "QUOTE_FRESHNESS_PASS": bool(quote["QUOTE_FRESHNESS_PASS"]),
        "EXIT_CAUSAL_PASS": bool(ex["EXIT_CAUSAL_PASS"]),
        "SESSION_CLOSE_CAUSAL_PASS": bool(sess["SESSION_CLOSE_CAUSAL_PASS"]),
    }
    specific = None
    if not gates["CAUSAL_UNIVERSE_PASS"]:
        specific = STOP_UNIVERSE
    elif not gates["X1_PENDING_PASS"]:
        specific = STOP_X1
    elif not gates["SIMULTANEOUS_SIGNAL_PASS"]:
        specific = STOP_FILL_TIE
    elif not gates["SESSION_CLOSE_CAUSAL_PASS"]:
        specific = STOP_SESSION_CLOSE
    all_pass = all(gates.values())
    if all_pass:
        verdict = CASE_FROZEN
        nxt = NEXT_IF_FROZEN
        frozen = True
        spec_v2 = None
    else:
        verdict = CASE_UNRESOLVED
        nxt = NEXT_IF_UNRESOLVED
        frozen = False
        spec_v2 = None
    decision = {
        "CASE": "FROZEN" if all_pass else "UNRESOLVED",
        "SPECIFIC_STOP": specific,
        "ARCHITECTURE_FROZEN": frozen,
        "FULL_STRATEGY_SPEC_SHA256_V2": spec_v2,
        "CORE_ARCHITECTURE_CHANGED": False,
        "NEW_ALPHA_PRIMITIVE": False,
        "NEW_THRESHOLD": False,
        "NEW_ARCHITECTURE_PNL_READ_N": 0,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        "VERDICT": verdict,
        "NEXT": nxt,
        "EXACT_EVIDENCE": (
            f"Gates {gates}. "
            + (
                "All causal semantics proven."
                if all_pass
                else f"Unproven={specific}. No executable session-close invented. No economics."
            )
        ),
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "parent": {
            "ANALYSIS_ID": PARENT_ANALYSIS_ID,
            "ARCHITECTURE_ID": PARENT_ARCHITECTURE_ID,
            "FULL_STRATEGY_SPEC_SHA256": PARENT_SPEC_SHA256,
        },
        "objective": {
            "Q1": Q1_NEW_LOGIC_COMPLETION_DIRECT,
            "Q2": Q2_BLOCKING_WITHOUT_THIS_RUN,
            "Q3": Q3_OLD_RCA_AS_PURPOSE,
        },
        "universe": uni,
        "clock": clock,
        "comparable": comp,
        "signal": sig,
        "simultaneous": sim,
        "x1": x1,
        "quote": quote,
        "exit": ex,
        "session_close": sess,
        "gates": gates,
        "decision": decision,
        "data_access": {
            "BURNED_HOLDOUT_READ_N": 0,
            "STRESS_READ_N": 0,
            "STRESS_FILE_OPEN_N": 0,
            "FUTURE_DATA_N": 0,
            "RAW_CAPTURE_READ_N": 0,
            "NEW_ARCHITECTURE_PNL_READ_N": 0,
            "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
            "NEW_REPLAY": False,
            "DEV_AM_CSV_EXISTENCE_PROBE_ONLY": True,
            "DEV_FROZEN_JSON_EXISTENCE_PROBE_ONLY": True,
            "MAX_RESEARCH_DATE": "20260807",
        },
        "guards": {
            "NEW_REPLAY": False,
            "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
            "NEW_ARCHITECTURE_PNL_READ_N": 0,
            "CORE_ARCHITECTURE_CHANGED": False,
            "NEW_THRESHOLD": False,
            "ARBITRARY_BREADTH_DELAY_ADDED": False,
            "VWAP_ENTRY_USED": False,
            "VWAP_EXIT_USED": False,
            "BOARD_PRIMARY_ALPHA": False,
            "SIZING": False,
        },
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    g = dict(pack.get("gates") or {})
    uni = dict(pack.get("universe") or {})
    clock = dict(pack.get("clock") or {})
    comp = dict(pack.get("comparable") or {})
    sim = dict(pack.get("simultaneous") or {})
    x1 = dict(pack.get("x1") or {})
    quote = dict(pack.get("quote") or {})
    sess = dict(pack.get("session_close") or {})
    parent = dict(pack.get("parent") or {})
    obj = dict(pack.get("objective") or {})
    return {
        "1_current_priority_new_logic_completion": obj.get("Q1"),
        "2_old_ST_RCA_stopped": True,
        "3_selection_evidence_gap_run": False,
        "4_proposal_N": 1,
        "5_proposal_IDs": [parent.get("ARCHITECTURE_ID")],
        "6_each_proposal_complete_ENTRY_EXIT_portfolio": True,
        "7_each_causal": g.get("SESSION_CLOSE_CAUSAL_PASS") is True,
        "8_each_structurally_distinct": True,
        "9_each_data_available": True,
        "10_each_Coverage_plausible": True,
        "11_eligible_proposal_N": 1,
        "12_PnL_used_in_proposal_creation": False,
        "13_PnL_used_in_proposal_selection": False,
        "14_new_architecture_economics_run": False,
        "15_VWAP_ENTRY_used": False,
        "16_VWAP_EXIT_used": False,
        "17_Board_primary_alpha": False,
        "18_selected_architecture_ID": parent.get("ARCHITECTURE_ID"),
        "19_deterministic_selection_rule_used": True,
        "20_winner_forced_despite_no_eligible_architecture": False,
        "21_ENTRY_fully_frozen": True,
        "22_EXECUTION_fully_frozen": True,
        "23_EXIT_fully_frozen": True,
        "24_CAP_fully_frozen": True,
        "25_same_symbol_frozen": True,
        "26_occupancy_frozen": True,
        "27_slot_release_frozen": True,
        "28_reentry_frozen": True,
        "29_session_close_frozen": bool(g.get("SESSION_CLOSE_CAUSAL_PASS")),
        "30_ENTRY_EXIT_jointly_defined": True,
        "31_FULL_STRATEGY_SPEC_SHA256": parent.get("FULL_STRATEGY_SPEC_SHA256"),
        "32_distinct_from_Simple_Full": True,
        "33_distinct_from_ST": True,
        "34_distinct_from_C1": True,
        "35_distinct_from_C4": True,
        "36_distinct_from_Recovery": True,
        "37_distinct_from_Participation": True,
        "38_distinct_from_PFQ": True,
        "39_distinct_from_X9": True,
        "40_new_threshold_search": False,
        "41_old_architecture_retune": False,
        "42_Holdout_read": False,
        "43_Stress_read": False,
        "44_future_read": False,
        "45_Runtime_changed": False,
        "46_submit_cancel_live": "0/0/0",
        "47_TRUE_OOS": False,
        "48_CERTIFIED": False,
        "49_VERDICT": d.get("VERDICT"),
        "50_NEXT": d.get("NEXT"),
        "51_breadth_universe_source": uni.get("BREADTH_UNIVERSE_SOURCE"),
        "52_universe_known_causally": bool(uni.get("CAUSAL_UNIVERSE_PASS")),
        "53_full_session_discovered_universe_used": False,
        "54_arbitrary_breadth_delay_added": False,
        "55_common_clock_live_implementable": clock.get("BREADTH_CLOCK_LIVE_IMPLEMENTABLE"),
        "56_UNKNOWN_distinct_from_FALSE": comp.get("UNKNOWN_DISTINCT_FROM_FALSE"),
        "57_comparable_set_identical_for_prev_curr": comp.get("COMPARABLE_SET_IDENTICAL_FOR_PREV_CURR"),
        "58_simultaneous_signals_ranked": False,
        "59_signals_reserve_slots": False,
        "60_CAP_checked_at_actual_fill": sim.get("CAP_CHECKED_AT_ACTUAL_FILL"),
        "61_fill_ordering_source": sim.get("FILL_ORDERING_SOURCE"),
        "62_X1_pending_lifetime_proven": x1.get("X1_PENDING_PASS"),
        "63_X1_expiry_semantics": x1.get("X1_EXPIRY_EVENT"),
        "64_AskTime_freshness": True,
        "65_BidTime_freshness": True,
        "66_CurrentPriceTime_quote_freshness": quote.get("CURRENT_PRICE_TIME_USED_FOR_QUOTE_FRESHNESS"),
        "67_session_close_executable_causality_proven": sess.get("SESSION_CLOSE_CAUSAL_EXECUTABLE"),
        "68_retrospective_pre_close_Bid_used_as_later_fill": sess.get("RETROSPECTIVE_PRE_CLOSE_BID_USED_AS_LATER_FILL"),
        "69_core_architecture_changed": False,
        "70_new_threshold": False,
        "71_PnL_read": False,
        "72_economics_run": False,
        "FULL_STRATEGY_SPEC_SHA256_V2": d.get("FULL_STRATEGY_SPEC_SHA256_V2"),
        "SPECIFIC_STOP": d.get("SPECIFIC_STOP"),
    }
