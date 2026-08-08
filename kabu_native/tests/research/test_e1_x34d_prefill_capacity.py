"""E1_X34D pre-fill hard capacity tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x34d_prefill_capacity"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"

ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
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


def test_source_identity(interim):
    assert (interim.get("identities") or {}).get("unlimited_x34c") is True
    assert (interim.get("identities") or {}).get("post_fill_x34c") is True


def test_entry_sha(interim):
    assert interim.get("entry_sha") == ENTRY_SHA
    body = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == ENTRY_SHA


def test_anchor_sha(interim):
    assert interim.get("anchor_sha") == ANCHOR_SHA


def test_execution_sha(interim):
    assert interim.get("execution_sha") == EXEC_SHA


def test_pending_reserves_slot(interim):
    assert interim.get("pending_reserves_slot") is True


def test_open_plus_pending_never_gt5(interim):
    assert interim.get("hard_cap_violations") == 0
    assert interim.get("max_open_plus_pending") <= 5


def test_no_post_fill_retroactive(interim):
    assert interim.get("no_post_fill_retroactive") is True


def test_pending_expiry_1s(interim):
    assert interim.get("pending_expiry_sec") == 1.0


def test_duplicate_block(report):
    adm = (report.get("C2") or {}).get("admission") or {}
    assert "duplicate_blocked" in adm


def test_neutral_deterministic_order(report):
    assert (report.get("C2") or {}).get("primary_ordering") == "symbol_ascending"


def test_no_future_ranking(interim):
    assert interim.get("no_future_ranking") is True


def test_denominator_includes_blocked(interim):
    assert interim.get("denominator_includes_blocked") is True


def test_600s_explicitly_proxy(interim):
    assert interim.get("occupancy_label") == "OCCUPANCY_PROXY_600S"


def test_unlimited_x34c_identity(interim):
    assert (interim.get("identities") or {}).get("unlimited_x34c") is True


def test_post_fill_x34c_identity(interim):
    assert (interim.get("identities") or {}).get("post_fill_x34c") is True


def test_prefill_replay(report):
    assert "C2" in report
    assert (report.get("C2") or {}).get("admission") is not None


def test_day_balance(report):
    assert (report.get("C2") or {}).get("economics", {}).get("day_balanced_ret600") is not None


def test_ss_balance(report):
    assert (report.get("C2") or {}).get("economics", {}).get("ss_balanced_ret600") is not None


def test_lodo(report):
    assert "majority_positive" in (report.get("lodo") or {})


def test_loso(report):
    assert (report.get("loso") or {}).get("n_folds", 0) > 0


def test_neutral_ordering_sensitivity(report):
    assert "ordering_sensitivity" in report


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_exit_design(interim):
    assert interim.get("no_exit_design") is True


def test_no_short(interim):
    assert interim.get("no_short") is True


def test_20260810_unopened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live_zero(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(report):
    assert (report.get("ab_determinism") or {}).get("ok") is True


def test_x34c_qualification_documented(interim):
    q = interim.get("x34c_qualification") or ""
    assert "post-fill" in q.lower() or "post-fill" in q
