"""E1_X39B universe bridge tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x39b_universe_bridge import (
    FORBIDDEN_FROM,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    X36_RUN_ID,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x39b_universe_bridge"
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


def test_shas(interim):
    assert interim.get("v1r_sha") == V1R_SHA
    assert interim.get("model_artifact_sha") == MODEL_ARTIFACT_SHA
    assert interim.get("precommit_sha") == PRECOMMIT_SHA
    assert json.loads((X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").read_text(encoding="utf-8"))["sha256"] == V1R_SHA
    assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))["sha256"] == PRECOMMIT_SHA


def test_universe_contract(interim):
    assert interim.get("universe_contract") == UNIVERSE_CONTRACT
    assert interim.get("same_day_am_universe") is True
    assert interim.get("day_fixed_all16") is True
    assert interim.get("no_refresh_switching") is True
    assert interim.get("no_cluster_filter_on_test") is True


def test_no_future(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"
    assert interim.get("prospective_observer") == "NOT_STARTED"


def test_final_diag_not_evidence(interim):
    assert interim.get("final_diag_label") == "IN_SAMPLE_OPERATIONAL_DIAGNOSTIC_ONLY"
    assert interim.get("final_diag_not_evidence") is True


def test_old_artifacts_unchanged(interim):
    assert interim.get("old_precommit_unchanged") is True
    assert interim.get("strategy_mutation") is False
    assert interim.get("model_mutation") is False
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_binding_only_on_supported(interim):
    if interim.get("verdict") == "E1_X39B_CAUSAL_UNIVERSE_BRIDGE_SUPPORTED":
        assert interim.get("universe_binding") is True
        assert interim.get("universe_binding_sha")
        assert interim.get("new_precommit") is True
        assert (OUT / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").exists()
        assert (OUT / "PROSPECTIVE_PRECOMMIT_V1R_U1.json").exists()
    else:
        if interim.get("verdict") != "E1_X39B_OUTER_MODEL_IDENTITY_UNRESOLVED":
            assert interim.get("universe_binding") is False
            assert interim.get("new_precommit") is False


def test_ab_artifacts(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    assert interim.get("verdict") in (
        "E1_X39B_CAUSAL_UNIVERSE_BRIDGE_SUPPORTED",
        "E1_X39B_CAUSAL_UNIVERSE_BRIDGE_NOT_SUPPORTED",
        "E1_X39B_BRIDGE_REVIEW_REQUIRED",
        "E1_X39B_OUTER_MODEL_IDENTITY_UNRESOLVED",
    )


def test_x36_bind(interim):
    assert X36_RUN_ID == "e1x36_joint_20260808_203828_A"
