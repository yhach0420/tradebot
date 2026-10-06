"""Start-day fail-closed identity gate. Mismatch => do not open Old Confirmation."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import (
    file_inventory,
    source_inventory_sha,
)
from research.pb1_v4_frozen_old_confirmation_blind_validation import (
    CASE_HASH,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    EXPECTED_V4_SOURCE_SHA,
)


def identity_gate() -> dict[str, Any]:
    live_machine = machine_sha256()
    live_inventory = source_inventory_sha(file_inventory())
    live_source = v4_source_sha256()
    machine_ok = live_machine == EXPECTED_MACHINE_SHA256
    inventory_ok = live_inventory == EXPECTED_SOURCE_INVENTORY_SHA256
    source_ok = live_source == EXPECTED_V4_SOURCE_SHA
    ok = machine_ok and inventory_ok and source_ok
    return {
        "ok": ok,
        "verdict": "IDENTITY_OK" if ok else CASE_HASH,
        "machine_sha": live_machine,
        "expected_machine_sha": EXPECTED_MACHINE_SHA256,
        "machine_sha_ok": machine_ok,
        "source_inventory_sha": live_inventory,
        "expected_source_inventory_sha": EXPECTED_SOURCE_INVENTORY_SHA256,
        "source_inventory_sha_ok": inventory_ok,
        "v4_source_sha": live_source,
        "expected_v4_source_sha": EXPECTED_V4_SOURCE_SHA,
        "v4_source_sha_ok": source_ok,
        "DO_NOT_OPEN_OLD_CONFIRMATION": not ok,
    }
