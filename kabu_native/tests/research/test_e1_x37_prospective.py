"""E1_X37 prospective precommit tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x37_prospective import (
    FEATURE_ORDER,
    FORBIDDEN_FROM,
    MODEL_ARTIFACT_SHA,
    PROSPECTIVE_FROM,
    V1R_SHA,
)
from research.e1_x37_prospective.freeze import MutationGuard, load_model_artifact, load_v1r

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x37_prospective"
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_v1r_sha(interim):
    assert interim.get("v1r_sha") == V1R_SHA
    body = json.loads((X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").read_text(encoding="utf-8"))
    assert body["sha256"] == V1R_SHA


def test_model_artifact_sha(interim):
    assert interim.get("model_artifact_sha") == MODEL_ARTIFACT_SHA


def test_coefficients_scaler_features(interim):
    assert interim.get("coefficients_identity") is True
    assert interim.get("scaler_identity") is True
    assert interim.get("feature_order_identity") is True
    ser = load_model_artifact()
    assert list(ser["feature_order"]) == list(FEATURE_ORDER)


def test_no_refit(interim):
    assert interim.get("no_refit") is True


def test_cohort_tiebreak(interim):
    assert interim.get("cohort_ranking") is True
    assert interim.get("tie_break") == "symbol_ascending"


def test_pending_cap(interim):
    assert interim.get("pending_reservation") is True
    assert interim.get("cap_le_5") is True


def test_duplicate(interim):
    assert interim.get("duplicate_semantics") == "no_overlap_replace"


def test_fill_exit_wired(interim):
    assert interim.get("conservative_fill_wired") is True
    assert interim.get("fixed600_exit_wired") is True


def test_mutation_guard(interim):
    assert interim.get("mutation_guard") is True
    g = MutationGuard({"x": 1})
    with pytest.raises(RuntimeError):
        g.refuse("coefficients", [])


def test_boundary(interim):
    assert interim.get("prospective_date_boundary") == PROSPECTIVE_FROM
    assert FORBIDDEN_FROM == "20260810"


def test_20260810_unopened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True


def test_precommit_artifact(interim):
    p = OUT / "PROSPECTIVE_PRECOMMIT_V1.json"
    assert p.exists()
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body.get("sha256") == interim.get("precommit_sha")
    assert body.get("full_strategy_sha") == V1R_SHA
    assert body.get("prospective_start_boundary") == "20260810"


def test_artifacts(interim):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "prospective_ledger.xlsx").exists()
    assert interim.get("verdict") == "E1_X37_PROSPECTIVE_PRECOMMIT_READY"
