"""Driver catalog and source classification. Probe only. Do not purchase. Do not wait on futures."""
from __future__ import annotations

import os
from typing import Any

from research.aligned_historical_panel_v1 import ENDPOINT_FUTURES_DAILY, ENDPOINT_INDICES_DAILY, ENDPOINT_MINUTE
from research.fixed_daytrade_universe_v1.jquants_client import request_json
from research.fixed_universe_historical_foundation_v1.sources import source_rows
from research.identify_minimum_missing_external_causal_information_v1.isolation import DATACUBE, NATIVE
from research.sector_symbol_driver_response_mapping_v1.isolation import PROXY_CACHE

HISTORICAL_AVAILABLE = "HISTORICAL_AVAILABLE"
PROSPECTIVE_ONLY = "PROSPECTIVE_ONLY"
PROXY_ONLY = "PROXY_ONLY"
UNAVAILABLE_NO_PURCHASE = "UNAVAILABLE_NO_PURCHASE"

FX_MINUTE_ENDPOINTS = ("/v2/fx/bars/minute", "/v2/markets/fx/bars/minute", "/v2/indices/bars/minute")


def _env(*names: str) -> bool:
    return any(str(os.environ.get(n) or "").strip() for n in names)


def _probe(path: str, params: dict[str, str]) -> dict[str, Any]:
    got = request_json(path=path, params=params)
    payload = got.get("payload") if isinstance(got.get("payload"), dict) else {}
    data = list(payload.get("data") or []) if payload else []
    return {
        "endpoint": path,
        "ok": bool(got.get("ok") and data),
        "http_status": got.get("status"),
        "reason": got.get("reason"),
        "row_n": len(data),
        "error_snippet": str(got.get("error_snippet") or "")[:160],
        "api_key_logged": False,
    }


def _local_n(rel: str) -> int:
    root = NATIVE / rel
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def build_catalog() -> dict[str, Any]:
    print("CATALOG_PROBE_OPTIONAL_ENDPOINTS", flush=True)
    fx_probes = [_probe(p, {"date": "2024-09-17"}) for p in FX_MINUTE_ENDPOINTS]
    idx_daily = _probe(ENDPOINT_INDICES_DAILY, {"date": "2024-09-17"})
    fut_daily = _probe(ENDPOINT_FUTURES_DAILY, {"date": "2024-09-17"})
    proxy_nk = PROXY_CACHE / "minute_1321_20240917_20251126.parquet"
    proxy_tx = PROXY_CACHE / "minute_1306_20240917_20251126.parquet"
    datacube_n = sum(1 for p in DATACUBE.rglob("*") if p.is_file()) if DATACUBE.exists() else 0
    dukas = _env("DUKASCOPY_USER", "DUKASCOPY_PASSWORD")
    databento = _env("DATABENTO_API_KEY", "DATABENTO_KEY")
    usdjpy_local = _local_n("data/reference/historical_panel/usdjpy")
    esnq_local = _local_n("data/reference/historical_panel/us_futures")
    src = {str(r.get("instrument")): r for r in source_rows()}

    def row(**kw: Any) -> dict[str, Any]:
        kw.setdefault("did_not_purchase", True)
        kw.setdefault("yfinance_used", False)
        kw.setdefault("additional_purchase_required", kw.get("class_") == UNAVAILABLE_NO_PURCHASE or kw.get("class") == UNAVAILABLE_NO_PURCHASE)
        return kw

    drivers = [
        row(
            driver_id="MKT_MEDIAN_RET_1M",
            family="JAPAN_BROAD_MARKET",
            class_=HISTORICAL_AVAILABLE,
            source="105_stock_minute_panel_ex_sector_control",
            frequency="1min_BAR_START",
            history="20240917-20251126_Discovery",
            timezone="Asia/Tokyo",
            timestamp_semantics="BAR_START_available_at_Tplus1m",
            availability_time="last_complete_bar_end_<=_T",
            cost="existing_jquants_equity_minute_entitlement",
            current_entitlement="usable",
            realtime_availability="research_panel_not_kabu_entry_feed",
            runtime_deployability="RESEARCH_THEN_BREADTH_CAPTURE",
            historically_testable=True,
        ),
        row(
            driver_id="MKT_BREADTH",
            family="JAPAN_BROAD_MARKET",
            class_=HISTORICAL_AVAILABLE,
            source="105_stock_fraction_up_1m",
            frequency="1min_derived",
            history="Discovery",
            timezone="Asia/Tokyo",
            timestamp_semantics="BAR_START",
            historically_testable=True,
            runtime_deployability="BREADTH_CAPTURE_OR_PANEL",
        ),
        row(
            driver_id="MKT_DISPERSION",
            family="JAPAN_BROAD_MARKET",
            class_=HISTORICAL_AVAILABLE,
            source="cross_sectional_std_ret_1m",
            frequency="1min_derived",
            historically_testable=True,
        ),
        row(
            driver_id="MKT_LEADERSHIP",
            family="JAPAN_BROAD_MARKET",
            class_=HISTORICAL_AVAILABLE,
            source="hhi_abs_ret_1m",
            frequency="1min_derived",
            historically_testable=True,
        ),
        row(
            driver_id="ETF_NK_1321",
            family="JAPAN_BROAD_MARKET",
            class_=PROXY_ONLY,
            source="jquants_equity_minute_1321",
            label="PROXY_NOT_FUTURES",
            never_claim_etf_equals_futures=True,
            frequency="1min_BAR_START",
            history="20240917-20251126",
            local_path=str(proxy_nk),
            local_present=proxy_nk.is_file(),
            historically_testable=bool(proxy_nk.is_file()),
            runtime_deployability="CASH_ETF_NOT_FUTURES",
        ),
        row(
            driver_id="ETF_TOPIX_1306",
            family="JAPAN_BROAD_MARKET",
            class_=PROXY_ONLY,
            source="jquants_equity_minute_1306",
            label="PROXY_NOT_FUTURES",
            never_claim_etf_equals_futures=True,
            frequency="1min_BAR_START",
            history="20240917-20251126",
            local_path=str(proxy_tx),
            local_present=proxy_tx.is_file(),
            historically_testable=bool(proxy_tx.is_file()),
            runtime_deployability="CASH_ETF_NOT_FUTURES",
        ),
        row(
            driver_id="NK225MINI_TRUE",
            family="TRUE_JAPAN_FUTURES",
            class_=PROSPECTIVE_ONLY,
            source="kabu_NEW_INFO_live_capture",
            historically_testable=False,
            history="20260911_20260914_only",
            datacube_local_n=datacube_n,
            additional_purchase_required=datacube_n == 0,
            catalog=src.get("NK225MINI_FUTURES_1MIN"),
            runtime_deployability="LIVE_CAPTURE_PROSPECTIVE",
        ),
        row(
            driver_id="TOPIX_FUT_TRUE",
            family="TRUE_JAPAN_FUTURES",
            class_=PROSPECTIVE_ONLY,
            source="kabu_NEW_INFO_live_capture",
            historically_testable=False,
            datacube_local_n=datacube_n,
            additional_purchase_required=datacube_n == 0,
            catalog=src.get("TOPIX_FUTURES_1MIN"),
        ),
        row(
            driver_id="USDJPY",
            family="FX",
            class_=UNAVAILABLE_NO_PURCHASE if not (dukas and usdjpy_local) else HISTORICAL_AVAILABLE,
            source="Dukascopy_candidate",
            credentials_present=dukas,
            local_files=usdjpy_local,
            historically_testable=bool(dukas and usdjpy_local),
            jquants_fx_minute_ok=any(p.get("ok") for p in fx_probes),
            fx_probes=fx_probes,
            catalog=src.get("USDJPY"),
            note="May be studied when legitimate history exists. Not blocked by futures wait in this architecture.",
        ),
        row(
            driver_id="NQ",
            family="GLOBAL_EQUITY_RISK",
            class_=UNAVAILABLE_NO_PURCHASE,
            source="Databento_candidate",
            credentials_present=databento,
            local_files=esnq_local,
            historically_testable=False,
            catalog=src.get("ES_NQ_FUTURES_1MIN"),
        ),
        row(
            driver_id="ES",
            family="GLOBAL_EQUITY_RISK",
            class_=UNAVAILABLE_NO_PURCHASE,
            source="Databento_candidate",
            credentials_present=databento,
            historically_testable=False,
        ),
        row(
            driver_id="KOSPI",
            family="ASIA",
            class_=UNAVAILABLE_NO_PURCHASE,
            historically_testable=False,
            catalog=src.get("KOSPI_KOSPI200"),
        ),
        row(
            driver_id="HSI",
            family="ASIA",
            class_=UNAVAILABLE_NO_PURCHASE,
            historically_testable=False,
            catalog=src.get("HSI"),
        ),
        row(
            driver_id="JGB_RATES",
            family="RATES",
            class_=UNAVAILABLE_NO_PURCHASE,
            historically_testable=False,
            note="DataCube JGB 1min not local. FRED cash yields are daily-only, not used as same-minute features.",
        ),
        row(
            driver_id="US_RATES",
            family="RATES",
            class_=UNAVAILABLE_NO_PURCHASE,
            historically_testable=False,
            note="ZT/ZN Databento not present. FRED daily not an intraday driver.",
        ),
        row(
            driver_id="WTI",
            family="COMMODITY",
            class_=UNAVAILABLE_NO_PURCHASE,
            historically_testable=False,
            note="Do not use stock 1605 as an oil driver (circular).",
        ),
    ]
    for d in drivers:
        if "class" not in d and "class_" in d:
            d["class"] = d.pop("class_")
        elif "class_" in d:
            d["class"] = d.pop("class_")
    testable = [d for d in drivers if d.get("historically_testable")]
    return {
        "drivers": drivers,
        "testable_ids": [d["driver_id"] for d in testable],
        "by_class": {
            HISTORICAL_AVAILABLE: [d["driver_id"] for d in drivers if d.get("class") == HISTORICAL_AVAILABLE],
            PROXY_ONLY: [d["driver_id"] for d in drivers if d.get("class") == PROXY_ONLY],
            PROSPECTIVE_ONLY: [d["driver_id"] for d in drivers if d.get("class") == PROSPECTIVE_ONLY],
            UNAVAILABLE_NO_PURCHASE: [d["driver_id"] for d in drivers if d.get("class") == UNAVAILABLE_NO_PURCHASE],
        },
        "did_not_purchase": True,
        "did_not_wait_for_futures_before_other_families": True,
        "did_not_fit_one_giant_model": True,
        "indices_daily_probe": idx_daily,
        "futures_daily_probe": fut_daily,
        "equity_minute_endpoint": ENDPOINT_MINUTE,
        "proxy_nk_present": proxy_nk.is_file(),
        "proxy_tx_present": proxy_tx.is_file(),
    }


assert HISTORICAL_AVAILABLE == "HISTORICAL_AVAILABLE"
assert PROXY_ONLY == "PROXY_ONLY"
