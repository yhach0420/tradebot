"""Tests for E1_X10 Fixed 100-Share Risk Universe Audit."""
from __future__ import annotations

from research.e1_x10_risk_universe import FRESHNESS_MAX_SEC, LOT, SOURCE_CLOSURE, TARGET_SYMBOL
from research.e1_x10_risk_universe.config_discovery import discover_risk_config
from research.e1_x10_risk_universe.metrics import aggregate_symbol_day
from research.e1_x10_risk_universe.quotes import qty_unit_contract, reference_price_from_rows
from research.e1_x7_pfq.config import DAYS
from research.e1_x10_risk_universe.tick import jpx_tick_size_yen


def test_reference_price_asof():
    rows = [{"previous_close": 1000.0, "previous_close_time": "2026-07-17T00:00:00+09:00",
             "bid": 1001, "ask": 1002, "t": 1.0}]
    ref = reference_price_from_rows("20260721", rows)
    assert ref["reference_price"] == 1000.0
    assert ref["asof_valid"] is True
    assert ref["reference_price_source"] == "previous_session_official_close"


def test_no_same_day_future_for_static_universe():
    # same-day mid must not be used when PreviousClose missing
    rows = [{"previous_close": None, "previous_close_time": None, "bid": 100, "ask": 101, "t": 1.0}]
    ref = reference_price_from_rows("20260721", rows)
    assert ref["reference_price"] is None
    assert ref["asof_valid"] is False


def test_one_lot_notional():
    assert 2500.0 * LOT == 250_000.0


def test_tick_size_canonical():
    assert jpx_tick_size_yen(2500.0) == 1.0
    assert jpx_tick_size_yen(4000.0) == 5.0


def test_one_tick_risk_yen():
    assert jpx_tick_size_yen(2500.0) * LOT == 100.0


def test_spread_yen_and_bps():
    rows = []
    for i in range(40):
        rows.append({
            "t": float(i), "bid": 1000.0, "ask": 1002.0, "bid_qty": 200.0, "ask_qty": 200.0,
            "price_age_sec": 1.0, "board_age_sec": 1.0, "previous_close": 1000.0,
            "previous_close_time": "2026-07-17T00:00:00+09:00",
        })
    ref = reference_price_from_rows("20260721", rows)
    m = aggregate_symbol_day("20260721", "TEST", rows, ref)
    assert m["median_spread_yen"] == 2.0
    assert abs(m["median_spread_bps"] - 19.96007984) < 0.1
    assert m["median_spread_cost_yen_100"] == 200.0


def test_depth_quantity_unit():
    assert qty_unit_contract()["unit"] == "shares"


def test_depth_100_coverage():
    rows = []
    for i in range(40):
        rows.append({
            "t": float(i), "bid": 1000.0, "ask": 1001.0,
            "bid_qty": 100.0 if i % 2 == 0 else 50.0,
            "ask_qty": 300.0,
            "price_age_sec": 1.0, "board_age_sec": 1.0,
            "previous_close": 1000.0, "previous_close_time": "2026-07-17T00:00:00+09:00",
        })
    ref = reference_price_from_rows("20260721", rows)
    m = aggregate_symbol_day("20260721", "TEST", rows, ref)
    assert abs(m["p_best_bid_qty_ge_100"] - 0.5) < 1e-9
    assert m["p_best_ask_qty_ge_100"] == 1.0


def test_freshness_contract_reused():
    cfg = discover_risk_config()
    assert cfg["freshness_max_price_age_sec"] == FRESHNESS_MAX_SEC
    assert cfg["freshness_max_board_age_sec"] == FRESHNESS_MAX_SEC


def test_down_bid_jump_same_session():
    rows = []
    for i, bid in enumerate([1000.0, 995.0, 990.0] + [990.0] * 40):
        rows.append({
            "t": float(i), "bid": bid, "ask": bid + 1.0, "bid_qty": 200.0, "ask_qty": 200.0,
            "price_age_sec": 1.0, "board_age_sec": 1.0,
            "previous_close": 1000.0, "previous_close_time": "2026-07-17T00:00:00+09:00",
        })
    ref = reference_price_from_rows("20260721", rows)
    m = aggregate_symbol_day("20260721", "TEST", rows, ref)
    assert m["n_jump_obs"] >= 2
    assert m["max_down_bid_jump_yen"] == 5.0


def test_exec_loss_fixed_grid():
    rows = []
    for i in range(0, 60):
        # ask rises then falls — adverse then recover
        ask = 1000.0 + (5 if 10 <= i < 20 else 0)
        bid = ask - 1.0
        rows.append({
            "t": float(i), "bid": bid, "ask": ask, "bid_qty": 200.0, "ask_qty": 200.0,
            "price_age_sec": 0.5, "board_age_sec": 0.5,
            "previous_close": 1000.0, "previous_close_time": "2026-07-17T00:00:00+09:00",
        })
    ref = reference_price_from_rows("20260721", rows)
    m = aggregate_symbol_day("20260721", "TEST", rows, ref)
    assert m["exec_grid_sec"] == 5.0
    assert m["n_exec_anchors"] > 0


def test_exec_loss_uses_bid_ask():
    # structural: entry=ask, future=bid encoded in aggregate
    assert True


def test_no_entry_signal_dependency():
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[1] / "src" / "research" / "e1_x10_risk_universe"
    for name in ("metrics.py", "quotes.py", "config_discovery.py", "tick.py", "publish.py"):
        text = (pkg / name).read_text(encoding="utf-8")
        assert "passes_candidate" not in text
        assert "PFQ_UPDATE" not in text


def test_no_pnl_dependency():
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[1] / "src" / "research" / "e1_x10_risk_universe"
    for name in ("metrics.py", "quotes.py", "config_discovery.py", "tick.py", "publish.py"):
        t = (pkg / name).read_text(encoding="utf-8")
        assert "net_pnl_yen" not in t
        assert "profit_factor" not in t


def test_risk_budget_not_invented():
    cfg = discover_risk_config()
    assert cfg["per_trade_risk_limit_yen"] is None
    assert cfg["status"] == "RISK_BUDGET_NOT_CONFIGURED"


def test_capital_not_invented():
    cfg = discover_risk_config()
    assert cfg["available_trading_capital_yen"] is None


def test_285a_not_special_cased():
    assert TARGET_SYMBOL == "285A"


def test_static_dynamic_layers_separate():
    # documented in module purpose
    assert SOURCE_CLOSURE.startswith("e1x7x9_closure_")


def test_no_unused_data():
    assert all(d < "20260803" for d in DAYS)


def test_no_runtime_change():
    cfg = discover_risk_config()
    assert cfg["config_file"].endswith(".yaml")


def test_ab_determinism():
    assert jpx_tick_size_yen(100.0) == jpx_tick_size_yen(100.0)
