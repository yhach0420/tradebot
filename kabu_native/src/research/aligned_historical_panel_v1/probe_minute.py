"""Probe J-Quants minute entitlement, schema, and tick cross-check. No daily substitute."""
from __future__ import annotations

import gzip
import io
import time
import urllib.request
from typing import Any

from research.aligned_historical_panel_v1 import (
    ENDPOINT_BULK_GET,
    ENDPOINT_BULK_LIST,
    ENDPOINT_MINUTE,
    ENDPOINT_TICK_BULK,
    PROBE_DATE,
    PROBE_SYMBOL,
    PROBE_TICK_SYMBOLS,
)
from research.aligned_historical_panel_v1.minute_schema import map_minute_row, minute_schema_status
from research.aligned_historical_panel_v1.time_semantics import (
    SEMANTICS_UNKNOWN,
    infer_time_semantics,
    ohlcv_from_prints,
    tick_minute_hhmm,
)
from research.fixed_daytrade_universe_v1.jquants_auth import resolve_api_key_meta
from research.fixed_daytrade_universe_v1.jquants_client import request_json
from research.fixed_daytrade_universe_v1.schema import symbol4

TICK_STREAM_MAX_BYTES = 8_000_000_000
TICK_STREAM_MAX_SEC = 600
MINUTE_PAGE_MAX = 20
OPEN_WINDOW = ("08:59", "09:00", "09:01")


def _iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _addon_status(http_status: int | None, reason: str | None) -> bool:
    if http_status in {401, 402, 403}:
        return True
    text = str(reason or "").lower()
    return "addon" in text or "not subscribed" in text or "permission" in text


def _fetch_minute_rows(*, code: str, date: str, request) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    params = {"code": code, "date": date}
    last = None
    pages = 0
    while pages < MINUTE_PAGE_MAX:
        pages += 1
        last = request(path=ENDPOINT_MINUTE, params=params)
        if not last.get("ok"):
            return {**last, "rows": rows, "pages": pages}
        payload = last.get("payload") or {}
        chunk = list(payload.get("data") or [])
        rows.extend(item for item in chunk if isinstance(item, dict))
        nxt = payload.get("pagination_key")
        if not nxt:
            break
        params = {"code": code, "date": date, "pagination_key": str(nxt)}
    return {**last, "rows": rows, "pages": pages}


def probe_minute_entitlement(*, request=request_json) -> dict[str, Any]:
    cred = resolve_api_key_meta()
    base = {
        "credentials_present": bool(cred.get("present")),
        "env_var_name": cred.get("env_var_name"),
        "yfinance_used": False,
        "daily_not_used_as_intraday": True,
        "probe_date": PROBE_DATE,
        "probe_symbol": PROBE_SYMBOL,
        "endpoint": ENDPOINT_MINUTE,
        "official_time_docs": "Time_HH:mm_not_labeled_bar_start_or_end",
        "history_depth_official": "past_2_years",
        "empty_minutes_omitted_official": True,
    }
    if not cred.get("present"):
        return {**base, "available": False, "addon_required": False, "reason": "jquants_api_key_missing"}
    got = _fetch_minute_rows(code=PROBE_SYMBOL, date=_iso(PROBE_DATE), request=request)
    if (not got.get("ok")) and got.get("status") in {400, 404}:
        got = _fetch_minute_rows(code=PROBE_SYMBOL + "0", date=_iso(PROBE_DATE), request=request)
        base["code_retried_5digit"] = True
    status = got.get("status")
    if not got.get("ok"):
        addon = _addon_status(status, got.get("reason"))
        return {
            **base,
            "available": False,
            "addon_required": addon,
            "http_status": status,
            "reason": got.get("reason"),
            "error_snippet": got.get("error_snippet"),
        }
    rows = list(got.get("rows") or [])
    sample = rows[0] if rows else None
    schema = minute_schema_status(sample)
    mapped = []
    if schema["ok"]:
        for raw in rows:
            mapped.append(map_minute_row(raw))
    times = sorted({r["time_label"] for r in mapped})
    bar_0900 = next((r for r in mapped if r["time_label"] == "09:00"), None)
    dates = sorted({r["date"] for r in mapped})
    return {
        **base,
        "available": True,
        "addon_required": False,
        "http_status": status,
        "row_n_sample": len(rows),
        "pages": got.get("pages"),
        "schema": schema,
        "sample_raw_keys": schema["raw_keys"],
        "has_0900_bar": bar_0900 is not None,
        "bar_0900": bar_0900,
        "sample_times_head": times[:8],
        "reason": None if schema["ok"] and rows else ("schema_mismatch:" + str(schema["missing_required"]) if not schema["ok"] else "minute_empty"),
        "min_date_in_sample": dates[0] if dates else None,
        "max_date_in_sample": dates[-1] if dates else None,
    }


def _select_tick_file(files: list[dict[str, Any]], *, probe_date: str) -> dict[str, Any] | None:
    if not files:
        return None
    day = str(probe_date)
    iso = _iso(day)
    ym = day[:6]
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for rec in files:
        key = str(rec.get("Key") or rec.get("key") or "")
        score = 0
        if day in key.replace("-", "") or iso in key:
            score += 100
        if ym in key.replace("-", ""):
            score += 10
        if "trades" in key:
            score += 1
        ranked.append((score, int(rec.get("Size") or 0), rec))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if ranked[0][0] > 0:
        return ranked[0][2]
    return files[0]


class _PrefixedStream(io.RawIOBase):
    def __init__(self, prefix: bytes, fh):
        self._prefix = prefix
        self._fh = fh

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._prefix:
            if size is None or size < 0:
                out = self._prefix + self._fh.read()
                self._prefix = b""
                return out
            take = self._prefix[:size]
            self._prefix = self._prefix[size:]
            if len(take) < size:
                take += self._fh.read(size - len(take))
            return take
        return self._fh.read(size if size is not None else -1)


def _open_csv_text(resp):
    head = resp.read(2)
    stacked = _PrefixedStream(head, resp)
    if head == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=stacked)
        return io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
    return io.TextIOWrapper(stacked, encoding="utf-8", errors="replace")


def _stream_tick_prints(*, url: str, want_codes: set[str], want_date: str) -> dict[str, Any]:
    prints: list[dict[str, Any]] = []
    bytes_read = 0
    header = None
    started = time.monotonic()
    done_codes: set[str] = set()
    req = urllib.request.Request(url, headers={"User-Agent": "kabu_native-aligned-panel/1"})
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
                continue
            cols = line.rstrip("\n").split(",")
            rec = dict(zip(header, cols))
            code = symbol4(rec.get("Code") or "")
            if code not in want_codes:
                if done_codes and len(done_codes) >= len(want_codes):
                    break
                continue
            date = str(rec.get("Date") or "").replace("-", "")
            if date and date != want_date.replace("-", ""):
                continue
            t = str(rec.get("Time") or "")
            hhmm = tick_minute_hhmm(t)
            if hhmm not in OPEN_WINDOW:
                if hhmm and hhmm > "09:01" and code in want_codes:
                    done_codes.add(code)
                continue
            prints.append(
                {
                    "code": code,
                    "time": t,
                    "price": rec.get("Price"),
                    "volume": rec.get("TradingVolume"),
                }
            )
            if hhmm == "09:01":
                done_codes.add(code)
            if len(done_codes) >= len(want_codes) and all(
                any(p["code"] == c and str(p["time"]).startswith("09:00") for p in prints) for c in want_codes
            ):
                break
    return {"prints": prints, "bytes_read": bytes_read, "header": header, "elapsed_sec": round(time.monotonic() - started, 3)}


def _payload_url(payload: dict[str, Any]) -> str:
    return str(payload.get("url") or payload.get("download_url") or payload.get("DownloadUrl") or "")


def probe_tick_crosscheck(*, minute_probe: dict[str, Any], request=request_json) -> dict[str, Any]:
    out: dict[str, Any] = {
        "performed": False,
        "tick_source": "jquants_bulk_csv_equities_trades",
        "official_tick_time_field": "Time_execution_HH:MM:SS.ffffff",
        "reason": None,
        "semantics": SEMANTICS_UNKNOWN,
        "probe_symbols": list(PROBE_TICK_SYMBOLS),
        "documentation_alone_insufficient": True,
    }
    if not minute_probe.get("available"):
        out["reason"] = "minute_not_available"
        return out
    date_iso = _iso(PROBE_DATE)
    direct = request(path=ENDPOINT_BULK_GET, params={"endpoint": ENDPOINT_TICK_BULK, "date": date_iso})
    url = _payload_url(direct.get("payload") or {}) if direct.get("ok") else ""
    file_rec = None
    listed = None
    if not url:
        listed = request(
            path=ENDPOINT_BULK_LIST,
            params={"endpoint": ENDPOINT_TICK_BULK, "from": date_iso, "to": date_iso},
        )
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
        got = request(path=ENDPOINT_BULK_GET, params={"key": key})
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
        streamed = _stream_tick_prints(url=url, want_codes=want, want_date=PROBE_DATE)
    except Exception as exc:
        out["reason"] = f"tick_stream_failed:{type(exc).__name__}"
        return out
    prints = list(streamed.get("prints") or [])
    label_prints = [p for p in prints if str(p.get("time") or "").startswith("09:00")]
    prev_prints = [p for p in prints if str(p.get("time") or "").startswith("08:59")]
    bar = dict(minute_probe.get("bar_0900") or {})
    bar_ohlcv = None
    if bar:
        bar_ohlcv = {
            "O": bar.get("open"),
            "H": bar.get("high"),
            "L": bar.get("low"),
            "C": bar.get("close"),
            "Vo": bar.get("volume"),
        }
    inferred = infer_time_semantics(
        labeled_bar="09:00",
        tick_times=[str(p.get("time") or "") for p in prints],
        bar_ohlcv=bar_ohlcv,
        ticks_in_label_ohlcv=ohlcv_from_prints(label_prints),
        ticks_in_prev_ohlcv=ohlcv_from_prints(prev_prints),
    )
    out.update(
        {
            "performed": True,
            "download_url_recorded": False,
            "tick_print_n": len(prints),
            "tick_0900_n": len(label_prints),
            "tick_0859_n": len(prev_prints),
            "bytes_read": streamed.get("bytes_read"),
            "elapsed_sec": streamed.get("elapsed_sec"),
            "header": streamed.get("header"),
            "first_tick": prints[0] if prints else None,
            "last_tick": prints[-1] if prints else None,
            "infer": inferred,
            "semantics": inferred.get("EQUITY_MINUTE_TIME_SEMANTICS"),
            "reason": None
            if inferred.get("EQUITY_MINUTE_TIME_SEMANTICS") != SEMANTICS_UNKNOWN
            else "ticks_did_not_disambiguate",
        }
    )
    return out


assert PROBE_DATE == "20260911"
assert ENDPOINT_MINUTE == "/v2/equities/bars/minute"
assert _select_tick_file(
    [{"Key": "equities/trades/historical/2026/equities_trades_202608.csv.gz", "Size": 1},
     {"Key": "equities/trades/historical/2026/equities_trades_202609.csv.gz", "Size": 9}],
    probe_date="20260911",
)["Key"].endswith("202609.csv.gz")
