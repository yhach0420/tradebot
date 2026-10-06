"""Driver-response mapping tests. Frozen Validation closed. No purchase. No Kabu 50. No ENTRY mining."""
from __future__ import annotations

import inspect

from research.sector_symbol_driver_response_mapping_v1 import (
    CLUSTER_K,
    FROZEN_VALIDATION_OPENED,
    KABU_50_APPLIED,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
)
from research.sector_symbol_driver_response_mapping_v1.analyze import decide
from research.sector_symbol_driver_response_mapping_v1.isolation import EXTERNAL_OUT, OUT, write_overlap_n
from research.sector_symbol_driver_response_mapping_v1.panel import tod_bucket
from research.sector_symbol_driver_response_mapping_v1.publish import SHEET_ORDER


def test_constants():
    assert PARENT_VERDICT == "HISTORICAL_FUTURES_SOURCE_NOT_AVAILABLE_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert CLUSTER_K == 5
    assert OUT.name == "sector_symbol_driver_response_mapping_v1"
    assert EXTERNAL_OUT.name == "identify_minimum_missing_external_causal_information_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Driver_Sector_Matrix" in SHEET_ORDER
    assert "HM1_PostMap_Diagnostic" in SHEET_ORDER
    assert tod_bucket("09:30") == "open"
    assert tod_bucket("10:30") == "mid_am"
    assert tod_bucket("13:00") == "early_pm"
    assert tod_bucket("14:30") == "late_pm"


def test_decide_and_forbidden():
    bind_fail = decide(bind_ok=False, stable_sec_n=9, usable_lead_n=9, justified_n=9, futures_blocking=False)
    assert "BIND" in bind_fail["VERDICT"]
    mapped = decide(bind_ok=True, stable_sec_n=9, usable_lead_n=9, justified_n=9, futures_blocking=False)
    assert mapped["VERDICT"] == "SECTOR_SYMBOL_DRIVER_MAP_FOUND_V1"
    sector = decide(bind_ok=True, stable_sec_n=9, usable_lead_n=9, justified_n=1, futures_blocking=False)
    assert sector["VERDICT"] == "SECTOR_DRIVER_MAP_FOUND_STOCK_SPECIFICITY_WEAK_V1"
    insuff = decide(bind_ok=True, stable_sec_n=0, usable_lead_n=0, justified_n=0, futures_blocking=False)
    assert insuff["VERDICT"] == "CURRENT_DRIVER_SET_INSUFFICIENT_V1"
    fut = decide(bind_ok=True, stable_sec_n=9, usable_lead_n=0, justified_n=0, futures_blocking=True)
    assert fut["VERDICT"] == "TRUE_FUTURES_REQUIRED_FOR_KEY_DRIVER_GROUPS_V1"
    import research.sector_symbol_driver_response_mapping_v1.analyze as a

    src = inspect.getsource(a)
    assert "DESIGN_KABU_50" not in src
    assert "yfinance" not in src.lower()
    assert FROZEN_VALIDATION_OPENED is False
