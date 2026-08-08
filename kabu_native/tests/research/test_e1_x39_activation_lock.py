"""E1_X39 activation lock tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x39_activation_lock import (
    FORBIDDEN_FROM,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    V1R_SHA,
    X38_RUN_ID,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x39_activation_lock"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_shas(interim):
    assert interim.get("v1r_sha") == V1R_SHA
    assert interim.get("model_artifact_sha") == MODEL_ARTIFACT_SHA
    assert interim.get("precommit_sha") == PRECOMMIT_SHA
    assert interim.get("x38_run_id") == X38_RUN_ID


def test_universe_provenance_recorded(interim):
    assert interim.get("historical_parity_explained") is True
    assert interim.get("rule_1000") is not None
    assert interim.get("rule_1430_1440") is not None
    # Binding unresolved is an allowed (and expected) outcome of this preflight
    assert interim.get("verdict") in (
        "E1_X39_PAPER_PRIMARY_ACTIVATION_READY",
        "E1_X39_UNIVERSE_BINDING_UNRESOLVED",
        "E1_X39_WARMUP_PARITY_UNRESOLVED",
        "E1_X39_OPERATIONAL_ACTIVATION_BLOCKED",
    )


def test_no_future_universe(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"
    assert interim.get("universe_prospective_mapping") is None or interim.get("universe_pass") is True


def test_warmup(interim):
    assert interim.get("parity_0905") is True
    assert interim.get("parity_1240") is True
    assert interim.get("six_feature_identity") is True
    assert interim.get("score_identity") is True
    assert interim.get("rank_identity") is True
    assert interim.get("admission_identity") is True


def test_notification_ooo(interim):
    assert interim.get("notification_accounting") is True
    assert interim.get("notification_cause")
    assert interim.get("ooo_pass") is True
    assert (interim.get("ooo_classification") or {}).get("synthetic_scheduling") == 9


def test_recovery(interim):
    assert interim.get("pending_recovery") is True
    assert interim.get("open_recovery") is True
    assert interim.get("past_target_recovery") is True


def test_shadow_1m_cap(interim):
    assert interim.get("shadow_isolation") is True
    assert interim.get("capital_1m_carry") is True
    assert interim.get("cap5") is True
    assert interim.get("pbv2") == "SHADOW_ONLY"
    assert interim.get("capital_1m") == "SHADOW_ONLY"
    assert interim.get("primary") == "V1R"


def test_safety(interim):
    assert interim.get("strategy_mutation") is False
    assert interim.get("opened_20260810") is False
    assert interim.get("submit_cancel_live") == "0/0/0"
    assert interim.get("prospective_observer") == "NOT_STARTED"


def test_ab_artifacts(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    # activation manifest only on full READY
    if interim.get("verdict") == "E1_X39_PAPER_PRIMARY_ACTIVATION_READY":
        assert (OUT / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json").exists()
        assert interim.get("activation_manifest") is True
    else:
        assert interim.get("activation_manifest") is False
        assert not (OUT / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json").exists()
