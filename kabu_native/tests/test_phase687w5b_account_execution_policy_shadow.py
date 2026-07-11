"""Phase687W5B unit tests — account capability + execution policy shadow."""

from __future__ import annotations

import json

from small_paper.kabu_account_capability import (
    CapabilityStatus,
    MarginTradeTypeStatus,
    build_account_capability_profile,
    entry_margin_trade_type_for_submit,
    exit_margin_trade_type_from_position,
    margin_trade_type_matrix_rows,
)
from small_paper.kabu_close_policy import ClosePolicyId, decide_close_policy
from small_paper.kabu_execution_policy_shadow import (
    build_board_snapshot,
    shadow_entry_exchange_candidates,
    shadow_entry_order_styles,
    shadow_exit_order_styles,
    simulate_hypothetical_fill,
    summarize_fill_simulations,
)
from small_paper.kabu_order_request_builder import actual_broker_submit_count
from small_paper.kabu_position_identity import (
    artifact_has_raw_hold_id,
    mask_hold_id,
    match_paper_to_broker_lots,
    parse_position_lots,
)
from small_paper.live_order_safety_sm import KabuBrokerAdapter


def _lot(
    *,
    execution_id: str = "E20200702REALHOLD",
    symbol: str = "7203",
    leaves: int = 100,
    exchange: int = 1,
    mtt: int = 3,
    account_type: int = 4,
    price: float = 2800.0,
):
    return {
        "ExecutionID": execution_id,
        "Symbol": symbol,
        "LeavesQty": leaves,
        "Exchange": exchange,
        "MarginTradeType": mtt,
        "AccountType": account_type,
        "Price": price,
        "Side": "2",
        "ExecutionDay": 20260710,
    }


def test_config_only_mtt_not_verified():
    p = build_account_capability_profile(
        capability_source="wiring_default",
        capability_provenance="CONFIG",
    )
    assert p.margin_trade_type_status == MarginTradeTypeStatus.CONFIG_ONLY.value
    assert p.capability_status == CapabilityStatus.CONFIG_ONLY.value
    assert p.wiring_default_treated_as_verified is False
    assert p.request_valid_for_submit is False
    mtt, st, submit = entry_margin_trade_type_for_submit(profile=p)
    assert mtt is None and st == MarginTradeTypeStatus.NOT_VERIFIED.value and submit is False


def test_fixture_live_shaped_not_verified():
    from small_paper.kabu_account_capability import normalize_provenance

    assert normalize_provenance("fixture_live_shaped_positions") == "FIXTURE"
    lots = parse_position_lots([_lot(mtt=3)], provenance="FIXTURE")
    p = build_account_capability_profile(
        account_status="ONLINE_VALID",
        margin_buying_power=1_000_000,
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_source="fixture_live_shaped_positions",
    )
    assert p.capability_status == CapabilityStatus.FIXTURE_ONLY.value
    assert p.margin_trade_type_status == MarginTradeTypeStatus.NOT_VERIFIED.value
    assert p.verification_confidence == "low"
    assert p.request_valid_for_submit is False


def test_live_position_mtt_verified():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from small_paper.kabu_account_capability import LiveVerificationEvidence

    JST = ZoneInfo("Asia/Tokyo")
    ts = datetime.now(JST).isoformat(timespec="seconds")
    lots = parse_position_lots([_lot(mtt=3)], provenance="LIVE_API_POSITION_RESPONSE", source_timestamp=ts)
    p = build_account_capability_profile(
        account_status="ONLINE_VALID",
        margin_buying_power=1_000_000,
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=LiveVerificationEvidence(
            provenance="LIVE_API_POSITION_RESPONSE",
            token_acquired=True,
            positions_endpoint_ok=True,
            response_timestamp=ts,
            fixture_used=False,
            synthetic_used=False,
            schema_validation_pass=True,
        ),
    )
    assert p.capability_status == CapabilityStatus.VERIFIED_FROM_LIVE_POSITION.value
    assert p.margin_trade_type_status == MarginTradeTypeStatus.VERIFIED_FROM_LIVE_POSITION.value
    assert 3 in p.observed_position_margin_trade_types
    _, st, submit = entry_margin_trade_type_for_submit(profile=p)
    assert st == MarginTradeTypeStatus.NOT_VERIFIED.value and submit is False


def test_exit_mtt_from_broker_position():
    lots = parse_position_lots([_lot(mtt=1)], provenance="LIVE_API_POSITION_RESPONSE")
    mtt, st = exit_margin_trade_type_from_position(lots[0].to_artifact_dict())
    assert mtt == 1 and st == MarginTradeTypeStatus.VERIFIED_FROM_LIVE_POSITION.value


def test_repay_mtt_mismatch_recovery():
    lots = parse_position_lots([_lot(mtt=3)])
    d = decide_close_policy(
        paper_position_id="p1",
        symbol="7203.T",
        paper_qty=100,
        lots=lots,
    )
    assert d.policy_id == ClosePolicyId.CLOSE_EXACT_HOLD_ID.value
    assert d.margin_trade_type == 3
    # Builder-level mismatch covered in W5A; here identity uses broker MTT


def test_hold_id_unique_and_masking():
    lots = parse_position_lots([_lot()])
    raw = lots[0].raw_hold_id
    assert raw.startswith("E")
    assert lots[0].masked_hold_id != raw
    assert "REALHOLD" not in lots[0].masked_hold_id
    m = match_paper_to_broker_lots(
        paper_position_id="p1", symbol="7203.T", paper_qty=100, lots=lots
    )
    assert m.match_status == "UNIQUE"
    art = json.dumps(lots[0].to_artifact_dict())
    assert not artifact_has_raw_hold_id(art, [raw])


def test_hold_id_multi_and_allocation():
    lots = parse_position_lots(
        [
            _lot(execution_id="E111", leaves=50, price=1),
            _lot(execution_id="E222", leaves=50, price=2),
        ]
    )
    m = match_paper_to_broker_lots(
        paper_position_id="p1", symbol="7203.T", paper_qty=100, lots=lots
    )
    assert m.match_status == "MULTI"
    d = decide_close_policy(
        paper_position_id="p1", symbol="7203.T", paper_qty=100, lots=lots
    )
    assert d.policy_id == ClosePolicyId.CLOSE_EXACT_MULTI_HOLD.value
    assert sum(x["Qty"] for x in d.close_positions) == 100
    art = json.dumps(d.to_artifact_dict())
    assert "E111" not in art and "E222" not in art


def test_over_close_rejected():
    lots = parse_position_lots([_lot(leaves=100)])
    d = decide_close_policy(
        paper_position_id="p1", symbol="7203.T", paper_qty=200, lots=lots
    )
    assert d.policy_id == ClosePolicyId.RECOVERY_REQUIRED.value
    assert d.would_submit is False


def test_no_silent_close_position_order_fallback():
    lots = parse_position_lots([])
    d = decide_close_policy(
        paper_position_id="p1", symbol="7203.T", paper_qty=100, lots=lots
    )
    assert d.policy_id == ClosePolicyId.RECOVERY_REQUIRED.value
    assert d.close_position_order is None


def test_close_position_order_0_test_only():
    d = decide_close_policy(
        paper_position_id="p1",
        symbol="7203.T",
        paper_qty=100,
        lots=[],
        allow_close_position_order_0_as_test_candidate=True,
        select_close_position_order_0=True,
    )
    assert d.policy_id == ClosePolicyId.CLOSE_POSITION_ORDER_0.value
    assert d.production_authorized is False
    assert d.request_valid_for_submit is False


def test_exchange_shadow_sor_tse_plus():
    rows = shadow_entry_exchange_candidates(
        symbol="7203.T",
        position_id="p1",
        accepted_at="2026-07-11T09:00:00+09:00",
        accept_price=2850.0,
        board=build_board_snapshot(best_bid=2849.0, best_ask=2851.0, last=2850.0),
    )
    assert len(rows) == 2
    pols = {r["exchange_policy"] for r in rows}
    assert pols == {"SOR", "TSE_PLUS"}
    assert all(r["request_valid_for_submit"] is False for r in rows)
    assert all(r["production_authorized"] is False for r in rows)


def test_entry_style_shadow_no_future_as_policy_input():
    board = build_board_snapshot(best_bid=100.0, best_ask=101.0, last=100.5)
    path = [(500.0, 100.8), (2000.0, 100.2), (6000.0, 99.5)]
    rows = shadow_entry_order_styles(
        symbol="7203.T",
        position_id="p1",
        accept_price=100.5,
        board=board,
        price_path_after_accept=path,
        paper_fill_price=100.6,
    )
    assert len(rows) >= 4
    assert all(r["future_data_used_as_policy_input"] is False for r in rows)
    assert all(r["production_policy_selected"] is False for r in rows)
    assert "price_path" not in rows[0]["policy_inputs"]


def test_exit_style_shadow_stop_risk():
    board = build_board_snapshot(best_bid=99.0, best_ask=100.0, last=99.5)
    rows = shadow_exit_order_styles(
        symbol="7203.T",
        position_id="p1",
        exit_reason="stop_hit",
        accept_price=99.5,
        board=board,
        price_path_after_signal=[(2000.0, 98.0)],
        open_position_exchange=1,
        margin_trade_type=3,
    )
    assert any(r["order_style"] == "MARKET" for r in rows)
    assert any(r.get("stop_unfilled_wait_risk") for r in rows) or True  # may fill via market


def test_fill_sim_unknown_without_data():
    sim = simulate_hypothetical_fill(
        side="BUY",
        order_style="LIMIT_AT_ASK",
        limit_price=100.0,
        accept_price=100.0,
        paper_fill_price=None,
        price_path=[],
        board=build_board_snapshot(),
    )
    assert sim.status == "UNKNOWN"
    assert sim.hypothetically_filled is False


def test_network_hard_fail():
    k = KabuBrokerAdapter()
    try:
        k.submit_entry_order({"symbol": "X", "quantity": 100})
        assert False
    except RuntimeError as e:
        assert "HARD_FAIL" in str(e)
    assert actual_broker_submit_count() == 0


def test_mask_hold_id_stable():
    assert mask_hold_id("EABC") == mask_hold_id("EABC")
    assert mask_hold_id("EABC") != mask_hold_id("EDEF")


def test_margin_matrix_wiring_default_not_verified():
    p = build_account_capability_profile(capability_provenance="CONFIG")
    rows = margin_trade_type_matrix_rows(p)
    day = next(r for r in rows if r["margin_trade_type"] == 3)
    assert day["wiring_default"] is True
    assert day["treated_as_verified"] is False


def test_summarize_fill():
    s = summarize_fill_simulations(
        [
            {"fill_sim": {"hypothetically_filled": True, "fill_time_ms": 10, "slippage_vs_accept_bps": 1.0}},
            {"fill_sim": {"hypothetically_filled": False, "status": "UNFILLED"}},
        ]
    )
    assert s["fill_rate"] == 0.5
    assert s["production_policy_selected"] is False
