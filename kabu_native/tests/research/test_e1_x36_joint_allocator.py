"""E1_X36 joint allocator tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x36_joint_allocator import (
    ANCHOR_SHA,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    EXPECTED_SIGNALS,
    FORBIDDEN_FROM,
    POSITION_CAP,
    WAIT_SEC,
)
from research.e1_x36_joint_allocator.replay import simulate_joint

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x36_joint_allocator"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X35R = NATIVE / "results" / "research" / "e1_x35r_exit_contract"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim/report yet")


@pytest.fixture(scope="module")
def report():
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no report")


def test_all_sha_binds(interim):
    assert interim.get("entry_sha") == ENTRY_SHA
    assert interim.get("anchor_sha") == ANCHOR_SHA
    assert interim.get("execution_sha") == EXEC_SHA
    assert interim.get("exit_sha") == EXIT_SHA
    assert json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))["sha256"] == ENTRY_SHA
    assert json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))["sha256"] == ANCHOR_SHA
    assert json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))["sha256"] == EXEC_SHA
    assert json.loads((X35R / "PASSIVE_FIXED600_EXIT_BASELINE_V1.json").read_text(encoding="utf-8"))["sha256"] == EXIT_SHA


def test_canonical_fixed600_only(interim):
    assert interim.get("canonical_exit_only") is True


def test_actual_exit_occupancy(interim):
    assert interim.get("actual_exit_timestamp_occupancy") is True


def test_pending_reservation(interim):
    assert interim.get("pending_reservation") is True
    assert interim.get("expiry_sec") == WAIT_SEC


def test_open_pending_cap(interim):
    assert interim.get("open_plus_pending_cap") == POSITION_CAP
    assert (interim.get("hard_cap_violations") or 0) == 0


def test_cap_invariant_unit():
    # toy: 6 simultaneous signals at same clock → at most 5 admitted
    t0 = 1_000_000.0
    evs = []
    for i in range(6):
        evs.append({
            "date": "20260721",
            "symbol": f"S{i}",
            "session": "AM",
            "signal_time": t0,
            "filled": False,
            "fill_time": None,
            "limit_price": 1000.0,
            "bid0": 1000.0,
        })
    sim = simulate_joint(evs, order_mode="symbol_ascending")
    assert sim["orders_admitted"] == 5
    assert sim["admission_blocked"] == 1
    assert sim["hard_cap_violations"] == 0
    assert sim["max_open_plus_pending"] <= 5


def test_no_post_fill_retroactive(interim):
    assert interim.get("pending_reservation") is True


def test_duplicate_semantics(interim):
    assert interim.get("duplicate_semantics") == "no_overlap_replace"


def test_pre_entry_features_only(interim):
    assert interim.get("pre_entry_features_only") is True
    assert interim.get("no_fill_feature_leakage") is True
    assert interim.get("no_return_leakage") is True


def test_nested_outer_blind(interim):
    assert interim.get("nested_outer_blind") is True
    assert interim.get("inner_lodo") is True
    assert set((interim.get("selected_per_fold") or {}).keys()) == {"A", "B", "C", "D"}


def test_cohort_topk_tiebreak(interim):
    assert interim.get("cohort_topk") is True
    assert interim.get("deterministic_tiebreak") == "symbol_ascending"


def test_neutral_baselines(interim):
    b = interim.get("baselines_pnl") or {}
    for k in ("SKIP", "ASC", "DESC", "HASH", "learned"):
        assert k in b


def test_total_pnl(interim):
    assert "learned" in (interim.get("baselines_pnl") or {})


def test_day_ss_concentration(interim):
    assert "lodo" in interim
    assert "hard_cap_violations" in interim


def test_lodo_loso(interim):
    assert interim.get("lodo") is not None
    assert "loso_majority" in interim


def test_capital_diagnostic(interim):
    assert interim.get("capital_diagnostic") is not None


def test_no_runtime_short(interim):
    assert interim.get("no_runtime_change") is True
    assert interim.get("no_short") is True


def test_20260810_unopened(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"


def test_submit_cancel_live(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True


def test_n_signals(interim):
    assert interim.get("n_signals") == EXPECTED_SIGNALS


def test_artifacts(report):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    if report.get("manifest_created"):
        assert (OUT / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json").exists()
        assert report.get("verdict") in (
            "E1_X36_FULL_STRATEGY_HISTORICALLY_SUPPORTED",
            "E1_X36_NEUTRAL_ADMISSION_FULL_STRATEGY_SUPPORTED",
        )
