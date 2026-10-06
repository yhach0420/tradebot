"""USDJPY causal response tests. Frozen Validation closed. No purchase. No Kabu 50. No ENTRY mining."""
from __future__ import annotations

import inspect

from research.usd_jpy_sector_symbol_response_v1 import (
    FROZEN_VALIDATION_OPENED,
    FWD_HORIZONS,
    KABU_50_APPLIED,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
    STATUS_ACCESSIBLE,
    STATUS_BLOCKED,
)
from research.usd_jpy_sector_symbol_response_v1.analyze import decide
from research.usd_jpy_sector_symbol_response_v1.isolation import MAPPING_OUT, OUT, write_overlap_n
from research.usd_jpy_sector_symbol_response_v1.publish import SHEET_ORDER
from research.usd_jpy_sector_symbol_response_v1.reconcile import reconcile_stock_row
from research.usd_jpy_sector_symbol_response_v1.source import decode_candles, decode_ticks


def test_constants():
    assert PARENT_VERDICT == "CURRENT_DRIVER_SET_INSUFFICIENT_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert FWD_HORIZONS == (1, 2, 3, 5, 10)
    assert OUT.name == "usd_jpy_sector_symbol_response_v1"
    assert OUT.parent.name == "expand_causal_driver_catalog_v1"
    assert MAPPING_OUT.name == "sector_symbol_driver_response_mapping_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "USDJPY_Source" in SHEET_ORDER
    assert "Prior_Residual_Flags" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER


def test_decoder_and_decide():
    candles = decode_candles(
        {
            "timestamp": 1726531200000,
            "multiplier": 0.001,
            "shift": 60000,
            "open": 140.795,
            "high": 140.843,
            "low": 140.778,
            "close": 140.799,
            "times": [0, 1],
            "opens": [0, 10],
            "highs": [0, 5],
            "lows": [0, -2],
            "closes": [0, 4],
            "volumes": [1, 1],
        }
    )
    assert candles[0]["ts_utc_ms"] == 1726531200000
    assert candles[1]["ts_utc_ms"] == 1726531200000 + 60000
    assert candles[1]["close"] == 140.803
    ticks = decode_ticks(
        {
            "timestamp": 1726531200000,
            "multiplier": 0.001,
            "bid": 140.795,
            "ask": 140.802,
            "times": [87, 52],
            "bids": [0, 1],
            "asks": [0, 2],
            "bidVolumes": [1, 1],
            "askVolumes": [1, 1],
        }
    )
    assert ticks[0]["ts_utc_ms"] == 1726531200000 + 87
    assert ticks[0]["bid"] == 140.795
    blocked = decide(bind_ok=True, source_status=STATUS_BLOCKED, semantics_ok=False, fx_n=0, sector_lead_n=0, stable_group_n=0)
    assert blocked["VERDICT"] == "USDJPY_SOURCE_ACCESS_BLOCKED_V1"
    none = decide(bind_ok=True, source_status=STATUS_ACCESSIBLE, semantics_ok=True, fx_n=10, sector_lead_n=0, stable_group_n=0)
    assert none["VERDICT"] == "USDJPY_NO_CAUSAL_LEAD_V1"
    sector = decide(bind_ok=True, source_status=STATUS_ACCESSIBLE, semantics_ok=True, fx_n=10, sector_lead_n=0, stable_group_n=0, preopen_stable_n=1)
    assert sector["VERDICT"] == "USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1"
    sector2 = decide(bind_ok=True, source_status=STATUS_ACCESSIBLE, semantics_ok=True, fx_n=10, sector_lead_n=1, stable_group_n=0)
    assert sector2["VERDICT"] == "USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1"
    groups = decide(bind_ok=True, source_status=STATUS_ACCESSIBLE, semantics_ok=True, fx_n=10, sector_lead_n=2, stable_group_n=3)
    assert groups["VERDICT"] == "USDJPY_CAUSAL_RESPONSE_GROUPS_FOUND_V1"
    bind_fail = decide(bind_ok=False, source_status=STATUS_ACCESSIBLE, semantics_ok=True, fx_n=10, sector_lead_n=9, stable_group_n=9)
    assert "BIND" in bind_fail["VERDICT"]
    import research.usd_jpy_sector_symbol_response_v1.analyze as a

    src = inspect.getsource(a)
    assert "yfinance" not in src.lower()
    assert "DESIGN_KABU_50" not in src
    assert "grid_clocks" not in src
    assert FROZEN_VALIDATION_OPENED is False


def test_reconcile_direct_is_not_usable():
    news = reconcile_stock_row(
        {"symbol": "6266", "class": "DIRECT_STOCK_SENSITIVITY", "lead_lag": "simultaneous_common_news"}
    )
    assert news["direct_sensitivity"] is True
    assert news["causal_lead"] is False
    assert news["simultaneous_common_news"] is True
    assert news["usable_before_stock_move"] is False
    near = reconcile_stock_row(
        {"symbol": "6779", "class": "DIRECT_STOCK_SENSITIVITY", "lead_lag": "unusable_near_contemporaneous"}
    )
    assert near["usable_before_stock_move"] is False
    lead = reconcile_stock_row(
        {"symbol": "6787", "class": "DIRECT_STOCK_SENSITIVITY", "lead_lag": "fx_leads_about_1m"}
    )
    assert lead["usable_before_stock_move"] is True
    assert lead["causal_lead"] is True
