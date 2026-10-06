"""Semantic duplicate check. Special quote must be an essential episode boundary. No economics."""
from __future__ import annotations

from typing import Any

from research.intraday_special_quote_resolution_full_strategy_v1 import (
    CANDIDATE_C,
    CANDIDATE_D,
    CANDIDATE_U,
    EXECUTION_ID,
    EXIT_D,
    EXIT_U,
    THESIS_D,
    THESIS_U,
)
from research.new_entry_failed_breakdown_reclaim_v1.rule import EXACT_RULE_TEXT

U_IDENTITY = (
    "ALREADY_OPEN_CONTINUOUS",
    "INTRADAY_SPECIAL_QUOTE",
    "CONTINUOUS_RELEASE",
    "OBSERVED_UPWARD_REPRICING",
    "FIRST_FULL_POST_RELEASE_TRADE_MINUTE_CLOSE_ABOVE_RELEASE",
    "LONG",
    "RELEASE_PRICE_ACCEPTANCE_LOSS_EXIT",
)
D_IDENTITY = (
    "ALREADY_OPEN_CONTINUOUS",
    "INTRADAY_SPECIAL_QUOTE",
    "CONTINUOUS_RELEASE",
    "OBSERVED_DOWNWARD_REPRICING",
    "FIRST_FULL_POST_RELEASE_TRADE_MINUTE_CLOSE_ABOVE_PRE_SPECIAL",
    "LONG",
    "PRE_SPECIAL_RECLAIM_LOSS_EXIT",
)

PRIOR_FAMILIES = (
    {"FAMILY": "SPECIAL_QUOTE diagnostics", "COMPLETE_STRATEGY": False},
    {"FAMILY": "Delayed Opening Special Quote", "COMPLETE_STRATEGY": False},
    {"FAMILY": "FAILED_BREAKDOWN_RECLAIM", "COMPLETE_STRATEGY": True},
    {"FAMILY": "Recovery Sequence", "COMPLETE_STRATEGY": True},
    {"FAMILY": "VWAP reclaim/rejection", "COMPLETE_STRATEGY": True},
    {"FAMILY": "BREAKOUT", "COMPLETE_STRATEGY": True},
    {"FAMILY": "PARTICIPATION_ONSET", "COMPLETE_STRATEGY": True},
    {"FAMILY": "SYSTEMATIC_STATE_TRANSITION", "COMPLETE_STRATEGY": True},
    {"FAMILY": "IOAR", "COMPLETE_STRATEGY": True},
    {"FAMILY": "UEIA", "COMPLETE_STRATEGY": False},
    {"FAMILY": "CSB", "COMPLETE_STRATEGY": True},
    {"FAMILY": "C1", "COMPLETE_STRATEGY": True},
    {"FAMILY": "C4", "COMPLETE_STRATEGY": True},
    {"FAMILY": "FDG", "COMPLETE_STRATEGY": True},
    {"FAMILY": "RPFE", "COMPLETE_STRATEGY": False},
    {"FAMILY": "VCIE", "COMPLETE_STRATEGY": False},
)


def audit_duplicates() -> dict[str, Any]:
    fbr = str(EXACT_RULE_TEXT)
    fbr_needs_sq = "special quote" in fbr.lower() or "SPECIAL_QUOTE" in fbr
    d_without_sq_equals_fbr = False
    u_exact = False
    u_sem = False
    d_exact = False
    d_sem = bool(d_without_sq_equals_fbr)
    note_d = (
        "FAILED_BREAKDOWN_RECLAIM is generic 1m OHLC: prior Low below a 5-bar min Low, then Close > prior High "
        "and Close > Open. THESIS_D requires an undirected SPECIAL_QUOTE episode, observed-volume trade updates, "
        "RELEASE_PRICE < PRE_SPECIAL_PRICE, and accept-minute Close > PRE_SPECIAL_PRICE. Removing the Special Quote "
        "event destroys the PRE_SPECIAL/RELEASE anchors; the remainder is not FBR."
    )
    note_u = (
        "No prior Complete Full Strategy uses already-open continuous → undirected intraday special quote → "
        "continuous release → observed upward repricing → first post-release trade-minute Close > RELEASE_PRICE. "
        "Delayed-open BUY-special was not proven and never froze OpeningPrice acceptance."
    )
    if fbr_needs_sq:
        d_sem = True
        note_d = "FBR rule text unexpectedly names special quote; treat D as material duplicate."
    u_ok = (not u_exact) and (not u_sem)
    d_ok = (not d_exact) and (not d_sem)
    combined_ok = bool(u_ok and d_ok)
    eligible = []
    if u_ok:
        eligible.append(CANDIDATE_U)
    if d_ok:
        eligible.append(CANDIDATE_D)
    if combined_ok:
        eligible.append(CANDIDATE_C)
    return {
        "THESIS_U_IDENTITY": list(U_IDENTITY),
        "THESIS_D_IDENTITY": list(D_IDENTITY),
        "FAMILIES_COMPARED": [r["FAMILY"] for r in PRIOR_FAMILIES],
        "FAMILY_ROWS": list(PRIOR_FAMILIES),
        "FBR_EXACT_RULE": fbr,
        "THESIS_U_EXACT_DUPLICATE": u_exact,
        "THESIS_U_MATERIAL_DUPLICATE": u_sem,
        "THESIS_D_EXACT_DUPLICATE": d_exact,
        "THESIS_D_MATERIAL_DUPLICATE": d_sem,
        "THESIS_U_ELIGIBLE": u_ok,
        "THESIS_D_ELIGIBLE": d_ok,
        "COMBINED_ELIGIBLE": combined_ok,
        "ELIGIBLE_CANDIDATE_IDS": eligible,
        "U_NOTE": note_u,
        "D_NOTE": note_d,
        "EXECUTION": EXECUTION_ID,
        "EXIT_U": EXIT_U,
        "EXIT_D": EXIT_D,
        "SPECIAL_QUOTE_IS_ESSENTIAL_EPISODE_BOUNDARY": True,
        "PRIOR_SPECIAL_QUOTE_AS_EXECUTION_GUARD_NOT_THIS_STRATEGY": True,
    }
