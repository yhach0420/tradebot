"""Decide freeze vs unimplementable. No PnL. No replay."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_redesign_v1 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_AFTER_PASS,
    CASE_FROZEN,
    CASE_UNIMPLEMENTABLE,
    NEXT_IF_FROZEN,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    SESSION_FLATTEN_T_LABEL,
)
from research.new_full_strategy_architecture_redesign_v1.isolation import OUT
from research.new_full_strategy_architecture_redesign_v1.source_audit import (
    breadth_audit,
    flatten_audit,
    quote_audit,
    simultaneous_audit,
    timer_audit,
    universe_audit,
    x1_audit,
)
from research.new_full_strategy_architecture_redesign_v1.spec import dumps_sha256, frozen_strategy, spec_sha256

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

GATE_IDS = (
    "CAUSAL_UNIVERSE_PASS",
    "COMMON_BARRIER_PASS",
    "BREADTH_SEMANTIC_FIDELITY_PASS",
    "LATE_EVENT_CAUSALITY_PASS",
    "COMPARABLE_SET_PASS",
    "SIMULTANEOUS_SIGNAL_PASS",
    "X1_IDENTITY_PASS",
    "QUOTE_FRESHNESS_PASS",
    "EXIT_CAUSAL_PASS",
    "SESSION_FLATTEN_CAUSAL_PASS",
    "ENTRY_CUTOFF_CAUSAL_PASS",
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
    timer = timer_audit()
    breadth = breadth_audit()
    sim = simultaneous_audit()
    x1 = x1_audit()
    quote = quote_audit()
    flat = flatten_audit()
    frozen = frozen_strategy()
    _assert_no_pnl([uni, timer, breadth, sim, x1, quote, flat, frozen])
    gates = {
        "CAUSAL_UNIVERSE_PASS": bool(uni["CAUSAL_UNIVERSE_PASS"]),
        "COMMON_BARRIER_PASS": bool(timer["COMMON_BARRIER_PASS"]),
        "BREADTH_SEMANTIC_FIDELITY_PASS": bool(breadth["BREADTH_SEMANTIC_FIDELITY_PASS"]),
        "LATE_EVENT_CAUSALITY_PASS": bool(timer["LATE_EVENT_CAUSALITY_PASS"]),
        "COMPARABLE_SET_PASS": bool(breadth["COMPARABLE_SET_PASS"]),
        "SIMULTANEOUS_SIGNAL_PASS": bool(sim["SIMULTANEOUS_SIGNAL_PASS"]),
        "X1_IDENTITY_PASS": bool(x1["X1_IDENTITY_PASS"]),
        "QUOTE_FRESHNESS_PASS": bool(quote["QUOTE_FRESHNESS_PASS"]),
        "EXIT_CAUSAL_PASS": bool(flat["EXIT_CAUSAL_PASS"]),
        "SESSION_FLATTEN_CAUSAL_PASS": bool(flat["SESSION_FLATTEN_CAUSAL_PASS"]),
        "ENTRY_CUTOFF_CAUSAL_PASS": bool(flat["ENTRY_CUTOFF_CAUSAL_PASS"]),
    }
    implementable = bool(timer["IMPLEMENTABLE"] and flat["IMPLEMENTABLE"] and all(gates.values()))
    if implementable:
        verdict = CASE_FROZEN
        nxt = NEXT_IF_FROZEN
        sha = dumps_sha256(frozen)
        another = False
    else:
        verdict = CASE_UNIMPLEMENTABLE
        nxt = None
        sha = None
        another = False
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "FULL_STRATEGY_SPEC_SHA256_V3": sha,
        "ANOTHER_PRECOMMIT_AFTER_PASS": another,
        "ARCHITECTURE_FROZEN": bool(implementable),
        "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        "NEW_ARCHITECTURE_PNL_READ_N": 0,
        "SESSION_FLATTEN_SELECTED_FROM_PNL": False,
        "G1_G6": False,
        "EXACT_EVIDENCE": (
            f"Gates {gates}. "
            + (
                f"Frozen V3 sha256={sha}. Next implementation/DEV eval. No further precommit."
                if implementable
                else "Common timer or 11:29 flatten not faithfully implementable. CSB stopped."
            )
        ),
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective": {"Q1": True, "Q2": True, "Q3": False},
        "universe": uni,
        "timer": timer,
        "breadth": breadth,
        "simultaneous": sim,
        "x1": x1,
        "quote": quote,
        "flatten": flat,
        "frozen_strategy": frozen if implementable else None,
        "gates": gates,
        "decision": decision,
        "data_access": {
            "BURNED_HOLDOUT_READ_N": 0,
            "STRESS_READ_N": 0,
            "FUTURE_DATA_N": 0,
            "RAW_CAPTURE_READ_N": 0,
            "NEW_ARCHITECTURE_PNL_READ_N": 0,
            "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        },
        "guards": {
            "ALPHA_MECHANISM_CHANGED": False,
            "ENTRY_ALPHA_CHANGED": False,
            "TECHNICAL_EXIT_CHANGED": False,
            "PORTFOLIO_CAP_CHANGED": False,
            "BAR_FINALIZATION_SEMANTICS_CLARIFIED": True,
            "OPERATIONAL_SESSION_RULE_CHANGED": True,
            "ANOTHER_PRECOMMIT_AFTER_PASS": ANOTHER_PRECOMMIT_AFTER_PASS,
            "VWAP_ENTRY_USED": False,
            "CURRENT_PRICE_TIME_QUOTE_FRESHNESS": False,
            "RETROSPECTIVE_BID_WALKBACK": False,
        },
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    timer = dict(pack.get("timer") or {})
    flat = dict(pack.get("flatten") or {})
    g = dict(pack.get("guards") or {})
    return {
        "45_timer_uses_ingress_arrival_causality": timer.get("TIMER_ORDER_DOMAIN") == "INGRESS_CAUSAL_ORDER",
        "46_late_pre_boundary_timestamp_can_mutate_finalized_bar": False,
        "47_historical_replay_uses_future_arriving_event_in_old_bar": False,
        "48_timer_event_precedence_defined": timer.get("TIMER_EVENT_PRECEDENCE_DEFINED"),
        "49_SESSION_FLATTEN_T": SESSION_FLATTEN_T_LABEL,
        "50_pending_ENTRY_expires_at_flatten": True,
        "51_old_pending_ENTRY_can_fill_after_flatten": False,
        "52_new_signal_accepted_after_flatten": False,
        "53_retrospective_Bid_walkback": False,
        "54_session_unfilled_handling_defined": flat.get("SESSION_EXIT_UNFILLED_DEFINED"),
        "55_duplicate_EXIT_possible": False,
        "56_alpha_mechanism_changed": False,
        "57_technical_EXIT_changed": False,
        "58_operational_session_rule_changed": True,
        "59_new_V3_hash_generated": bool(d.get("FULL_STRATEGY_SPEC_SHA256_V3")),
        "60_another_precommit_planned_after_PASS": g.get("ANOTHER_PRECOMMIT_AFTER_PASS"),
        "61_next_after_PASS": d.get("NEXT"),
        "VERDICT": d.get("VERDICT"),
        "FULL_STRATEGY_SPEC_SHA256_V3": d.get("FULL_STRATEGY_SPEC_SHA256_V3"),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "PnL_read": False,
        "economics_run": False,
    }
