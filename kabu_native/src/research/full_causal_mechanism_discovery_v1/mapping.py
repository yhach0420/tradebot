"""Freeze O1/O2/O3 technical EXIT mapping before any candidate economics."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.full_causal_mechanism_discovery_v1 import EXIT_O1, EXIT_O2, EXIT_O3, EXECUTION_ID, SESSION_FLATTEN_HM

O1_EXIT_DEFINITION = (
    "After actual ENTRY fill, at the FIRST later completed 1m bar where P=FALSE: "
    "EXIT_PENDING, then first fresh causal Bid1, then actual EXIT fill, then SLOT_RELEASE. "
    "No timeout. No alternate EXIT."
)
O2_EXIT_DEFINITION = (
    "After actual ENTRY fill, at the FIRST later completed 1m bar where NOT (P AND Q): "
    "EXIT_PENDING, then first fresh causal Bid1, then actual EXIT fill, then SLOT_RELEASE. "
    "Do not separately test P-loss only, Q-loss only, or P-or-Q alternatives. Exactly this one EXIT."
)
O3_EXIT_DEFINITION = (
    "At ENTRY signal freeze THESIS_LEVEL = the causal numeric VWAP level used by the frozen "
    "reclaim signal at T0. The level is immutable for this trade. After actual ENTRY fill, "
    "at the FIRST later completed 1m bar where Close < THESIS_LEVEL: EXIT_PENDING, then first "
    "fresh causal Bid1, then actual EXIT fill, then SLOT_RELEASE. Later EMA/VWAP movement "
    "cannot alter THESIS_LEVEL. No timeout. No alternative EXIT."
)


def exit_mapping() -> dict[str, Any]:
    return {
        "EXECUTION_ID": EXECUTION_ID,
        "EXEC_EVAL_ID": "X1",
        "SESSION_FLATTEN_HM": list(SESSION_FLATTEN_HM),
        "EXIT_SEARCH": False,
        "ENTRY_EXIT_GRID": False,
        "O1_PERSIST_NEXT": {
            "TECHNICAL_EXIT_ID": EXIT_O1,
            "DEFINITION": O1_EXIT_DEFINITION,
            "TIMEOUT": False,
            "ALTERNATE_EXIT": False,
        },
        "O2_HANDOFF_NEXT": {
            "TECHNICAL_EXIT_ID": EXIT_O2,
            "DEFINITION": O2_EXIT_DEFINITION,
            "TIMEOUT": False,
            "ALTERNATE_EXIT": False,
            "P_LOSS_ONLY": False,
            "Q_LOSS_ONLY": False,
            "P_OR_Q_ALTERNATIVES": False,
        },
        "O3_RECLAIM_ACCEPT_NEXT": {
            "TECHNICAL_EXIT_ID": EXIT_O3,
            "DEFINITION": O3_EXIT_DEFINITION,
            "TIMEOUT": False,
            "ALTERNATE_EXIT": False,
            "THESIS_LEVEL_IMMUTABLE": True,
            "MOVING_LATER_LEVEL": False,
        },
    }


def mapping_sha256() -> str:
    return dumps_sha256(exit_mapping())


def freeze_mapping() -> dict[str, Any]:
    body = exit_mapping()
    return {
        **body,
        "FULL_STRATEGY_MAPPING_SHA256": mapping_sha256(),
        "MAPPING_FROZEN_BEFORE_ECONOMICS": True,
        "EXIT_SEARCH": False,
        "ENTRY_EXIT_GRID": False,
    }
