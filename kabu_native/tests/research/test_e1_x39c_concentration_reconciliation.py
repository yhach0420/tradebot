"""E1_X39C concentration reconciliation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x39c_concentration_reconciliation import (
    FORBIDDEN_FROM,
    PRECOMMIT_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    X39B_RUN_ID,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x39c_concentration_reconciliation"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_identity(interim):
    assert interim.get("identity_pass") is True
    assert interim.get("x39b_run_id") == X39B_RUN_ID


def test_no_mutation(interim):
    assert interim.get("strategy_mutation") is False
    assert interim.get("model_mutation") is False
    assert interim.get("universe_mutation") is False
    assert interim.get("universe_unchanged") is True
    assert interim.get("no_post_hoc_symbol_policy") is True
    assert json.loads((X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").read_text(encoding="utf-8"))["sha256"] == V1R_SHA
    assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))["sha256"] == PRECOMMIT_SHA
    assert interim.get("old_precommit_unchanged") is True


def test_diagnostics_flags(interim):
    assert interim.get("d1_no_readmission") is True
    assert interim.get("d2_frozen_ranking_only") is True
    assert interim.get("loso_no_refit") is True
    assert interim.get("universe_contract") == UNIVERSE_CONTRACT


def test_concentration_fields(interim):
    assert interim.get("concentration_formula") == "max_symbol_contrib_share"
    assert interim.get("concentration_threshold") == 0.5
    assert interim.get("top_symbol")
    assert interim.get("top_share") is not None


def test_safety(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_binding_on_reconciled(interim):
    if interim.get("verdict") == "E1_X39C_BRIDGE_CONCENTRATION_RECONCILED":
        assert interim.get("universe_binding") is True
        assert interim.get("new_precommit") is True
        assert (OUT / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").exists()
        assert (OUT / "PROSPECTIVE_PRECOMMIT_V1R_U1.json").exists()
    elif interim.get("verdict") != "E1_X39C_BRIDGE_IDENTITY_UNRESOLVED":
        assert interim.get("universe_binding") is False


def test_ab_artifacts(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    assert interim.get("verdict") in (
        "E1_X39C_BRIDGE_CONCENTRATION_RECONCILED",
        "E1_X39C_BRIDGE_SINGLE_SYMBOL_DEPENDENT",
        "E1_X39C_CONCENTRATION_REVIEW_REQUIRED",
        "E1_X39C_BRIDGE_IDENTITY_UNRESOLVED",
    )
