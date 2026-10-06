"""Loss waterfall with single primary attribution. Does not rescore V1."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.pb1_v4_complete_strategy_economic_failure_decomposition import DEATH_LATE_MIN, EXPAND_BPS

PRIMARY_ORDER = (
    "ENTRY_WRONG_FROM_START",
    "INITIAL_EDGE_BUT_GIVEBACK",
    "EXIT_DELAY_AFTER_STRUCTURE_FAILURE",
    "SESSION_FLAT_OCCUPANCY",
    "EXECUTION_TAX",
    "POSITION_SIZE_CONCENTRATION",
    "OTHER_LOSS",
)


def primary_attr(row: dict[str, Any]) -> str | None:
    net = float(row.get("net_pnl_yen") or 0.0)
    if net >= 0:
        return None
    cls = str(row.get("entry_class") or "")
    reason = str(row.get("THESIS_LOST_REASON") or "")
    mfe = float(row.get("MFE_bps") or 0.0)
    delay = row.get("minutes_MFE_to_THESIS_LOST")
    if cls == "A_IMMEDIATE_WRONG_DIRECTION":
        return "ENTRY_WRONG_FROM_START"
    if cls == "C_FAVORABLE_THEN_FULL_GIVEBACK":
        return "INITIAL_EDGE_BUT_GIVEBACK"
    if (
        reason in {"ACCEPTED_STRUCTURAL_FAILURE", "REPEATED_OR_RECROSS"}
        and mfe >= float(EXPAND_BPS)
        and delay is not None
        and int(delay) >= int(DEATH_LATE_MIN)
    ):
        return "EXIT_DELAY_AFTER_STRUCTURE_FAILURE"
    if row.get("ops_flatten"):
        return "SESSION_FLAT_OCCUPANCY"
    if float(row.get("gross_pnl_yen") or 0.0) > 0:
        return "EXECUTION_TAX"
    if float(row.get("entry_px") or 0.0) >= 10000.0:
        return "POSITION_SIZE_CONCENTRATION"
    if cls == "B_SMALL_EDGE_NEVER_EXPANDED":
        return "ENTRY_WRONG_FROM_START"
    return "OTHER_LOSS"


def waterfall(rows: list[dict[str, Any]], *, blocked: list[dict[str, Any]]) -> dict[str, Any]:
    total_net = float(sum(float(r.get("net_pnl_yen") or 0.0) for r in rows))
    loss_net = float(sum(float(r.get("net_pnl_yen") or 0.0) for r in rows if float(r.get("net_pnl_yen") or 0.0) < 0))
    bags: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        attr = primary_attr(r)
        if attr:
            bags[attr].append(r)
    lines = []
    for name in PRIMARY_ORDER:
        bag = bags.get(name) or []
        g = float(sum(min(0.0, float(r.get("gross_pnl_yen") or 0.0)) for r in bag))
        n = float(sum(float(r.get("net_pnl_yen") or 0.0) for r in bag))
        lines.append(
            {
                "category": name,
                "trade_n": len(bag),
                "gross_loss_yen": g,
                "net_loss_yen": n,
                "share_of_total_loss": (n / loss_net if loss_net < 0 else None),
            }
        )
    blocked_net = float(sum(float(r.get("net_pnl_yen") or 0.0) for r in blocked))
    return {
        "v1_total_net_pnl_yen": total_net,
        "v1_sum_negative_net_yen": loss_net,
        "primary": lines,
        "double_counted": False,
        "PORTFOLIO_BLOCKING": {
            "COUNTERFACTUAL_NOT_STRATEGY_RESULT": True,
            "blocked_n": len(blocked),
            "counterfactual_net_pnl_yen": blocked_net,
            "note": "not added to V1 net",
        },
    }
