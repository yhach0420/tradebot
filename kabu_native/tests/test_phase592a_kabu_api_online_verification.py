"""Phase592A offline payload/capacity tests."""

from research.phase592a_kabu_api_online_verification import (
    _capacity_rows,
    _validate_stop_exit_payloads,
)
from small_paper.live_order_api_wiring import build_entry_sendorder_payload


def test_entry_payload_has_required_fields():
    p = build_entry_sendorder_payload(symbol="7203.T", exchange=1, limit_price=2850.0)
    for fld in (
        "Symbol",
        "Exchange",
        "SecurityType",
        "Side",
        "CashMargin",
        "MarginTradeType",
        "Qty",
        "FrontOrderType",
        "Price",
    ):
        assert fld in p


def test_stop_exit_payloads_valid():
    _, ok = _validate_stop_exit_payloads()
    assert ok


def test_capacity_cap2_with_sufficient_margin():
    rows = _capacity_rows(
        cash=500_000,
        margin_wallet=500_000,
        buying_power=500_000,
        assumed_price=3000.0,
    )
    cap2 = next(r for r in rows if r["scenario"] == "CAP=2")
    assert cap2["operational_ok"] is True
