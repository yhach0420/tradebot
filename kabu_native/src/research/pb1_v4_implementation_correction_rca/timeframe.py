"""Timeframe interpretation A/B/C. Semantic comparison only. No returns."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_opening_drive_location_reaccel_spec.specification import INVARIANTS, ROLE_MAP, five_m_continuation_def, location_def, one_m_role_def


def compare(*, loc3382: dict[str, Any], s2_s3_note: str) -> dict[str, Any]:
    loc = location_def()
    s4 = five_m_continuation_def()
    e1 = one_m_role_def()
    a = {
        "id": "A",
        "name": "STRICT_COMPLETED_5M",
        "claim": "Opening drive, leave, retest, hold and continuation all require completed 5m bars.",
        "matches_frozen": [
            "5m is the strategy/setup timeframe",
            "TRUE uses completed 09:00-09:14 5m bars",
            "hidden_1m_test for continuation",
        ],
        "conflicts_frozen": [
            "3382 exemplar: 1m only times an already-valid 5m trade; OR_LOW hold was accepted without proving a fully-beyond completed 5m leave",
            "retest spec is 'first meaningful pullback while thesis alive', not 'wait for 5m close'",
        ],
        "would_kill_3382_location": True,
        "turns_into_1m_strategy": False,
    }
    b = {
        "id": "B",
        "name": "5M_THESIS_PLUS_CAUSAL_INTRABAR_EXECUTION",
        "claim": (
            "5m establishes stock, direction, drive, structural location, thesis. "
            "Completed 1m may observe first retest/hold/execution timing of that pre-identified level. "
            "1m cannot create a trade without valid 5m thesis."
        ),
        "matches_frozen": [
            INVARIANTS[0],
            INVARIANTS[1],
            INVARIANTS[2],
            INVARIANTS[3],
            e1.get("may_answer"),
            "3382_must_match: 1m only times the already-valid 5m trade",
            loc.get("requirement"),
        ],
        "conflicts_frozen": [
            "Must not let 1m invent the drive, direction, or the meaningful level itself",
        ],
        "boundary": {
            "1m_execution_ok": "the pre-identified 5m level was just retested and held",
            "1m_creating_strategy": "1m is required to prove opening drive, direction, or the meaningful level",
        },
        "would_kill_3382_location": False,
        "turns_into_1m_strategy": False,
    }
    c = {
        "id": "C",
        "name": "HYBRID_IN_PROGRESS_5M",
        "claim": "Use live/in-progress 5m state for leave/retest/hold.",
        "matches_frozen": ["faster than waiting for 5m close"],
        "conflicts_frozen": [
            "BAR_START / completed-bar causality used everywhere else in native research",
            "in-progress 5m is a different timestamp contract",
        ],
        "would_kill_3382_location": False,
        "turns_into_1m_strategy": False,
        "caution": "different timestamp semantics",
    }
    chosen = "B"
    why = (
        "Frozen V4 is an intraday 5m continuation. 1m must not create eligibility. "
        "The same spec also treats 3382 OR_LOW hold as a valid location with 1m as timing only, "
        "and never proved that every retest interaction must wait for a completed 5m candle. "
        "A would rewrite the frozen exemplar. C changes clock semantics. "
        "B keeps drive/direction/location identity on 5m and allows causal 1m to observe a retest of that 5m level."
    )
    return {
        "role_map": dict(ROLE_MAP),
        "invariants": list(INVARIANTS),
        "interpretations": {"A": a, "B": b, "C": c},
        "best_match_frozen_intent": chosen,
        "why": why,
        "would_that_turn_pb1_into_1m_strategy": False,
        "completed_5m_required_for_every_retest": "NOT PROVEN in frozen spec — ambiguity",
        "s2_currently_conflates_location_with_retest": True,
        "s2_s3_note": s2_s3_note,
        "s4_hidden_1m_test": s4.get("hidden_1m_test"),
        "3382_visibility": loc3382.get("visibility"),
        "any_rule_changed": False,
    }
