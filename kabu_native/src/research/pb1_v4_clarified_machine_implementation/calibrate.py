"""Numeric-boundary semantic log. No future outcome. No grid/ML."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_implementation import (
    ATR_SANITY_FRAC,
    E1_BODY_N1M,
    E1_NET_N1M,
    E1_RANGE_N1M,
    FAIL_DRIVE_BODY_MIN,
    FAIL_DRIVE_DISP_MIN,
    FAIL_VISIBLE_RANGE_MIN,
    LAST_BODY_MIN,
    ONE_BAR_SHARE_MAX,
    REPEATED_NO_EXPANSION_N,
    S0_RANGE_MIN,
    TRUE_BODY_FRAC_MIN,
    TRUE_DISP_MIN,
    TRUE_RANGE_MIN,
    UNWIND_FRAC,
    WEAK_BAR_BODY_MAX,
    WIDE_REJECTION_BODY_MAX,
)


def numeric_boundaries() -> list[dict[str, Any]]:
    return [
        {
            "name": "S0_RANGE_MIN",
            "value": S0_RANGE_MIN,
            "semantic_concept": "opening 5m range vs own same-clock baseline is distinctive",
            "candidates_considered": [0.75],
            "why_selected": "Inherited correspondence from leaked S0. Same-clock ratio replaces first-bar-only normalizer. Not retuned.",
            "future_outcome_used": False,
        },
        {
            "name": "ATR_SANITY_FRAC",
            "value": ATR_SANITY_FRAC,
            "semantic_concept": "ATR20 sanity scale so a tiny same-clock median cannot mint a drive from ordinary movement",
            "candidates_considered": [0.25, 0.40, 0.50],
            "why_selected": (
                "0.25 still lets 9501/20251113 ordinary net/ATR~0.30 look like 0.88 vs a tiny first-bar median. "
                "0.40 makes scale = max(same-clock median, 0.40*ATR20). Human TRUE median net/ATR ~0.62. "
                "No returns used."
            ),
            "future_outcome_used": False,
        },
        {
            "name": "TRUE_DISP_MIN / TRUE_RANGE_MIN / TRUE_BODY_FRAC_MIN",
            "value": {"disp": TRUE_DISP_MIN, "range": TRUE_RANGE_MIN, "body": TRUE_BODY_FRAC_MIN},
            "semantic_concept": "meaningful displacement, visible range vs own clock, directional bodies",
            "candidates_considered": ["inherited leaked TRUE numeric as correspondence, not a new search"],
            "why_selected": "Keep displacement/range/body correspondence. Add continued-intent STATE on top.",
            "future_outcome_used": False,
        },
        {
            "name": "LAST_BODY_MIN",
            "value": LAST_BODY_MIN,
            "semantic_concept": "continued directional intent: last opening 5m is not a crawl/doji",
            "candidates_considered": [0.15, 0.25, 0.35],
            "why_selected": "6920 last body 0.06 and 9501 last body 0.17 are crawls. Human TRUE last-body median ~0.52. 0.25 is the smallest interpretable crawl cutoff.",
            "future_outcome_used": False,
        },
        {
            "name": "ONE_BAR_SHARE_MAX + WEAK_BAR_BODY_MAX",
            "value": {"share": ONE_BAR_SHARE_MAX, "weak_body": WEAK_BAR_BODY_MAX},
            "semantic_concept": "one-bar domination: large first print plus weak follow-through is not a drive",
            "candidates_considered": [{"share": 0.55, "weak": 0.20}],
            "why_selected": "7011/20241205 first-bar share 0.63 with bar2 body 0.18. Encode the missing continued-intent state, no MICRO hardcoded symbol rule.",
            "future_outcome_used": False,
        },
        {
            "name": "FAIL_VISIBLE_RANGE_MIN / WIDE_REJECTION_BODY_MAX",
            "value": {"range": FAIL_VISIBLE_RANGE_MIN, "wide_body": WIDE_REJECTION_BODY_MAX},
            "semantic_concept": "FAILED_OPEN seed from directional failed attempt OR wide doji/range rejection",
            "candidates_considered": ["body>=0.40 only (rejected)", "wide body<=0.25 with range>=0.80 (kept)"],
            "why_selected": "7011/20250523 first body 0.017 range/clock 1.89 must be a failed seed. 4063 first body 0.03 range 1.15 same family.",
            "future_outcome_used": False,
        },
        {
            "name": "FAIL_DRIVE reclaim + DISP/BODY",
            "value": {"disp": FAIL_DRIVE_DISP_MIN, "body": FAIL_DRIVE_BODY_MIN},
            "semantic_concept": "opposite completed-5m directional auction through the failed bar, no N-bar lifetime",
            "candidates_considered": ["fixed 6-bar failed-open lifetime (forbidden)", "reclaim failed-bar extreme + 2 same-dir bars"],
            "why_selected": "State, not clock. 6758 bounce that never reclaims the failed bar expires by stale/failed progress.",
            "future_outcome_used": False,
        },
        {
            "name": "UNWIND_FRAC / REPEATED_NO_EXPANSION_N",
            "value": {"unwind": UNWIND_FRAC, "no_expansion": REPEATED_NO_EXPANSION_N},
            "semantic_concept": "ACTIVE dies when displacement is unwound, two-sided re-forms, or repeated failed progress / stale range",
            "candidates_considered": ["session-clock semantic expiry (forbidden)", "N-bar failed-open lifetime (forbidden)", "state of 3 completed 5m without new extreme"],
            "why_selected": "Repeated failed progress is a state of the auction, not a session clock.",
            "future_outcome_used": False,
        },
        {
            "name": "E1 N1M families",
            "value": {"net": E1_NET_N1M, "range": E1_RANGE_N1M, "body": E1_BODY_N1M},
            "semantic_concept": "1m state-change vs prior 1m noise at a pre-identified 5m level. Micro-cross alone insufficient. TV not a gate.",
            "candidates_considered": ["inherited leaked E1 correspondence"],
            "why_selected": "Do not optimize numeric thresholds now. Not RCA medians as a profit search.",
            "future_outcome_used": False,
        },
        {
            "name": "FIVE_M_CONTINUATION",
            "value": "location defended + counter wick weakened or renewed progress; loc>=0.50",
            "semantic_concept": "5m continuation after the level is known",
            "candidates_considered": ["0.35×opening5m and 1.5×N1M as required gates (rejected as semantic requirement)"],
            "why_selected": "Clarified spec: those numbers are not frozen semantics.",
            "future_outcome_used": False,
        },
    ]


def calibrate_from_descriptors() -> dict[str, Any]:
    return {
        "label": "SEMANTIC_CORRESPONDENCE_ONLY",
        "grid_search": False,
        "decision_tree": False,
        "random_forest": False,
        "boosting": False,
        "ml_classifier": False,
        "symbol_specific_rules": False,
        "future_outcome_used": False,
        "pnl_used": False,
        "mfe_mae_used": False,
        "numeric_boundaries": numeric_boundaries(),
    }
