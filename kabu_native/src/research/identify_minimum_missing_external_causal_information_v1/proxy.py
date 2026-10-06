"""TRACK A: equity-minute index ETF proxies. PROXY_NOT_FUTURES. Discovery dates only."""
from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd

from research.aligned_historical_panel_v1 import ENDPOINT_MINUTE
from research.aligned_historical_panel_v1.minute_schema import map_minute_row
from research.aligned_historical_panel_v1.time_semantics import SEMANTICS_BAR_START, bar_clock
from research.fixed_daytrade_universe_v1.jquants_client import request_json
from research.identify_minimum_missing_external_causal_information_v1 import PROXY_NK, PROXY_TOPIX
from research.identify_minimum_missing_external_causal_information_v1.isolation import CACHE

PAGE_GUARD = 2500
SLEEP_SEC = 0.15


def _iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _parquet_path(symbol: str, first: str, last: str) -> Any:
    root = CACHE / "proxy_not_futures"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"minute_{symbol}_{first}_{last}.parquet"


def fetch_proxy_symbol(*, symbol: str, first: str, last: str, allowed_dates: set[str]) -> dict[str, Any]:
    path = _parquet_path(symbol, first, last)
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if meta.get("complete") and int(meta.get("row_n") or 0) > 0:
            print(f"PROXY_CACHE {symbol} n={meta.get('row_n')}", flush=True)
            return {**meta, "ok": True, "from_cache": True, "path": str(path), "label": "PROXY_NOT_FUTURES"}
    rows: list[dict[str, Any]] = []
    code = symbol
    params = {"code": code, "from": _iso(first), "to": _iso(last)}
    pages = 0
    last_status = None
    sample = None
    while pages < PAGE_GUARD:
        pages += 1
        got = request_json(path=ENDPOINT_MINUTE, params=params)
        last_status = got.get("status")
        if (not got.get("ok")) and last_status in {400, 404} and len(code) == 4 and pages == 1:
            code = symbol + "0"
            params = {"code": code, "from": _iso(first), "to": _iso(last)}
            got = request_json(path=ENDPOINT_MINUTE, params=params)
            last_status = got.get("status")
        if not got.get("ok"):
            return {
                "symbol": symbol,
                "ok": False,
                "reason": got.get("reason"),
                "status": last_status,
                "label": "PROXY_NOT_FUTURES",
                "from_cache": False,
            }
        payload = got.get("payload") or {}
        chunk = list(payload.get("data") or [])
        for raw in chunk:
            if not isinstance(raw, dict):
                continue
            if sample is None:
                sample = raw
            try:
                mapped = map_minute_row(raw)
            except Exception:
                continue
            day = str(mapped["date"])
            if allowed_dates and day not in allowed_dates:
                continue
            clock = bar_clock(labeled_date=mapped["date"], labeled_time=mapped["time_label"], semantics=SEMANTICS_BAR_START)
            rows.append(
                {
                    "symbol": mapped["symbol"],
                    "date": mapped["date"],
                    "time_label": mapped["time_label"],
                    "open": mapped["open"],
                    "high": mapped["high"],
                    "low": mapped["low"],
                    "close": mapped["close"],
                    "volume": mapped["volume"],
                    "bar_start_jst": None if not clock else clock["bar_start_jst"],
                    "bar_end_jst": None if not clock else clock["bar_end_jst"],
                    "available_at_jst": None if not clock else clock["available_at_jst"],
                }
            )
        nxt = payload.get("pagination_key")
        if not nxt:
            break
        params = {"code": code, "from": _iso(first), "to": _iso(last), "pagination_key": str(nxt)}
        time.sleep(SLEEP_SEC)
    if not rows:
        return {"symbol": symbol, "ok": False, "reason": "empty_proxy_rows", "status": last_status, "label": "PROXY_NOT_FUTURES"}
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)
    meta = {
        "symbol": symbol,
        "ok": True,
        "complete": True,
        "row_n": int(len(df)),
        "first": str(df["date"].min()),
        "last": str(df["date"].max()),
        "pages": pages,
        "requested_code": code,
        "endpoint": ENDPOINT_MINUTE,
        "semantics": SEMANTICS_BAR_START,
        "label": "PROXY_NOT_FUTURES",
        "never_claim_etf_proves_futures_lead": True,
        "frozen_validation_dates_fetched": False,
        "confirmation_dates_fetched": False,
        "path": str(path),
        "sample_keys": sorted(sample.keys()) if sample else [],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PROXY_FETCHED {symbol} n={meta['row_n']} {meta['first']}..{meta['last']}", flush=True)
    return meta


def load_proxy_frame(symbol: str, first: str, last: str) -> pd.DataFrame:
    path = _parquet_path(symbol, first, last)
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def fetch_discovery_proxies(*, first: str, last: str, allowed_dates: set[str]) -> dict[str, Any]:
    nk = fetch_proxy_symbol(symbol=PROXY_NK, first=first, last=last, allowed_dates=allowed_dates)
    tx = fetch_proxy_symbol(symbol=PROXY_TOPIX, first=first, last=last, allowed_dates=allowed_dates)
    return {
        "label": "PROXY_NOT_FUTURES",
        "nk": nk,
        "topix": tx,
        "ok": bool(nk.get("ok") and tx.get("ok")),
        "never_claim_etf_proves_futures_lead": True,
        "true_futures": False,
        "instruments": {"nk_proxy": PROXY_NK, "topix_proxy": PROXY_TOPIX},
        "history_requested": [first, last],
    }
