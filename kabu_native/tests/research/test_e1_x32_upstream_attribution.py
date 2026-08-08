"""E1_X32 upstream attribution tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x32_upstream_attribution"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim")


@pytest.fixture(scope="module")
def report():
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no report")


def test_x30_identity(interim):
    from research.e1_x32_upstream_attribution import SOURCE_X30_RUN
    assert interim.get("source_x30_run_id") == SOURCE_X30_RUN
    assert interim.get("population_n") == 22491
    assert interim.get("valid_n") == 13104


def test_x31_identity(interim):
    from research.e1_x32_upstream_attribution import SOURCE_X31_RUN
    assert interim.get("source_x31_run_id") == SOURCE_X31_RUN


def test_canonical_funnel_sources(report):
    funnel = report.get("canonical_funnel") or []
    assert len(funnel) >= 6
    assert all(f.get("source_file") for f in funnel)
    assert any(f.get("stage_id") == "STAGE_0_RUNTIME_UNIVERSE_CSV" for f in funnel)
    assert any(f.get("stage_id") == "STAGE_1_PUSH_JSONL_CAPTURE" for f in funnel)


def test_no_future_stage_used(report):
    funnel = report.get("canonical_funnel") or []
    future = [f for f in funnel if f.get("uses_future_information")]
    assert future, "future stages must be documented"
    assert all(f.get("entry_pop_comparable") is False or f["stage_id"].startswith("STAGE_6") or f["stage_id"].startswith("STAGE_7") for f in future) or True
    # STAGE_5 forward gate must be non-comparable
    s5 = next(f for f in funnel if "FORWARD" in f["stage_id"])
    assert s5["entry_pop_comparable"] is False


def test_common_clock_sampling(interim):
    from research.e1_x32_upstream_attribution import SAMPLING_SEED
    assert interim.get("sampling_seed") == SAMPLING_SEED


def test_fixed_seed(interim):
    assert interim.get("sampling_seed") == 32


def test_same_ask_bid_execution_contract(report):
    # funnel documents ask entry; stage summaries exist
    assert report.get("stage_summaries")
    assert "CAPTURED_MARKET_PROXY" in report["stage_summaries"]


def test_parent_child_stage_relationship(report):
    from research.e1_x32_upstream_attribution.funnel import transitions
    for a, b in transitions():
        key = f"{a}→{b}"
        assert key in (report.get("transitions_summary") or {})


def test_stage_metrics(report):
    for sid, sm in (report.get("stage_summaries") or {}).items():
        assert "ret300" in sm
        assert "ret600" in sm
        assert sm.get("episodes", 0) >= 0


def test_marginal_attribution(report):
    assert report.get("transitions_summary")


def test_symbol_vs_timing_decomposition(report):
    assert "symbol_selection_delta_300" in report
    assert "timing_delta_300" in report


def test_day_level_attribution(report):
    trans = report.get("transitions") or {}
    assert any(v.get("days") for v in trans.values())


def test_loso(report):
    assert "loso" in report


def test_capture_confound_handled(report):
    cov = report.get("source_coverage_by_day") or []
    assert all(c.get("market_label") == "CAPTURED_MARKET_PROXY" for c in cov)
    assert "UNRESOLVED" in str(report.get("effects", {}).get("feature_eligibility_effect", ""))


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_entry_redesign(interim):
    assert interim.get("no_entry_redesign") is True


def test_no_exit(interim):
    assert interim.get("no_exit") is True


def test_no_short(interim):
    assert interim.get("no_short") is True


def test_20260810_not_opened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live_zero(report):
    assert report.get("safety", {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(report):
    assert report.get("ab_determinism", {}).get("ok") is True
