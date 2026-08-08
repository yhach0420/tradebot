"""E1_X39E E2E dry-run tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x39e_e2e_dry_run import (
    ACTIVATION_SHA,
    DEMO_DAY,
    FORBIDDEN_FROM,
    V1R_SHA,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x39e_e2e_dry_run"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_verdict_ready(interim):
    assert interim.get("verdict") == "V1R_20260810_END_TO_END_DRY_RUN_READY"


def test_core_path(interim):
    assert interim.get("startup_pass") is True
    assert interim.get("binds_pass") is True
    assert interim.get("raw_push_feature") is True
    assert interim.get("score_rank") is True
    assert interim.get("admitted_cap_blocked") is True
    assert interim.get("fill") is True
    assert interim.get("expired") is True
    assert interim.get("qty_freshness_special_reject") is True
    assert interim.get("exit600") is True


def test_recovery_shadow(interim):
    assert interim.get("pending_recovery") is True
    assert interim.get("open_recovery") is True
    assert interim.get("past_target_recovery") is True
    assert interim.get("pbv2_isolation") is True
    assert interim.get("one_m_isolation") is True


def test_notify_hb_ledger(interim):
    assert interim.get("discord_queue") is True
    assert interim.get("heartbeat") is True
    assert interim.get("ledger_separation") is True


def test_protection(interim):
    assert interim.get("opened_20260810") is False
    assert interim.get("prospective_observer") == "NOT_STARTED"
    assert DEMO_DAY != FORBIDDEN_FROM
    assert interim.get("submit_cancel_live") == "0/0/0"
    assert interim.get("v1r_sha") == V1R_SHA
    assert interim.get("activation_sha") == ACTIVATION_SHA


def test_artifacts(interim):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    assert (OUT / "V1R_OPERATIONAL_REALIZABLE_TEST.jsonl").exists()
    assert (interim.get("ab_determinism") or {}).get("ok") is True
