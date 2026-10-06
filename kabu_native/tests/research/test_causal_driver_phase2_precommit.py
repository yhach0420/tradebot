"""Phase 2 precommit tests. No lead outcomes. No Frozen Validation economics."""
from __future__ import annotations

from pathlib import Path

import pytest

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit import (
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    FX_LOOKBACKS_MIN,
    HORIZON_LAST_T,
    RESPONSE_HORIZONS_MIN,
)
from research.causal_driver_pb1.phase2_precommit.eligibility import split_count_ordered
from research.causal_driver_pb1.phase2_precommit.isolation import NATIVE, OUT, PHASE0_OUT, PHASE1_OUT
from research.causal_driver_pb1.phase2_precommit.publish import SHEETS
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import prove_stock_timestamp_semantics


def test_isolation_repo_root():
    assert (NATIVE / "src" / "research" / "causal_driver_pb1").is_dir()
    assert (PHASE0_OUT / "report.json").is_file()
    assert (PHASE1_OUT / "report.json").is_file()
    assert "src/results" not in str(OUT).replace("\\", "/")


def test_stock_timestamp_semantics_dev_only():
    got = prove_stock_timestamp_semantics()
    assert got["pass"], got
    assert got["bar_timestamp_convention"] == "BAR_START"
    assert got["available_at"] == "bar_start_plus_1_minute"
    assert got["price_field"] == "close"
    assert got["mid_fabricated"] is False
    assert got["fv_rows_read"] is False
    assert str(got["sample_available_at_jst"]).endswith("09:01:00+09:00")
    assert got.get("c1_date_last") == "20260421"


def test_sector_mapping_frozen_before_outcomes():
    got = bind_sector_mapping()
    assert got["pass"]
    assert got["universe105_sha256"] == EXPECTED_UNIVERSE_MANIFEST_SHA256
    assert got["universe105_count"] == 105
    assert got["old_usdjpy_winners_used"] is False
    assert got["dynamic40_used"] is False
    assert got["price_weighted"] is False
    assert got["eligible_sector_n"] >= 1
    assert all(s["constituent_n"] >= 3 for s in got["eligible_sectors"])
    assert got["target_scopes"][0] == "MKT105_EQW"
    assert len(got["sector_mapping_sha256"]) == 64


def test_fold_split_date_count():
    days = [f"202409{17+i:02d}" for i in range(10)]
    a, b = split_count_ordered(days, 2)
    assert len(a) == 5 and len(b) == 5
    x, y, z = split_count_ordered(days, 3)
    assert len(x) == 3 and len(y) == 3 and len(z) == 4
    assert a[-1] < b[0]


def test_clock_and_families_fixed():
    assert FX_LOOKBACKS_MIN == (1, 3, 5, 10, 20, 30)
    assert RESPONSE_HORIZONS_MIN == (5, 10, 20, 30)
    assert HORIZON_LAST_T["5"] == "11:25"
    assert HORIZON_LAST_T["30"] == "11:00"
    assert "2" not in {str(x) for x in FX_LOOKBACKS_MIN}


def test_fv_and_prospective_denied():
    with pytest.raises(IngestDateDenied):
        assert_ingest_date_allowed("20260422")
    with pytest.raises(IngestDateDenied):
        assert_ingest_date_allowed("20260924")


def test_no_phase2_outcome_modules_and_no_old_winners():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "phase2_precommit"
    forbidden = ("USDJPY_RET_1M", "winning_symbols", "profit_factor", "net_pnl", "XGBoost", "random_forest")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text and not (path.name == "contract.py" and tok in {"XGBoost", "random_forest"}):
                hits.append(f"{path.name}:{tok}")
    assert hits == []


def test_audit_sheets():
    assert SHEETS[0] == "Manifest"
    assert "Stock_Semantics" in SHEETS
    assert "Contamination" in SHEETS
    assert len(SHEETS) == 15
