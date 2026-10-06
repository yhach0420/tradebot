"""Minute panel ingest to parquet. Tick semantics via official trades. No daily substitute."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

import pandas as pd

from research.aligned_historical_panel_v1 import (
    ENDPOINT_BULK_GET,
    ENDPOINT_BULK_LIST,
    ENDPOINT_TICK_BULK,
    PROBE_TICK_SYMBOLS,
)
from research.aligned_historical_panel_v1.minute_schema import map_minute_row, minute_schema_status
from research.aligned_historical_panel_v1.probe_minute import (
    OPEN_WINDOW,
    _addon_status,
    _fetch_minute_rows,
    _open_csv_text,
    _payload_url,
    _select_tick_file,
    probe_minute_entitlement,
)
from research.aligned_historical_panel_v1.time_semantics import (
    SEMANTICS_BAR_END,
    SEMANTICS_BAR_START,
    SEMANTICS_UNKNOWN,
    bar_clock,
    infer_time_semantics,
    ohlcv_from_prints,
    tick_minute_hhmm,
)
from research.daytrade_historical_research_foundation_v2 import (
    ENDPOINT_MINUTE,
    MINUTE_FROM_PREFERRED,
    MINUTE_TO,
    PROBE_DATE,
    PROBE_SYMBOL,
)
from research.daytrade_historical_research_foundation_v2.fetch_cache import SLEEP_SEC, _iso
from research.daytrade_historical_research_foundation_v2.isolation import REF
from research.fixed_daytrade_universe_v1.jquants_client import request_json
from research.fixed_daytrade_universe_v1.schema import symbol4

MINUTE_PAGE_GUARD = 2500
TICK_STREAM_MAX_BYTES = 8_000_000_000
TICK_STREAM_MAX_SEC = 900


def _minute_bar_0900(*, code: str, date_iso: str) -> dict[str, Any] | None:
    got = _fetch_minute_rows(code=code, date=date_iso, request=request_json)
    if (not got.get("ok")) and got.get("status") in {400, 404} and len(code) == 4:
        got = _fetch_minute_rows(code=code + "0", date=date_iso, request=request_json)
    if not got.get("ok"):
        return None
    mapped = []
    for raw in list(got.get("rows") or []):
        if not isinstance(raw, dict):
            continue
        try:
            mapped.append(map_minute_row(raw))
        except Exception:
            continue
    return next((r for r in mapped if r.get("time_label") == "09:00"), None)


def _bar_ohlcv(bar: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bar:
        return None
    return {
        "O": bar.get("open"),
        "H": bar.get("high"),
        "L": bar.get("low"),
        "C": bar.get("close"),
        "Vo": bar.get("volume"),
    }


def _stream_open_prints(*, url: str, want_codes: set[str], want_date: str) -> dict[str, Any]:
    """Stream official trades CSV. Date-skip without mixing Bid/Ask inference."""
    prints: list[dict[str, Any]] = []
    bytes_read = 0
    header = None
    started = time.monotonic()
    done_codes: set[str] = set()
    want_compact = str(want_date).replace("-", "")
    want_iso = _iso(want_compact) if len(want_compact) == 8 else str(want_date)
    date_idx = code_idx = time_idx = price_idx = vol_idx = 0
    req = urllib.request.Request(url, headers={"User-Agent": "kabu_native-daytrade-foundation-v2/1"})
    with urllib.request.urlopen(req, timeout=TICK_STREAM_MAX_SEC) as resp:
        buf = _open_csv_text(resp)
        for line in buf:
            bytes_read += len(line)
            if time.monotonic() - started > TICK_STREAM_MAX_SEC:
                break
            if bytes_read > TICK_STREAM_MAX_BYTES:
                break
            if header is None:
                header = [c.strip() for c in line.strip().split(",")]
                date_idx = header.index("Date") if "Date" in header else 0
                code_idx = header.index("Code") if "Code" in header else 1
                time_idx = header.index("Time") if "Time" in header else 2
                price_idx = header.index("Price") if "Price" in header else 4
                vol_idx = header.index("TradingVolume") if "TradingVolume" in header else 5
                continue
            if want_compact not in line and want_iso not in line:
                continue
            cols = line.rstrip("\n").split(",")
            if max(date_idx, code_idx, time_idx) >= len(cols):
                continue
            date = str(cols[date_idx]).replace("-", "")
            if date != want_compact:
                continue
            code = symbol4(cols[code_idx])
            if code not in want_codes:
                continue
            t = str(cols[time_idx] if time_idx < len(cols) else "")
            hhmm = tick_minute_hhmm(t)
            if hhmm not in OPEN_WINDOW:
                if hhmm and hhmm > "09:01" and code in want_codes:
                    done_codes.add(code)
                continue
            prints.append(
                {
                    "code": code,
                    "time": t,
                    "price": cols[price_idx] if price_idx < len(cols) else None,
                    "volume": cols[vol_idx] if vol_idx < len(cols) else None,
                }
            )
            if hhmm == "09:01":
                done_codes.add(code)
            if len(done_codes) >= len(want_codes) and all(
                any(p["code"] == c and str(p["time"]).startswith("09:00") for p in prints) for c in want_codes
            ):
                break
    return {
        "prints": prints,
        "bytes_read": bytes_read,
        "header": header,
        "elapsed_sec": round(time.monotonic() - started, 3),
    }


def probe_tick_crosscheck_v2(*, minute_7203: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "performed": False,
        "tick_source": "jquants_bulk_csv_equities_trades",
        "official_tick_time_field": "Time_execution_HH:MM:SS.ffffff",
        "reason": None,
        "semantics": SEMANTICS_UNKNOWN,
        "probe_symbols": list(PROBE_TICK_SYMBOLS),
        "documentation_alone_insufficient": True,
        "ohlcv_matched_per_symbol": True,
        "bid_ask_fields_in_header": False,
    }
    if not minute_7203.get("available"):
        out["reason"] = "minute_not_available"
        return out
    date_iso = _iso(PROBE_DATE)
    bars = {PROBE_SYMBOL: dict(minute_7203.get("bar_0900") or {})}
    bar_1605 = _minute_bar_0900(code="1605", date_iso=date_iso)
    if bar_1605:
        bars["1605"] = bar_1605
    direct = request_json(path=ENDPOINT_BULK_GET, params={"endpoint": ENDPOINT_TICK_BULK, "date": date_iso})
    url = _payload_url(direct.get("payload") or {}) if direct.get("ok") else ""
    if not url:
        listed = request_json(path=ENDPOINT_BULK_LIST, params={"endpoint": ENDPOINT_TICK_BULK, "from": date_iso, "to": date_iso})
        if not listed.get("ok"):
            out["reason"] = listed.get("reason")
            out["http_status"] = listed.get("status")
            out["addon_required"] = _addon_status(listed.get("status"), listed.get("reason"))
            return out
        files = list((listed.get("payload") or {}).get("data") or [])
        if not files:
            out["reason"] = "tick_bulk_list_empty"
            out["performed"] = True
            return out
        file_rec = _select_tick_file(files, probe_date=PROBE_DATE)
        key = str((file_rec or {}).get("Key") or (file_rec or {}).get("key") or "")
        if not key:
            out["reason"] = "tick_bulk_key_missing"
            return out
        got = request_json(path=ENDPOINT_BULK_GET, params={"key": key})
        if not got.get("ok"):
            out["reason"] = got.get("reason")
            out["http_status"] = got.get("status")
            return out
        url = _payload_url(got.get("payload") or {})
        out["file_key_prefix"] = key[:48]
        out["file_size"] = (file_rec or {}).get("Size")
        out["payload_keys"] = list((got.get("payload") or {}).keys())
    else:
        out["bulk_get_endpoint_date"] = True
        out["payload_keys"] = list((direct.get("payload") or {}).keys())
    if not url:
        out["reason"] = "tick_download_url_missing"
        return out
    want = {symbol4(c) for c in PROBE_TICK_SYMBOLS}
    try:
        streamed = _stream_open_prints(url=url, want_codes=want, want_date=PROBE_DATE)
    except Exception as exc:
        out["reason"] = f"tick_stream_failed:{type(exc).__name__}"
        return out
    prints = list(streamed.get("prints") or [])
    header = list(streamed.get("header") or [])
    bid_ask = any(k in header for k in ("Bid", "Ask", "BestBid", "BestAsk", "BidPrice", "AskPrice"))
    inferred_by_symbol: dict[str, dict[str, Any]] = {}
    semantics_set: set[str] = set()
    for code in sorted(want):
        subset = [p for p in prints if p.get("code") == code]
        label_prints = [p for p in subset if str(p.get("time") or "").startswith("09:00")]
        prev_prints = [p for p in subset if str(p.get("time") or "").startswith("08:59")]
        inferred = infer_time_semantics(
            labeled_bar="09:00",
            tick_times=[str(p.get("time") or "") for p in subset],
            bar_ohlcv=_bar_ohlcv(bars.get(code)),
            ticks_in_label_ohlcv=ohlcv_from_prints(label_prints),
            ticks_in_prev_ohlcv=ohlcv_from_prints(prev_prints),
        )
        inferred_by_symbol[code] = inferred
        sem = str(inferred.get("EQUITY_MINUTE_TIME_SEMANTICS") or SEMANTICS_UNKNOWN)
        if sem in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
            semantics_set.add(sem)
    semantics = SEMANTICS_UNKNOWN
    if len(semantics_set) == 1:
        semantics = next(iter(semantics_set))
    elif not semantics_set:
        # Times-only fallback across mixed symbols if OHLCV did not match a single name.
        mixed = infer_time_semantics(
            labeled_bar="09:00",
            tick_times=[str(p.get("time") or "") for p in prints],
        )
        semantics = str(mixed.get("EQUITY_MINUTE_TIME_SEMANTICS") or SEMANTICS_UNKNOWN)
        inferred_by_symbol["mixed_times_only"] = mixed
    out.update(
        {
            "performed": True,
            "download_url_recorded": False,
            "tick_print_n": len(prints),
            "tick_0900_n": sum(1 for p in prints if str(p.get("time") or "").startswith("09:00")),
            "tick_0859_n": sum(1 for p in prints if str(p.get("time") or "").startswith("08:59")),
            "bytes_read": streamed.get("bytes_read"),
            "elapsed_sec": streamed.get("elapsed_sec"),
            "header": header,
            "first_tick": prints[0] if prints else None,
            "last_tick": prints[-1] if prints else None,
            "infer_by_symbol": inferred_by_symbol,
            "semantics": semantics,
            "bid_ask_fields_in_header": bid_ask,
            "reason": None if semantics in {SEMANTICS_BAR_START, SEMANTICS_BAR_END} else "ticks_did_not_disambiguate",
        }
    )
    return out


def probe_entitlement_and_ticks() -> dict[str, Any]:
    minute = probe_minute_entitlement()
    ticks = probe_tick_crosscheck_v2(minute_7203=minute)
    sem = str(ticks.get("semantics") or SEMANTICS_UNKNOWN)
    clock = None
    if sem in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
        clock = bar_clock(labeled_date=PROBE_DATE, labeled_time="09:00", semantics=sem)
    return {
        "minute": minute,
        "ticks": ticks,
        "EQUITY_MINUTE_TIME_SEMANTICS": sem,
        "clock_example_0900": clock,
        "same_bar_close_entry": False,
        "feature_available_no_earlier_than": "bar_end",
        "bid_ask_in_official_trades": bool(ticks.get("bid_ask_fields_in_header")),
        "official_tick_fields": ["Date", "Code", "Time", "SessionDistinction", "Price", "TradingVolume", "TransactionId"],
    }


def _parquet_path(symbol: str):
    return REF / "minute" / f"minute_{symbol}_{MINUTE_FROM_PREFERRED}_{MINUTE_TO}.parquet"


def _meta_path(path):
    return path.with_suffix(path.suffix + ".meta.json")


def ingest_symbol_minute(*, symbol: str, semantics: str) -> dict[str, Any]:
    path = _parquet_path(symbol)
    meta_path = _meta_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if meta.get("complete") and int(meta.get("row_n") or 0) > 0:
            return {
                "symbol": symbol,
                "ok": True,
                "from_cache": True,
                "row_n": int(meta.get("row_n") or 0),
                "first": meta.get("first"),
                "last": meta.get("last"),
                "path": str(path),
                "pages": meta.get("pages"),
            }
    rows: list[dict[str, Any]] = []
    code = symbol
    params = {"code": code, "from": _iso(MINUTE_FROM_PREFERRED), "to": _iso(MINUTE_TO)}
    pages = 0
    sample = None
    last_status = None
    truncated = False
    while pages < MINUTE_PAGE_GUARD:
        pages += 1
        got = request_json(path=ENDPOINT_MINUTE, params=params)
        last_status = got.get("status")
        if (not got.get("ok")) and last_status in {400, 404} and len(code) == 4 and pages == 1:
            code = symbol + "0"
            params = {"code": code, "from": _iso(MINUTE_FROM_PREFERRED), "to": _iso(MINUTE_TO)}
            got = request_json(path=ENDPOINT_MINUTE, params=params)
            last_status = got.get("status")
        if not got.get("ok"):
            return {"symbol": symbol, "ok": False, "reason": got.get("reason"), "status": last_status, "from_cache": False, "pages": pages}
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
            clock = None
            if semantics in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
                clock = bar_clock(labeled_date=mapped["date"], labeled_time=mapped["time_label"], semantics=semantics)
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
                    "trading_value": mapped.get("trading_value"),
                    "bar_start_jst": None if not clock else clock["bar_start_jst"],
                    "bar_end_jst": None if not clock else clock["bar_end_jst"],
                    "available_at_jst": None if not clock else clock["available_at_jst"],
                }
            )
        nxt = payload.get("pagination_key")
        if not nxt:
            break
        if pages >= MINUTE_PAGE_GUARD:
            truncated = True
            break
        params = {"code": code, "from": _iso(MINUTE_FROM_PREFERRED), "to": _iso(MINUTE_TO), "pagination_key": str(nxt)}
        time.sleep(SLEEP_SEC)
    if truncated:
        return {"symbol": symbol, "ok": False, "reason": "minute_pagination_truncated", "from_cache": False, "status": last_status, "pages": pages, "row_n": len(rows)}
    if not rows:
        return {"symbol": symbol, "ok": False, "reason": "minute_empty", "schema": minute_schema_status(sample), "from_cache": False, "status": last_status}
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.is_file():
        tmp.unlink()
    df = pd.DataFrame(rows)
    df.to_parquet(tmp, index=False)
    if path.is_file():
        path.unlink()
    tmp.replace(path)
    first = str(df["date"].min())
    last = str(df["date"].max())
    meta = {
        "complete": True,
        "symbol": symbol,
        "row_n": int(len(df)),
        "first": first,
        "last": last,
        "pages": pages,
        "preferred_from": MINUTE_FROM_PREFERRED,
        "preferred_to": MINUTE_TO,
        "api_key_recorded": False,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "symbol": symbol,
        "ok": True,
        "from_cache": False,
        "row_n": int(len(df)),
        "first": first,
        "last": last,
        "schema": minute_schema_status(sample),
        "path": str(path),
        "pages": pages,
    }


def ingest_pool_minutes(*, symbols: list[str], semantics: str) -> dict[str, Any]:
    cov = []
    firsts: list[str] = []
    lasts: list[str] = []
    fail = []
    for i, sym in enumerate(symbols, start=1):
        got = ingest_symbol_minute(symbol=sym, semantics=semantics)
        print(f"MINUTE {i}/{len(symbols)} {sym} ok={got.get('ok')} n={got.get('row_n')} cache={got.get('from_cache')}", flush=True)
        cov.append({k: got.get(k) for k in ("symbol", "ok", "row_n", "first", "last", "from_cache", "reason", "status", "pages")})
        if got.get("ok"):
            if got.get("first"):
                firsts.append(str(got["first"]))
            if got.get("last"):
                lasts.append(str(got["last"]))
        else:
            fail.append(sym)
    return {
        "ok": bool(cov) and not fail,
        "symbol_n": len(symbols),
        "ok_n": sum(1 for r in cov if r.get("ok")),
        "fail_symbols": fail,
        "coverage": cov,
        "history_first": min(firsts) if firsts else None,
        "history_last": max(lasts) if lasts else None,
        "format": "parquet",
        "results_csv_n": 0,
        "preferred_from": MINUTE_FROM_PREFERRED,
        "preferred_to": MINUTE_TO,
        "full_tick_panel_ingested": False,
        "tick_panel_note": "Official ticks are market-wide monthly bulk files. This run uses ticks for Time semantics sample cross-check only. Per-symbol 2y tick frequency is not computed. Spread is not inferred.",
    }


assert PROBE_SYMBOL == "7203"
assert ENDPOINT_MINUTE.endswith("minute")
assert MINUTE_PAGE_GUARD >= 400
