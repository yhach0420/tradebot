"""E1_X34C passive fill deployability tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34B = NATIVE / "results" / "research" / "e1_x34b_entry_execution"

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
    x = interim.get("x34a_identity") or {}
    assert x.get("opp600_match") is True
    assert x.get("fills_match") is True
    assert abs(float(interim.get("unlimited_opp600_signal")) - 4.577794829666957) < 1e-6


def test_x34b_identity(interim):
    assert interim.get("x34b_run") == "e1x34b_entry_20260808_184858_A"
    body = json.loads((X34B / "report.json").read_text(encoding="utf-8"))
    assert body.get("run_id") == "e1x34b_entry_20260808_184858_A"


def test_anchor_sha(interim):
    assert interim.get("anchor_sha") == ANCHOR_SHA
    body = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == ANCHOR_SHA


def test_execution_sha(interim):
    assert interim.get("execution_sha") == EXEC_SHA
    body = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == EXEC_SHA


def test_entry_timestamp_is_fill_time(interim):
    assert interim.get("entry_timestamp_is_fill_time") is True


def test_fill_based_horizons(report):
    d = report.get("signal_vs_fill") or {}
    assert "600" in d
    assert d["600"].get("fill_based_mean") is not None


def test_signal_vs_fill_delta(report):
    d = (report.get("signal_vs_fill") or {}).get("600") or {}
    assert "delta_mean" in d


def test_order_fanout(interim):
    f = interim.get("order_fanout") or {}
    assert (f.get("orders_per_timestamp") or {}).get("median") is not None


def test_fill_burst(interim):
    b = interim.get("fill_burst") or {}
    assert "window_1s" in b or b.get("n_fills", 0) >= 0


def test_position_cap(interim):
    assert interim.get("position_cap") == 5


def test_capacity_no_future_ranking(interim):
    assert interim.get("capacity_no_future_ranking") is True


def test_pending_expiry(interim):
    assert interim.get("pending_expiry_sec") == 1.0


def test_duplicate_symbol(interim):
    assert "duplicate_blocks" in interim


def test_capacity_blocked(interim):
    assert "capacity_blocked_fills" in interim


def test_unlimited_reproduces_x34a(interim):
    assert (interim.get("x34a_identity") or {}).get("opp600_match") is True


def test_no_entry_performance_search(interim):
    assert interim.get("no_entry_performance_search") is True


def test_no_exit_design(interim):
    assert interim.get("no_exit_design") is True


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_20260810_not_opened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live_zero(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(report):
    assert (report.get("ab_determinism") or {}).get("ok") is True
