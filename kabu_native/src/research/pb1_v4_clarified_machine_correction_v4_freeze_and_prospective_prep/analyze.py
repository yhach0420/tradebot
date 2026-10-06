"""Decide freeze vs blocked. No accuracy. No PnL."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    CASE_BIND,
    CASE_IDENTITY,
    CASE_LEDGER,
    CASE_NBAR,
    CASE_READY,
    NEXT_PROSPECTIVE,
    NEXT_STOP,
)


def decide(*, bind_ok: bool, nbar: dict[str, Any], identity: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_STOP, "V4_FROZEN": False, "reasons": ["bind_failed"]}
    if not nbar.get("ok"):
        return {
            "VERDICT": CASE_NBAR,
            "NEXT": NEXT_STOP,
            "V4_FROZEN": False,
            "reasons": ["hidden_n_bar_death_path"],
            "N_BAR_EXPIRY_USED": True,
            "HIDDEN_N_BAR_DEATH_PATH_FOUND": True,
        }
    if not identity.get("ok"):
        return {
            "VERDICT": CASE_IDENTITY,
            "NEXT": NEXT_STOP,
            "V4_FROZEN": False,
            "reasons": ["identity_or_inventory_mismatch"],
        }
    if not ledger.get("ok"):
        return {
            "VERDICT": CASE_LEDGER,
            "NEXT": NEXT_STOP,
            "V4_FROZEN": False,
            "reasons": ["contamination_boundary_unresolved"],
            "missing_sources": list(ledger.get("missing_sources") or []),
        }
    return {
        "VERDICT": CASE_READY,
        "NEXT": NEXT_PROSPECTIVE,
        "V4_FROZEN": True,
        "reasons": [],
        "note": "Prospective batch is NOT opened in this task.",
        "SPEC_CHANGE_REQUIRED": False,
    }
