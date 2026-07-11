"""Phase687W6 — Production enablement governance gate fail-closed tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.kabu_order_request_builder import actual_broker_submit_count
from small_paper.live_order_safety_sm import KabuBrokerAdapter
from small_paper.production_enablement_gate import (
    ApprovalStatus,
    ProductionEnablementEvidence,
    empty_evidence,
    evaluate_production_enablement,
    sample_approval_artifact_not_authorized,
    technical_pass_evidence_not_authorized,
)
from small_paper.check_production_enablement_readiness import main as readiness_main

JST = ZoneInfo("Asia/Tokyo")


def _base(**kw) -> ProductionEnablementEvidence:
    e = technical_pass_evidence_not_authorized()
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _codes(result: dict) -> set[str]:
    return {b["code"] for b in result["blockers"]}


def test_empty_evidence_blocked():
    r = evaluate_production_enablement(empty_evidence())
    assert r["production_ready"] is False
    assert r["blocker_count"] > 0
    assert r["exit_code"] == 2  # soak first


def test_zero_of_three_soak_blocks():
    r = evaluate_production_enablement(_base(w4s_session_count=0, w4s_verdict="READONLY_SOAK_NOT_STARTED"))
    assert "W4S_SESSIONS_INSUFFICIENT" in _codes(r)
    assert r["exit_code"] == 2
    assert r["production_ready"] is False


def test_fixture_provenance_blocks():
    r = evaluate_production_enablement(
        _base(
            capability_status="FIXTURE_ONLY",
            capability_provenance="FIXTURE",
            live_api_provenance_confirmed=False,
            margin_trade_type_live_verified=False,
        )
    )
    assert "CAPABILITY_FIXTURE_OR_SYNTHETIC_OR_UNKNOWN" in _codes(r)
    assert r["production_ready"] is False


def test_zero_positions_mtt_unverified_blocks():
    r = evaluate_production_enablement(
        _base(
            capability_status="LIVE_API_NO_POSITIONS",
            capability_provenance="LIVE_API_POSITION_RESPONSE",
            live_api_provenance_confirmed=True,
            margin_trade_type_live_verified=False,
        )
    )
    assert "MARGIN_TRADE_TYPE_NOT_LIVE_VERIFIED" in _codes(r)
    assert r["exit_code"] == 3


def test_policy_not_selected_blocks():
    r = evaluate_production_enablement(
        _base(
            execution_policy_selected=False,
            approved_execution_policy_id="NOT_SELECTED",
            entry_exchange_policy_explicitly_approved=False,
        )
    )
    assert "EXECUTION_POLICY_NOT_SELECTED" in _codes(r)
    assert r["exit_code"] == 3


def test_approval_missing_blocks():
    r = evaluate_production_enablement(_base(approval_present=False, approval_status=None))
    assert "APPROVAL_MISSING" in _codes(r)
    assert r["exit_code"] == 3
    assert r["production_ready"] is False


def test_approval_expired_blocks():
    past = (datetime.now(JST) - timedelta(days=1)).isoformat(timespec="seconds")
    r = evaluate_production_enablement(
        _base(
            approval_status=ApprovalStatus.APPROVED.value,
            approval_expires_at=past,
            approval_present=True,
        )
    )
    assert "APPROVAL_EXPIRED" in _codes(r)
    assert r["production_ready"] is False


def test_sha_mismatch_blocks():
    r = evaluate_production_enablement(_base(config_sha256="aaa", expected_config_sha256="bbb"))
    assert "CONFIG_SHA_MISMATCH" in _codes(r)
    assert r["exit_code"] == 5


def test_duplicate_intent_blocks():
    r = evaluate_production_enablement(_base(duplicate_intent_created=1))
    assert "DUPLICATE_INTENT_CREATED" in _codes(r)
    assert r["exit_code"] == 4


def test_reservation_leak_blocks():
    r = evaluate_production_enablement(_base(reservation_leak=1))
    assert "RESERVATION_LEAK" in _codes(r)


def test_latency_not_measured_blocks():
    r = evaluate_production_enablement(_base(latency_p95_computed=False))
    assert "LATENCY_P95_NOT_COMPUTED" in _codes(r)


def test_reconciliation_mismatch_blocks():
    r = evaluate_production_enablement(_base(unexplained_reconciliation_mismatch=2))
    assert "RECONCILIATION_MISMATCH" in _codes(r)


def test_all_technical_pass_still_not_authorized():
    r = evaluate_production_enablement(technical_pass_evidence_not_authorized())
    assert r["technical_conditions_pass"] is True
    assert r["approval_status"] == "NOT_AUTHORIZED"
    assert r["production_ready"] is False
    assert r["exit_code"] == 0
    assert r["canary_execution_forbidden"] is True
    assert "APPROVAL_NOT_AUTHORIZED" in _codes(r)


def test_sample_approval_never_authorized():
    sample = sample_approval_artifact_not_authorized()
    assert sample["approval_status"] == "NOT_AUTHORIZED"
    assert sample["secrets_present"] is False
    assert sample["signing_keys_present"] is False


def test_cli_does_not_mutate_flags(tmp_path: Path):
    # Capture that CLI returns without flipping production_ready true
    code = readiness_main(["--demo-technical-pass"])
    assert code == 0
    # Flags remain conceptually false (CLI forces them)
    from small_paper.config import load_pilot_config

    root = Path(__file__).resolve().parents[1]
    cfg = load_pilot_config(
        root
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    assert cfg.live_trading_enabled is False
    assert cfg.order_enabled is False


def test_network_write_and_submit_zero():
    assert actual_broker_submit_count() == 0
    hard = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 100})
    except RuntimeError as exc:
        hard = "HARD_FAIL" in str(exc)
    assert hard
    try:
        KabuBrokerAdapter().cancel_order("OID")
        assert False, "cancel must HARD_FAIL"
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)


def test_boolean_defaults_fail_closed():
    e = ProductionEnablementEvidence(
        w4s_session_count=3,
        w4s_verdict="READONLY_SOAK_READY",
        readonly_success_sessions=1,
        # all other booleans default False → many blockers
    )
    r = evaluate_production_enablement(e)
    assert r["blocker_count"] > 5
    assert r["production_ready"] is False


def test_stale_verification_blocks():
    r = evaluate_production_enablement(_base(verification_stale=True))
    assert "VERIFICATION_STALE" in _codes(r)


def test_write_adapter_present_blocks():
    r = evaluate_production_enablement(_base(write_adapter_present=True))
    assert "WRITE_ADAPTER_PRESENT" in _codes(r)
