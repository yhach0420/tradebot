"""Non-overlap offset correction gate tests. No panel load."""
from __future__ import annotations

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import (
    C1_CONFIRMED_SET_SHA256,
    COMMON_SAMPLE,
    EXPECTED_PRECOMMIT_SHA256,
    FROZEN_IDS,
    OFFSET_ABS_RATIO,
)
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.gates import apply_offset_gates
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.isolation import DISC_V2_OUT, OUT, V13_OUT
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.publish import SHEETS
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import future_placebo_offsets


def test_gate_o3_fail_is_labeled_separately_from_o1():
    g = apply_offset_gates(
        beta_m5=0.1,
        beta_m3=-0.2,
        beta_m1=0.3,
        beta_0=1.0,
        beta_k1=10.0,
        beta_k2=0.1,
        beta_k3=0.1,
        overlap_minutes_k1=0,
        overlap_minutes_k2=0,
        overlap_minutes_k3=0,
    )
    assert g["O1"] is True
    assert g["O2"] is True
    assert g["O3"] is False
    assert g["O4"] is True
    assert g["offset_pass"] is False
    assert g["failed_at"] == "FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD"
    assert g["threshold"] == OFFSET_ABS_RATIO == 0.50


def test_o2_does_not_accept_only_minus_5():
    g = apply_offset_gates(
        beta_m5=1.0,
        beta_m3=-0.2,
        beta_m1=-0.1,
        beta_0=1.0,
        beta_k1=0.1,
        beta_k2=0.1,
        beta_k3=0.1,
        overlap_minutes_k1=0,
        overlap_minutes_k2=0,
        overlap_minutes_k3=0,
    )
    assert g["O1"] is True
    assert g["O2"] is False
    assert g["O3"] is True
    assert "O2" in str(g["failed_at"])
    assert "FOLLOWER" not in str(g["failed_at"])


def test_o1_and_o3_are_not_collapsed():
    g = apply_offset_gates(
        beta_m5=0.2,
        beta_m3=0.2,
        beta_m1=0.2,
        beta_0=-1.0,
        beta_k1=10.0,
        beta_k2=0.1,
        beta_k3=0.1,
        overlap_minutes_k1=0,
        overlap_minutes_k2=0,
        overlap_minutes_k3=0,
    )
    assert g["failed_at"] == "O1+FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD"


def test_overlap_blocks_o4():
    g = apply_offset_gates(
        beta_m5=0.1,
        beta_m3=0.1,
        beta_m1=0.1,
        beta_0=1.0,
        beta_k1=0.1,
        beta_k2=0.1,
        beta_k3=0.1,
        overlap_minutes_k1=1,
        overlap_minutes_k2=0,
        overlap_minutes_k3=0,
    )
    assert g["O4"] is False
    assert g["offset_pass"] is False


def test_frozen_ids_and_sample_binding():
    assert len(FROZEN_IDS) == 8
    assert set(COMMON_SAMPLE) == set(FROZEN_IDS)
    assert EXPECTED_PRECOMMIT_SHA256 == "83c2b92056941f5cae72d1123499c549e9e34ea3280d3ec4588e63a261509a80"
    assert C1_CONFIRMED_SET_SHA256 == "5118c867ed76a5558c93640f782dc6d69a97106a1a8740232fa97afa2f88dc67"
    assert OUT.resolve() != V13_OUT.resolve()
    assert OUT.resolve() != DISC_V2_OUT.resolve()
    for tid in FROZEN_IDS:
        w = int(tid.split("|w")[1].split("|")[0])
        h = int(tid.split("|h")[1])
        k1, k2, k3 = future_placebo_offsets(lookback=w, horizon=h)
        assert tuple(COMMON_SAMPLE[tid]["offsets"]) == (-5, -3, -1, 0, k1, k2, k3)
        assert 1 not in COMMON_SAMPLE[tid]["offsets"]


def test_audit_sheets():
    for name in (
        "Manifest",
        "Identity",
        "Candidate_Binding",
        "Common_Sample",
        "NonOverlap_Proof",
        "Offset_Map",
        "Offset_Gates",
        "Offset_Passers",
        "Day_Shuffle",
        "Sector_Identity",
        "Driver_Concentration",
        "Target_Concentration",
        "Common_Factor",
        "Final_Decision",
        "Contamination",
        "Firewall",
        "Safety",
    ):
        assert name in SHEETS
