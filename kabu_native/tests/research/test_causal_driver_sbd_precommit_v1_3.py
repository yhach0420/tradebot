"""Sector breadth/dispersion precommit V1.3 tests. No corrected OFFSET beta run."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import (
    CANDIDATE_LIST_SHA256,
    CASE_READY,
    EXPECTED_C1_CONFIRMED_N,
    PARENT_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.candidates import bind_c1_confirmed_set
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.isolation import DISC_V2_OUT, OUT, V12_OUT
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import (
    future_placebo_offsets,
    old_future_overlap_minutes,
    overlap_minutes,
    overlap_proof_for_candidate,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.publish import SHEETS

C2_FAILURE_IDS = (
    "BREADTH|GLOBAL_105|w1|h10",
    "BREADTH|SECTOR_3500|w1|h1",
    "BREADTH|SECTOR_3650|w1|h10",
)


def test_isolation_does_not_overwrite_v2():
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_precommit_v1_3")
    assert OUT.resolve() != DISC_V2_OUT.resolve()
    assert OUT.resolve() != V12_OUT.resolve()
    assert (DISC_V2_OUT / "report.json").is_file()
    assert (V12_OUT / "report.json").is_file()
    assert PRECOMMIT_ID.endswith("V1_3")
    assert PARENT_PRECOMMIT_SHA256 == "d93a65804cd316462deeec01a0723b8565bbf2b4504e54244059ccac75c73598"
    assert CANDIDATE_LIST_SHA256 == "848b0d72a748a0705af5079038f90db9c057068f8a1a4ba7f8989fad046e3985"
    assert CASE_READY.endswith("READY_V1_3")
    assert EXPECTED_C1_CONFIRMED_N == 8


def test_future_offsets_examples():
    assert future_placebo_offsets(lookback=1, horizon=1) == (3, 5, 7)
    assert future_placebo_offsets(lookback=1, horizon=3) == (5, 7, 9)
    assert future_placebo_offsets(lookback=3, horizon=1) == (5, 7, 9)


def test_old_plus1_overlaps_for_w1():
    assert old_future_overlap_minutes(lookback=1, horizon=1, k=1) > 0
    assert old_future_overlap_minutes(lookback=1, horizon=3, k=1) > 0
    k1, _, _ = future_placebo_offsets(lookback=1, horizon=1)
    assert overlap_minutes(t_min=0, lookback=1, horizon=1, k=k1) == 0
    k1b, _, _ = future_placebo_offsets(lookback=1, horizon=3)
    assert overlap_minutes(t_min=0, lookback=1, horizon=3, k=k1b) == 0
    k1c, _, _ = future_placebo_offsets(lookback=3, horizon=1)
    assert overlap_minutes(t_min=0, lookback=3, horizon=1, k=k1c) == 0


def test_required_audit_sheets():
    for name in (
        "Manifest",
        "V2_Invalidation",
        "Candidate_Binding",
        "C1_Confirmed_Set",
        "Old_Offset_Overlap",
        "New_Offset_Definition",
        "Window_Overlap_Proof",
        "Session_Feasibility",
        "Common_Offset_Sample",
        "Contamination",
        "Firewall",
        "Safety",
    ):
        assert name in SHEETS


def test_fv_still_denied():
    try:
        assert_ingest_date_allowed(FV_FIRST)
        raise AssertionError("fv_allowed")
    except IngestDateDenied:
        pass
    try:
        assert_ingest_date_allowed(PROSPECTIVE_FROM)
        raise AssertionError("prospective_allowed")
    except IngestDateDenied:
        pass


def test_c1_confirmed_set_frozen_and_nonoverlap():
    bound = bind_c1_confirmed_set()
    assert bound.get("pass")
    assert bound.get("n") == 8
    ids = [str(r.get("test_id")) for r in bound.get("records") or []]
    assert len(ids) == len(set(ids)) == 8
    for tid in C2_FAILURE_IDS:
        assert tid not in ids
    for rec in bound.get("records") or []:
        proof = rec.get("offset_def") or {}
        assert proof.get("all_future_overlap_zero") is True
        assert proof.get("session_feasible") is True
        assert int(proof.get("session_feasible_clock_n") or 0) > 0
        w = int(rec["lookback"])
        h = int(rec["horizon"])
        k1, k2, k3 = future_placebo_offsets(lookback=w, horizon=h)
        assert (k1, k2, k3) == (h + w + 1, h + w + 3, h + w + 5)
        for k in (k1, k2, k3):
            assert overlap_minutes(t_min=0, lookback=w, horizon=h, k=int(k)) == 0


def test_overlap_proof_blocks_if_driver_starts_on_target_end():
    proof = overlap_proof_for_candidate(lookback=1, horizon=1)
    assert proof["K1"] == 3
    assert proof["all_future_overlap_zero"] is True
    assert old_future_overlap_minutes(lookback=1, horizon=1, k=1) > 0


def test_no_beta_tokens_in_offset_def():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "sector_breadth_precommit_v1_3"
    for name in ("offset_def.py", "sample.py", "contract.py", "evaluate.py", "publish.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "fit_frozen" not in text
        assert "point_b_fx" not in text
