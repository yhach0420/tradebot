"""E1_X35R EXIT contract reconciliation tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from research.e1_x35r_exit_contract import (
    CANONICAL_LOOKUP,
    ENTRY_SHA,
    EXPECTED_FILLS,
    FORBIDDEN_FROM,
    SOURCE_X35_RUN,
)
from research.e1_x35r_exit_contract.contracts import (
    classify_mismatch,
    contract_table,
    fixed_exit_at,
    path_exit_at,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x35r_exit_contract"
X35 = NATIVE / "results" / "research" / "e1_x35_passive_exit"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"


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


def _toy(offs, rets, entry_price=1000.0):
    return {
        "ok": True,
        "offs": np.asarray(offs, dtype=float),
        "rets": np.asarray(rets, dtype=float),
        "mids": np.asarray(rets, dtype=float),
        "times": np.asarray([1e9 + o for o in offs], dtype=float),
        "sess_end": 1e9 + 10000,
        "entry_t": 1e9,
        "entry_price": entry_price,
    }


def test_x35_identity(interim):
    assert interim.get("source_x35_run") == SOURCE_X35_RUN
    x35 = json.loads((X35 / "report.json").read_text(encoding="utf-8"))
    assert x35.get("run_id") == SOURCE_X35_RUN


def test_entry_sha(interim):
    assert interim.get("entry_sha") == ENTRY_SHA
    body = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == ENTRY_SHA


def test_contract_extraction(interim):
    ct = interim.get("contract_table") or contract_table()
    fields = {r["field"] for r in ct}
    assert "quote_lookup_direction" in fields
    assert "first_or_last_quote" in fields


def test_episode_horizon_identity_helpers():
    path = _toy([0, 599, 601], [0.0, 10.0, 20.0])
    pr = path_exit_at(path, 600.0)
    fr = fixed_exit_at(path, 600.0)
    assert pr["exit_ret_bps"] == 10.0  # last <= 600
    assert fr["exit_ret_bps"] == 20.0  # first >= 600
    assert classify_mismatch(pr, fr, 600.0) == "TARGET_QUOTE_MAPPING"


def test_fill_time_origin(interim):
    assert interim.get("entry_origin_fill_time") is True


def test_exact_target_timestamp(interim):
    assert interim.get("exact_target_timestamp") is True
    assert interim.get("canonical_lookup") == CANONICAL_LOOKUP


def test_executable_buy1_only(interim):
    assert interim.get("executable_bid_exit") is True


def test_qty_freshness_special(interim):
    assert interim.get("qty_min") == 100
    assert interim.get("freshness_max_sec") == 5.0
    assert interim.get("no_special_quote") is True


def test_same_session(interim):
    assert interim.get("same_session") is True


def test_session_close_deterministic(interim):
    assert interim.get("session_close_deterministic") is True
    path = _toy([0, 100, 200], [0.0, 5.0, 8.0])
    fr = fixed_exit_at(path, 600.0)
    assert fr["reason"] == "SESSION_CLOSE"
    assert fr["exit_ret_bps"] == 8.0


def test_no_synthetic_price(interim):
    assert interim.get("no_synthetic_price") is True


def test_horizons_recomputed(interim):
    hs = interim.get("horizon_summaries") or {}
    for H in ("180", "300", "600", "900"):
        assert H in hs
        assert hs[H].get("path_mean") is not None
        assert hs[H].get("fixed_mean") is not None


def test_x35_verdict_recheck(interim):
    assert "x35_verdict_changed" in interim


def test_no_new_exit_search(interim):
    assert interim.get("no_new_exit_search") is True


def test_no_allocator_tuning(interim):
    assert interim.get("no_allocator_tuning") is True


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_20260810_unopened(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"


def test_submit_cancel_live(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True


def test_n_fills(interim):
    assert interim.get("n_fills") == EXPECTED_FILLS


def test_artifacts(report):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    if report.get("manifest_created"):
        assert (OUT / "PASSIVE_FIXED600_EXIT_BASELINE_V1.json").exists()
        assert report.get("verdict") == "E1_X35R_FIXED600_CONTRACT_RECONCILED"
        assert report.get("manifest_sha")
    else:
        assert report.get("verdict") in (
            "E1_X35R_BASELINE_CHANGED_AFTER_CONTRACT_REPAIR",
            "E1_X35R_FIXED600_NOT_SUPPORTED_AFTER_RECONCILIATION",
        )
