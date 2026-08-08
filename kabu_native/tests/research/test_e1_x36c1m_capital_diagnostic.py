"""E1_X36C1M capital diagnostic tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.e1_x36c1m_capital_diagnostic import (
    FORBIDDEN_FROM,
    INITIAL_CASH_PRIMARY,
    PRECOMMIT_SHA,
    V1R_SHA,
)
from research.e1_x36c1m_capital_diagnostic.capital_replay import simulate_joint_capital

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x36c1m_capital_diagnostic"
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_unlimited_identity(interim):
    assert interim.get("unlimited_identity_pass") is True


def test_initial_cash(interim):
    assert interim.get("initial_cash") == INITIAL_CASH_PRIMARY


def test_qty_no_fractional(interim):
    assert interim.get("qty") == 100
    assert interim.get("no_fractional") is True


def test_pending_cash(interim):
    assert interim.get("pending_cash_reservation") is True
    assert interim.get("cash_never_negative") is True


def test_cap(interim):
    assert interim.get("open_pending_le_5") is True


def test_canonical_exit(interim):
    assert interim.get("canonical_fixed600") is True


def test_no_refit(interim):
    assert interim.get("no_model_refit") is True


def test_v1r_precommit_unchanged(interim):
    assert interim.get("v1r_unchanged") is True
    assert interim.get("precommit_unchanged") is True
    v1 = json.loads((X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").read_text(encoding="utf-8"))
    assert v1["sha256"] == V1R_SHA
    pre = json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))
    assert pre["sha256"] == PRECOMMIT_SHA


def test_20260810(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"


def test_submit_cancel_live(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab(interim):
    assert (interim.get("ab_determinism") or {}).get("ok") is True


def test_capital_skip_to_next():
    """High-priced first, cheap second — both same clock; first capital-blocked, second admits."""
    t0 = 1e9
    evs = [
        {
            "date": "20260721", "symbol": "EXP", "session": "AM",
            "signal_time": t0, "filled": False, "limit_price": 20000.0, "bid0": 20000.0,
            # score high via alloc — use order by injecting score through score_fn
        },
        {
            "date": "20260721", "symbol": "CHE", "session": "AM",
            "signal_time": t0, "filled": False, "limit_price": 1000.0, "bid0": 1000.0,
        },
    ]
    def sfn(e):
        return 0.9 if e["symbol"] == "EXP" else 0.5
    sim = simulate_joint_capital(evs, score_fn=sfn, initial_cash=1_000_000.0)
    by = {e["symbol"]: e for e in sim["events"]}
    assert by["EXP"].get("CAPITAL_BLOCKED") is True
    assert by["CHE"].get("admitted") is True


def test_artifacts(interim):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    assert interim.get("verdict") in (
        "E1_X36C1M_CAPITAL_CONSTRAINED_POSITIVE",
        "E1_X36C1M_CAPITAL_CONSTRAINED_WEAK",
        "E1_X36C1M_CAPITAL_CONSTRAINED_NEGATIVE",
    )
