"""Aligned historical panel tests. No strategy. Frozen universe bound from artifacts. Time semantics fail-closed."""
from __future__ import annotations

import inspect

from research.aligned_historical_panel_v1 import (
    CASE_ADDON,
    CASE_TIME_BLOCK,
    CASE_UNIVERSE,
    ENTRY,
    EXIT,
    LIVE_20260914_CHANGED,
    PNL_USED,
    RUNTIME_CHANGED,
    SAME_BAR_CLOSE_ENTRY,
    STRATEGY_SEARCH_STARTED,
)
from research.aligned_historical_panel_v1.analyze import decide
from research.aligned_historical_panel_v1.minute_schema import minute_schema_status
from research.aligned_historical_panel_v1.publish import SHEET_ORDER
from research.aligned_historical_panel_v1.time_semantics import (
    SEMANTICS_BAR_END,
    SEMANTICS_BAR_START,
    SEMANTICS_UNKNOWN,
    infer_time_semantics,
)
from research.aligned_historical_panel_v1.universe_bind import bind_frozen_universe
from research.fixed_daytrade_universe_v1.analyze import universe_identity_sha


def test_frozen_universe_bound_from_artifacts():
    u = bind_frozen_universe()
    assert u["verified"] is True
    assert u["frozen"] is True
    assert u["symbol_n"] == 45
    assert u["did_not_change_universe"] is True
    assert u["console_verdict_not_used"] is True
    assert u["identity_sha256"] == universe_identity_sha(u["symbols"])
    assert "9983" not in u["symbols"]
    assert "6861" not in u["symbols"]
    assert u["PANEL_CONDITIONED"] is True
    assert u["xlsx_final_n"] == 45
    assert u["liquidity_gates"]["all_45_passed_liquidity_and_listing"] is True
    assert u["liquidity_fail_symbols"] == []
    assert u["source_period"]["first"] == "20260618"
    assert u["source_period"]["last"] == "20260911"
    assert u["source_period"]["n"] == 60
    assert {e["symbol"] for e in u["exclusions_9983_6861"]} == {"9983", "6861"}


def test_minute_schema_official_sample():
    sample = {
        "Date": "2023-03-24",
        "Time": "09:00",
        "Code": "86970",
        "O": 2047.0,
        "H": 2055.0,
        "L": 2045.0,
        "C": 2050.0,
        "Vo": 12500.0,
        "Va": 25625000.0,
    }
    sch = minute_schema_status(sample)
    assert sch["ok"] is True
    assert sch["trading_value_raw_field"] == "Va"
    assert minute_schema_status({"Date": "2026-09-11", "Code": "72030"})["ok"] is False


def test_time_semantics_from_ticks_not_docs():
    start = infer_time_semantics(labeled_bar="09:00", tick_times=["09:00:00.067558", "09:00:01.039337"])
    end = infer_time_semantics(labeled_bar="09:00", tick_times=["08:59:00.100000", "08:59:59.900000"])
    unk = infer_time_semantics(labeled_bar="09:00", tick_times=[])
    assert start["EQUITY_MINUTE_TIME_SEMANTICS"] == SEMANTICS_BAR_START
    assert end["EQUITY_MINUTE_TIME_SEMANTICS"] == SEMANTICS_BAR_END
    assert unk["EQUITY_MINUTE_TIME_SEMANTICS"] == SEMANTICS_UNKNOWN
    assert SAME_BAR_CLOSE_ENTRY is False


def test_addon_and_time_block_decisions():
    universe = {"ok": True}
    addon = decide(
        universe=universe,
        minute={"available": False, "addon_required": True, "http_status": 403, "reason": "jquants_http_403"},
        ticks={"semantics": SEMANTICS_UNKNOWN, "performed": False},
        external={"core": {}},
    )
    assert addon["VERDICT"] == CASE_ADDON
    time_block = decide(
        universe=universe,
        minute={"available": True, "schema": {"ok": True}},
        ticks={"semantics": SEMANTICS_UNKNOWN, "performed": True, "reason": "ticks_did_not_disambiguate"},
        external={"core": {}},
    )
    assert time_block["VERDICT"] == CASE_TIME_BLOCK
    bad_u = decide(
        universe={"ok": False, "reason": "not_frozen"},
        minute={"available": True, "schema": {"ok": True}},
        ticks={"semantics": SEMANTICS_BAR_START},
        external={"core": {}},
    )
    assert bad_u["VERDICT"] == CASE_UNIVERSE


def test_no_strategy_and_safety_constants():
    import research.aligned_historical_panel_v1.analyze as a
    import research.aligned_historical_panel_v1.__main__ as m

    src = inspect.getsource(a) + inspect.getsource(m)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert "sendorder" not in src
    assert STRATEGY_SEARCH_STARTED is False
    assert PNL_USED is False
    assert RUNTIME_CHANGED is False
    assert LIVE_20260914_CHANGED is False
    assert ENTRY is False and EXIT is False


def test_sheet_order():
    assert SHEET_ORDER[0] == "UNIVERSE_BIND"
    assert "TIME_SEMANTICS" in SHEET_ORDER
    assert "EXTERNAL" in SHEET_ORDER


def test_tick_file_selector_prefers_probe_month():
    from research.aligned_historical_panel_v1.probe_minute import _select_tick_file

    chosen = _select_tick_file(
        [
            {"Key": "equities/trades/historical/2026/equities_trades_202608.csv.gz", "Size": 1},
            {"Key": "equities/trades/historical/2026/equities_trades_202609.csv.gz", "Size": 99},
        ],
        probe_date="20260911",
    )
    assert chosen["Key"].endswith("202609.csv.gz")
