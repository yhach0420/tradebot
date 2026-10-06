"""Numeric-boundary semantic log. Derived once. No grid. No future outcome."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v3 import (
    ATR_SANITY_FRAC,
    E1_BODY_N1M,
    E1_NET_N1M,
    E1_RANGE_N1M,
    FAIL_DRIVE_BODY_MIN,
    FAIL_DRIVE_DISP_MIN,
    FAIL_VISIBLE_RANGE_MIN,
    LAST_BODY_MIN,
    PROGRESS_HEAVY_OVERLAP,
    PROGRESS_TINY_EXTREME_FRAC,
    REPEATED_NO_EXPANSION_N,
    S0_RANGE_MIN,
    TRUE_BODY_FRAC_MIN,
    TRUE_DISP_MIN,
    TRUE_RANGE_MIN,
    UNWIND_FRAC,
    WIDE_REJECTION_BODY_MAX,
)


def numeric_boundaries() -> list[dict[str, Any]]:
    return [
        {
            "name": "S0_RANGE_MIN",
            "value": S0_RANGE_MIN,
            "semantic_concept": "opening 5m range vs own same-clock baseline is distinctive",
            "candidates_considered": [0.75],
            "examples_on_both_sides": "inherited leaked S0 correspondence",
            "why_selected": "Diagnostic counter only. V3 ACTIVE death is not N-bar expiry.",
            "future_outcome_used": False,
        },
        {
            "name": "ATR_SANITY_FRAC",
            "value": ATR_SANITY_FRAC,
            "semantic_concept": "ATR20 sanity scale; also FORM B first-bar range must exceed this vs ATR so ordinary wide dojis are not failed auctions",
            "candidates_considered": [0.40],
            "examples_on_both_sides": "7011/20250523 range 58 >= 0.40*110; 3382/20241115 range 24 < 0.40*63; 7182/20251112 range 10.5 < 0.40*31.7",
            "why_selected": "Reuse existing ATR sanity. Not a new search.",
            "future_outcome_used": False,
        },
        {
            "name": "TRUE_DISP_MIN / TRUE_RANGE_MIN / TRUE_BODY_FRAC_MIN",
            "value": {"disp": TRUE_DISP_MIN, "range": TRUE_RANGE_MIN, "body": TRUE_BODY_FRAC_MIN},
            "semantic_concept": "meaningful displacement, visible range vs own clock, directional bodies",
            "candidates_considered": ["inherited"],
            "why_selected": "Keep displacement/range/body correspondence. Two-sided now uses path state, not uncommitted loc.",
            "future_outcome_used": False,
        },
        {
            "name": "LAST_BODY_MIN",
            "value": LAST_BODY_MIN,
            "semantic_concept": "continued directional intent: last opening 5m is not a crawl/doji; also weak-body progress class",
            "candidates_considered": [0.25],
            "why_selected": "Not retuned. One-bar 0.55/0.20 pair removed.",
            "future_outcome_used": False,
        },
        {
            "name": "ONE_BAR_SHARE_MAX / WEAK_BAR_BODY_MAX",
            "value": None,
            "semantic_concept": "ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH inside continued-intent state",
            "candidates_considered": [{"share": 0.55, "weak": 0.20, "status": "removed_as_standalone_hard_rule"}],
            "examples_on_both_sides": "without followthrough: 7011/20241205-like first-bar share; with followthrough human TRUE 6787/20250214, 9101/20250807, 6501/20250612 must not auto-reject",
            "why_selected": "Single-exemplar pair must not survive unchanged. Majority one-bar is invalid only when followthrough does not continue the auction.",
            "future_outcome_used": False,
            "retained_unchanged": False,
        },
        {
            "name": "FORM_B_UNRESOLVED_INTERIOR",
            "value": {"close_loc_min": 0.35, "close_loc_max": 0.65},
            "semantic_concept": "wide-rejection failed attempt requires an unresolved first-bar close, not a completed hammer/star",
            "candidates_considered": ["reuse existing uncommitted loc band 0.35-0.65"],
            "examples_on_both_sides": "7011/20250523 loc~0.38 and 4063 loc~0.59 pass; 3382/20241115 loc~0.94 and 6273 loc~0.86 fail as already-resolved hammers",
            "why_selected": "Existing two-sided uncommitted band. Distinguishes VISIBLE FAILED AUCTION from AMBIGUOUS WIDE RANGE. Not a new grid.",
            "future_outcome_used": False,
        },
        {
            "name": "FAIL_VISIBLE_RANGE_MIN / WIDE_REJECTION_BODY_MAX",
            "value": {"range": FAIL_VISIBLE_RANGE_MIN, "wide_body": WIDE_REJECTION_BODY_MAX},
            "semantic_concept": "FORM B small-body first bar with visible range vs own clock, then later opposite sequence. Not wide-doji-alone.",
            "candidates_considered": ["wide_rejection flag alone (forbidden)"],
            "examples_on_both_sides": "FORM B 7011/20250523 and 4063/20251118; FORM A 3382/20241004; negatives 3382/20241115 6273 5802 7182",
            "why_selected": "Causal path. Later two-bar opposite sequence required. ATR sanity plus unresolved interior.",
            "future_outcome_used": False,
        },
        {
            "name": "FAIL_DRIVE reclaim + DISP/BODY",
            "value": {"disp": FAIL_DRIVE_DISP_MIN, "body": FAIL_DRIVE_BODY_MIN},
            "semantic_concept": "opposite completed-5m directional auction through the failed-bar close, no N-bar lifetime",
            "candidates_considered": ["fixed N-bar expiry (forbidden)"],
            "why_selected": "State, not clock. Reclaim is failed-bar close for both forms.",
            "future_outcome_used": False,
        },
        {
            "name": "PROGRESS_TINY_EXTREME_FRAC / PROGRESS_HEAVY_OVERLAP",
            "value": {"tiny": PROGRESS_TINY_EXTREME_FRAC, "overlap": PROGRESS_HEAVY_OVERLAP},
            "semantic_concept": "ACTIVE stall resets only on REAL_BREAKOUT_EXTENSION or MEANINGFUL_DIRECTIONAL_EXTENSION",
            "candidates_considered": ["RCA qualitative classes; not a grid"],
            "examples_on_both_sides": "6963/20241002 09:19/09:24/09:34 leftover delta 1.0/1.0/0.5 vs bar range 8/8/6.5 are MARGINAL or RANGE_DRIFT; a genuine range expansion with directional close is REAL_BREAKOUT",
            "why_selected": "Copied once from RCA qualitative correspondence. Any new wick extreme is not progress.",
            "future_outcome_used": False,
        },
        {
            "name": "UNWIND_FRAC / REPEATED_NO_EXPANSION_N",
            "value": {"unwind": UNWIND_FRAC, "no_expansion": REPEATED_NO_EXPANSION_N},
            "semantic_concept": "ACTIVE dies when displacement is unwound or repeated failed progress / stale range",
            "candidates_considered": ["09:30/09:45 clock (forbidden)"],
            "why_selected": "Repeated failed progress is a state. Not retuned.",
            "future_outcome_used": False,
        },
        {
            "name": "E1 N1M families",
            "value": {"net": E1_NET_N1M, "range": E1_RANGE_N1M, "body": E1_BODY_N1M},
            "semantic_concept": "1m state-change vs prior 1m noise at a pre-identified 5m level",
            "candidates_considered": ["inherited"],
            "why_selected": "Not optimized.",
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
        "iterative_accuracy_optimization": False,
        "numeric_boundaries": numeric_boundaries(),
    }
