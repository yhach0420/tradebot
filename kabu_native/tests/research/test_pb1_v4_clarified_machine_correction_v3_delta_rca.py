"""Tests for Correction V3 Delta RCA. Frozen hashes must not change. No implementation."""
from __future__ import annotations

import inspect

from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v3.definitions import machine_sha256 as correction_v3_sha256
from research.pb1_v4_clarified_machine_correction_v3_delta_rca import (
    ANY_CODE_CHANGED,
    ANY_THRESHOLD_OPTIMIZED,
    CASE_COMPLETE,
    CASE_INCOMPLETE,
    CASE_PROVENANCE,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_CORRECTION_V3_SHA256,
    EXPECTED_PARENT_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    NEXT_DEEPEN,
    NEXT_REDESIGN,
    NEXT_V4,
)
from research.pb1_v4_clarified_machine_correction_v3_delta_rca.analyze import decide
from research.pb1_v4_clarified_machine_correction_v3_delta_rca.isolation import OUT, V3_OUT, V3_SRC, write_overlap_n
from research.pb1_v4_clarified_machine_correction_v3_delta_rca.spec import SOURCE_FILES, source_sha256
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def test_frozen_hashes_and_isolation():
    assert spec_sha256() == EXPECTED_SPEC_SHA256
    assert correction_v3_sha256() == EXPECTED_CORRECTION_V3_SHA256
    assert correction_v2_sha256() == EXPECTED_CORRECTION_V2_SHA256
    assert parent_machine_sha256() == EXPECTED_PARENT_MACHINE_SHA256
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert ANY_CODE_CHANGED is False
    assert ANY_THRESHOLD_OPTIMIZED is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_correction_v3_delta_rca" in str(OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(V3_OUT.resolve())
    root = V3_SRC.parent / "pb1_v4_clarified_machine_correction_v3_delta_rca"
    for name in SOURCE_FILES:
        assert (root / name).is_file(), name
    assert len(source_sha256()) == 64


def test_decide_complete_when_causes_unique():
    done = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        a_unique=True,
        b_unique=True,
        provenance_reconstitute=False,
    )
    assert done["VERDICT"] == CASE_COMPLETE
    assert done["NEXT"] == NEXT_V4
    assert done["SPEC_CHANGE_REQUIRED"] is False
    assert done["v4_not_implemented_in_this_task"] is True
    prov = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        a_unique=True,
        b_unique=True,
        provenance_reconstitute=True,
    )
    assert prov["VERDICT"] == CASE_PROVENANCE
    assert prov["NEXT"] == NEXT_REDESIGN
    inc = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        a_unique=True,
        b_unique=False,
        provenance_reconstitute=False,
    )
    assert inc["VERDICT"] == CASE_INCOMPLETE
    assert inc["NEXT"] == NEXT_DEEPEN
    src = inspect.getsource(decide)
    assert "PnL" not in src
    assert "0.55 vs 0.60" not in src
