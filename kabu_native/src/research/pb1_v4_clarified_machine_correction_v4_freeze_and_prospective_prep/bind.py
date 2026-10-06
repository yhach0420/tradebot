"""Bind frozen V4 + parents. Read-only. No prospective load."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v3.definitions import machine_sha256 as correction_v3_sha256
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    EXPECTED_SPEC_SHA,
    EXPECTED_V2_SHA,
    EXPECTED_V3_SHA,
    EXPECTED_V4_MACHINE_SHA256,
    EXPECTED_V4_SOURCE_SHA256,
    PARENT_PARITY_VERDICT,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.isolation import V4_OUT
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def bind_prior() -> dict[str, Any]:
    path = V4_OUT / "report.json"
    v4 = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    dec = dict(v4.get("decision") or {})
    hashes = dict(v4.get("hashes") or {})
    live_v4 = v4_machine_sha256()
    live_src = v4_source_sha256()
    live_spec = spec_sha256()
    ok = (
        path.is_file()
        and str(dec.get("VERDICT") or "") == PARENT_PARITY_VERDICT
        and live_v4 == EXPECTED_V4_MACHINE_SHA256
        and str(hashes.get("PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_SHA256") or "") == EXPECTED_V4_MACHINE_SHA256
        and live_src == EXPECTED_V4_SOURCE_SHA256
        and live_spec == EXPECTED_SPEC_SHA
        and correction_v2_sha256() == EXPECTED_V2_SHA
        and correction_v3_sha256() == EXPECTED_V3_SHA
        and v4.get("future_economic_outcome_used") is False
        and v4.get("prospective_event_consumed") is False
    )
    return {
        "ok": ok,
        "reason": None if ok else "v4_parity_parent_mismatch",
        "parent_verdict": dec.get("VERDICT"),
        "live_v4_sha": live_v4,
        "report_v4_sha": hashes.get("PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_SHA256"),
        "live_spec": live_spec,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "prospective_data_opened": False,
    }
