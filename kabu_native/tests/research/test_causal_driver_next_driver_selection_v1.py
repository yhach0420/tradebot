"""Next Causal Driver family selection tests. No future-return research."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.next_driver_selection import (
    CASE_FUTURES,
    CASE_HARNESS,
    CASE_LEADER,
    CASE_NONE,
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    FROZEN_LEADER_SET_MAX_N,
    KABU_RUNTIME_SLOT_N,
    NEW_DRIVER_FAMILY_ID,
    NEXT_PRECOMMIT_LEADER,
    PARENT_PHASE2_VERDICT,
    PRIMARY_LEADER_LAGGARD,
    USDJPY_FAMILY_STATUS,
)
from research.causal_driver_pb1.next_driver_selection.isolation import OUT, PHASE2_OUT
from research.causal_driver_pb1.next_driver_selection.publish import SHEETS
from research.causal_driver_pb1.next_driver_selection.select import selection_matrix


def test_isolation_and_parent_bind():
    assert str(OUT).replace("\\", "/").endswith("next_driver_selection_v1")
    assert (PHASE2_OUT / "report.json").is_file()
    assert EXPECTED_PRECOMMIT_SHA256 != FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256
    assert PARENT_PHASE2_VERDICT == "USDJPY_STANDALONE_CAUSAL_LEAD_NOT_FOUND_V1"
    assert USDJPY_FAMILY_STATUS == "STOPPED_CURRENT_ARCHITECTURE_V1"


def test_audit_sheets():
    assert SHEETS[0] == "Manifest"
    assert "USDJPY_Closeout" in SHEETS
    assert "Selection_Matrix" in SHEETS
    assert "Safety" in SHEETS
    assert len(SHEETS) == 12


def test_runtime_leader_set_fits_kabu50():
    assert FROZEN_LEADER_SET_MAX_N <= 8
    assert FROZEN_LEADER_SET_MAX_N + 42 <= KABU_RUNTIME_SLOT_N
    assert NEW_DRIVER_FAMILY_ID == "CROSS_SECTIONAL_DRIVER"


def test_futures_not_ready_selects_leader_when_panel_ok():
    rca = {
        "ok": True,
        "missing_files": [],
        "dev_asof_usable_rate": 0.92,
        "c1_mean_dates_with_am_bar_per_symbol": 96,
        "c1_expected_dates": 96,
        "dev_mean_dates_with_am_bar_per_symbol": 287,
        "dev_expected_dates": 287,
        "contract_gap": {"shared_harness_blocks_next_driver": False},
    }
    futures = {"ready_as_next_driver": False, "reason": "no hist"}
    d = selection_matrix(rca=rca, futures=futures)
    assert d["VERDICT"] == CASE_LEADER
    assert d["NEXT"] == NEXT_PRECOMMIT_LEADER
    assert d["PRIMARY_NEXT_DRIVER"] == PRIMARY_LEADER_LAGGARD
    assert d["phase0_enum_mutated"] is False


def test_futures_ready_preferred():
    rca = {
        "ok": True,
        "missing_files": [],
        "dev_asof_usable_rate": 0.92,
        "c1_mean_dates_with_am_bar_per_symbol": 96,
        "c1_expected_dates": 96,
        "dev_mean_dates_with_am_bar_per_symbol": 287,
        "dev_expected_dates": 287,
        "contract_gap": {"shared_harness_blocks_next_driver": False},
    }
    d = selection_matrix(rca=rca, futures={"ready_as_next_driver": True})
    assert d["VERDICT"] == CASE_FUTURES


def test_harness_block():
    rca = {
        "ok": False,
        "missing_files": ["x"],
        "dev_asof_usable_rate": 0.92,
        "c1_mean_dates_with_am_bar_per_symbol": 96,
        "c1_expected_dates": 96,
        "dev_mean_dates_with_am_bar_per_symbol": 287,
        "dev_expected_dates": 287,
        "contract_gap": {"shared_harness_blocks_next_driver": True},
    }
    d = selection_matrix(rca=rca, futures={"ready_as_next_driver": False})
    assert d["VERDICT"] == CASE_HARNESS


def test_no_family_ready():
    rca = {
        "ok": True,
        "missing_files": [],
        "dev_asof_usable_rate": 0.1,
        "c1_mean_dates_with_am_bar_per_symbol": 10,
        "c1_expected_dates": 96,
        "dev_mean_dates_with_am_bar_per_symbol": 10,
        "dev_expected_dates": 287,
        "contract_gap": {"shared_harness_blocks_next_driver": False},
    }
    d = selection_matrix(rca=rca, futures={"ready_as_next_driver": False})
    assert d["VERDICT"] == CASE_NONE


def test_no_forbidden_tokens():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "next_driver_selection"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
