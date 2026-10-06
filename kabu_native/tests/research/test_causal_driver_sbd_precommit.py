"""Sector breadth/dispersion precommit tests. No discovery outcomes."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.contracts.enums import DriverFamily
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_breadth_precommit import (
    CASE_READY,
    EXPECTED_SECTOR_IDS,
    EXPECTED_SECTOR_MAPPING_SHA256,
    EXPECTED_UNIVERSE_SHA256,
    FAMILY_N,
    HORIZONS,
    LOOKBACKS,
    METRICS,
    PARENT_C1_CONFIRMED_N,
    PARENT_CANDIDATE_LIST_SHA256,
    PARENT_DECISION_REASON,
    PARENT_DEV_CANDIDATE_N,
    PARENT_FINAL_PASS_N,
    PARENT_PRECOMMIT_SHA256,
    PARENT_REPORT_REASON_RAW,
    PARENT_STATUS,
    PARENT_VERDICT,
    PHASE0_DRIVER_FAMILY_VALUES,
    PRECOMMIT_ID,
    SCOPE_N,
)
from research.causal_driver_pb1.sector_breadth_precommit.days import N_DEP, N_DEP_MIN_OK
from research.causal_driver_pb1.sector_breadth_precommit.family import build_family_384, build_scopes
from research.causal_driver_pb1.sector_breadth_precommit.isolation import LL_DISC_OUT, LL_PRE_V11, OUT
from research.causal_driver_pb1.sector_breadth_precommit.publish import SHEETS


def test_isolation_does_not_overwrite_parent():
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_precommit_v1")
    assert str(LL_DISC_OUT).replace("\\", "/").endswith("cross_sectional_leader_laggard_discovery_v1")
    assert OUT.resolve() != LL_DISC_OUT.resolve()
    assert OUT.resolve() != LL_PRE_V11.resolve()
    assert (LL_DISC_OUT / "report.json").is_file()
    assert PRECOMMIT_ID == "SECTOR_BREADTH_DISPERSION_CAUSAL_DISCOVERY_PRECOMMIT_V1"
    assert CASE_READY.endswith("PRECOMMIT_READY_V1")


def test_parent_erratum_bindings():
    assert PARENT_VERDICT == "CROSS_SECTIONAL_LEADER_LAGGARD_CAUSAL_LEAD_NOT_FOUND_V1"
    assert PARENT_REPORT_REASON_RAW == "NO_C1_CONFIRMED_ROBUST_CANDIDATE"
    assert PARENT_DECISION_REASON == "ALL_C1_CONFIRMED_CANDIDATES_FAILED_OFFSET_PLACEBO"
    assert PARENT_CANDIDATE_LIST_SHA256 == "be79499d7d29bab28d6847479ba649ba09e231cbd4eeb1fe1866ead50f6027bd"
    assert PARENT_DEV_CANDIDATE_N == 28
    assert PARENT_C1_CONFIRMED_N == 9
    assert PARENT_FINAL_PASS_N == 0
    assert PARENT_PRECOMMIT_SHA256 == "7cc118005173c236f7e8548f4f9dd8a4d8d36504722fa30d27fc52b98164e52e"
    assert PARENT_STATUS == "STOPPED_CURRENT_ARCHITECTURE_V1"


def test_family_384():
    dummy = [{"sector_id": s, "sector_name": s, "constituent_n": 3} for s in EXPECTED_SECTOR_IDS]
    scopes = build_scopes(dummy)
    tests = build_family_384(scopes)
    assert len(METRICS) * SCOPE_N * len(LOOKBACKS) * len(HORIZONS) == FAMILY_N == 384
    assert len(scopes) == 12
    assert scopes[-1]["scope_id"] == "GLOBAL_105"
    assert len(tests) == 384
    assert EXPECTED_UNIVERSE_SHA256.startswith("1d0a78b1")
    assert EXPECTED_SECTOR_MAPPING_SHA256.startswith("e2b96283")


def test_dependency_window_math():
    assert N_DEP == 151
    assert N_DEP_MIN_OK == 144
    assert (150 / 151) >= 0.95
    assert (143 / 151) < 0.95


def test_phase0_driver_family_not_mutated():
    assert tuple(m.value for m in DriverFamily) == PHASE0_DRIVER_FAMILY_VALUES
    assert "SECTOR_BREADTH_DISPERSION" not in {m.value for m in DriverFamily}


def test_required_audit_sheets():
    required = [
        "Manifest",
        "Parent_Closeout",
        "Universe105",
        "Sector_Mapping",
        "Eligible_Sectors",
        "PointInTime_Listing",
        "Input_Coverage",
        "Eligible_Days",
        "Folds",
        "Timestamp_Semantics",
        "Freshness",
        "Driver_Definitions",
        "Scopes",
        "Family_384",
        "Models",
        "DEV_Gates",
        "C1_Gates",
        "Offset_Placebo",
        "Day_Shuffle",
        "Sector_Identity",
        "Concentration",
        "Common_Factor",
        "Contamination",
        "Firewall",
        "Safety",
    ]
    assert list(SHEETS) == required


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


def test_no_forbidden_tokens():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "sector_breadth_precommit"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
