"""Tests for Correction V2 RCA. Frozen hashes must not change. No PnL. No implementation."""
from __future__ import annotations

import inspect

from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v2_rca import (
    ANY_CODE_CHANGED,
    ANY_THRESHOLD_OPTIMIZED,
    CASE_GAPS,
    CASE_SPEC,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_PARENT_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    NEXT_CORR_V3,
    NEXT_SPEC_V3,
)
from research.pb1_v4_clarified_machine_correction_v2_rca.analyze import decide
from research.pb1_v4_clarified_machine_correction_v2_rca.isolation import CORRECTION_OUT, CORRECTION_SRC, OUT, write_overlap_n
from research.pb1_v4_clarified_machine_correction_v2_rca.spec import SOURCE_FILES, source_sha256
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def test_frozen_hashes_and_isolation():
    assert spec_sha256() == EXPECTED_SPEC_SHA256
    assert correction_v2_sha256() == EXPECTED_CORRECTION_V2_SHA256
    assert parent_machine_sha256() == EXPECTED_PARENT_MACHINE_SHA256
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert ANY_CODE_CHANGED is False
    assert ANY_THRESHOLD_OPTIMIZED is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_correction_v2_rca" in str(OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(CORRECTION_OUT.resolve())
    root = CORRECTION_SRC.parent / "pb1_v4_clarified_machine_correction_v2_rca"
    for name in SOURCE_FILES:
        assert (root / name).is_file(), name
    assert len(source_sha256()) == 64


def test_decide_gaps_when_v2_sufficient():
    gaps = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        v2_sufficient=True,
        three_blockers_are_encodings=True,
    )
    assert gaps["VERDICT"] == CASE_GAPS
    assert gaps["NEXT"] == NEXT_CORR_V3
    assert gaps["SPEC_CHANGE_REQUIRED"] is False
    spec = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        v2_sufficient=False,
        three_blockers_are_encodings=False,
    )
    assert spec["VERDICT"] == CASE_SPEC
    assert spec["NEXT"] == NEXT_SPEC_V3
    src = inspect.getsource(decide)
    assert "PnL" not in src
    assert "0.55 vs 0.60" not in src
