"""Phase687W5B1 — Capability provenance hardening tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from small_paper.kabu_account_capability import (
    CapabilityProvenance,
    CapabilityStatus,
    LiveVerificationEvidence,
    MarginTradeTypeStatus,
    build_account_capability_profile,
    normalize_provenance,
    soak_provenance_fields,
)
from small_paper.kabu_position_identity import artifact_has_raw_hold_id, parse_position_lots
from small_paper.kabu_order_request_builder import actual_broker_submit_count
from small_paper.live_order_safety_sm import KabuBrokerAdapter

JST = ZoneInfo("Asia/Tokyo")


def _lot(**kw):
    base = {
        "ExecutionID": "E20260711LIVEHOLD99",
        "Symbol": "7203",
        "LeavesQty": 100,
        "Exchange": 1,
        "MarginTradeType": 3,
        "AccountType": 4,
        "Price": 2800.0,
        "Side": "2",
        "ExecutionDay": 20260711,
    }
    base.update(kw)
    return base


def _live_evidence(**kw):
    ts = datetime.now(JST).isoformat(timespec="seconds")
    base = dict(
        provenance=CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value,
        token_acquired=True,
        positions_endpoint_ok=True,
        response_timestamp=ts,
        fixture_used=False,
        synthetic_used=False,
        schema_validation_pass=True,
    )
    base.update(kw)
    return LiveVerificationEvidence(**base)


def test_normalize_fixture_live_shaped():
    assert normalize_provenance("fixture_live_shaped_positions") == "FIXTURE"
    assert normalize_provenance("FIXTURE") == "FIXTURE"


def test_fixture_not_live_verified():
    lots = parse_position_lots([_lot()], provenance="FIXTURE")
    p = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_source="fixture_live_shaped_positions",
    )
    assert p.capability_status == CapabilityStatus.FIXTURE_ONLY.value
    assert p.margin_trade_type_status == MarginTradeTypeStatus.NOT_VERIFIED.value
    assert p.verification_confidence == "low"
    assert p.request_valid_for_submit is False
    assert p.production_authorized is False
    assert p.margin_trade_type_live_verified is False


def test_synthetic_not_live_verified():
    lots = parse_position_lots([_lot()], provenance="SYNTHETIC")
    p = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_provenance="SYNTHETIC",
    )
    assert p.capability_status == CapabilityStatus.SYNTHETIC_ONLY.value
    assert p.margin_trade_type_status == MarginTradeTypeStatus.NOT_VERIFIED.value


def test_config_mtt3_not_verified():
    p = build_account_capability_profile(capability_provenance="CONFIG")
    assert p.capability_status == CapabilityStatus.CONFIG_ONLY.value
    assert p.wiring_default_treated_as_verified is False


def test_live_positions_only_verified():
    ts = datetime.now(JST).isoformat(timespec="seconds")
    lots = parse_position_lots(
        [_lot()], provenance="LIVE_API_POSITION_RESPONSE", source_timestamp=ts
    )
    p = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=_live_evidence(response_timestamp=ts),
    )
    assert p.capability_status == CapabilityStatus.VERIFIED_FROM_LIVE_POSITION.value
    assert p.margin_trade_type_live_verified is True


def test_live_zero_positions_not_mtt_verified():
    ts = datetime.now(JST).isoformat(timespec="seconds")
    p = build_account_capability_profile(
        position_lots=[],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=_live_evidence(response_timestamp=ts),
    )
    assert p.capability_status == CapabilityStatus.LIVE_API_NO_POSITIONS.value
    assert p.margin_trade_type_status == MarginTradeTypeStatus.NOT_VERIFIED.value


def test_malformed_live_response_not_verified():
    ts = datetime.now(JST).isoformat(timespec="seconds")
    bad = _lot()
    del bad["MarginTradeType"]
    lots = parse_position_lots([bad], provenance="LIVE_API_POSITION_RESPONSE", source_timestamp=ts)
    p = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=_live_evidence(response_timestamp=ts, schema_validation_pass=False),
    )
    assert p.capability_status == CapabilityStatus.NOT_VERIFIED.value
    assert "schema" in p.verification_failure_reason or "lot_missing" in p.verification_failure_reason or p.verification_failure_reason


def test_stale_live_response_not_verified():
    old = (datetime.now(JST) - timedelta(hours=2)).isoformat(timespec="seconds")
    lots = parse_position_lots(
        [_lot()], provenance="LIVE_API_POSITION_RESPONSE", source_timestamp=old
    )
    p = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=_live_evidence(response_timestamp=old, max_age_sec=300),
    )
    assert p.capability_status == CapabilityStatus.NOT_VERIFIED.value
    assert p.verification_failure_reason == "stale_live_response"


def test_fixture_live_mixed_conflict():
    live = parse_position_lots([_lot(ExecutionID="E1")], provenance="LIVE_API_POSITION_RESPONSE")
    fix = parse_position_lots([_lot(ExecutionID="E2", Symbol="9984")], provenance="FIXTURE")
    mixed = [live[0].to_artifact_dict(), fix[0].to_artifact_dict()]
    p = build_account_capability_profile(
        position_lots=mixed,
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=_live_evidence(),
    )
    assert p.capability_status == CapabilityStatus.CONFLICT.value


def test_raw_holdid_not_in_artifact():
    lots = parse_position_lots([_lot()], provenance="FIXTURE")
    art = json.dumps(lots[0].to_artifact_dict())
    assert not artifact_has_raw_hold_id(art, [lots[0].raw_hold_id])
    assert lots[0].to_artifact_dict()["hold_id_live_verified"] is False


def test_submit_flags_remain_false():
    p = build_account_capability_profile(capability_provenance="FIXTURE")
    assert p.request_valid_for_submit is False
    k = KabuBrokerAdapter()
    try:
        k.submit_entry_order({"symbol": "X", "quantity": 100})
        assert False
    except RuntimeError as e:
        assert "HARD_FAIL" in str(e)
    assert actual_broker_submit_count() == 0


def test_soak_provenance_fields_present():
    p = build_account_capability_profile(capability_provenance="FIXTURE")
    fields = soak_provenance_fields(p)
    for k in (
        "capability_provenance",
        "fixture_used",
        "synthetic_used",
        "live_account_response_received",
        "live_position_response_received",
        "live_position_count",
        "margin_trade_type_live_verified",
        "exchange_live_verified",
        "hold_id_live_verified",
        "verified_response_time",
        "verification_failure_reason",
    ):
        assert k in fields
    assert fields["fixture_used"] is True
    assert fields["margin_trade_type_live_verified"] is False
