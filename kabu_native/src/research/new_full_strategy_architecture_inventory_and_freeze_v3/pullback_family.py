"""Pullback family definition for novelty. Rescue forbidden."""
from __future__ import annotations

from typing import Any


def pullback_family() -> dict[str, Any]:
    return {
        "PULLBACK_FAMILY_RESCUE_FORBIDDEN": True,
        "FAMILY_MECHANISM": (
            "A prior impulse or trend context, then a retracement toward a moving reference "
            "(EMA, BB, prior bar high/low, or selling-exhaustion), then a rebound/continuation "
            "in the original direction."
        ),
        "NOT_AUTOMATICALLY_NEW": [
            "impulse → MA pullback → rebound",
            "trend → BB pullback → recovery",
            "context → PULLBACK_ACTIVE → continuation",
        ],
        "CHANGES_THAT_DO_NOT_ESTABLISH_NEW_ARCHITECTURE": [
            "EMA9 vs EMA21",
            "BB touch vs MA touch",
            "Volume added before pullback",
            "RCI added after pullback",
            "break of pullback high",
        ],
        "INVENTORIED_MEMBERS": [
            {
                "ID": "SIMPLE_TECH_PULLBACK_V1",
                "ROLE": "canonical 1m trend+pullback+rebound ENTRY family",
                "INVENTORIED": True,
            },
            {
                "ID": "SIMPLE_TECH_V6_TREND_PULLBACK_STAGE_RCA",
                "ROLE": "diagnostic stage RCA of the same parent V1 identity",
                "INVENTORIED": True,
            },
            {
                "ID": "E1_X6_FCRR_PULLBACK_ACTIVE",
                "ROLE": "sequential CONTEXT → PULLBACK_ACTIVE → SELLING_EXHAUSTED",
                "INVENTORIED": True,
            },
            {
                "ID": "E1_X6R3_CONT_PULL_BREAK",
                "ROLE": "CONT/PULL/BREAK registry; PULL already closed robust-failure",
                "INVENTORIED": True,
            },
            {
                "ID": "PRODUCTION_PBV2_MAINLINE",
                "ROLE": "production continuation-quality mainline in the same family",
                "INVENTORIED": True,
            },
            {
                "ID": "BB_VOL_IMPULSE_MA_PULLBACK_X1_Z_SUPPORT_FAIL",
                "ROLE": "withdrawn prior V5 proposal; same family",
                "INVENTORIED": True,
                "WITHDRAWN": True,
            },
        ],
        "SIMPLE_TECH_PULLBACK_V1_INVENTORIED": True,
        "V6_PULLBACK_LINEAGE_INVENTORIED": True,
        "E1_X6_FCRR_PULLBACK_INVENTORIED": True,
    }
