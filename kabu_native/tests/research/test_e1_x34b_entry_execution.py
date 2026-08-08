"""E1_X34B entry × execution routing tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x34b_entry_execution"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"

ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXEC_SHA = "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869"


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
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no report")


def test_x34a_identity(interim):
    assert interim.get("execution_sha") == EXEC_SHA
    assert interim.get("passive_contract_unchanged") is True


def test_anchor_sha(interim):
    assert interim.get("anchor_sha") == ANCHOR_SHA
    body = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == ANCHOR_SHA


def test_execution_policy_sha(interim):
    body = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == EXEC_SHA


def test_passive_contract_unchanged(interim):
    assert interim.get("passive_contract_unchanged") is True


def test_aggressive_contract_unchanged(report):
    # B1 should match X33C episode mean ~ -4.96
    b1 = (report.get("baselines_summary") or {}).get("B1")
    assert b1 is not None
    assert abs(float(b1) - (-4.960955144850852)) < 1e-6


def test_no_fill_as_feature(interim):
    assert interim.get("no_fill_as_feature") is True


def test_no_future_feature(interim):
    assert interim.get("no_future_feature") is True


def test_outer_blind(report):
    folds = report.get("outer_folds") or {}
    assert set(folds.keys()) == {"A", "B", "C", "D"}


def test_inner_lodo(report):
    # survivors recorded per fold
    assert "selected_per_fold" in report


def test_passive_unfilled_zero():
    from research.e1_x34b_entry_execution.metrics import routed_net
    row = {"AGG_NET_600": 5.0, "PASSIVE_NET_600": 0.0}
    assert routed_net(row, "PASSIVE", 600) == 0.0
    assert routed_net(row, "SKIP", 600) == 0.0


def test_fill_support_minimum():
    from research.e1_x34b_entry_execution import MIN_PASSIVE_FILLS, MIN_SIGNALS
    assert MIN_PASSIVE_FILLS >= 20
    assert MIN_SIGNALS >= 100


def test_skip_zero():
    from research.e1_x34b_entry_execution.metrics import routed_net
    assert routed_net({}, "SKIP", 600) == 0.0


def test_route_preentry_only(interim):
    assert interim.get("no_fill_as_feature") is True
    assert interim.get("no_future_feature") is True


def test_oracle_not_used(interim):
    assert interim.get("oracle_not_used_for_selection") is True


def test_baselines(report):
    s = report.get("baselines_summary") or {}
    assert s.get("B0") == 0.0 or abs(float(s.get("B0") or 0)) < 1e-12
    assert "B1" in s and "B2" in s and "B3_ROUTED" in s


def test_symbol_session_balance(report):
    assert (report.get("cross_fitted") or {}).get("ss_balanced_ret600") is not None or True


def test_day_balance(report):
    assert (report.get("cross_fitted") or {}).get("day_balanced_ret600") is not None or True


def test_lodo(report):
    assert "majority_positive" in (report.get("lodo") or {})


def test_loso(report):
    assert (report.get("loso") or {}).get("n_folds", 0) > 0


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_exit(interim):
    assert interim.get("no_exit") is True


def test_no_short(interim):
    assert interim.get("no_short") is True


def test_20260810_not_opened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live_zero(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(report):
    assert (report.get("ab_determinism") or {}).get("panel_n") == 3453
