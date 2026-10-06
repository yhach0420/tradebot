"""Fail-closed identity. Mismatch => do not open Confirmation 1 economics."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import (
    COMPLETE_STRATEGY_IDENTITY,
    complete_strategy_sha256,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate as entry_gate
from research.pb1_v4_complete_strategy_economic_confirmation1 import (
    CASE_HASH,
    EXPECTED_COMPLETE_STRATEGY_ID,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_ENTRY_ID,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
)


def identity_gate() -> dict[str, Any]:
    g = entry_gate()
    live_cs = complete_strategy_sha256(
        machine_sha=str(g.get("machine_sha") or ""),
        source_inventory_sha=str(g.get("source_inventory_sha") or ""),
    )
    cs_id_ok = COMPLETE_STRATEGY_IDENTITY == EXPECTED_COMPLETE_STRATEGY_ID
    cs_sha_ok = live_cs == EXPECTED_COMPLETE_STRATEGY_SHA256
    entry_ok = str(g.get("frozen_identity") or "") == EXPECTED_ENTRY_ID
    ok = bool(g.get("ok")) and cs_id_ok and cs_sha_ok and entry_ok
    return {
        **g,
        "ok": ok,
        "complete_strategy_identity": COMPLETE_STRATEGY_IDENTITY,
        "expected_complete_strategy_identity": EXPECTED_COMPLETE_STRATEGY_ID,
        "complete_strategy_identity_ok": cs_id_ok,
        "COMPLETE_STRATEGY_SHA256": live_cs,
        "expected_complete_strategy_sha256": EXPECTED_COMPLETE_STRATEGY_SHA256,
        "complete_strategy_sha_ok": cs_sha_ok,
        "entry_id_ok": entry_ok,
        "expected_machine_sha": EXPECTED_MACHINE_SHA256,
        "expected_source_inventory_sha": EXPECTED_SOURCE_INVENTORY_SHA256,
        "DO_NOT_OPEN_ECONOMIC_CONFIRMATION1": not ok,
        "hash_verdict": None if ok else CASE_HASH,
        "COMPLETE_STRATEGY_CHANGED": False,
        "V4_CHANGED": False,
    }
