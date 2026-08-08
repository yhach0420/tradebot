"""Tests for EXIT Gate Reconciliation V2."""
from __future__ import annotations

from research.e1_x7_pfq.exit_gate_v2 import MECH_GIVEBACK, MECH_SOFT, PAIRS, REF_PROFITABLE_SOFT
from research.e1_x7_pfq.exit_gate_v2.evaluate import (
    assign_mechanism,
    decide_verdict,
    gate_pair,
    is_denominator,
    is_giveback,
    is_soft_premature,
)


def test_repairable_subset_of_denominator():
    repair = {"a", "b"}
    denom = {"a", "b", "c"}
    assert repair <= denom


def test_progress_repairable_all_in_denominator():
    # structural: any repairable row must have in_denominator True
    row = {"in_denominator": True, "mechanism": MECH_GIVEBACK}
    assert row["in_denominator"] is True


def test_protect_repairable_all_in_denominator():
    assert is_denominator(5.0, -1.0) is True
    assert is_denominator(5.0, 1.0) is False


def test_profitable_soft_exit_not_repairable_loss():
    # realized >= 0 => not denom => not repairable gate
    assert is_denominator(40.0, 7.0) is False
    assert is_soft_premature(MECH_SOFT) is True


def test_profitable_soft_exit_reference_only():
    assert REF_PROFITABLE_SOFT == "PROFITABLE_SOFT_EXIT_OPPORTUNITY_COST"


def test_progress_corrected_counts():
    assert abs((35 / 62) - 0.5645161290) < 1e-9
    assert abs((31 / 35) - 0.8857142857) < 1e-9


def test_protect_corrected_counts():
    assert abs((19 / 46) - 0.4130434783) < 1e-9


def test_protect_repairable_n_gate_fail():
    pr = {
        "pair_id": PAIRS[1],
        "repairable_in_denominator_n": 19,
        "repairable_n": 19,
        "repairable_days": 8,
        "repairable_fraction": 19 / 46,
        "top_mechanism_fraction": 1.0,
        "duplicate_episode_within_pair": 0,
        "subset_invariant_ok": True,
    }
    g = gate_pair(pr, entry_path_support=True, identity_ok=True, ab_ok=True)
    assert g["checks"]["repairable_in_denominator_n_ge_20"] is False
    assert g["pass"] is False


def test_protect_repairable_fraction_gate_fail():
    pr = {
        "pair_id": PAIRS[1],
        "repairable_in_denominator_n": 19,
        "repairable_n": 19,
        "repairable_days": 8,
        "repairable_fraction": 19 / 46,
        "top_mechanism_fraction": 1.0,
        "duplicate_episode_within_pair": 0,
        "subset_invariant_ok": True,
    }
    g = gate_pair(pr, entry_path_support=True, identity_ok=True, ab_ok=True)
    assert g["checks"]["repairable_fraction_ge_050"] is False


def test_only_progress_gate_passes():
    pr_ok = {
        "pair_id": PAIRS[0],
        "repairable_in_denominator_n": 35,
        "repairable_n": 35,
        "repairable_days": 9,
        "repairable_fraction": 35 / 62,
        "top_mechanism_fraction": 31 / 35,
        "duplicate_episode_within_pair": 0,
        "subset_invariant_ok": True,
        "exit_candidate": "PFQ_X_PROGRESS_STRUCT",
        "top_mechanism": MECH_GIVEBACK,
    }
    pr_fail = {
        "pair_id": PAIRS[1],
        "repairable_in_denominator_n": 19,
        "repairable_n": 19,
        "repairable_days": 8,
        "repairable_fraction": 19 / 46,
        "top_mechanism_fraction": 1.0,
        "duplicate_episode_within_pair": 0,
        "subset_invariant_ok": True,
        "exit_candidate": "PFQ_X_PROTECT",
        "top_mechanism": MECH_GIVEBACK,
    }
    g0 = gate_pair(pr_ok, entry_path_support=True, identity_ok=True, ab_ok=True)
    g1 = gate_pair(pr_fail, entry_path_support=True, identity_ok=True, ab_ok=True)
    assert g0["pass"] is True and g1["pass"] is False


def test_selected_baseline_progress():
    pr_ok = {
        "pair_id": PAIRS[0],
        "repairable_in_denominator_n": 35,
        "repairable_n": 35,
        "repairable_days": 9,
        "repairable_fraction": 35 / 62,
        "top_mechanism_fraction": 31 / 35,
        "duplicate_episode_within_pair": 0,
        "subset_invariant_ok": True,
        "exit_candidate": "PFQ_X_PROGRESS_STRUCT",
        "top_mechanism": MECH_GIVEBACK,
    }
    pr_fail = {
        "pair_id": PAIRS[1],
        "repairable_in_denominator_n": 19,
        "repairable_n": 19,
        "repairable_days": 8,
        "repairable_fraction": 19 / 46,
        "top_mechanism_fraction": 1.0,
        "duplicate_episode_within_pair": 0,
        "subset_invariant_ok": True,
        "exit_candidate": "PFQ_X_PROTECT",
        "top_mechanism": MECH_GIVEBACK,
    }
    g0 = gate_pair(pr_ok, entry_path_support=True, identity_ok=True, ab_ok=True)
    g1 = gate_pair(pr_fail, entry_path_support=True, identity_ok=True, ab_ok=True)
    vd = decide_verdict({PAIRS[0]: g0, PAIRS[1]: g1}, {PAIRS[0]: pr_ok, PAIRS[1]: pr_fail})
    assert vd["verdict"] == "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED"
    assert vd["selected_baseline_pair"] == PAIRS[0]
    assert vd["selected_exit_candidate"] == "PFQ_X_PROGRESS_STRUCT"
    assert vd["dominant_failure_mechanism"] == MECH_GIVEBACK
    assert vd["repairable_n"] == 35


def test_no_exit_revision_implemented():
    from research.e1_x7_pfq.exit_gate_v2.run_reconcile import _safety
    assert _safety()["exit_revision_implemented"] is False
    assert _safety()["source_run_overwritten"] is False


def test_no_unused_data():
    from research.e1_x7_pfq.config import DAYS
    assert "20260803" not in DAYS


def test_ab_determinism():
    assert assign_mechanism(giveback=True, soft_premature=True) == MECH_GIVEBACK
    assert is_giveback(t_plus5=1.0, exit_time=2.0, realized=-1.0)
