"""Phase687W5A — Official sendorder contract reconciliation tests."""

from __future__ import annotations

from small_paper.kabu_order_execution_policy import dryrun_limit_policy, dryrun_market_policy
from small_paper.kabu_order_request_builder import OrderIntentContract, OrderRequestBuilder
from small_paper.kabu_sendorder_contract import (
    EXCHANGE_SOR,
    EXCHANGE_TSE,
    EXCHANGE_TSE_PLUS,
    ClosePositionMode,
    ExchangePolicy,
    FundTypeMode,
    TransactionType,
    load_official_contract,
)
from small_paper.live_order_safety_sm import KabuBrokerAdapter


def _entry(**kw):
    base = dict(
        intent_id="e",
        idempotency_key="k",
        side="BUY",
        symbol="7203.T",
        quantity=100,
        position_id="p1",
        entry_or_exit="ENTRY",
        limit_price=2851.0,
        price_snapshot=2851.0,
        exchange_policy=ExchangePolicy.SOR.value,
        margin_trade_type_source="FIXTURE_EXPLICIT",
        fund_type_mode=FundTypeMode.OMIT_AUTO_11.value,
    )
    base.update(kw)
    return OrderIntentContract(**base)


def _exit(**kw):
    base = dict(
        intent_id="x",
        idempotency_key="kx",
        side="SELL",
        symbol="7203.T",
        quantity=100,
        position_id="p1",
        entry_or_exit="EXIT",
        exit_reason="hard_stop",
        holding_qty=100,
        exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
        open_position_exchange=EXCHANGE_TSE,
        expected_margin_trade_type=3,
        margin_trade_type=3,
        close_position_mode=ClosePositionMode.CLOSE_POSITION_ORDER.value,
        margin_trade_type_source="FIXTURE_EXPLICIT",
    )
    base.update(kw)
    return OrderIntentContract(**base)


def test_margin_new_sor_valid():
    r = OrderRequestBuilder().build(_entry(idempotency_key="sor1"), dryrun_limit_policy())
    assert r.request_valid
    assert r.api_payload["Exchange"] == EXCHANGE_SOR
    assert r.api_payload["Side"] == "2"
    assert r.api_payload["CashMargin"] == 2
    assert r.api_payload["DelivType"] == 0
    assert "ClosePositions" not in r.api_payload
    assert "ClosePositionOrder" not in r.api_payload
    assert r.fund_type_audit["intentional_omission"] is True


def test_margin_new_tse_plus_valid():
    r = OrderRequestBuilder().build(
        _entry(idempotency_key="tp1", exchange_policy=ExchangePolicy.TSE_PLUS.value),
        dryrun_limit_policy(),
    )
    assert r.request_valid
    assert r.api_payload["Exchange"] == EXCHANGE_TSE_PLUS


def test_normal_new_exchange_1_invalid():
    r = OrderRequestBuilder().build(
        _entry(idempotency_key="tse1", exchange_policy=ExchangePolicy.TSE_MAINTENANCE_EXCEPTION.value),
        dryrun_limit_policy(),
    )
    # maintenance exception IS allowed — separate test; force raw TSE without exception via SOR then mutate
    assert r.request_valid  # maintenance exception path
    # Without exception policy, Exchange=1 must fail
    r2 = OrderRequestBuilder().build(
        _entry(idempotency_key="bad1", exchange_policy=ExchangePolicy.NOT_SELECTED.value),
        dryrun_limit_policy(),
    )
    assert r2.request_valid is False
    assert r2.request_valid_for_submit is False


def test_maintenance_exception_allows_tse():
    r = OrderRequestBuilder().build(
        _entry(idempotency_key="maint", exchange_policy=ExchangePolicy.TSE_MAINTENANCE_EXCEPTION.value),
        dryrun_limit_policy(),
    )
    assert r.request_valid
    assert r.api_payload["Exchange"] == EXCHANGE_TSE


def test_repay_exchange_matches_position():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="repay1", open_position_exchange=EXCHANGE_TSE),
        dryrun_market_policy(),
    )
    assert r.request_valid
    assert r.api_payload["Exchange"] == EXCHANGE_TSE


def test_repay_exchange_unknown_recovery():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="repayu", open_position_exchange=None),
        dryrun_market_policy(),
    )
    assert r.request_valid is False
    assert r.final_state == "RECOVERY_REQUIRED"


def test_repay_silent_sor_forbidden():
    r = OrderRequestBuilder().build(
        _exit(
            idempotency_key="repaysor",
            exchange_policy=ExchangePolicy.SOR.value,
            open_position_exchange=EXCHANGE_TSE,
        ),
        dryrun_market_policy(),
    )
    assert r.request_valid is False


def test_cash_buy_rejected():
    r = OrderRequestBuilder().build(
        _entry(idempotency_key="cashb", transaction_type=TransactionType.CASH_BUY.value),
        dryrun_limit_policy(),
    )
    assert r.request_valid is False
    assert "cash_order_NOT_IMPLEMENTED" in r.error_category


def test_cash_sell_rejected():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="cashs", transaction_type=TransactionType.CASH_SELL.value),
        dryrun_market_policy(),
    )
    assert r.request_valid is False


def test_fund_type_omit_audited():
    r = OrderRequestBuilder().build(_entry(idempotency_key="ft1"), dryrun_limit_policy())
    assert r.request_valid
    assert "FundType" not in r.api_payload
    assert r.fund_type_audit["intentional_omission"] is True
    assert r.fund_type_audit["auto_11_assumed"] is True


def test_fund_type_explicit_11():
    r = OrderRequestBuilder().build(
        _entry(idempotency_key="ft2", fund_type_mode=FundTypeMode.EXPLICIT_11.value),
        dryrun_limit_policy(),
    )
    assert r.request_valid
    assert r.api_payload["FundType"] == "11"


def test_fund_type_invalid_rejected():
    r = OrderRequestBuilder().build(
        _entry(idempotency_key="ft3", fund_type_mode="INVALID"),
        dryrun_limit_policy(),
    )
    assert r.request_valid is False


def test_close_positions_only():
    r = OrderRequestBuilder().build(
        _exit(
            idempotency_key="cp1",
            close_position_mode=ClosePositionMode.CLOSE_POSITIONS.value,
            hold_id="MOCK-E20200702TEST",
        ),
        dryrun_market_policy(),
    )
    assert r.request_valid
    assert "ClosePositions" in r.api_payload
    assert "ClosePositionOrder" not in r.api_payload


def test_close_position_order_only():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="cpo1", close_position_mode=ClosePositionMode.CLOSE_POSITION_ORDER.value),
        dryrun_market_policy(),
    )
    assert r.request_valid
    assert "ClosePositionOrder" in r.api_payload
    assert "ClosePositions" not in r.api_payload


def test_close_both_invalid():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="both", close_position_mode=ClosePositionMode.BOTH_FORBIDDEN.value),
        dryrun_market_policy(),
    )
    assert r.request_valid is False
    assert any("both" in e for e in r.validation_errors)


def test_close_neither_invalid():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="neither", close_position_mode=ClosePositionMode.NEITHER.value),
        dryrun_market_policy(),
    )
    assert r.request_valid is False
    assert any("neither" in e for e in r.validation_errors)


def test_market_price_rules():
    ok = OrderRequestBuilder().build(
        _exit(idempotency_key="mktok"),
        dryrun_market_policy(),
    )
    assert ok.request_valid
    assert ok.api_payload["FrontOrderType"] == 10
    assert ok.api_payload["Price"] == 0.0

    bad = OrderRequestBuilder().build(_entry(idempotency_key="limok"), dryrun_limit_policy())
    assert bad.request_valid
    api = dict(bad.api_payload)
    api["FrontOrderType"] = 10
    api["Price"] = 100.0
    errs = OrderRequestBuilder()._validate_api_payload(_entry(), dryrun_limit_policy(), api)
    assert "market_order_with_invalid_price_field" in errs

    api2 = dict(bad.api_payload)
    api2["Price"] = 0
    errs2 = OrderRequestBuilder()._validate_api_payload(_entry(), dryrun_limit_policy(), api2)
    assert "limit_order_without_price" in errs2


def test_margin_trade_type_mismatch():
    r = OrderRequestBuilder().build(
        _exit(idempotency_key="mtt", margin_trade_type=3, expected_margin_trade_type=1),
        dryrun_market_policy(),
    )
    assert r.request_valid is False
    assert "MarginTradeType_mismatch" in r.error_category


def test_official_snapshot_loads():
    c = load_official_contract()
    assert c["contract_version"].startswith("687W5A")
    assert "Exchange" in c["fields"]


def test_hard_fail_and_zero_submit():
    from small_paper.kabu_order_request_builder import actual_broker_submit_count

    k = KabuBrokerAdapter()
    try:
        k.submit_entry_order({"symbol": "X", "quantity": 100})
        assert False
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)
    assert actual_broker_submit_count() == 0
