"""Start-day fail-closed gate. One mismatch => DO_NOT_START_PROSPECTIVE."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import file_inventory, source_inventory_sha
from research.pb1_v4_prospective_semantic_validation_preflight import (
    DO_NOT_START,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
)


def start_day_gate(*, precommit_sha: str, expected_precommit_sha: str) -> dict[str, Any]:
    live_machine = machine_sha256()
    live_inventory = source_inventory_sha(file_inventory())
    machine_ok = live_machine == EXPECTED_MACHINE_SHA256
    inventory_ok = live_inventory == EXPECTED_SOURCE_INVENTORY_SHA256
    precommit_ok = str(precommit_sha) == str(expected_precommit_sha) and bool(precommit_sha)
    ok = machine_ok and inventory_ok and precommit_ok
    return {
        "ok": ok,
        "verdict": "START_OK" if ok else DO_NOT_START,
        "machine_sha": live_machine,
        "expected_machine_sha": EXPECTED_MACHINE_SHA256,
        "machine_sha_ok": machine_ok,
        "source_inventory_sha": live_inventory,
        "expected_source_inventory_sha": EXPECTED_SOURCE_INVENTORY_SHA256,
        "source_inventory_sha_ok": inventory_ok,
        "precommit_sha": precommit_sha,
        "expected_precommit_sha": expected_precommit_sha,
        "precommit_sha_ok": precommit_ok,
        "DO_NOT_START_PROSPECTIVE": not ok,
    }
