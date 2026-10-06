"""Frozen thesis, stage roles, and the unbound EMA-structure exit family."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _sha(body: dict[str, Any]) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def thesis() -> dict[str, Any]:
    body = {
        "THESIS_ID": "SYMBOL_SETUP_THESIS_V1",
        "baseline_id": "SIMPLE_TECH_PULLBACK_V1",
        "clauses": [
            {
                "text": "A bullish short-versus-intermediate intraday trend is present.",
                "stage_id": "MA_TREND",
                "executable": "EMA9[t] > EMA21[t] and EMA21[t] > EMA21[t-3] on the completed 1-minute bar.",
                "role": "THESIS_BEARING",
            },
            {
                "text": "Price has pulled back into that trend without closing through the lower band.",
                "stage_id": "BB_LOCATION",
                "executable": "Over the last 3 completed bars, some low <= EMA9 and every close >= BB lower.",
                "role": "ENTRY_TIMING_ONLY",
            },
            {
                "text": "The short-term oversold state is recovering on this bar.",
                "stage_id": "RCI_REVERSAL",
                "executable": "RCI9[t-1] <= -80 and RCI9[t] > -80.",
                "role": "ENTRY_TIMING_ONLY",
            },
            {
                "text": "Participation on the trigger bar is elevated versus the recent bar volume.",
                "stage_id": "VOLUME_PARTICIPATION",
                "executable": "volume[t] > 0 and volume[t] >= 1.5 * median of the prior 5 completed volumes.",
                "role": "ENTRY_PARTICIPATION_ONLY",
            },
            {
                "text": "The completed bar resumes upward through the prior bar high while remaining at or below the upper band.",
                "stage_id": "PRICE_ACTION_TRIGGER",
                "executable": "close[t] > EMA9[t] and close[t] > high[t-1] and close[t] <= BB upper[t].",
                "role": "ENTRY_TIMING_ONLY",
            },
            {
                "text": "The board at bar finalize does not show an immediate execution headwind.",
                "stage_id": "BOARD_SUPPORT_VETO",
                "executable": "freshness <= 5 seconds, ask quantity <= 2 * bid quantity, both quantities >= 100.",
                "role": "EXECUTION_SUPPORT_ONLY",
            },
        ],
        "post_fill_invariants": ["MA_TREND was true at the signal bar. No other stage is required to remain true after fill."],
        "not_position_exits": [
            "RCI returning to or below -80",
            "volume returning to its recent median",
            "board quantity ratio changing after the fill",
            "price no longer touching EMA9",
            "close moving above the upper band",
        ],
    }
    body["THESIS_SHA256"] = _sha({k: v for k, v in body.items() if k != "THESIS_SHA256"})
    return body


def mechanism_family() -> dict[str, Any]:
    body = {
        "FAMILY_ID": "SYMBOL_SETUP_EXIT_MECHANISM_FAMILY_V1",
        "status": "MECHANISM_FAMILY_NOT_AN_EXECUTABLE_EXIT",
        "source": "V26 primitive definition plus V27 mechanism discovery. V28 K=6 is not a member.",
        "state_id": "A_EMA_STRUCTURE_LOSS",
        "state_rule": "On a completed bar after the fill, both EMA9 and EMA21 are finite and EMA9 <= EMA21.",
        "state_source": "research.simple_tech_redesign.v26_harvest.primitive_true",
        "why_this_state": "It is the exact negation of the V1 trend clause EMA9 > EMA21.",
        "persistence": "Consecutive completed bars in that state are the mechanism. The bar count K is unbound.",
        "k_not_selected": True,
        "timeframe_not_selected": True,
        "rejected_bindings": [
            "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS was a V27 discovery rank, not a frozen exit.",
            "V28 K=6 was derived from V27 path medians, was not frozen, applied only to added fills, and was later winner-harm.",
        ],
        "excluded": [
            "RCI rollover",
            "volume deterioration",
            "board ratio",
            "fixed holding time",
            "MFE capture",
            "profit target",
            "symbol-specific stop",
        ],
        "session_close": "Separate FAIL_CLOSE. Not thesis invalidation.",
        "candidates_run": False,
    }
    body["SYMBOL_SETUP_EXIT_MECHANISM_FAMILY_SHA256"] = _sha(
        {k: v for k, v in body.items() if k != "SYMBOL_SETUP_EXIT_MECHANISM_FAMILY_SHA256"}
    )
    return body
