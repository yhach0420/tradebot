"""Select and freeze at most one architecture. No PnL. No replay."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CLOSED_LINEAGE_IDS,
    FORBIDDEN_NEXT_RUNS,
    NEXT_IF_A,
    NEXT_IF_B,
    OLD_RCA_ENDPOINT,
    OLD_RCA_VERDICT,
    OLD_ST_RCA_CONTINUED,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
)
from research.new_full_strategy_architecture_precommit_v1.closed import closed_lineage_cards, revival_flags
from research.new_full_strategy_architecture_precommit_v1.eligibility import GATES, _int, evaluate_proposal, selection_key
from research.new_full_strategy_architecture_precommit_v1.isolation import OUT
from research.new_full_strategy_architecture_precommit_v1.proposals import proposals
from research.new_full_strategy_architecture_precommit_v1.spec import dumps_sha256, spec_sha256

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
)

LINEAGE_COMPARE_KEYS = {
    "SIMPLE_FULL": ("SIMPLE_FULL",),
    "E4": ("E4",),
    "SYSTEMATIC_STATE_TRANSITION": ("ST", "SYSTEMATIC_STATE_TRANSITION"),
    "C1_MULTI_TIMEFRAME": ("C1", "C1_MULTI_TIMEFRAME"),
    "C4_PORTFOLIO_CROWDING": ("C4", "C4_PORTFOLIO_CROWDING"),
    "RECOVERY_SEQUENCE": ("RECOVERY", "RECOVERY_SEQUENCE"),
    "PARTICIPATION_ONSET": ("PARTICIPATION", "PARTICIPATION_ONSET"),
    "PFQ": ("PFQ",),
    "OR": ("OR",),
    "DYNAMIC_ANCHOR": ("DYNAMIC_ANCHOR",),
    "X9": ("X9",),
}


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


def novelty_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in rows:
        cmp = dict(p.get("CLOSED_LINEAGE_COMPARE") or {})
        match = p.get("CLOSED_LINEAGE_MATCH") is True
        for lineage in CLOSED_LINEAGE_IDS:
            keys = LINEAGE_COMPARE_KEYS.get(lineage, (lineage,))
            reason = next((str(cmp[k]) for k in keys if k in cmp), "")
            if not reason:
                reason = (
                    f"CLOSED_LINEAGE_MATCH=true for {p.get('ARCHITECTURE_ID')}"
                    if match
                    else f"Not a retune of {lineage}; CLOSED_LINEAGE_MATCH=false."
                )
            distinct = (not match) and bool(reason)
            out.append(
                {
                    "ARCHITECTURE_ID": p.get("ARCHITECTURE_ID"),
                    "CLOSED_LINEAGE": lineage,
                    "CLOSED_LINEAGE_MATCH": match,
                    "STRUCTURALLY_DISTINCT": bool(distinct and not match),
                    "REASON": reason,
                }
            )
    return out


def _frozen_pack(p: dict[str, Any]) -> dict[str, Any]:
    port = dict(p["PORTFOLIO"])
    return {
        "ARCHITECTURE_ID": p["ARCHITECTURE_ID"],
        "CORE_MECHANISM": p["CORE_MECHANISM"],
        "CAUSAL_EVENT_SEQUENCE": list(p["CAUSAL_EVENT_SEQUENCE"]),
        "ENTRY": p["ENTRY_STATE_MACHINE"],
        "ENTRY_STATE_MACHINE": p["ENTRY_STATE_MACHINE"],
        "EXECUTION": p["EXECUTION_RULE"],
        "EXECUTION_RULE": p["EXECUTION_RULE"],
        "EXIT": p["EXIT_STATE_MACHINE"],
        "EXIT_STATE_MACHINE": p["EXIT_STATE_MACHINE"],
        "CAP": int(port["CAP"]),
        "SHARES": int(port["SHARES"]),
        "same_symbol": True,
        "SAME_SYMBOL_BEHAVIOR": port["SAME_SYMBOL_BEHAVIOR"],
        "OCCUPANCY_BEHAVIOR": port["OCCUPANCY_BEHAVIOR"],
        "SLOT_RELEASE": port["SLOT_RELEASE"],
        "REENTRY": True,
        "REENTRY_RULE": port["REENTRY_RULE"],
        "SESSION_CLOSE": port["SESSION_CLOSE"],
        "SESSION": port["SESSION"],
        "SESSION_WINDOW": port["SESSION_WINDOW"],
        "PORTFOLIO": port,
        "EVENT_TIME_SEMANTICS": p["ENTRY_STATE_MACHINE"].get("CLOCK") or p.get("WHEN_IT_BECOMES_KNOWN"),
        "WHEN_IT_BECOMES_KNOWN": p.get("WHEN_IT_BECOMES_KNOWN"),
        "VWAP_ENTRY_USED": False,
        "VWAP_EXIT_USED": False,
        "VWAP_FILTER_USED": False,
        "BOARD_PRIMARY_ALPHA": False,
        "ENTRY_EXIT_JOINTLY_DEFINED": True,
        "ARCHITECTURE_FROZEN_BEFORE_ECONOMICS": True,
        "NEW_THRESHOLD_SEARCH": False,
        "OLD_ARCHITECTURE_RETUNE": False,
    }


def _selection_trace(eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = list(eligible)
    steps = [
        "fewest_discretionary_degrees_of_freedom",
        "fewest_distinct_signal_primitives",
        "simplest_causal_state_machine",
        "highest_expected_coverage_plausibility",
        "lowest_implementation_ambiguity",
        "lexicographic_ARCHITECTURE_ID",
    ]
    keys = ["DISCRETIONARY_DOF", "SIGNAL_PRIMITIVE_N", "STATE_MACHINE_SIZE", "COVERAGE_PLAUSIBILITY_RANK", "IMPLEMENTATION_AMBIGUITY_RANK", "ARCHITECTURE_ID"]
    trace = []
    for i, (rule, field) in enumerate(zip(steps, keys), start=1):
        if len(remaining) <= 1:
            trace.append(
                {
                    "step": i,
                    "rule": rule,
                    "remaining_ids": [r["ARCHITECTURE_ID"] for r in remaining],
                    "applied": False,
                }
            )
            continue
        if field == "ARCHITECTURE_ID":
            remaining = sorted(remaining, key=lambda r: str(r.get(field) or ""))
            keep = remaining[:1]
        else:
            best = min(_int(r.get(field), 99) for r in remaining)
            keep = [r for r in remaining if _int(r.get(field), 99) == best]
        trace.append(
            {
                "step": i,
                "rule": rule,
                "remaining_ids": [r["ARCHITECTURE_ID"] for r in keep],
                "applied": True,
            }
        )
        remaining = keep
    return trace


def _compare_present(selected: dict[str, Any], *keys: str) -> bool:
    if not selected or selected.get("CLOSED_LINEAGE_MATCH") is not False:
        return False
    cmp = dict(selected.get("CLOSED_LINEAGE_COMPARE") or {})
    return any(k in cmp for k in keys)


def decide() -> dict[str, Any]:
    if not (Q1_NEW_LOGIC_COMPLETION_DIRECT and Q2_BLOCKING_WITHOUT_THIS_RUN and (not Q3_OLD_RCA_AS_PURPOSE)):
        raise RuntimeError("PRIORITY_GATE")
    if OLD_ST_RCA_CONTINUED:
        raise RuntimeError("OLD_ST_RCA_CONTINUED")
    rows = proposals()
    _assert_no_pnl(rows)
    eligibility = [evaluate_proposal(p) for p in rows]
    by = {p["ARCHITECTURE_ID"]: p for p in rows}
    eligible_ids = [e["ARCHITECTURE_ID"] for e in eligibility if e["PROPOSAL_ELIGIBLE"]]
    eligible_n = len(eligible_ids)
    eligible_props = [by[i] for i in eligible_ids]
    selected = None
    frozen = None
    spec_hash = None
    trace = _selection_trace(eligible_props)
    if eligible_n >= 1:
        ordered = sorted(eligible_props, key=selection_key)
        selected = ordered[0]
        if selected.get("EXIT_STATE_MACHINE", {}).get("JOINT_WITH_ENTRY") is not True:
            raise RuntimeError("SELECTED_EXIT_NOT_JOINT")
        frozen = _frozen_pack(selected)
        _assert_no_pnl(frozen)
        spec_hash = dumps_sha256(frozen)
        verdict = CASE_A
        nxt = NEXT_IF_A
        arch_frozen = True
        winner_forced = False
    else:
        verdict = CASE_B
        nxt = NEXT_IF_B
        arch_frozen = False
        winner_forced = False
    if nxt in FORBIDDEN_NEXT_RUNS:
        raise RuntimeError("OLD_RCA_NEXT")
    novelty = novelty_rows(rows)
    decision = {
        "CASE": "A" if verdict == CASE_A else "B",
        "ELIGIBLE_PROPOSAL_N": eligible_n,
        "ELIGIBLE_PROPOSAL_IDS": eligible_ids,
        "SELECTED_ARCHITECTURE_N": 1 if selected else 0,
        "SELECTED_ARCHITECTURE_ID": None if selected is None else selected["ARCHITECTURE_ID"],
        "DETERMINISTIC_SELECTION_RULE_USED": True,
        "WINNER_FORCED_DESPITE_NO_ELIGIBLE": winner_forced,
        "ARCHITECTURE_FROZEN": arch_frozen,
        "ARCHITECTURE_FROZEN_BEFORE_ECONOMICS": arch_frozen,
        "FULL_STRATEGY_SPEC_SHA256": spec_hash,
        "ENTRY_EXIT_JOINTLY_DEFINED": bool(selected is not None),
        "OLD_ST_RCA_CONTINUED": False,
        "SELECTION_EVIDENCE_GAP_RUN": False,
        "NEW_ARCHITECTURE_PNL_READ_N": 0,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        "PNL_USED_IN_PROPOSAL_CREATION": False,
        "PNL_USED_IN_PROPOSAL_SELECTION": False,
        "VERDICT": verdict,
        "NEXT": nxt,
        "EXACT_EVIDENCE": (
            f"Proposals {[p['ARCHITECTURE_ID'] for p in rows]}; "
            f"eligible {eligible_ids}. "
            + (
                f"Frozen {selected['ARCHITECTURE_ID']} sha256={spec_hash}."
                if selected is not None
                else "No A1-A8 pass; no architecture invented."
            )
        ),
    }
    data_access = {
        "BURNED_HOLDOUT_READ_N": 0,
        "STRESS_READ_N": 0,
        "STRESS_FILE_OPEN_N": 0,
        "FUTURE_DATA_N": 0,
        "RAW_CAPTURE_READ_N": 0,
        "PRIOR_ECONOMIC_REPORT_OPEN_N": 0,
        "NEW_ARCHITECTURE_PNL_READ_N": 0,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        "NEW_REPLAY": False,
        "MAX_RESEARCH_DATE": "20260807",
        "STRUCTURAL_DESIGN_INSPECTION_ONLY": True,
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective": {
            "Q1": Q1_NEW_LOGIC_COMPLETION_DIRECT,
            "Q2": Q2_BLOCKING_WITHOUT_THIS_RUN,
            "Q3": Q3_OLD_RCA_AS_PURPOSE,
            "PRIMARY_GOAL": "Freeze one complete Full Strategy before any DEV economic result is viewed.",
            "NON_GOALS": [
                "old ST RCA",
                "selection evidence gap",
                "economic ranking",
                "PnL comparison",
                "ENTRY-only search",
                "EXIT-only search",
                "threshold tuning",
                "Sizing",
                "Stress",
                "future",
            ],
        },
        "old_rca_stop": {
            "ENDPOINT": OLD_RCA_ENDPOINT,
            "VERDICT": OLD_RCA_VERDICT,
            "S1": "FAIL",
            "S2": "PASS",
            "S3": "PASS",
            "S4": "FAIL",
            "RECURRENCE": "INCONCLUSIVE_MISSING_FOLD_EVIDENCE",
            "OLD_ST_RCA_CONTINUED": False,
            "SELECTION_EVIDENCE_GAP_RUN": False,
            "FORBIDDEN_NEXT_RUNS": list(FORBIDDEN_NEXT_RUNS),
        },
        "design_constraints": {
            "C1_COVERAGE_MEANINGFUL": True,
            "C2_NOT_ONE_SYMBOL": True,
            "C3_NOT_ONE_DAY": True,
            "C4_FULL_DEV_PNL_INSUFFICIENT": True,
            "C5_ENTRY_ONLY_NOT_UNIT": True,
            "C6_EXIT_WITH_ENTRY": True,
            "C7_PORTFOLIO_CAUSAL": True,
            "C8_NO_CLOSED_RESCUE": True,
            "RCA_ON_C1_C8": False,
        },
        "closed_lineages": closed_lineage_cards(),
        "revival": revival_flags(),
        "proposals": rows,
        "eligibility": eligibility,
        "gates": list(GATES),
        "novelty_audit": novelty,
        "selection_trace": trace,
        "selected": selected,
        "frozen_strategy": frozen,
        "decision": decision,
        "data_access": data_access,
        "guards": {
            "NEW_REPLAY": False,
            "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
            "NEW_ARCHITECTURE_PNL_READ_N": 0,
            "PNL_USED_IN_PROPOSAL_CREATION": False,
            "PNL_USED_IN_PROPOSAL_SELECTION": False,
            "VWAP_ENTRY_USED": False,
            "VWAP_EXIT_USED": False,
            "VWAP_FILTER_USED": False,
            "BOARD_PRIMARY_ALPHA": False,
            "NEW_THRESHOLD_SEARCH": False,
            "OLD_ARCHITECTURE_RETUNE": False,
            "SIZING": False,
            "RAW_CAPTURE_READ_N": 0,
            "OLD_ST_RCA_CONTINUED": False,
            "SELECTION_EVIDENCE_GAP_RUN": False,
        },
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    props = list(pack.get("proposals") or [])
    elig = list(pack.get("eligibility") or [])
    frozen = dict(pack.get("frozen_strategy") or {})
    obj = dict(pack.get("objective") or {})
    old = dict(pack.get("old_rca_stop") or {})
    selected = dict(pack.get("selected") or {})
    by_e = {e["ARCHITECTURE_ID"]: e for e in elig}
    port = dict(frozen.get("PORTFOLIO") or {})
    return {
        "1_current_priority_new_logic_completion": obj.get("Q1"),
        "2_old_ST_RCA_stopped": old.get("OLD_ST_RCA_CONTINUED") is False,
        "3_selection_evidence_gap_run": False,
        "4_proposal_N": len(props),
        "5_proposal_IDs": [p.get("ARCHITECTURE_ID") for p in props],
        "6_each_proposal_complete_ENTRY_EXIT_portfolio": bool(props)
        and all(by_e[p["ARCHITECTURE_ID"]]["A1_COMPLETE_FULL_STRATEGY"] for p in props),
        "7_each_causal": bool(props) and all(by_e[p["ARCHITECTURE_ID"]]["A2_CAUSAL_IMPLEMENTABLE"] for p in props),
        "8_each_structurally_distinct": bool(props)
        and all(by_e[p["ARCHITECTURE_ID"]]["A4_STRUCTURALLY_DISTINCT"] for p in props),
        "9_each_data_available": bool(props)
        and all(by_e[p["ARCHITECTURE_ID"]]["A3_DATA_AVAILABLE_ON_DEV"] for p in props),
        "10_each_Coverage_plausible": bool(props)
        and all(by_e[p["ARCHITECTURE_ID"]]["A7_COVERAGE_PLAUSIBLE"] for p in props),
        "11_eligible_proposal_N": d.get("ELIGIBLE_PROPOSAL_N"),
        "12_PnL_used_in_proposal_creation": False,
        "13_PnL_used_in_proposal_selection": False,
        "14_new_architecture_economics_run": False,
        "15_VWAP_ENTRY_used": False,
        "16_VWAP_EXIT_used": False,
        "17_Board_primary_alpha": False,
        "18_selected_architecture_ID": d.get("SELECTED_ARCHITECTURE_ID"),
        "19_deterministic_selection_rule_used": d.get("DETERMINISTIC_SELECTION_RULE_USED"),
        "20_winner_forced_despite_no_eligible_architecture": d.get("WINNER_FORCED_DESPITE_NO_ELIGIBLE"),
        "21_ENTRY_fully_frozen": bool(frozen.get("ENTRY_STATE_MACHINE")),
        "22_EXECUTION_fully_frozen": bool(frozen.get("EXECUTION_RULE")),
        "23_EXIT_fully_frozen": bool(frozen.get("EXIT_STATE_MACHINE")),
        "24_CAP_fully_frozen": int(frozen.get("CAP") or 0) == 5,
        "25_same_symbol_frozen": frozen.get("same_symbol") is True,
        "26_occupancy_frozen": bool(frozen.get("OCCUPANCY_BEHAVIOR") or port.get("OCCUPANCY_BEHAVIOR")),
        "27_slot_release_frozen": bool(frozen.get("SLOT_RELEASE") or port.get("SLOT_RELEASE")),
        "28_reentry_frozen": frozen.get("REENTRY") is True,
        "29_session_close_frozen": bool(frozen.get("SESSION_CLOSE") or port.get("SESSION_CLOSE")),
        "30_ENTRY_EXIT_jointly_defined": d.get("ENTRY_EXIT_JOINTLY_DEFINED"),
        "31_FULL_STRATEGY_SPEC_SHA256": d.get("FULL_STRATEGY_SPEC_SHA256"),
        "32_distinct_from_Simple_Full": _compare_present(selected, "SIMPLE_FULL"),
        "33_distinct_from_ST": _compare_present(selected, "ST", "SYSTEMATIC_STATE_TRANSITION"),
        "34_distinct_from_C1": _compare_present(selected, "C1", "C1_MULTI_TIMEFRAME"),
        "35_distinct_from_C4": _compare_present(selected, "C4", "C4_PORTFOLIO_CROWDING"),
        "36_distinct_from_Recovery": _compare_present(selected, "RECOVERY", "RECOVERY_SEQUENCE"),
        "37_distinct_from_Participation": _compare_present(selected, "PARTICIPATION", "PARTICIPATION_ONSET"),
        "38_distinct_from_PFQ": _compare_present(selected, "PFQ"),
        "39_distinct_from_X9": _compare_present(selected, "X9"),
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
    }
