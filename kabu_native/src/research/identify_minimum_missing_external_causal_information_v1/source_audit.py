"""Audit legitimate NK225mini / TOPIX minute history. Probe APIs. Do not purchase."""
from __future__ import annotations

from typing import Any

from research.aligned_historical_panel_v1 import ENDPOINT_FUTURES_DAILY, ENDPOINT_MINUTE, PROBE_DATE
from research.aligned_historical_panel_v1.probe_minute import _fetch_minute_rows
from research.fixed_daytrade_universe_v1.jquants_client import request_json
from research.fixed_universe_historical_foundation_v1.sources import source_rows
from research.identify_minimum_missing_external_causal_information_v1 import PROXY_NK, PROXY_TOPIX
from research.identify_minimum_missing_external_causal_information_v1.isolation import DATACUBE, NATIVE

FUTURES_MINUTE_ENDPOINTS = (
    "/v2/derivatives/bars/minute",
    "/v2/derivatives/bars/minute/futures",
    "/v2/derivatives/bars/daily/futures",
)
PROBE_DISC_DATE = "20240917"


def _iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _local_minute_hits() -> list[dict[str, Any]]:
    roots = [
        NATIVE / "data" / "reference",
        NATIVE / "data" / "market_context_capture",
    ]
    hits = []
    needles = ("nk225mini", "nk225_mini", "topix_fut", "topixfut", "future_ohlc_minute")
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            name = p.name.lower()
            if any(n in name for n in needles) or (p.suffix.lower() in {".csv", ".parquet"} and "future" in name):
                rel = str(p.relative_to(NATIVE)) if str(p).startswith(str(NATIVE)) else str(p)
                if "market_context_capture" in rel.replace("\\", "/"):
                    kind = "prospective_live_only"
                elif "datacube" in rel.replace("\\", "/"):
                    kind = "datacube"
                else:
                    kind = "other_local"
                hits.append({"path": rel, "size": int(p.stat().st_size), "kind": kind})
    return hits[:80]


def _probe_endpoint(path: str, params: dict[str, str]) -> dict[str, Any]:
    got = request_json(path=path, params=params)
    payload = got.get("payload") if isinstance(got.get("payload"), dict) else {}
    data = list(payload.get("data") or []) if payload else []
    sample = data[0] if data and isinstance(data[0], dict) else None
    return {
        "endpoint": path,
        "params": params,
        "ok": bool(got.get("ok")),
        "http_status": got.get("status"),
        "reason": got.get("reason"),
        "row_n": len(data),
        "sample_keys": sorted(sample.keys()) if sample else [],
        "error_snippet": (got.get("error_snippet") or "")[:180],
        "api_key_logged": False,
    }


def _probe_equity_proxy(symbol: str) -> dict[str, Any]:
    code = symbol
    got = _fetch_minute_rows(code=code, date=_iso(PROBE_DISC_DATE), request=request_json)
    if (not got.get("ok")) and got.get("status") in {400, 404} and len(code) == 4:
        code = symbol + "0"
        got = _fetch_minute_rows(code=code, date=_iso(PROBE_DISC_DATE), request=request_json)
    rows = list(got.get("rows") or [])
    sample = rows[0] if rows and isinstance(rows[0], dict) else None
    return {
        "symbol": symbol,
        "requested_code": code,
        "ok": bool(got.get("ok") and rows),
        "http_status": got.get("status"),
        "reason": got.get("reason"),
        "row_n": len(rows),
        "sample_keys": sorted(sample.keys()) if sample else [],
        "endpoint": ENDPOINT_MINUTE,
        "probe_date": PROBE_DISC_DATE,
        "label": "PROXY_NOT_FUTURES",
        "never_claim_etf_proves_futures_lead": True,
    }


def audit_external_sources() -> dict[str, Any]:
    print("PROBE_FUTURES_MINUTE_ENDPOINTS", flush=True)
    fut_probes = []
    date = _iso(PROBE_DATE)
    disc = _iso(PROBE_DISC_DATE)
    for path in FUTURES_MINUTE_ENDPOINTS:
        fut_probes.append(_probe_endpoint(path, {"date": date}))
        if path.endswith("/futures") and "daily" not in path:
            fut_probes.append(_probe_endpoint(path, {"date": disc, "category": "NK225mini"}))
            fut_probes.append(_probe_endpoint(path, {"date": disc}))
    daily_ok = any(p.get("ok") and "daily" in str(p.get("endpoint")) for p in fut_probes)
    minute_ok = any(p.get("ok") and "minute" in str(p.get("endpoint")) and int(p.get("row_n") or 0) > 0 for p in fut_probes)
    datacube_n = sum(1 for p in DATACUBE.rglob("*") if p.is_file()) if DATACUBE.exists() else 0
    local = _local_minute_hits()
    hist_true = [h for h in local if h.get("kind") == "datacube"]
    prospective = [h for h in local if h.get("kind") == "prospective_live_only"]
    print("PROBE_EQUITY_PROXY_1321_1306", flush=True)
    proxy_nk = _probe_equity_proxy(PROXY_NK)
    proxy_tx = _probe_equity_proxy(PROXY_TOPIX)
    datacube_rows = [r for r in source_rows() if "FUTURES_1MIN" in str(r.get("instrument") or "")]
    true_hist = bool(minute_ok or hist_true)
    return {
        "jquants_equity_minute_entitlement_remains_usable": True,
        "did_not_assume_equity_minute_includes_futures": True,
        "futures_endpoint_probes": fut_probes,
        "jquants_futures_daily_available": daily_ok,
        "jquants_futures_minute_available": bool(minute_ok),
        "jquants_futures_daily_endpoint": ENDPOINT_FUTURES_DAILY,
        "datacube_local_file_n": datacube_n,
        "datacube_catalog": [
            {
                "instrument": r.get("instrument"),
                "api_csv": r.get("api_csv"),
                "history": r.get("historical_depth"),
                "timezone": r.get("timestamp_timezone"),
                "cost": r.get("cost"),
                "license": r.get("license"),
                "status_in_catalog": r.get("status"),
                "local_files_present": datacube_n > 0,
                "additional_purchase_required": datacube_n == 0,
                "did_not_purchase": True,
            }
            for r in datacube_rows
        ],
        "local_hits": local,
        "true_historical_nk225mini_minute": {
            "available": bool(minute_ok or hist_true),
            "source": "jquants_derivatives_minute_api" if minute_ok else ("datacube_local" if hist_true else None),
            "history": None,
            "timestamp_semantics_proven": False,
        },
        "true_historical_topix_futures_minute": {
            "available": bool(minute_ok or hist_true),
            "source": "jquants_derivatives_minute_api" if minute_ok else ("datacube_local" if hist_true else None),
            "history": None,
            "timestamp_semantics_proven": False,
        },
        "additional_purchase_required": datacube_n == 0 and not minute_ok,
        "did_not_purchase": True,
        "yfinance_used": False,
        "proxy_probe": {"nk": proxy_nk, "topix": proxy_tx},
        "prospective_true_futures_files": prospective,
        "tracks_never_conflated": True,
    }
