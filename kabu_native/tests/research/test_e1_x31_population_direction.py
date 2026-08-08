"""E1_X31 tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x31_population_direction"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_analysis_id(interim):
    from research.e1_x31_population_direction import ANALYSIS_ID
    assert interim.get("analysis_id") == ANALYSIS_ID


def test_x30_source(interim):
    from research.e1_x31_population_direction import SOURCE_X30_RUN
    assert interim.get("source_x30_run_id") == SOURCE_X30_RUN


def test_population_n(interim):
    assert interim.get("population_n") == 22491


def test_valid_n(interim):
    assert interim.get("valid_n") == 13104


def test_no_20260810(interim):
    assert interim.get("opened_20260810") is False


def test_entry_only(interim):
    assert interim.get("entry_only_no_exit") is True


def test_no_short_orders(interim):
    assert interim.get("no_short_order_implementation") is True


def test_no_margin(interim):
    assert interim.get("no_margin_path_enable") is True


def test_case_present(interim):
    assert interim.get("population_case") in {
        "MARKET_DOWNWARD_BACKGROUND",
        "CANDIDATE_GENERATOR_NEGATIVE_DIRECTIONAL_EDGE",
        "NO_STABLE_POPULATION_DIRECTION",
    }
