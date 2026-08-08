"""E1_X38 operational wiring tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x38_operational_wiring import (
    FORBIDDEN_FROM,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    V1R_SHA,
)
from research.e1_x38_operational_wiring.notify_queue import NonBlockingNotifyQueue
from research.e1_x38_operational_wiring.shadow import ShadowIsolationGuard

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x38_operational_wiring"
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"


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
    assert json.loads((X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").read_text(encoding="utf-8"))["sha256"] == V1R_SHA
    assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))["sha256"] == PRECOMMIT_SHA


def test_identities(interim):
    assert interim.get("semantic_parity_pass") is True
    assert interim.get("feature_identity") is True
    assert interim.get("score_identity") is True
    assert interim.get("rank_identity") is True
    assert interim.get("admission_identity") is True


def test_t0_future_free(interim):
    assert interim.get("t0_snapshot_future_free") is True


def test_pending_expiry_late(interim):
    assert interim.get("pending_reservation") is True
    assert interim.get("expiry_t0_plus_1s") is True
    assert interim.get("late_decision") is True


def test_non_blocking(interim):
    assert interim.get("discord_non_blocking") is True
    assert interim.get("file_io_non_blocking") is True
    q = NonBlockingNotifyQueue()
    r = q.enqueue("ENTRY", {"x": 1})
    assert r["blocking"] is False
    q.stop()


def test_shadow_isolation(interim):
    assert interim.get("pbv2_shadow_isolation") is True
    assert interim.get("capital_1m_shadow_isolation") is True
    g = ShadowIsolationGuard()
    with pytest.raises(RuntimeError):
        g.record_pbv2_attempt_primary_slot()


def test_fixed600_heartbeat(interim):
    assert interim.get("fixed600") is True
    assert interim.get("heartbeat") is True


def test_20260810_unopened(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"


def test_no_live(interim):
    assert interim.get("no_runtime_live_order") is True
    assert interim.get("submit_cancel_live") == "0/0/0"
    assert interim.get("strategy_mutation") is False


def test_ab(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True


def test_latency_fields(interim):
    for k in ("latency_p50", "latency_p90", "latency_p95", "latency_p99", "latency_max"):
        assert interim.get(k) is not None


def test_artifacts(interim):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    assert interim.get("verdict") in (
        "E1_X38_OPERATIONAL_WIRING_READY",
        "E1_X38_WIRING_LATENCY_REQUIRES_OPTIMIZATION",
    )
