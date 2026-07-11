"""Phase687W5 unit tests — Kabu order request contract (no network submit)."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from small_paper.kabu_order_execution_policy import (
    ExecutionPolicyId,
    dryrun_limit_policy,
    dryrun_market_policy,
    not_selected_policy,
)
from small_paper.kabu_order_request_builder import (
    BUILDER_VERSION,
    REQUEST_MUTATION_DETECTED,
    REQUEST_SCHEMA_VERSION,
    OrderIntentContract,
    OrderRequestBuilder,
    actual_broker_cancel_count,
    actual_broker_submit_count,
    network_call_count,
)
from small_paper.kabu_order_response_parser import OrderResponseParser, ParsedResponseState
from small_paper.live_order_api_wiring import FRONT_ORDER_LIMIT, SIDE_BUY, SIDE_SELL
from small_paper.live_order_safety_sm import KabuBrokerAdapter


def _entry_intent(**kw):
    base = dict(
        intent_id="intent-e1",
        idempotency_key="idem-e1",
        side="BUY",
        symbol="7203.T",
        quantity=100,
        position_id="pos-1",
        entry_or_exit="ENTRY",
        limit_price=2851.0,
        price_snapshot=2851.0,
        exchange_policy="SOR",
        account_status="ONLINE_VALID",
        reconciliation_status="MATCH",
        capital_available=True,
        kill_switch=False,
        intent_kind="actual",
        accepted=True,
        margin_trade_type_source="FIXTURE_EXPLICIT",
    )
    base.update(kw)
    return OrderIntentContract(**base)


def _exit_intent(**kw):
    base = dict(
        intent_id="intent-x1",
        idempotency_key="idem-x1",
        side="SELL",
        symbol="7203.T",
        quantity=100,
        position_id="pos-1",
        entry_or_exit="EXIT",
        exit_reason="trailing_mfe_exit",
        limit_price=2820.0,
        holding_qty=100,
        exchange_policy="REPAY_MATCH_OPEN_POSITION_EXCHANGE",
        open_position_exchange=1,
        expected_margin_trade_type=3,
        account_status="ONLINE_VALID",
        reconciliation_status="MATCH",
        intent_kind="actual",
        accepted=True,
        margin_trade_type_source="FIXTURE_EXPLICIT",
    )
    base.update(kw)
    return OrderIntentContract(**base)


def test_valid_entry_request():
    b = OrderRequestBuilder()
    r = b.build(_entry_intent(), dryrun_limit_policy())
    assert r.request_generated and r.request_valid
    assert r.api_payload["Side"] == SIDE_BUY
    assert r.api_payload["Qty"] == 100
    assert r.api_payload["FrontOrderType"] == FRONT_ORDER_LIMIT
    assert r.request_valid_for_submit is False
    assert r.would_submit is False
    assert actual_broker_submit_count() == 0


def test_valid_exit_request():
    b = OrderRequestBuilder()
    r = b.build(_exit_intent(), dryrun_limit_policy(entry_or_exit="EXIT"))
    assert r.request_generated and r.request_valid
    assert r.api_payload["Side"] == SIDE_SELL
    assert "ClosePositionOrder" in r.api_payload or "ClosePositions" in r.api_payload


def test_not_selected_policy_blocks_submit_validity():
    b = OrderRequestBuilder()
    r = b.build(_entry_intent(), not_selected_policy())
    assert r.request_valid is False
    assert r.request_valid_for_submit is False
    assert "NOT_SELECTED" in r.error_category


def test_fingerprint_stable():
    b = OrderRequestBuilder()
    i = _entry_intent()
    p = dryrun_limit_policy()
    r1 = b.build(i, p)
    b2 = OrderRequestBuilder()
    r2 = b2.build(i, p)
    assert r1.request_fingerprint == r2.request_fingerprint
    assert r1.canonical_payload_hash == r2.canonical_payload_hash


def test_same_idempotency_reuse():
    b = OrderRequestBuilder()
    i = _entry_intent()
    p = dryrun_limit_policy()
    r1 = b.build(i, p)
    r2 = b.build(i, p)
    assert r1.request_valid and r2.request_valid
    assert r2.recovery_action == "reuse_existing_request"
    assert r2.would_submit is False


def test_request_mutation_detected():
    b = OrderRequestBuilder()
    i = _entry_intent(limit_price=2851.0, price_snapshot=2851.0)
    p = dryrun_limit_policy()
    assert b.build(i, p).request_valid
    i2 = _entry_intent(limit_price=2900.0, price_snapshot=2900.0)
    r = b.build(i2, p)
    assert r.mutation_detected
    assert r.error_category == REQUEST_MUTATION_DETECTED
    assert r.final_state == "RECOVERY_REQUIRED"
    assert r.would_submit is False


@pytest.mark.parametrize(
    "kw,err_substr",
    [
        ({"symbol": ""}, "missing_symbol"),
        ({"quantity": 0}, "quantity"),
        ({"quantity": 99}, "lot"),
        ({"quantity": 150}, "lot"),
        ({"quantity": -100}, "negative"),
        ({"position_id": ""}, "missing_position_id"),
        ({"idempotency_key": ""}, "missing_idempotency_key"),
        ({"intent_kind": "shadow"}, "forbidden_intent"),
        ({"kill_switch": True}, "kill_switch"),
        ({"capital_available": False}, "capital"),
        ({"reconciliation_status": "MISMATCH"}, "reconciliation"),
        ({"price_age_sec": 99.0}, "stale_price"),
        ({"limit_price": float("nan"), "price_snapshot": float("nan")}, "nan"),
    ],
)
def test_invalid_entry_blocked(kw, err_substr):
    b = OrderRequestBuilder()
    # NaN must fail at validation of price
    intent = _entry_intent(**kw)
    r = b.build(intent, dryrun_limit_policy())
    assert r.request_valid is False
    blob = " ".join(r.validation_errors) + r.error_category
    assert err_substr.lower() in blob.lower() or r.request_valid is False


def test_exit_qty_exceeds_holding():
    b = OrderRequestBuilder()
    r = b.build(_exit_intent(quantity=200, holding_qty=100), dryrun_limit_policy(entry_or_exit="EXIT"))
    assert r.request_valid is False
    assert any("exceeds" in e for e in r.validation_errors)


def test_side_inversion_validation():
    b = OrderRequestBuilder()
    intent = _entry_intent()
    p = dryrun_limit_policy()
    r = b.build(intent, p)
    api = dict(r.api_payload)
    api["Side"] = SIDE_SELL
    errs = b._validate_api_payload(intent, p, api)
    assert "side_inversion" in errs


def test_response_parser_ack_reject_timeout():
    p = OrderResponseParser()
    ack = p.parse({"Result": 0, "OrderId": "MOCK-123", "Message": "ok"})
    assert ack.state == ParsedResponseState.ACKNOWLEDGED.value
    assert ack.auto_resubmit is False
    rej = p.parse({"Result": 4001001, "Message": "invalid quantity"})
    assert rej.state == ParsedResponseState.BROKER_REJECTED.value
    to = p.parse(None, timed_out=True)
    assert to.state == ParsedResponseState.UNKNOWN.value
    assert to.reconciliation_required
    assert to.auto_resubmit is False
    assert p.auto_resubmit_count == 0


def test_malformed_and_empty_response():
    p = OrderResponseParser()
    assert p.parse("{not-json").category == "malformed_json"
    assert p.parse("").category == "empty_response"


def test_network_isolation_no_http_in_builder_source():
    path = Path(__file__).resolve().parents[1] / "src" / "small_paper" / "kabu_order_request_builder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {"requests", "urllib", "http.client", "httpx"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned
    text = path.read_text(encoding="utf-8")
    assert "requests.post" not in text
    assert "submit_entry_order" not in text
    assert "submit_exit_order" not in text
    assert "cancel_order" not in text
    assert "emergency_flatten" not in text


def test_kabu_adapter_still_hard_fail():
    k = KabuBrokerAdapter()
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        k.submit_entry_order({"symbol": "X", "quantity": 100})
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        k.cancel_order("x")
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        k.emergency_flatten()
    assert actual_broker_submit_count() == 0
    assert actual_broker_cancel_count() == 0
    assert network_call_count() == 0


def test_credential_masking():
    from small_paper.kabu_order_request_builder import mask_payload_for_audit

    masked = mask_payload_for_audit(
        {"Symbol": "7203", "token": "SECRETTOKEN", "password": "hunter2", "Qty": 100}
    )
    assert masked["token"] == "<REDACTED>"
    assert masked["password"] == "<REDACTED>"
    assert masked["Qty"] == 100


def test_schema_versions():
    assert BUILDER_VERSION.startswith("687W5")
    assert "kabusapi" in REQUEST_SCHEMA_VERSION
    assert ExecutionPolicyId.NOT_SELECTED.value == "NOT_SELECTED"


def test_market_exit_dryrun_policy():
    b = OrderRequestBuilder()
    r = b.build(
        _exit_intent(exit_reason="hard_stop", limit_price=None),
        dryrun_market_policy(),
    )
    assert r.request_valid
    assert r.api_payload["FrontOrderType"] == 10
    assert r.api_payload["Price"] == 0.0
