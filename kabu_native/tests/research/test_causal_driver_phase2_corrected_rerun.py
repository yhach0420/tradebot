"""Corrected-rerun package tests. Does not run the 288-test discovery."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.phase2_corrected_rerun import (
    CASE_INVALIDATED,
    EXPECTED_PRECOMMIT_SHA256,
    INVALID_RUN_CANDIDATE_SHA256,
    OLD_PHASE2_CLASSIFICATION,
    PROVISIONAL_SELECTION,
)
from research.causal_driver_pb1.phase2_corrected_rerun.freeze import freeze_corrected
from research.causal_driver_pb1.phase2_corrected_rerun.isolation import NEXT_DRIVER_OUT, OUT, PHASE2_INVALID_OUT
from research.causal_driver_pb1.phase2_corrected_rerun.publish import SHEETS
from research.causal_driver_pb1.phase2_discovery import TARGET_SCOPES
from research.causal_driver_pb1.phase2_precommit import FX_LOOKBACKS_MIN, RESPONSE_HORIZONS_MIN
from research.causal_driver_pb1.response.cases import run_resolver_tests


def test_isolation_does_not_point_at_invalid_run():
    assert str(OUT).replace("\\", "/").endswith("phase2_usdjpy_contract_corrected_rerun_v1")
    assert PHASE2_INVALID_OUT.resolve() != OUT.resolve()
    assert NEXT_DRIVER_OUT.resolve() != OUT.resolve()
    assert (PHASE2_INVALID_OUT / "report.json").is_file()
    assert EXPECTED_PRECOMMIT_SHA256 == "40b4ac4b7574cb4980bf2047e12e4045db57f8399355a39317ddd8322d6b6e9a"
    assert CASE_INVALIDATED == "PHASE2_USDJPY_TECHNICALLY_INVALIDATED_BY_RESPONSE_CONTRACT_BUG_V1"
    assert OLD_PHASE2_CLASSIFICATION == "INVALID_FOR_CAUSAL_DRIVER_DECISION"
    assert PROVISIONAL_SELECTION == "PROVISIONAL_PENDING_VALID_USDJPY_PHASE2_RERUN"


def test_family_size_unchanged():
    assert len(FX_LOOKBACKS_MIN) * len(RESPONSE_HORIZONS_MIN) * len(TARGET_SCOPES) == 288


def test_empty_corrected_freeze_does_not_reuse_invalid_sha():
    payload, sha = freeze_corrected([])
    assert payload["candidates"] == []
    assert sha != INVALID_RUN_CANDIDATE_SHA256
    assert len(sha) == 64
    again, sha2 = freeze_corrected([])
    assert sha == sha2
    assert again["response_contract"] == "last_completed_close_available_at_le_T"


def test_required_audit_sheets():
    for name in (
        "Manifest",
        "Technical_Invalidation",
        "Contract_Diff",
        "Resolver_Tests",
        "Coverage_Before",
        "Coverage_After",
        "Price_Age",
        "DEV_All_288",
        "DEV_Gates",
        "Corrected_Candidate_Freeze",
        "C1_Access_Barrier",
        "Safety",
    ):
        assert name in SHEETS


def test_resolver_cases_all_pass():
    rows = run_resolver_tests()
    assert rows
    assert all(r["pass"] for r in rows)


def test_no_forbidden_tokens():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "phase2_corrected_rerun"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
