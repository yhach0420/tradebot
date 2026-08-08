"""E1_X30 Absolute-Rise ENTRY V2 tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x30_absolute_rise_entry_v2"


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
    from research.e1_x30_absolute_rise_entry_v2 import ANALYSIS_ID
    assert interim.get("analysis_id") == ANALYSIS_ID


def test_no_20260810_opened(interim):
    assert interim.get("opened_20260810") is False


def test_historical_14_days(interim):
    from research.e1_x30_absolute_rise_entry_v2 import HISTORICAL_DAYS
    assert list(interim.get("historical_days") or []) == list(HISTORICAL_DAYS)


def test_outer_blocks_exact(interim):
    from research.e1_x30_absolute_rise_entry_v2 import OUTER_BLOCKS
    got = interim.get("outer_blocks") or {}
    for k, v in OUTER_BLOCKS.items():
        assert list(got.get(k) or []) == list(v)


def test_entry_only_no_exit(interim):
    assert interim.get("entry_only_no_exit") is True


def test_board_mapping_sha(interim):
    from research.e1_x30_absolute_rise_entry_v2 import BOARD_MAPPING_SHA
    assert interim.get("board_mapping_sha") == BOARD_MAPPING_SHA


def test_primary_label_defined(interim):
    prev = interim.get("label_prevalence") or {}
    assert prev.get("primary_label") == "ABS_RISE_30_BEFORE_DOWN20_600"


def test_catalog_generated(interim):
    assert (interim.get("candidate_semantic_families_generated") or 0) > 0


def test_forbidden_day_guard():
    from research.e1_x30_absolute_rise_entry_v2 import FORBIDDEN_FROM, HISTORICAL_DAYS
    assert all(d < FORBIDDEN_FROM for d in HISTORICAL_DAYS)
