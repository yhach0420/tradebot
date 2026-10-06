"""External context inventory. No low-quality fallback. No futures autosplice. No 20260914 live."""
from __future__ import annotations

import os
from typing import Any

from research.aligned_historical_panel_v1 import (
    ENDPOINT_FUTURES_DAILY,
    ENDPOINT_INDICES_DAILY,
    ENDPOINT_TOPIX_DAILY,
    PROBE_DATE,
)
from research.aligned_historical_panel_v1.isolation import NATIVE, REF_PANEL
from research.fixed_daytrade_universe_v1.jquants_client import request_json

STATUS_AVAILABLE = "AVAILABLE"
STATUS_PARTIAL = "PARTIAL"
STATUS_MISSING = "MISSING"


def _env_present(*names: str) -> list[str]:
    return [n for n in names if str(os.environ.get(n) or "").strip()]


def _local_files(rel: str) -> int:
    root = NATIVE / rel
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def inventory_external(*, request=request_json) -> dict[str, Any]:
    idx = request(path=ENDPOINT_INDICES_DAILY, params={"date": f"{PROBE_DATE[:4]}-{PROBE_DATE[4:6]}-{PROBE_DATE[6:8]}"})
    topix = request(path=ENDPOINT_TOPIX_DAILY, params={"date": f"{PROBE_DATE[:4]}-{PROBE_DATE[4:6]}-{PROBE_DATE[6:8]}"})
    fut = request(path=ENDPOINT_FUTURES_DAILY, params={"date": f"{PROBE_DATE[:4]}-{PROBE_DATE[4:6]}-{PROBE_DATE[6:8]}"})

    def _jq(got: dict[str, Any], *, minute: bool) -> dict[str, Any]:
        ok = bool(got.get("ok"))
        n = len(list((got.get("payload") or {}).get("data") or [])) if ok else 0
        if minute:
            status = STATUS_MISSING
        elif ok and n:
            status = STATUS_PARTIAL
        else:
            status = STATUS_MISSING
        return {
            "ok": ok,
            "http_status": got.get("status"),
            "reason": got.get("reason"),
            "row_n": n,
            "status": status,
            "granularity": "daily_not_1min" if ok else "unavailable",
            "autosplice": False,
        }

    databento = _env_present("DATABENTO_API_KEY", "DATABENTO_KEY")
    dukas = _env_present("DUKASCOPY_USER", "DUKASCOPY_PASSWORD")
    rows = [
        {
            "name": "NK225_TOPIX_context",
            "status": STATUS_PARTIAL if (idx.get("ok") or topix.get("ok")) else STATUS_MISSING,
            "minute_1": False,
            "note": "J-Quants indices API is daily only; 1-min NK/TOPIX requires DataCube per-contract later",
            "indices_daily": _jq(idx, minute=False),
            "topix_daily": _jq(topix, minute=False),
        },
        {
            "name": "NK225_NK225mini_TOPIX_futures_1min",
            "status": STATUS_MISSING,
            "minute_1": False,
            "per_contract": True,
            "autosplice_forbidden": True,
            "note": "DataCube monthly per-contract CSVs not present; API futures bars are daily",
            "futures_daily": _jq(fut, minute=False),
            "local_datacube_files": _local_files("data/reference/datacube"),
        },
        {
            "name": "USDJPY",
            "status": STATUS_MISSING,
            "source": "Dukascopy_candidate",
            "raw_timezone": "UTC_required",
            "canonical": "UTC_to_JST_via_zoneinfo",
            "credentials_present": bool(dukas),
            "local_files": _local_files("data/reference/historical_panel/usdjpy"),
        },
        {
            "name": "ES_NQ",
            "status": STATUS_MISSING,
            "source": "Databento_candidate",
            "ts_event": "UNCONFIRMED",
            "credentials_present": bool(databento),
            "local_files": _local_files("data/reference/historical_panel/us_futures"),
        },
        {
            "name": "WTI_CL",
            "status": STATUS_MISSING,
            "source": "Databento_candidate",
            "not_mixed_with_brent": True,
            "brent": "optional_not_fetched",
            "credentials_present": bool(databento),
        },
        {
            "name": "rates_JGB_ZT_ZN",
            "status": STATUS_MISSING,
            "cash_yield_not_treated_as_1min": True,
        },
        {
            "name": "Asia_KOSPI_HSI_CSI_A50",
            "status": STATUS_MISSING,
            "note": "quality_1min_source_unconfirmed; no_low_quality_fallback",
        },
    ]
    core = {
        "japan_equity_fixed_universe": "pending_minute_probe",
        "nk_topix_context": rows[0]["status"],
        "usdjpy": rows[2]["status"],
        "us_risk_tech": rows[3]["status"],
        "oil": rows[4]["status"],
    }
    core_ok = all(core[k] == STATUS_AVAILABLE for k in ("usdjpy", "us_risk_tech", "oil")) and core["nk_topix_context"] in {
        STATUS_AVAILABLE,
        STATUS_PARTIAL,
    }
    return {
        "rows": rows,
        "core": core,
        "core_sufficient": False,
        "core_sufficient_computed": core_ok,
        "asia_optional_missing": True,
        "futures_kept_separate": True,
        "futures_autosplice": False,
        "yfinance_used": False,
        "did_not_use_20260914_live": True,
        "ref_panel": str(REF_PANEL),
    }


assert STATUS_MISSING == "MISSING"
assert STATUS_PARTIAL == "PARTIAL"
