"""Tests for clarified-machine parity RCA. Frozen hashes must not change. No PnL."""
from __future__ import annotations

import inspect

from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as clarified_machine_sha256
from research.pb1_v4_clarified_machine_parity_rca import (
    ANY_CODE_CHANGED,
    ANY_THRESHOLD_OPTIMIZED,
    CASE_GAPS,
    CASE_OVERSTATED,
    CASE_SPEC,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    NEXT_CORR_V2,
    NEXT_SPEC_V3,
    NEXT_TARGETED,
)
from research.pb1_v4_clarified_machine_parity_rca.analyze import decide
from research.pb1_v4_clarified_machine_parity_rca.isolation import MACHINE_OUT, MACHINE_SRC, OUT, write_overlap_n
from research.pb1_v4_clarified_machine_parity_rca.spec import SOURCE_FILES, source_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def test_frozen_hashes_and_isolation():
    assert spec_sha256() == EXPECTED_SPEC_SHA256
    assert clarified_machine_sha256() == EXPECTED_MACHINE_SHA256
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert ANY_CODE_CHANGED is False
    assert ANY_THRESHOLD_OPTIMIZED is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_parity_rca" in str(OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(MACHINE_OUT.resolve())
    root = MACHINE_SRC.parent / "pb1_v4_clarified_machine_parity_rca"
    for name in SOURCE_FILES:
        assert (root / name).is_file(), name
    assert len(source_sha256()) == 64


def test_decide_does_not_auto_select_first_branch():
    over = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        confirmed_machine_bug_n=0,
        spec_ambiguity_n=0,
        timing_or_human_n=5,
        v2_sufficient_for_confirmed_bugs=True,
        required_state_transition_unspecified=False,
    )
    assert over["VERDICT"] == CASE_OVERSTATED
    assert over["NEXT"] == NEXT_TARGETED
    assert over["auto_selected_first_branch"] is False
    spec = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        confirmed_machine_bug_n=0,
        spec_ambiguity_n=2,
        timing_or_human_n=0,
        v2_sufficient_for_confirmed_bugs=True,
        required_state_transition_unspecified=True,
    )
    assert spec["VERDICT"] == CASE_SPEC
    assert spec["NEXT"] == NEXT_SPEC_V3
    gaps = decide(
        bind_ok=True,
        hashes_unchanged=True,
        fingerprint_drift=False,
        confirmed_machine_bug_n=4,
        spec_ambiguity_n=0,
        timing_or_human_n=2,
        v2_sufficient_for_confirmed_bugs=True,
        required_state_transition_unspecified=False,
    )
    assert gaps["VERDICT"] == CASE_GAPS
    assert gaps["NEXT"] == NEXT_CORR_V2
    src = inspect.getsource(decide)
    assert "PnL" not in src
    assert "0.55 vs 0.60" not in src
