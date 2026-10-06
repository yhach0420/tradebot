"""Fail-closed Frozen V4 identity gate. Does not retune ENTRY."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    EXPECTED_V4_SOURCE_SHA256,
    FROZEN_IDENTITY,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import (
    file_inventory,
    source_inventory_sha,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    FROZEN_ENTRY_IDENTITY,
)


def identity_gate() -> dict[str, Any]:
    live_machine = machine_sha256()
    live_inventory = source_inventory_sha(file_inventory())
    live_source = v4_source_sha256()
    machine_ok = live_machine == EXPECTED_MACHINE_SHA256
    inventory_ok = live_inventory == EXPECTED_SOURCE_INVENTORY_SHA256
    source_ok = live_source == EXPECTED_V4_SOURCE_SHA256
    identity_ok = FROZEN_ENTRY_IDENTITY == FROZEN_IDENTITY
    ok = machine_ok and inventory_ok and source_ok and identity_ok
    return {
        "ok": ok,
        "frozen_identity": FROZEN_ENTRY_IDENTITY,
        "machine_sha": live_machine,
        "expected_machine_sha": EXPECTED_MACHINE_SHA256,
        "machine_sha_ok": machine_ok,
        "source_inventory_sha": live_inventory,
        "expected_source_inventory_sha": EXPECTED_SOURCE_INVENTORY_SHA256,
        "source_inventory_sha_ok": inventory_ok,
        "v4_source_sha": live_source,
        "expected_v4_source_sha": EXPECTED_V4_SOURCE_SHA256,
        "v4_source_sha_ok": source_ok,
        "ENTRY_FROZEN": True,
        "ENTRY_RETUNE_FORBIDDEN": True,
    }
