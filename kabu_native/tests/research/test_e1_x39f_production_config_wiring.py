"""E1_X39F production config wiring tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x39f_production_config_wiring"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if not p.exists():
        pytest.skip("no interim")
    return json.loads(p.read_text(encoding="utf-8"))


def test_verdict_ready(interim):
    assert interim["verdict"] == "V1R_20260810_PRODUCTION_CONFIG_WIRING_READY"


def test_yaml_pin(interim):
    assert interim["pin_match"] is True
    assert interim["dangerous_conflicts"] == 0


def test_effective_contracts(interim):
    assert interim["cap"] == 5
    assert interim["qty"] == 100
    assert interim["wait"] == 1.0
    assert "FIRST_VALID" in interim["exit"]
    assert "DAY_FIXED_AM" in interim["universe"]


def test_isolation_and_negatives(interim):
    assert interim["legacy_isolation"] is True
    assert interim["freshness_4sec"] is True
    assert interim["legacy_exit"] is True
    assert interim["universe_refresh"] is True
    assert interim["demo_push"] is True


def test_safety_protection(interim):
    assert interim["broker_reachable"] is False
    assert interim["submit_cancel_live"] == "0/0/0"
    assert interim["opened_20260810"] is False
    assert interim["prospective_observer"] == "NOT_STARTED"
    assert interim["strategy_mutation"] is False


def test_artifacts_and_regression(interim):
    assert (OUT / "report.json").exists()
    assert (OUT / "V1R_EFFECTIVE_RUNTIME_CONFIG_20260810.json").exists()
    assert (OUT / "audit.xlsx").exists()
    assert interim["x39d"] is True
    assert interim["x39e"] is True
    assert (interim.get("ab_determinism") or {}).get("ok") is True
