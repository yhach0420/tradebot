"""Tests for E1_X7–X9 Research Program Closure."""
from __future__ import annotations

from research.e1_x7_x9_closure import (
    EXPECTED_SOURCES,
    FINAL_VERDICT,
    REQUIRED_VERDICT_CHECKS,
    SUPERSEDED_SOURCES,
)
from research.e1_x7_x9_closure.assemble import assemble


def test_all_source_runs_identified():
    assert set(EXPECTED_SOURCES) >= {
        "pfq_design", "bridge_v2", "exit_gate_v2", "exit_revision",
        "symbol_leverage", "universe_regime",
    }
    assert "exit_gate_v1" in SUPERSEDED_SOURCES


def test_source_verdicts_exact():
    assert REQUIRED_VERDICT_CHECKS["bridge_v2"] == "E1_X7_PFQ_ENTRY_SUPPORTED_EXIT_CAPTURE_LIMITATION"
    assert REQUIRED_VERDICT_CHECKS["exit_gate_v2"] == "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED"
    assert REQUIRED_VERDICT_CHECKS["exit_revision"] == "E1_X7_PFQ_EXIT_REVISION_MECHANISM_FAILED"
    assert REQUIRED_VERDICT_CHECKS["symbol_leverage"] == "E1_X8_KIOXIA_THRESHOLD_LEVERAGE_SIGNAL_SURVIVES"
    assert REQUIRED_VERDICT_CHECKS["universe_regime"] == "E1_X9_NO_STABLE_UNIVERSE_REGIME_SEPARATION"


def test_exit_gate_v1_marked_superseded():
    s = SUPERSEDED_SOURCES["exit_gate_v1"]
    assert s["superseded"] is True
    assert s["superseded_by"] == "SUPERSEDED_BY_EXIT_GATE_RECONCILIATION_V2"


def test_exit_gate_v2_is_canonical():
    assert EXPECTED_SOURCES["exit_gate_v2"]["canonical"] is True
    assert EXPECTED_SOURCES["exit_gate_v2"]["superseded"] is False


def test_pfq_closed_rejected():
    r = assemble(label="T")
    assert r["verdict"] == FINAL_VERDICT
    assert r["program_status"]["E1_X7_PFQ"]["status"] == "CLOSED_REJECTED"
    assert r["program_status"]["E1_X7_PFQ"]["formal_line_status"] == "PFQ_CURRENT_LINE_CLOSED_REJECTED"


def test_no_frozen_candidate():
    r = assemble(label="T")
    assert r["program_status"]["E1_X7_PFQ"]["frozen_candidate"] is None
    assert r["program_status"]["E1_X7_PFQ"]["robust_entry_exit_pair"] is None


def test_update_signal_and_economics_separated():
    r = assemble(label="T")
    assert "supported" in r["findings"]["pfq_entry"]["entry_path"]
    assert r["findings"]["pfq_economics"]["existing_4_pairs"] == "all rejected"


def test_kioxia_threshold_and_signal_separated():
    r = assemble(label="T")
    assert "strongly moved" in r["findings"]["kioxia"]["threshold_leverage"]["conclusion"]
    assert r["findings"]["kioxia"]["signal_dependence"]["ex_285A_support"] is True


def test_direct_ownership_not_overclaimed():
    r = assemble(label="T")
    assert r["findings"]["universe"]["direct_ownership_status"] == "DIRECT_INSTITUTIONAL_DATA_NOT_EVALUABLE"
    assert "institutional ownership is disadvantageous" in " ".join(
        r["findings"]["universe"]["forbidden_claims"]
    )


def test_low_turnover_hypothesis_not_supported():
    r = assemble(label="T")
    assert r["findings"]["universe"]["low_turnover_advantage"] is False
    ft = r["findings"]["universe"]["turnover_first_touch_plus5_vs_minus10"]
    assert ft["LOW"] < ft["HIGH"]


def test_update_heavy_not_promoted():
    r = assemble(label="T")
    assert r["findings"]["update_heavy"]["status"] == "DESCRIPTIVE_NEAR_SIGNAL_NOT_PROMOTED"
    assert r["findings"]["update_heavy"]["promoted_to_new_family"] is False


def test_no_pfq_revival():
    r = assemble(label="T")
    assert r["safety"]["pfq_revived"] is False
    assert r["verdict_detail"]["pfq_closed"] is True


def test_no_new_candidate():
    r = assemble(label="T")
    assert r["safety"]["new_candidate"] is False
    assert r["safety"]["new_computation"] is False


def test_no_unused_data():
    r = assemble(label="T")
    assert r["safety"]["unused_data_used"] is False


def test_no_runtime_change():
    r = assemble(label="T")
    assert r["safety"]["mainline_changed"] is False
    assert r["verdict_detail"]["runtime_impact"] is False


def test_ab_determinism():
    a = assemble(label="A")
    b = assemble(label="B")
    keys = [
        "source_registry_sha", "canonical_run_sha", "final_findings_sha",
        "rejected_paths_sha", "future_principles_sha", "open_items_sha", "verdict",
    ]
    sa, sb = a["determinism_shas"], b["determinism_shas"]
    assert all(sa[k] == sb[k] for k in keys)
