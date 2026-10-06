"""NQ/ES causal response tests. Frozen Validation closed. No purchase. No Kabu 50. No ENTRY mining."""
from __future__ import annotations

import inspect

import numpy as np

from research.nq_es_sector_symbol_response_v1 import (
    FROZEN_VALIDATION_OPENED,
    FWD_HORIZONS,
    KABU_50_APPLIED,
    PARENT_VERDICT,
    PROXY_ES,
    PROXY_NQ,
    PURCHASE_REQUESTED,
    STATUS_BLOCKED,
    STATUS_PROXY,
    TECH_FOCUS,
)
from research.nq_es_sector_symbol_response_v1.analyze import decide
from research.nq_es_sector_symbol_response_v1.isolation import FX_MAP_OUT, OUT, USDJPY_OUT, write_overlap_n
from research.nq_es_sector_symbol_response_v1.publish import SHEET_ORDER
from research.nq_es_sector_symbol_response_v1.response import residualize
from research.usd_jpy_sector_symbol_response_v1.reconcile import reconcile_stock_row
from research.usd_jpy_sector_symbol_response_v1.source import decode_candles


def test_constants():
    assert PARENT_VERDICT == "USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert FWD_HORIZONS == (1, 2, 3, 5, 10)
    assert OUT.name == "nq_es_sector_symbol_response_v1"
    assert USDJPY_OUT.name == "usd_jpy_sector_symbol_response_v1"
    assert FX_MAP_OUT.name == "fx_response_map_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "USDJPY_Preserved_Map" in SHEET_ORDER
    assert "NQ_After_ES" in SHEET_ORDER
    assert "Tech_Name_Response" in SHEET_ORDER
    assert "True_Japan_Futures_Subtrack" in SHEET_ORDER
    assert PROXY_NQ == "USATECH.IDX-USD"
    assert PROXY_ES == "USA500.IDX-USD"
    assert "6857" in TECH_FOCUS


def test_residualize_and_decide():
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = 2.0 * x + rng.normal(scale=0.01, size=200)
    r = residualize(y, x)
    assert abs(float(np.corrcoef(x[np.isfinite(r)], r[np.isfinite(r)])[0, 1])) < 0.05
    blocked = decide(
        bind_ok=True,
        source_status=STATUS_BLOCKED,
        semantics_ok=False,
        nq_n=0,
        es_n=0,
        true_futures=False,
        nq_specific_n=0,
        es_broad_n=0,
        nq_sector_lead=False,
        proxy_mechanism=False,
    )
    assert blocked["VERDICT"] == "NQ_ES_SOURCE_ACCESS_BLOCKED_V1"
    proxy = decide(
        bind_ok=True,
        source_status=STATUS_PROXY,
        semantics_ok=True,
        nq_n=10,
        es_n=10,
        true_futures=False,
        nq_specific_n=3,
        es_broad_n=0,
        nq_sector_lead=True,
        proxy_mechanism=True,
    )
    assert proxy["VERDICT"] == "US_TECH_PROXY_RESPONSE_FOUND_TRUE_FUTURES_PROOF_REQUIRED_V1"
    none = decide(
        bind_ok=True,
        source_status=STATUS_PROXY,
        semantics_ok=True,
        nq_n=10,
        es_n=10,
        true_futures=False,
        nq_specific_n=0,
        es_broad_n=0,
        nq_sector_lead=False,
        proxy_mechanism=False,
    )
    assert none["VERDICT"] == "NQ_ES_NO_CAUSAL_LEAD_V1"
    candles = decode_candles(
        {
            "timestamp": 1726531200000,
            "times": [0, 1],
            "opens": [0, 1],
            "highs": [0, 1],
            "lows": [0, 1],
            "closes": [0, 1],
            "volumes": [1, 1],
            "open": 19424.0,
            "high": 19424.0,
            "low": 19424.0,
            "close": 19424.0,
            "multiplier": 0.1,
            "shift": 60000,
        }
    )
    assert candles[0]["ts_utc_ms"] == 1726531200000
    news = reconcile_stock_row({"symbol": "6266", "class": "DIRECT_STOCK_SENSITIVITY", "lead_lag": "simultaneous_common_news"})
    assert news["usable_before_stock_move"] is False
    import research.nq_es_sector_symbol_response_v1.analyze as a
    import research.nq_es_sector_symbol_response_v1.panel as p

    src = inspect.getsource(a)
    assert "yfinance" not in src.lower()
    assert "DESIGN_KABU_50" not in src
    assert "grid_clocks(" not in src
    assert "grid_clocks(" not in inspect.getsource(p)
    assert FROZEN_VALIDATION_OPENED is False
