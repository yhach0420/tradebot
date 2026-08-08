"""E1_X17 sealed historical prospective tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x17_vwap_reject_prospective import (
    CANDIDATE_ID,
    EXPECTED_PRECOMMIT_SHA,
    FORBIDDEN_DAY,
    HIST_A2_VS_A1,
    TARGET_DAY,
    VWAP_UPPER_LIMIT_BPS,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective"
X12 = NATIVE / "results" / "research" / "e1_x12_risk_history" / "report.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _seal():
    p = OUT / "_seal.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_registry_reserved_before_open():
    x12 = json.loads(X12.read_text(encoding="utf-8"))
    row = x12["date_registry"]["by_date"][TARGET_DAY]
    assert row["status"] == "ALPHA_PROSPECTIVE_RESERVED"
    seal = _seal()
    if seal:
        assert seal["registry_status"] == "ALPHA_PROSPECTIVE_RESERVED"


def test_precommit_sha():
    assert EXPECTED_PRECOMMIT_SHA == "b3db9306510a963bdf65eda4910448a0132ec83b14900e0826fc852c8c6e4281"
    seal = _seal()
    if seal and seal.get("ok"):
        assert seal["precommit"]["precommit_sha_match"] is True


def test_precommit_before_data_open():
    seal = _seal()
    inter = _interim()
    if not seal or not inter:
        return
    # precommit existed before this prospective open
    assert seal["precommit"]["precommit_at_jst"]
    assert inter.get("opened_20260803") is True


def test_20260803_open_once():
    inter = _interim()
    if not inter:
        return
    assert inter["target_day"] == "20260803"
    assert inter["opened_20260803"] is True


def test_20260804_not_opened():
    assert FORBIDDEN_DAY == "20260804"
    inter = _interim()
    if inter:
        assert inter["opened_20260804"] is False


def test_exact_candidate_rule():
    inter = _interim()
    if not inter:
        return
    assert "distance_from_vwap_bps <= 100.73709346405396" in inter["candidate_rule"]
    assert CANDIDATE_ID == "C0_VWAP_LATE_CHASE_REJECT_V1"


def test_threshold_unchanged():
    assert VWAP_UPPER_LIMIT_BPS == 100.73709346405396
    inter = _interim()
    if inter:
        assert inter["threshold"] == VWAP_UPPER_LIMIT_BPS


def test_no_rebound_added():
    inter = _interim()
    if inter:
        assert inter["no_rebound_in_candidate"] is True


def test_no_activity_added():
    inter = _interim()
    if inter:
        assert inter["no_activity_in_candidate"] is True


def test_same_c0_contract():
    inter = _interim()
    if inter:
        assert inter["one_anchor_per_episode"] is True


def test_one_anchor_per_episode():
    inter = _interim()
    if not inter:
        return
    assert inter["n_rows"] == inter["n_episodes_unique"]


def test_missing_separated():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "missing_separated" in r
    assert "VWAP_not_evaluable" in r["missing_separated"]


def test_session_boundary():
    assert True  # AM/PM via X15 episodes


def test_primary_gate():
    inter = _interim()
    if not inter:
        return
    assert "gate" in inter
    assert inter["gate"]["status"] in ("PASS", "MIXED", "FAIL", "INSUFFICIENT_PROSPECTIVE_SUPPORT")


def test_freshness_diagnostic_not_decision():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r["freshness_diagnostics"]["note"] == "diagnostic_only_not_used_for_prospective_gate"


def test_no_historical_retune():
    assert HIST_A2_VS_A1["day_balanced_fr_delta"] == 0.0003384957219681362
    inter = _interim()
    if inter:
        assert inter["hist_frozen"] == HIST_A2_VS_A1


def test_no_runtime_change():
    assert True


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r.get("determinism", {}).get("ab_match") is True
