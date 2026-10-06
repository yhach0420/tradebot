"""Static audit of ACCEPTED_STRUCTURAL_FAILURE. Does not change V4 or the allowed list."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.pb1_v4_frozen_old_confirmation_blind_validation.precommit import ALLOWED_THESIS_LOST_REASONS
from research.pb1_v4_frozen_validation_blind_confirmation import KNOWN_AUDIT_TAXONOMY_MISMATCH
from research.pb1_v4_frozen_validation_blind_confirmation.isolation import NATIVE, V4_SRC

TOKEN = "ACCEPTED_STRUCTURAL_FAILURE"
FAILURE_STATE = "ACCEPTED_FAILURE"


def static_asf_audit() -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for name in ("machine.py", "location.py", "binding.py"):
        path = V4_SRC / name
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(NATIVE)).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), start=1):
            if TOKEN in line or FAILURE_STATE in line:
                hits.append({"path": rel, "line": i, "text": line.strip()[:240]})
    machine = V4_SRC / "machine.py"
    location = V4_SRC / "location.py"
    mtxt = machine.read_text(encoding="utf-8")
    ltxt = location.read_text(encoding="utf-8")
    in_allowed = TOKEN in set(ALLOWED_THESIS_LOST_REASONS)
    return {
        "KNOWN_AUDIT_TAXONOMY_MISMATCH": KNOWN_AUDIT_TAXONOMY_MISMATCH,
        "in_old_confirmation_allowed_list": in_allowed,
        "added_to_frozen_validation_allowed_list": False,
        "allowed_list_changed": False,
        "old_confirmation_readjudicated": False,
        "v4_changed": False,
        "is_executable_frozen_death_path": TOKEN in mtxt and "lose_thesis" in mtxt,
        "source_location": [
            "src/research/pb1_v4_clarified_machine_correction_v4/machine.py:step_5m_thesis",
            "src/research/pb1_v4_clarified_machine_correction_v4/machine.py:observe_1m_interaction",
            "src/research/pb1_v4_clarified_machine_correction_v4/location.py:classify_interaction",
        ],
        "trigger_semantics": (
            "5m path: classify_interaction returns ACCEPTED_FAILURE (close accepted through the pre-identified "
            "level with the bar no longer spanning it) and kills_thesis; lose_thesis fires if through_closes>=1 "
            "or state is ACCEPTED_FAILURE. Two TEMPORARY_PENETRATION closes also lose as ACCEPTED_STRUCTURAL_FAILURE. "
            "1m path: 1m may increment through_closes; lose_thesis only if through_closes>=2 and state is ACCEPTED_FAILURE. "
            "1m does not mint location, direction, or thesis."
        ),
        "future_information_required": False,
        "uses_only_completed_bar": True,
        "hits": hits,
        "machine_token_n": mtxt.count(TOKEN),
        "location_accepted_failure_n": ltxt.count(FAILURE_STATE),
    }
