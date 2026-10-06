"""External causal-information tests. Frozen Validation closed. HM1 not retuned. No purchase."""
from __future__ import annotations

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

from research.identify_minimum_missing_external_causal_information_v1 import (
    CASE_INSUFFICIENT,
    CASE_PROXY,
    CASE_SOURCE,
    FROZEN_VALIDATION_OPENED,
    KABU_50_APPLIED,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
)
from research.identify_minimum_missing_external_causal_information_v1.analyze import decide
from research.identify_minimum_missing_external_causal_information_v1.features import features_at_T
from research.identify_minimum_missing_external_causal_information_v1.isolation import HIGHER_MAG_OUT, OUT, write_overlap_n
from research.identify_minimum_missing_external_causal_information_v1.publish import SHEET_ORDER

JST = ZoneInfo("Asia/Tokyo")


def test_constants():
    assert PARENT_VERDICT == "STOCK_PANEL_MAGNITUDE_LIMIT_CONFIRMED_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert OUT.name == "identify_minimum_missing_external_causal_information_v1"
    assert HIGHER_MAG_OUT.name == "higher_magnitude_state_discovery_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "HM1_Anchor" in SHEET_ORDER
    assert "Live_20260914" in SHEET_ORDER


def test_causal_join_does_not_use_future_bar():
    t = datetime(2024, 9, 17, 9, 41, tzinfo=JST)
    bars = [
        {
            "close": 100.0,
            "available_at": datetime(2024, 9, 17, 9, 40, tzinfo=JST),
            "bar_start": datetime(2024, 9, 17, 9, 39, tzinfo=JST),
            "source_timestamp": "09:39",
        },
        {
            "close": 101.0,
            "available_at": datetime(2024, 9, 17, 9, 41, tzinfo=JST),
            "bar_start": datetime(2024, 9, 17, 9, 40, tzinfo=JST),
            "source_timestamp": "09:40",
        },
        {
            "close": 110.0,
            "available_at": datetime(2024, 9, 17, 9, 42, tzinfo=JST),
            "bar_start": datetime(2024, 9, 17, 9, 41, tzinfo=JST),
            "source_timestamp": "09:41",
        },
    ]
    got = features_at_T(bars=bars, decision=t)
    assert got["source_timestamp"] == "09:40"
    assert got["ret_30s_bps"] is None
    assert got["used_same_bar_unavailable_at_T"] is False
    assert abs(float(got["ret_60s_bps"]) - 100.0) < 1e-6


def test_decide_source_vs_insufficient():
    bind_fail = decide(bind_ok=False, true_futures_hist=False, proxy_hist=False, structure=False)
    assert "BIND" in bind_fail["VERDICT"]
    source = decide(bind_ok=True, true_futures_hist=False, proxy_hist=False, structure=False)
    assert source["VERDICT"] == CASE_SOURCE
    source2 = decide(bind_ok=True, true_futures_hist=False, proxy_hist=True, structure=False)
    assert source2["VERDICT"] == CASE_SOURCE
    proxy = decide(bind_ok=True, true_futures_hist=False, proxy_hist=True, structure=True)
    assert proxy["VERDICT"] == CASE_PROXY
    insuff = decide(bind_ok=True, true_futures_hist=True, proxy_hist=False, structure=False)
    assert insuff["VERDICT"] == CASE_INSUFFICIENT
    import research.identify_minimum_missing_external_causal_information_v1.analyze as a
    import research.identify_minimum_missing_external_causal_information_v1.freeze as f

    src = inspect.getsource(a) + inspect.getsource(f)
    assert "DESIGN_KABU_50" not in src
    assert "VWAP" in inspect.getsource(f) or "hm1" in inspect.getsource(f).lower()
    assert FROZEN_VALIDATION_OPENED is False
