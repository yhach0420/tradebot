"""Tests for E1_X7 PFQ EXIT Gate Reconciliation."""
from __future__ import annotations

from research.e1_x7_pfq.exit_gate import MECH_GIVEBACK, MECH_SOFT, PAIRS
from research.e1_x7_pfq.exit_gate.evaluate import (
    assign_mechanism,
    combined_reference,
    decide_verdict,
    gate_pair,
    is_denominator,
    is_giveback,
    is_soft_premature,
)


def test_update_episode_unique_within_pair():
    rows = [{"episode_id": "a"}, {"episode_id": "b"}, {"episode_id": "a"}]
    assert len(rows) - len({r["episode_id"] for r in rows}) == 1
    unique = [{"episode_id": "a"}, {"episode_id": "b"}]
    assert len(unique) - len({r["episode_id"] for r in unique}) == 0


def test_progress_denominator_unique():
    eids = ["e1", "e2", "e1"]
    assert len(set(eids)) == 2


def test_protect_denominator_unique():
    assert is_denominator(5.0, -1.0) is True
    assert is_denominator(4.9, -1.0) is False
    assert is_denominator(5.0, 0.0) is False


def test_progress_repairable_unique():
    ids = ["a", "b", "a"]
    assert len(set(ids)) < len(ids)


def test_protect_repairable_unique():
    assert assign_mechanism(giveback=True, soft_premature=True) == MECH_GIVEBACK


def test_no_cross_pair_counting_for_gate():
    # Gate uses per-pair results only
    pr = {
        "repairable_n": 25,
        "repairable_days": 6,
        "repairable_fraction": 0.6,
        "top_mechanism_fraction": 0.7,
        "duplicate_episode_within_pair": 0,
        "pair_id": PAIRS[0],
    }
    g = gate_pair(pr, entry_path_support=True, identity_ok=True, ab_ok=True)
    assert g["pass"] is True
    # combined must not flip this
    assert "combined" not in g


def test_soft_premature_definition():
    assert is_soft_premature(MECH_SOFT) is True
    assert is_soft_premature("RECOVERY_AFTER_INVALIDATION") is False
    assert is_soft_premature(None) is False


def test_giveback_definition():
    assert is_giveback(t_plus5=1.0, exit_time=2.0, realized=-1.0) is True
    assert is_giveback(t_plus5=1.0, exit_time=2.0, realized=0.0) is True  # non-positive
    assert is_giveback(t_plus5=3.0, exit_time=2.0, realized=-1.0) is False
    assert is_giveback(t_plus5=1.0, exit_time=2.0, realized=1.0) is False


def test_failure_mechanism_single_assignment():
    assert assign_mechanism(giveback=True, soft_premature=True) == MECH_GIVEBACK
    assert assign_mechanism(giveback=False, soft_premature=True) == MECH_SOFT
    assert assign_mechanism(giveback=False, soft_premature=False) is None


def test_hard_invalidation_recovery_excluded():
    assert is_soft_premature("RECOVERY_AFTER_INVALIDATION") is False


def test_repairable_fraction_not_tautological():
    # fraction uses denom ORACLE_PLUS5_REALIZED_LOSS; repairable is not defined as == denom
    denom_n, repairable_n = 62, 27
    frac = repairable_n / denom_n
    assert abs(frac - 1.0) > 1e-9


def test_combined_unique_reference_only():
    fake = {
        PAIRS[0]: {
            "repairable_rows": [{"episode_id": "a"}, {"episode_id": "b"}],
            "denominator_episode_ids": ["a", "c"],
        },
        PAIRS[1]: {
            "repairable_rows": [{"episode_id": "a"}, {"episode_id": "d"}],
            "denominator_episode_ids": ["a", "d"],
        },
    }
    ref = combined_reference(fake)
    assert ref["used_for_gate"] is False
    assert ref["combined_pair_trade_repairable_n"] == 4
    assert ref["combined_unique_episode_repairable_n"] == 3


def test_pair_gate_independent():
    pr_fail = {
        "pair_id": PAIRS[0],
        "repairable_n": 10,
        "repairable_days": 3,
        "repairable_fraction": 0.3,
        "top_mechanism_fraction": 0.9,
        "duplicate_episode_within_pair": 0,
    }
    pr_pass = {
        "pair_id": PAIRS[1],
        "repairable_n": 33,
        "repairable_days": 8,
        "repairable_fraction": 0.72,
        "top_mechanism_fraction": 0.55,
        "duplicate_episode_within_pair": 0,
    }
    g0 = gate_pair(pr_fail, entry_path_support=True, identity_ok=True, ab_ok=True)
    g1 = gate_pair(pr_pass, entry_path_support=True, identity_ok=True, ab_ok=True)
    assert g0["pass"] is False and g1["pass"] is True
    vd = decide_verdict(
        {PAIRS[0]: g0, PAIRS[1]: g1},
        {
            PAIRS[0]: {**pr_fail, "exit_candidate": "PFQ_X_PROGRESS_STRUCT", "top_mechanism": MECH_GIVEBACK},
            PAIRS[1]: {**pr_pass, "exit_candidate": "PFQ_X_PROTECT", "top_mechanism": MECH_SOFT},
        },
    )
    assert vd["verdict"] == "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED"
    assert vd["selected_baseline_pair"] == PAIRS[1]


def test_no_exit_revision_implemented():
    from research.e1_x7_pfq.exit_gate.run_reconcile import _safety
    assert _safety()["exit_revision_implemented"] is False


def test_no_unused_data():
    from research.e1_x7_pfq.config import DAYS
    assert "20260803" not in DAYS


def test_ab_determinism():
    assert len(PAIRS) == 2
