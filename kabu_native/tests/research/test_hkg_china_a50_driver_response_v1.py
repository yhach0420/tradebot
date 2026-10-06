"""HKG/China A50 causal response tests. Frozen Validation closed. No purchase. No Kabu 50."""
from __future__ import annotations

import inspect

import numpy as np

from research.hkg_china_a50_driver_response_v1 import (
    FROZEN_VALIDATION_OPENED,
    FWD_HORIZONS,
    KABU_50_APPLIED,
    PARENT_VERDICT,
    PROXY_CHI,
    PROXY_HKG,
    PURCHASE_REQUESTED,
    STATUS_BLOCKED,
    STATUS_PROXY,
    TECH_DRIVER_BLOCKER_OPEN,
    TECH_FOCUS,
)
from research.hkg_china_a50_driver_response_v1.analyze import decide
from research.hkg_china_a50_driver_response_v1.isolation import KOREA_OUT, OUT, write_overlap_n
from research.hkg_china_a50_driver_response_v1.publish import SHEET_ORDER
from research.nq_es_sector_symbol_response_v1.response import residualize


def test_constants():
    assert PARENT_VERDICT == "KOREA_INTRADAY_SOURCE_NOT_AVAILABLE_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert TECH_DRIVER_BLOCKER_OPEN is True
    assert FWD_HORIZONS == (1, 2, 3, 5, 10)
    assert OUT.name == "hkg_china_a50_driver_response_v1"
    assert KOREA_OUT.name == "korea_semiconductor_driver_response_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Tech_Missing_Driver_Data" in SHEET_ORDER
    assert "Foreign_Open_Event" in SHEET_ORDER
    assert PROXY_HKG == "HKG.IDX-HKD"
    assert PROXY_CHI == "CHI.IDX-USD"
    assert "6857" in TECH_FOCUS


def test_decide_and_safety():
    blocked = decide(
        bind_ok=True,
        source_status=STATUS_BLOCKED,
        semantics_ok=False,
        hkg_n=0,
        chi_n=0,
        greater_n=0,
        hkg_n_lead=0,
        chi_n_lead=0,
        sim_only=True,
    )
    assert blocked["VERDICT"] == "HKG_CHINA_SOURCE_NOT_USABLE_V1"
    greater = decide(
        bind_ok=True,
        source_status=STATUS_PROXY,
        semantics_ok=True,
        hkg_n=10,
        chi_n=10,
        greater_n=4,
        hkg_n_lead=0,
        chi_n_lead=0,
        sim_only=False,
    )
    assert greater["VERDICT"] == "GREATER_CHINA_CAUSAL_RESPONSE_GROUP_FOUND_V1"
    a50 = decide(
        bind_ok=True,
        source_status=STATUS_PROXY,
        semantics_ok=True,
        hkg_n=10,
        chi_n=10,
        greater_n=0,
        hkg_n_lead=0,
        chi_n_lead=3,
        sim_only=False,
    )
    assert a50["VERDICT"] == "CHINA_A50_CAUSAL_RESPONSE_GROUP_FOUND_V1"
    sim = decide(
        bind_ok=True,
        source_status=STATUS_PROXY,
        semantics_ok=True,
        hkg_n=10,
        chi_n=10,
        greater_n=0,
        hkg_n_lead=0,
        chi_n_lead=0,
        sim_only=True,
    )
    assert sim["VERDICT"] == "HKG_CHINA_A50_SIMULTANEOUS_ONLY_V1"
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = 2.0 * x + rng.normal(scale=0.01, size=200)
    r = residualize(y, x)
    assert abs(float(np.corrcoef(x[np.isfinite(r)], r[np.isfinite(r)])[0, 1])) < 0.05
    import research.hkg_china_a50_driver_response_v1.analyze as a
    import research.hkg_china_a50_driver_response_v1.panel as p
    import research.hkg_china_a50_driver_response_v1.source as s

    src_a = inspect.getsource(a)
    src_p = inspect.getsource(p)
    src_s = inspect.getsource(s)
    assert "import yfinance" not in src_a.lower()
    assert "import yfinance" not in src_s.lower()
    assert "grid_clocks(" not in src_a
    assert "grid_clocks(" not in src_p
    assert "DESIGN_KABU_50" not in src_a
    assert "did_not_test_0900_without_live_driver" in src_p
    assert FROZEN_VALIDATION_OPENED is False
