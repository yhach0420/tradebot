"""E1_X36R freeze integrity tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x36r_freeze_integrity import (
    ENTRY_SHA,
    EXIT_SHA,
    FINAL_FEATURES,
    FORBIDDEN_FROM,
    SOURCE_X36_RUN,
    V1_SHA,
)
from research.e1_x36r_freeze_integrity.provenance import document_provenance

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"
X36 = NATIVE / "results" / "research" / "e1_x36_joint_allocator"


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
    pytest.skip("no report")


def test_upstream_sha(interim):
    assert interim.get("entry_sha") == ENTRY_SHA
    assert interim.get("exit_sha") == EXIT_SHA
    assert interim.get("v1_sha") == V1_SHA
    assert interim.get("source_x36_run") == SOURCE_X36_RUN
    v1 = json.loads((X36 / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json").read_text(encoding="utf-8"))
    assert v1["sha256"] == V1_SHA


def test_no_20260810(interim):
    assert interim.get("opened_20260810") is False
    assert interim.get("contains_20260810") is False
    assert FORBIDDEN_FROM == "20260810"


def test_provenance(interim):
    assert interim.get("provenance_ok") is True
    p = document_provenance()
    assert p["provenance_ok"] is True
    assert p["post_hoc_human_choice"] is False


def test_coefficients(interim):
    assert interim.get("coefficients") is not None
    assert len(interim["coefficients"]) == len(FINAL_FEATURES)
    assert interim.get("intercept") is not None


def test_feature_order(interim):
    assert list(interim.get("feature_order") or []) == list(FINAL_FEATURES)


def test_preprocessing(interim):
    pre = interim.get("preprocessing") or {}
    assert pre.get("type") == "StandardScaler"
    assert len(pre.get("mean") or []) == len(FINAL_FEATURES)
    assert len(pre.get("scale") or []) == len(FINAL_FEATURES)


def test_training_fingerprint(interim):
    assert interim.get("training_panel_sha")
    assert interim.get("model_artifact_sha")


def test_score_admission_identity(interim):
    assert interim.get("score_replay_pass") is True
    assert interim.get("admission_identity") is True


def test_cross_fitted_identity(interim):
    assert interim.get("cross_fitted_identity_pass") is True
    s = interim.get("cross_fitted_summary") or {}
    assert s.get("admitted") == 689
    assert s.get("fills") == 148
    assert s.get("positive_days") == 10


def test_concentration_formula(interim):
    assert "gross_positive" in (interim.get("concentration_formula") or "") or "pnl>0" in (interim.get("concentration_formula") or "")
    assert interim.get("max_symbol_contrib_share") is not None
    assert interim.get("285A_share_of_total_net") is not None


def test_d1_d2(interim):
    assert "d1_285A" in interim
    assert "d2_285A" in interim
    assert interim["d1_285A"].get("remaining_total_pnl_yen") is not None
    assert interim["d2_285A"].get("total_pnl_yen") is not None


def test_loso_285a(interim):
    assert interim.get("loso_285A") is not None
    assert interim["loso_285A"].get("opp_bps") is not None


def test_no_symbol_feature(interim):
    assert interim.get("no_symbol_identity_feature") is True


def test_no_retune(interim):
    assert interim.get("no_model_retune") is True
    assert interim.get("no_runtime_change") is True


def test_submit_cancel_live(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True


def test_artifacts(report):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    if report.get("manifest_created"):
        assert (OUT / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").exists()
        assert report.get("verdict") == "E1_X36R_FULL_STRATEGY_EXACTLY_FROZEN"
        # V1 not overwritten
        assert (X36 / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json").exists()
        v1 = json.loads((X36 / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json").read_text(encoding="utf-8"))
        assert v1["sha256"] == V1_SHA
