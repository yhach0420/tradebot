"""Internal-consistency resolutions. Not a strategy redesign."""
from __future__ import annotations

from typing import Any

BINDING = {
    "A_first_unsuccessful_interaction": {
        "authoritative_field": "location_interaction.first_unsuccessful_observation_does_not_automatically_kill",
        "authoritative_value": True,
        "ignored_convenience_field": "answers.first_unsuccessful_kills",
        "ignored_because": "contradicts the canonical spec; not authoritative",
        "implementation": "TOUCH/TESTING/TEMPORARY_PENETRATION cancel that E1 attempt only. Thesis stays alive until ACCEPTED_FAILURE or other thesis-loss states.",
    },
    "B_or_family_location_identity": {
        "LOCATION_IDENTIFIED_separate_from_LOCATION_INTERACTION": True,
        "or_family_B": (
            "After OPENING_DRIVE_ACTIVE and a real leave/displacement from OR, "
            "the relevant OR boundary becomes LOCATION_CANDIDATE / LOCATION_IDENTIFIED. "
            "A hold/rejection is NOT required to create LOCATION_IDENTIFIED. "
            "A later hold may validate execution. It must not retroactively invent the level identity."
        ),
        "or_touch_alone_execution_valid": False,
        "or_level_identity_needs_future_hold": False,
    },
    "no_strategy_redesign": True,
    "any_future_outcome_used": False,
}


def implementation_binding() -> dict[str, Any]:
    return dict(BINDING)
