"""Absolute ENTRY-edge gates. SESSION_CLOSE PnL is not a reject. No new search."""
from __future__ import annotations

from typing import Any, Optional

from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import EPS
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_spec import (
    CONCENTRATION_MAX_SHARE,
    ELIGIBLE_ENTRY_IDS,
    EXECUTION_EVALUABLE_MIN,
)


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > EPS


def _ge0(v: Any) -> bool:
    return v is not None and float(v) + EPS >= 0.0


def _share_le50(v: Any) -> bool:
    if v is None:
        return False
    return abs(float(v)) <= float(CONCENTRATION_MAX_SHARE) + 1e-15


def gates_for(m: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    exe = int(m.get("EXECUTION_EVALUABLE_N") or 0)
    pos180 = int(m.get("POSITIVE_DAY_N_180") or 0)
    neg180 = int(m.get("NEGATIVE_DAY_N_180") or 0)
    pos300 = int(m.get("POSITIVE_DAY_N_300") or 0)
    neg300 = int(m.get("NEGATIVE_DAY_N_300") or 0)
    sym_contrib = m.get("TOP_SYMBOL_CONTRIBUTION")
    if sym_contrib is None:
        sym_contrib = m.get("TOP_SYMBOL_SHARE")
    g = {
        "1_integrity": bool(integrity_ok),
        "2_EXECUTION_EVALUABLE_N_ge_52": exe >= int(EXECUTION_EVALUABLE_MIN),
        "3_MEAN_180_gt_0": _gt0(m.get("MEAN_180")),
        "4_MEAN_300_gt_0": _gt0(m.get("MEAN_300")),
        "5_positive_day_n_180_gt_negative": pos180 > neg180,
        "6_positive_day_n_300_ge_negative": pos300 >= neg300,
        "7_EX_BEST_DAY_MEAN_180_gt_0": _gt0(m.get("EX_BEST_DAY_MEAN_180")),
        "8_EX_BEST_DAY_MEAN_300_gt_0": _gt0(m.get("EX_BEST_DAY_MEAN_300")),
        "9_DROP_TOP_SYMBOL_MEAN_180_ge_0": _ge0(m.get("DROP_TOP_SYMBOL_MEAN_180")),
        "10_DROP_TOP_SYMBOL_MEAN_300_ge_0": _ge0(m.get("DROP_TOP_SYMBOL_MEAN_300")),
        "11_top_day_contribution_le_50pct": _share_le50(m.get("TOP_DAY_SHARE")),
        "12_top_symbol_contribution_le_50pct": _share_le50(sym_contrib),
    }
    qualified = all(g.values())
    return {"gates": g, "qualified": bool(qualified), "PNL_USED_FOR_SELECTION": False, "MEDIAN_USED_AS_HARD_GATE": False}


def score_all(metrics: dict[str, dict[str, Any]], *, integrity_ok: bool) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for eid in ELIGIBLE_ENTRY_IDS:
        m = dict(metrics.get(eid) or {})
        pack = gates_for(m, integrity_ok=integrity_ok)
        scored.append({"ENTRY_ID": eid, "metrics": m, **pack})
    return scored


def select_one(scored: list[dict[str, Any]]) -> dict[str, Any]:
    quals = [r for r in scored if bool(r.get("qualified"))]
    if not quals:
        return {"n": 0, "selected": None, "rule": "none", "PNL_USED_FOR_SELECTION": False}
    order = {eid: i for i, eid in enumerate(ELIGIBLE_ENTRY_IDS)}

    def key(r: dict[str, Any]) -> tuple:
        m = dict(r.get("metrics") or {})
        return (
            -int(m.get("EXECUTION_EVALUABLE_N") or 0),
            -int(m.get("POSITIVE_DAY_N_180") or 0),
            -int(m.get("POSITIVE_DAY_N_300") or 0),
            int(order.get(str(r.get("ENTRY_ID") or ""), 999)),
        )

    ordered = sorted(quals, key=key)
    sel = ordered[0]
    return {
        "n": len(quals),
        "selected": sel.get("ENTRY_ID"),
        "SELECTED_ENTRY_ID": sel.get("ENTRY_ID"),
        "rule": "1 max EXECUTION_EVALUABLE_N; 2 max positive_day_n_180; 3 max positive_day_n_300; 4 older precommitted rule",
        "qualified_ids": [str(r.get("ENTRY_ID")) for r in ordered],
        "PNL_USED_FOR_SELECTION": False,
    }


def decide(
    *,
    integrity_ok: bool,
    qualified_n: int,
    burned: Optional[dict[str, Any]],
    e4_ok: Optional[bool],
    session_close_pnl: Optional[float],
    full_causal_ran: bool = False,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_ENTRY_REBASE_INTEGRITY_FAILED",
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "CURRENT_T3_STACK_CLOSED": False,
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": False,
            "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": False,
            "ENTRY_EXECUTION_REBIND_REQUIRED": False,
            "PHASE_B_BURNED_RAN": False,
            "FULL_CAUSAL_DIAGNOSTIC_RAN": False,
            "NEXT": "FAIL_CLOSED. Source / timestamp / identity did not restore.",
        }
    if int(qualified_n) <= 0:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_ENTRY_FAMILY_ABSOLUTE_EDGE_EXHAUSTED",
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "CANDIDATE_FROZEN": False,
            "CURRENT_T3_STACK_CLOSED": True,
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": True,
            "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": False,
            "ENTRY_EXECUTION_REBIND_REQUIRED": False,
            "PHASE_B_BURNED_RAN": False,
            "FULL_CAUSAL_DIAGNOSTIC_RAN": False,
            "NEXT": (
                "DEV qualifier=0. Do not add T3 filter, RCI retune, Board revival, volume retune, or Dynamic. "
                "STOP. Strategy-level ENTRY family has no absolute 180/300 edge on existing causal rules."
            ),
        }
    b = dict(burned or {})
    if not b.get("evaluated"):
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_ENTRY_REBASE_INTEGRITY_FAILED",
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "CURRENT_T3_STACK_CLOSED": False,
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": False,
            "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": False,
            "ENTRY_EXECUTION_REBIND_REQUIRED": False,
            "PHASE_B_BURNED_RAN": False,
            "FULL_CAUSAL_DIAGNOSTIC_RAN": False,
            "NEXT": "DEV qualifier existed but Burned join-only metrics were missing. Recapture/restream forbidden.",
        }
    m180 = b.get("MEAN_180")
    m300 = b.get("MEAN_300")
    burned_ok = m180 is not None and m300 is not None and float(m180) >= -EPS and float(m300) >= -EPS
    if not burned_ok:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_ENTRY_REBASE_BURNED_FAILED",
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "CURRENT_T3_STACK_CLOSED": False,
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": False,
            "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": False,
            "ENTRY_EXECUTION_REBIND_REQUIRED": False,
            "PHASE_B_BURNED_RAN": True,
            "FULL_CAUSAL_DIAGNOSTIC_RAN": False,
            "NEXT": "DEV edge existed but Burned direction failed. Do not hop to the next candidate this run. STOP.",
        }
    if e4_ok is False:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_ENTRY_REBASE_EXECUTION_REBIND_REQUIRED",
            "ENTRY_BASE_CANDIDATE_FROZEN": True,
            "CURRENT_T3_STACK_CLOSED": False,
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": False,
            "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": False,
            "ENTRY_EXECUTION_REBIND_REQUIRED": True,
            "PHASE_B_BURNED_RAN": True,
            "FULL_CAUSAL_DIAGNOSTIC_RAN": False,
            "NEXT": "ENTRY base freeze as research component. Design Execution binding next run. Do not invent Execution now.",
        }
    if e4_ok is not True:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_ENTRY_REBASE_INTEGRITY_FAILED",
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "CURRENT_T3_STACK_CLOSED": False,
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": False,
            "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": False,
            "ENTRY_EXECUTION_REBIND_REQUIRED": False,
            "PHASE_B_BURNED_RAN": True,
            "FULL_CAUSAL_DIAGNOSTIC_RAN": False,
            "NEXT": "E4 binding compatibility was not established. Execution was not invented this run.",
        }
    nxt = "NEW_ENTRY_POPULATION_EXIT_REDESIGN on the frozen ENTRY base. SESSION_CLOSE negative does not reject ENTRY."
    if session_close_pnl is not None and float(session_close_pnl) < -EPS:
        nxt = "ENTRY_EDGE_SUPPORTED_EXIT_REQUIRED. " + nxt
    return {
        "CASE": "A",
        "VERDICT": "SIMPLE_TECH_ENTRY_REBASE_EDGE_SUPPORTED",
        "ENTRY_BASE_CANDIDATE_FROZEN": True,
        "CURRENT_T3_STACK_CLOSED": False,
        "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": False,
        "NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED": True,
        "ENTRY_EXECUTION_REBIND_REQUIRED": False,
        "PHASE_B_BURNED_RAN": True,
        "FULL_CAUSAL_DIAGNOSTIC_RAN": bool(full_causal_ran),
        "NEXT": nxt,
    }
