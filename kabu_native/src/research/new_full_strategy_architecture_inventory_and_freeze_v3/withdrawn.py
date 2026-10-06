"""Withdraw the incomplete-novelty V5 pullback proposal. No economics were run for it."""
from __future__ import annotations

from typing import Any

from research.new_full_strategy_architecture_inventory_and_freeze_v3 import WITHDRAWN_V5_ARCHITECTURE_ID


def withdrawn_v5() -> dict[str, Any]:
    return {
        "PREVIOUS_V5_PROPOSAL_WITHDRAWN": True,
        "ARCHITECTURE_ID": WITHDRAWN_V5_ARCHITECTURE_ID,
        "WHY_WITHDRAWN": (
            "Existing Pullback architecture lineage was incompletely included in the prior "
            "novelty audit. Impulse→MA pullback→rebound is the SIMPLE_TECH_PULLBACK / "
            "V6 TREND_PULLBACK / E1_X6 FCRR PULLBACK_ACTIVE family, not a new decision mechanism."
        ),
        "PULLBACK_FAMILY_RESCUE_FORBIDDEN": True,
        "NEW_CANDIDATE_ECONOMICS_RUN": False,
        "NEW_CANDIDATE_PNL_READ_N": 0,
        "FROZEN": False,
        "EVALUATED": False,
    }
