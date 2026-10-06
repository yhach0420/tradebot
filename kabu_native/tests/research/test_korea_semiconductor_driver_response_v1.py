"""Korea semiconductor causal response tests. Frozen Validation closed. No purchase. No Kabu 50. No ENTRY mining."""
from __future__ import annotations

import inspect

from research.korea_semiconductor_driver_response_v1 import (
    CASE_BLOCKED,
    CASE_BROAD,
    CASE_PROXY,
    CASE_SEMI,
    CASE_SIM,
    FROZEN_VALIDATION_OPENED,
    FWD_HORIZONS,
    KABU_50_APPLIED,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
    STATUS_BLOCKED,
    STATUS_NATIVE,
    STATUS_PROXY,
    TECH_FOCUS,
)
from research.korea_semiconductor_driver_response_v1.analyze import decide
from research.korea_semiconductor_driver_response_v1.isolation import FX_MAP_OUT, NQES_OUT, OUT, USDJPY_OUT, write_overlap_n
from research.korea_semiconductor_driver_response_v1.publish import SHEET_ORDER


def test_constants():
    assert PARENT_VERDICT == "US_TECH_PROXY_RESPONSE_FOUND_TRUE_FUTURES_PROOF_REQUIRED_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert FWD_HORIZONS == (1, 2, 3, 5, 10)
    assert OUT.name == "korea_semiconductor_driver_response_v1"
    assert USDJPY_OUT.name == "usd_jpy_sector_symbol_response_v1"
    assert FX_MAP_OUT.name == "fx_response_map_v1"
    assert NQES_OUT.name == "nq_es_sector_symbol_response_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Korea_Source_Audit" in SHEET_ORDER
    assert "Driver_Coverage_105" in SHEET_ORDER
    assert "Japan_Tech_Targets" in SHEET_ORDER
    assert "6857" in TECH_FOCUS
    assert len(TECH_FOCUS) == 13


def test_decide_and_safety_source():
    blocked = decide(
        bind_ok=True,
        source_status=STATUS_BLOCKED,
        native_ok=False,
        proxy_ok=False,
        semantics_ok=False,
        kospi_lead=False,
        samsung_lead=False,
        sk_lead=False,
        samsung_after_kospi=False,
        sk_after_kospi=False,
        simultaneous_only=False,
    )
    assert blocked["VERDICT"] == CASE_BLOCKED
    semi = decide(
        bind_ok=True,
        source_status=STATUS_NATIVE,
        native_ok=True,
        proxy_ok=False,
        semantics_ok=True,
        kospi_lead=True,
        samsung_lead=True,
        sk_lead=True,
        samsung_after_kospi=False,
        sk_after_kospi=True,
        simultaneous_only=False,
    )
    assert semi["VERDICT"] == CASE_SEMI
    broad = decide(
        bind_ok=True,
        source_status=STATUS_NATIVE,
        native_ok=True,
        proxy_ok=False,
        semantics_ok=True,
        kospi_lead=True,
        samsung_lead=True,
        sk_lead=True,
        samsung_after_kospi=False,
        sk_after_kospi=False,
        simultaneous_only=False,
    )
    assert broad["VERDICT"] == CASE_BROAD
    sim = decide(
        bind_ok=True,
        source_status=STATUS_NATIVE,
        native_ok=True,
        proxy_ok=False,
        semantics_ok=True,
        kospi_lead=False,
        samsung_lead=False,
        sk_lead=False,
        samsung_after_kospi=False,
        sk_after_kospi=False,
        simultaneous_only=True,
    )
    assert sim["VERDICT"] == CASE_SIM
    proxy = decide(
        bind_ok=True,
        source_status=STATUS_PROXY,
        native_ok=False,
        proxy_ok=True,
        semantics_ok=True,
        kospi_lead=True,
        samsung_lead=False,
        sk_lead=False,
        samsung_after_kospi=False,
        sk_after_kospi=False,
        simultaneous_only=False,
    )
    assert proxy["VERDICT"] == CASE_PROXY
    import research.korea_semiconductor_driver_response_v1.analyze as a
    import research.korea_semiconductor_driver_response_v1.source as s

    src_a = inspect.getsource(a)
    src_s = inspect.getsource(s)
    assert "import yfinance" not in src_a.lower()
    assert "import yfinance" not in src_s.lower()
    assert "yfinance.download" not in src_s.lower()
    assert "DESIGN_KABU_50" not in src_a
    assert "grid_clocks(" not in src_a
    assert "did_not_fabricate_korea_proxy" in src_s
    assert "did_not_use_hkg_or_chi_as_korea_proxy" in src_s
    assert FROZEN_VALIDATION_OPENED is False
