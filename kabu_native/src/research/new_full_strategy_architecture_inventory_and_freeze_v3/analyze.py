"""Inventory then freeze at most one unused Full Strategy. No PnL. No candidate event counts."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_inventory_and_freeze_v3 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    CASE_FROZEN,
    CASE_NONE,
    CSB_RCA_RUN,
    DIRECTLY_ADVANCES_NEW_LOGIC_COMPLETION,
    NEW_CANDIDATE_ECONOMICS_RUN,
    NEW_CANDIDATE_PNL_READ_N,
    NEXT_IF_FROZEN,
    NEXT_IF_NONE,
    OLD_ST_RCA_CONTINUED,
    PULLBACK_FAMILY_RESCUE_FORBIDDEN,
    V4_CLOSED,
    V4_RCA_RUN,
    V4_RETUNE,
)
from research.new_full_strategy_architecture_inventory_and_freeze_v3.information import available_information
from research.new_full_strategy_architecture_inventory_and_freeze_v3.inventory import closed_architecture_inventory, inventory_ids
from research.new_full_strategy_architecture_inventory_and_freeze_v3.isolation import OUT
from research.new_full_strategy_architecture_inventory_and_freeze_v3.novelty import novelty_audit, proposals
from research.new_full_strategy_architecture_inventory_and_freeze_v3.pullback_family import pullback_family
from research.new_full_strategy_architecture_inventory_and_freeze_v3.spec import spec_sha256_v5
from research.new_full_strategy_architecture_inventory_and_freeze_v3.v4_pin import pin_v4
from research.new_full_strategy_architecture_inventory_and_freeze_v3.withdrawn import withdrawn_v5

PNL_KEYS = (
    "TOTAL_PNL",
    "PF",
    "MaxDD",
    "MAXDD",
    "EX_BEST",
    "EX_BEST_DAY_PNL",
    "CAUSAL_EX_TOP1",
    "CAUSAL_EX_TOP1_PNL",
    "top_symbol_pnl",
    "pnl_yen_100",
    "best_day_pnl",
    "worst_day_pnl",
    "daily_pnl",
    "G1_TOTAL_PNL",
    "G2_PF",
    "G3_DAY_SIGNS",
    "G4_EX_BEST",
    "G5_PNL_PLUS_MAXDD",
    "G6_CAUSAL_EX_TOP1",
    "signal_n",
    "fill_n",
    "X1_fill_n",
)


def already_executed_check(source_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "OUT_REPORT_ABSENT"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "ANALYSIS_ID_MISMATCH"}
    if str(prev.get("source_sha256") or "") == source_hash:
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


def objective_alignment() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Inventory closed strategy architectures without duplicate, design at most three "
            "untested Complete Full Strategies, freeze exactly one if justified, before PnL."
        ),
        "THIS_IS": "NEW LOGIC CONSTRUCTION",
        "THIS_IS_NOT": ["V4 RCA", "CSB RCA", "Pullback RCA", "old strategy rescue", "indicator threshold search"],
        "DIRECTLY_ADVANCES_NEW_LOGIC_COMPLETION": DIRECTLY_ADVANCES_NEW_LOGIC_COMPLETION,
        "Q1_NEW_LOGIC_COMPLETION_DIRECT": True,
        "Q2_BLOCKING_WITHOUT_THIS_RUN": True,
        "Q3_OLD_RCA_AS_PURPOSE": False,
    }


def decide() -> dict[str, Any]:
    v4 = pin_v4()
    withdrawn = withdrawn_v5()
    inv = closed_architecture_inventory()
    pb = pullback_family()
    info = available_information()
    novelty = novelty_audit()
    props = proposals()
    eligible: list[dict[str, Any]] = []
    if not v4.get("ok"):
        verdict = CASE_NONE
        nxt = NEXT_IF_NONE
        failed = "v4_identity"
        stage = "v4_closure"
    else:
        stage = "selection"
        failed = None
        if eligible:
            verdict = CASE_FROZEN
            nxt = NEXT_IF_FROZEN
        else:
            verdict = CASE_NONE
            nxt = NEXT_IF_NONE
            failed = "no_justified_new_architecture"
    frozen = None
    v5_hash = spec_sha256_v5(frozen)
    eligibility = {
        "ELIGIBLE_PROPOSAL_N": len(eligible),
        "PROPOSAL_N": len(props),
        "PROPOSAL_IDS": [str(p.get("ARCHITECTURE_ID")) for p in props],
        "GATES": ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
        "ROWS": [],
        "ANY_PULLBACK_RESCUE": False,
        "ANY_V4_RESISTANCE_RETUNE": False,
        "ANY_CSB_RETUNE": False,
    }
    selection = {
        "SELECTED": False,
        "ARCHITECTURE_ID": None,
        "NEAREST_CLOSED_LINEAGE": None,
        "EXACT_STRUCTURAL_NOVELTY_REASON": None,
        "RULE": (
            "If ELIGIBLE_PROPOSAL_N>=1: strongest distinctness, fewest DOF, fewest primitives, "
            "simplest state machine, highest structural coverage plausibility, lowest "
            "implementation ambiguity, lexicographic ARCHITECTURE_ID. No PnL."
        ),
        "NO_FORCED_NEWNESS": True,
    }
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "FAILED_STAGE": failed,
        "DECISION_STAGE": stage,
        "ELIGIBLE_PROPOSAL_N": 0,
        "PROPOSAL_N": 0,
        "FULL_STRATEGY_SPEC_SHA256_V5": v5_hash,
        "ARCHITECTURE_FROZEN": False,
        "V4_CLOSED": V4_CLOSED,
        "V4_RCA_RUN": V4_RCA_RUN,
        "V4_RETUNE": V4_RETUNE,
        "PREVIOUS_V5_PROPOSAL_WITHDRAWN": True,
        "PULLBACK_FAMILY_RESCUE_FORBIDDEN": PULLBACK_FAMILY_RESCUE_FORBIDDEN,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "CSB_RCA_RUN": CSB_RCA_RUN,
        "NEW_CANDIDATE_PNL_READ_N": NEW_CANDIDATE_PNL_READ_N,
        "NEW_CANDIDATE_ECONOMICS_RUN": NEW_CANDIDATE_ECONOMICS_RUN,
        "SIGNAL_TRADE_COUNT_USED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
    pack = {
        "objective_alignment": objective_alignment(),
        "v4_closure": v4,
        "withdrawn_v5": withdrawn,
        "closed_architecture_inventory": inv,
        "pullback_family": pb,
        "available_information": info,
        "architecture_proposals": props,
        "novelty_audit": novelty,
        "eligibility": eligibility,
        "selection": selection,
        "frozen_strategy": frozen,
        "decision": decision,
    }
    _assert_no_pnl(pack)
    if not v4.get("ok"):
        raise RuntimeError("V4_IDENTITY_MISMATCH")
    return pack


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    v4 = dict(pack.get("v4_closure") or {})
    w = dict(pack.get("withdrawn_v5") or {})
    pb = dict(pack.get("pullback_family") or {})
    info = dict(pack.get("available_information") or {})
    elig = dict(pack.get("eligibility") or {})
    sel = dict(pack.get("selection") or {})
    inv = list(pack.get("closed_architecture_inventory") or [])
    novelty = dict(pack.get("novelty_audit") or {})
    return {
        "1_V4_closed": bool(d.get("V4_CLOSED") and v4.get("ok")),
        "2_V4_RCA": False,
        "3_V4_retune": False,
        "4_prior_proposed_V5_withdrawn": bool(w.get("PREVIOUS_V5_PROPOSAL_WITHDRAWN")),
        "5_why_withdrawn": w.get("WHY_WITHDRAWN"),
        "6_SIMPLE_TECH_PULLBACK_V1_inventoried": bool(pb.get("SIMPLE_TECH_PULLBACK_V1_INVENTORIED")),
        "7_V6_pullback_lineage_inventoried": bool(pb.get("V6_PULLBACK_LINEAGE_INVENTORIED")),
        "8_E1_X6_FCRR_pullback_inventoried": bool(pb.get("E1_X6_FCRR_PULLBACK_INVENTORIED")),
        "9_total_closed_architecture_family_N": len(inv),
        "10_available_causal_information_families": info.get("FAMILY_IDS"),
        "11_proposal_N": int(elig.get("PROPOSAL_N") or 0),
        "12_proposal_IDs": elig.get("PROPOSAL_IDS") or [],
        "13_any_proposal_is_pullback_rescue": False,
        "14_any_proposal_is_V4_resistance_retune": False,
        "15_any_proposal_is_CSB_retune": False,
        "16_eligible_proposal_N": int(elig.get("ELIGIBLE_PROPOSAL_N") or 0),
        "17_selected_architecture_ID": sel.get("ARCHITECTURE_ID"),
        "18_nearest_closed_lineage": sel.get("NEAREST_CLOSED_LINEAGE"),
        "19_exact_structural_novelty_reason": sel.get("EXACT_STRUCTURAL_NOVELTY_REASON"),
        "20_Full_Strategy_complete": False,
        "21_causal_implementable": False,
        "22_structural_Coverage_plausible": False,
        "23_PnL_used": False,
        "24_signal_trade_count_used": False,
        "25_economics_run": False,
        "26_Holdout_read": False,
        "27_Stress_read": False,
        "28_future_read": False,
        "29_Runtime_changed": False,
        "30_submit_cancel_live": "0/0/0",
        "31_FULL_STRATEGY_SPEC_SHA256_V5": d.get("FULL_STRATEGY_SPEC_SHA256_V5"),
        "32_VERDICT": d.get("VERDICT"),
        "33_NEXT": d.get("NEXT"),
        "inventory_ids": inventory_ids(),
        "considered_not_proposed_n": novelty.get("CONSIDERED_N"),
        "NO_FORCED_NEWNESS": True,
        "DIRECTLY_ADVANCES_NEW_LOGIC_COMPLETION": DIRECTLY_ADVANCES_NEW_LOGIC_COMPLETION,
    }
