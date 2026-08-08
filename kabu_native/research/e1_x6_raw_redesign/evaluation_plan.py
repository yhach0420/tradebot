"""Phase B evaluation plan (DEFINITION ONLY — not executed in Phase A).

Stored into P1 so the gates exist before any economics are generated.
"""
from __future__ import annotations

PHASE_B_EVALUATION_PLAN = {
    "execution": {
        "buy": "ask", "sell": "bid",
        "cost_bps_once": 5.0,
        "lot": 100,
        "cap": 5,
        "day_weighting": "equal per day",
    },
    "gates": [
        "total PnL over all 9 days > 0",
        "median daily PnL > 0 (no-trade days count as 0 and are included)",
        "PnL after removing best 1 day > 0",
        "PnL after removing best 2 days > 0",
        "best day contribution <= 30% of gross positive day PnL",
        "top 2 days contribution <= 50% of gross positive day PnL",
        "PnL after removing top 1 trade > 0",
        "PnL after removing top 1 symbol > 0",
        "PF >= 1.10",
        "completed trades >= 30",
        "days with trades >= 6 of 9",
        "max single-day trade-count share <= 30%",
        "top-2-day trade-count share <= 50%",
        "max DD and STOP loss not worse than E1_X5 base",
        "Rolling-origin (confirm total>0, confirm median>0, ex-best-confirm-day>0)",
        "RefitLODO (held-out total>0, median>0, ex-best-1/2 held-out days>0)",
        "A/B determinism exact match",
        "INVALID_SOURCE = 0",
    ],
    "explicitly_not_required": ["per-day completed trades >= 3"],
    "best_day_rule": "day pnl desc, tie-break day asc; mechanical, never date-fitted",
    "ranking_priority": [
        "1. PnL after removing best 2 days",
        "2. median daily PnL",
        "3. lower-quartile (25%) daily PnL",
        "4. profit concentration of top 1/2 days (lower better)",
        "5. max DD (shallower better)",
        "6. PF",
        "7. simplicity (fewer parameters)",
        "8. full-period PnL",
    ],
    "not_generated_in_phase_a": [
        "future MFE/MAE", "daily PnL", "PF", "per-trade PnL", "any economics",
    ],
}

# ---- Phase A-R1 (§10): conventions made unique BEFORE any economics ----

ROLLING_ORIGIN_5FOLD = {
    "F1": {"build": ["20260721", "20260722", "20260723", "20260724"], "confirm": "20260727"},
    "F2": {"build": ["20260721", "20260722", "20260723", "20260724", "20260727"], "confirm": "20260728"},
    "F3": {"build": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728"], "confirm": "20260729"},
    "F4": {"build": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728", "20260729"], "confirm": "20260730"},
    "F5": {"build": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728", "20260729", "20260730"], "confirm": "20260731"},
    "rule": (
        "within each fold candidates are ranked on BUILD days only; the fold is "
        "never re-selected using its confirm-day result; ties break by Strategy "
        "ID ascending"
    ),
}

LODO_MODES = {
    "FIXED_SPEC_DAY_DELETION": (
        "the SAME fixed candidate is re-aggregated with each day deleted once "
        "(9 deletions); no re-selection, no threshold change"
    ),
    "RESELECT_LODO_STABILITY": (
        "candidate selected on the 8 non-held-out days only (frozen ranking "
        "rule, tie: Strategy ID asc), applied EXACTLY ONCE to the held-out day"
    ),
    "terminology": (
        "current candidates have no learned parameters; the word REFIT is not "
        "used without basis; fixed thresholds are never changed on held-out results"
    ),
}

SENS_722 = {
    "required_saved_and_gated": [
        "PnL excluding 20260722 > 0",
        "PF excluding 20260722 > 1.00",
        "20260722 contribution share of gross positive day PnL",
        "median daily PnL excluding 20260722",
        "direction agreement between ex-722 result and full-9-day result",
    ],
}

CAP5_CONVENTION = {
    "cap": 5,
    "scope": "each candidate runs an independent CAP5 portfolio",
    "same_grid_tie_break": [
        "1. trigger timestamp ascending",
        "2. decision grid index ascending",
        "3. symbol ascending",
    ],
    "cap_blocked_ledger": "every CAP-blocked OPEN is stored in the ledger (full rows)",
}


def cap5_tie_break_key(row: dict) -> tuple:
    """Frozen ordering for simultaneous OPENs competing for CAP slots."""
    return (float(row["trigger_ts"]), int(row["decision_grid"]), str(row["symbol"]))


def sens_722_summary(day_pnls: dict[str, float]) -> dict:
    """Mechanical 7/22 sensitivity block (pure aggregation, no selection)."""
    import statistics

    ex = {d: p for d, p in day_pnls.items() if d != "20260722"}
    ex_vals = list(ex.values())
    full_total = sum(day_pnls.values())
    ex_total = sum(ex_vals)
    gross_pos = sum(p for p in day_pnls.values() if p > 0)
    p722 = day_pnls.get("20260722", 0.0)
    return {
        "ex722_total_pnl": ex_total,
        "ex722_median_day_pnl": statistics.median(ex_vals) if ex_vals else 0.0,
        "contribution_722_share_of_gross_positive": (
            (p722 / gross_pos) if gross_pos > 0 and p722 > 0 else 0.0
        ),
        "direction_agreement_with_full": (ex_total > 0) == (full_total > 0),
    }
