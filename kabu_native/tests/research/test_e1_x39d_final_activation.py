"""E1_X39D final activation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x39d_final_activation import (
    FORBIDDEN_FROM,
    MODEL_ARTIFACT_SHA,
    OLD_PRECOMMIT_SHA,
    PRECOMMIT_U1_SHA,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x39d_final_activation"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_shas(interim):
    assert interim.get("v1r_sha") == V1R_SHA
    assert interim.get("model_artifact_sha") == MODEL_ARTIFACT_SHA
    assert interim.get("universe_binding_sha") == UNIVERSE_BINDING_SHA
    assert interim.get("prospective_precommit_sha") == PRECOMMIT_U1_SHA
    assert json.loads((X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").read_text(encoding="utf-8"))["sha256"] == V1R_SHA
    assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))["sha256"] == OLD_PRECOMMIT_SHA


def test_preflight(interim):
    assert interim.get("binds_pass") is True
    assert interim.get("startup_preflight") is True
    assert interim.get("semantic_parity") is True
    assert interim.get("recovery") is True
    assert interim.get("discord") is True
    assert interim.get("heartbeat") is True
    assert interim.get("preflight_pass") is True


def test_roles_universe(interim):
    assert interim.get("primary") == "V1R"
    assert interim.get("pbv2") == "SHADOW_ONLY"
    assert interim.get("capital_1m") == "SHADOW_ONLY"
    assert interim.get("universe_contract") == UNIVERSE_CONTRACT


def test_safety(interim):
    assert interim.get("opened_20260810") is False
    assert interim.get("prospective_observer") == "NOT_STARTED"
    assert FORBIDDEN_FROM == "20260810"
    assert interim.get("strategy_mutation") is False
    assert interim.get("model_mutation") is False
    assert interim.get("universe_mutation") is False
    assert interim.get("submit_cancel_live") == "0/0/0"
    assert interim.get("old_precommit_unchanged") is True


def test_activation_manifest(interim):
    if interim.get("verdict") == "E1_X39D_V1R_PAPER_PRIMARY_ACTIVATION_READY":
        assert interim.get("activation_manifest_sha")
        p = OUT / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json"
        assert p.exists()
        body = json.loads(p.read_text(encoding="utf-8"))
        assert body.get("sha256") == interim.get("activation_manifest_sha")
        assert body.get("kind") == "operational_activation_manifest_not_strategy"
        assert body.get("opened_20260810") is False
        assert body.get("prospective_observer_started") is False


def test_ab_artifacts(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
