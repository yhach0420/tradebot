"""Market-language decomposition. No Full Causal. No numeric freeze."""
from __future__ import annotations

from typing import Any

from research.post_open_execution_tax_decomposition_v1 import CASE_A, CASE_B, CASE_C


def interpret(*, decision: dict[str, Any], pops: dict[str, Any], sel: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    case = str(decision.get("CASE") or "")
    p0 = dict(pops.get("P0") or {})
    p1 = dict(pops.get("P1") or {})
    p2 = dict(pops.get("P2") or {})
    improve = pops.get("P2_minus_P1_mean")
    if case == "A":
        story = (
            "Immediate Ask crossing was destroying a weak post-open edge. "
            "On the same anchors, Corrected Passive Fill W5 (frozen Bid1 limit, 5s wait, no reprice) "
            "turns executable 10m Bid markout positive at the W5 fill price. "
            "CURRENT CAPTURE INFORMATION EXISTS, BUT IMMEDIATE-ASK EXECUTION DESTROYS EDGE."
        )
        entry = "Next run: W5-aligned ENTRY on the qualifying W5-filled state only. Thresholds not frozen here."
        exit_t = "ENTRY-aligned Technical EXIT from the same W5-filled state invalidation. Not the 10m label window."
    elif case == "B":
        story = (
            f"W5 saves about {improve:.1f} bps versus paying Ask1 on the same filled anchors "
            f"(P1 mean {p1.get('mean')}, P2 mean {p2.get('mean')}), but mean and median W5 markout "
            "do not become a stable positive state. EXECUTION TAX IS MATERIAL, BUT INFORMATION EDGE STILL INSUFFICIENT."
        )
        entry = None
        exit_t = None
    else:
        story = (
            f"W5 does not recover a post-open upside. P0 X1 mean {p0.get('mean')} bps, "
            f"P2 W5 mean {p2.get('mean')} bps, selection={sel.get('label')}. "
            "CURRENT CAPTURE POST-OPEN INFORMATION LIMIT IS CONFIRMED EVEN AFTER EXECUTION DECOMPOSITION."
        )
        entry = None
        exit_t = None
    return {
        "CASE": case,
        "MARKET_MECHANISM": story,
        "ENTRY_THESIS": entry,
        "TECHNICAL_EXIT": exit_t,
        "PRICE_IMPROVEMENT_mean": common.get("PRICE_IMPROVEMENT_mean"),
        "selection": sel.get("label"),
        "EXACT_CLOSED_ENTRY_REUSE": False,
        "COMPLETE_STRATEGY_PRECOMMITTED": False,
        "NONFILL_TREATED_AS_PNL": False,
        "VERDICT_IF_A": CASE_A,
        "VERDICT_IF_B": CASE_B,
        "VERDICT_IF_C": CASE_C,
    }
