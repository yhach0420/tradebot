"""Dukascopy Jetta 1-minute Bid/Ask ingest. Legacy raw is read-only. No Yahoo / 5m / daily substitute."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.phase1 import JETTA_INSTRUMENT, UTC_FILE_FIRST, UTC_FILE_LAST
from research.causal_driver_pb1.phase1.dates import assert_utc_source_file_date_allowed, day_iter, yyyymmdd
from research.causal_driver_pb1.phase1.errors import IngestDateDenied, UsdJpySourceUnavailable
from research.causal_driver_pb1.phase1.isolation import CACHE, LEGACY_JETTA

JETTA = "https://jetta.dukascopy.com/v1"
UA = "kabu_native-causal-driver-phase1-usdjpy/1"
TIMEOUT = 30
WORKERS = 8
SIDES = ("BID", "ASK")
LEGACY_UTC_LAST = date(2025, 11, 26)


def _nt(mult: float) -> float:
    if not mult:
        return 1.0
    e = math.floor(math.log10(abs(mult)))
    return float(mult) if e > 0 else float(10 ** abs(e))


def _k(prev: float, delta: float, mult: float, rnd: float) -> float:
    return float(round((prev + delta * mult) * rnd) / rnd)


def decode_candles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dukascopy Jetta delta-encoded 1-minute OHLCV. Timestamp is bar start UTC epoch ms."""
    times = list(payload.get("times") or [])
    if not times:
        return []
    opens = list(payload.get("opens") or [0] * len(times))
    highs = list(payload.get("highs") or [0] * len(times))
    lows = list(payload.get("lows") or [0] * len(times))
    closes = list(payload.get("closes") or [0] * len(times))
    vols = list(payload.get("volumes") or [0] * len(times))
    shift = float(payload.get("shift") or 60_000)
    mult = float(payload.get("multiplier") or 1.0)
    ts0 = int(payload.get("timestamp") or 0)
    o = float(payload.get("open") or 0.0)
    h = float(payload.get("high") or 0.0)
    l = float(payload.get("low") or 0.0)
    c = float(payload.get("close") or 0.0)
    rnd = _nt(mult)
    m = ts0
    rows: list[dict[str, Any]] = []
    n = len(times)
    if not (len(opens) == len(highs) == len(lows) == len(closes) == n):
        raise ValueError("ohlcv_inconsistent")
    for i in range(n):
        m = int(m + shift * float(times[i]))
        o = _k(o, float(opens[i]), mult, rnd)
        h = _k(h, float(highs[i]), mult, rnd)
        l = _k(l, float(lows[i]), mult, rnd)
        c = _k(c, float(closes[i]), mult, rnd)
        vol = float(vols[i]) * 1e6 if i < len(vols) else float("nan")
        rows.append(
            {
                "ts_utc_ms": m,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": vol,
                "shift_ms": shift,
            }
        )
    return rows


def candle_url(day: date, side: str) -> str:
    return f"{JETTA}/candles/minute/{JETTA_INSTRUMENT}/{side}/{day.year}/{day.month}/{day.day}"


def candle_name(day: date, side: str) -> str:
    return f"USD-JPY_{side}_{day.year}_{day.month}_{day.day}.json"


def phase1_candle_dir() -> Path:
    root = CACHE / "jetta" / "candles_minute"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_candle_path(day: date, side: str) -> tuple[Path, str]:
    name = candle_name(day, side)
    legacy = LEGACY_JETTA / name
    if legacy.is_file():
        return legacy, "LEGACY_READONLY"
    return phase1_candle_dir() / name, "PHASE1_CACHE"


def _http_get(url: str, *, timeout: int = TIMEOUT) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return {
                "ok": 200 <= int(resp.status) < 300,
                "status": int(resp.status),
                "url": url,
                "body": raw,
                "content_type": resp.headers.get("Content-Type"),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        return {"ok": False, "status": int(exc.code), "url": url, "body": raw, "reason": str(exc.reason)}
    except Exception as exc:
        return {"ok": False, "status": None, "url": url, "body": b"", "reason": f"{type(exc).__name__}:{exc}"}


def fetch_json_to_phase1(path: Path, url: str, *, retries: int = 3) -> dict[str, Any]:
    if path.is_file() and path.stat().st_size >= 2:
        try:
            return {
                "ok": True,
                "cached": True,
                "status": 200,
                "payload": json.loads(path.read_text(encoding="utf-8")),
                "url": url,
            }
        except Exception:
            pass
    last: dict[str, Any] = {}
    for i in range(retries):
        got = _http_get(url)
        last = got
        if got.get("ok") and got.get("body"):
            try:
                payload = json.loads(got["body"].decode("utf-8"))
            except Exception as exc:
                last = {**got, "ok": False, "reason": f"json:{exc}"}
                time.sleep(0.25 * (i + 1))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "cached": False, "status": got.get("status"), "payload": payload, "url": url}
        if got.get("status") in (404, 204):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return {"ok": True, "cached": False, "empty": True, "status": got.get("status"), "payload": {}, "url": url}
        time.sleep(0.4 * (i + 1))
    return {"ok": False, "cached": False, "status": last.get("status"), "reason": last.get("reason"), "url": url, "payload": {}}


def read_or_fetch_day(day: date, side: str) -> dict[str, Any]:
    assert_utc_source_file_date_allowed(day)
    path, origin = resolve_candle_path(day, side)
    url = candle_url(day, side)
    if origin == "LEGACY_READONLY":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise UsdJpySourceUnavailable(f"legacy_unreadable:{path.name}:{exc}") from exc
        return {
            "ok": True,
            "cached": True,
            "empty": not bool(payload),
            "status": 200,
            "payload": payload,
            "url": url,
            "path": str(path),
            "origin": origin,
            "side": side,
            "utc_date": day.isoformat(),
        }
    got = fetch_json_to_phase1(path, url)
    got["path"] = str(path)
    got["origin"] = origin
    got["side"] = side
    got["utc_date"] = day.isoformat()
    return got


def ensure_utc_files(*, start: str = UTC_FILE_FIRST, end: str = UTC_FILE_LAST) -> dict[str, Any]:
    days = day_iter(start, end)
    jobs = [(d, side) for d in days for side in SIDES]
    ok_n = 0
    empty_n = 0
    fail: list[dict[str, Any]] = []
    fetched_n = 0
    legacy_n = 0
    print(f"USDJPY_PHASE1_INGEST days={len(days)} files={len(jobs)} utc={start}-{end}", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(read_or_fetch_day, d, side): (d, side) for d, side in jobs}
        done = 0
        for fut in as_completed(futs):
            d, side = futs[fut]
            done += 1
            try:
                got = fut.result()
            except IngestDateDenied as exc:
                fail.append({"date": d.isoformat(), "side": side, "reason": str(exc)})
                continue
            except UsdJpySourceUnavailable as exc:
                fail.append({"date": d.isoformat(), "side": side, "reason": str(exc)})
                continue
            if got.get("origin") == "LEGACY_READONLY":
                legacy_n += 1
            elif not got.get("cached"):
                fetched_n += 1
            if got.get("ok") and got.get("payload"):
                ok_n += 1
            elif got.get("ok"):
                empty_n += 1
            else:
                fail.append(
                    {
                        "date": d.isoformat(),
                        "side": side,
                        "status": got.get("status"),
                        "reason": got.get("reason"),
                    }
                )
            if done % 100 == 0:
                print(
                    f"USDJPY_PHASE1_INGEST {done}/{len(jobs)} ok={ok_n} empty={empty_n} fail={len(fail)}",
                    flush=True,
                )
    if fail:
        raise UsdJpySourceUnavailable(f"USDJPY_SOURCE_UNAVAILABLE fail_n={len(fail)} head={fail[:6]}")
    return {
        "days_requested": len(days),
        "files_requested": len(jobs),
        "ok_n": ok_n,
        "empty_n": empty_n,
        "fail_n": 0,
        "legacy_n": legacy_n,
        "fetched_n": fetched_n,
        "utc_from": start,
        "utc_to": end,
        "provider": "DUKASCOPY_JETTA",
        "endpoint_family": f"{JETTA}/candles/minute/{JETTA_INSTRUMENT}/{{BID|ASK}}/Y/M/D",
        "yfinance_used": False,
        "resolution_downgrade": False,
        "source_substituted": False,
    }


def inventory_rows(*, start: str = UTC_FILE_FIRST, end: str = UTC_FILE_LAST) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in day_iter(start, end):
        for side in SIDES:
            path, origin = resolve_candle_path(d, side)
            if not path.is_file():
                rows.append(
                    {
                        "utc_date": d.isoformat(),
                        "side": side,
                        "path": str(path),
                        "origin": origin,
                        "exists": False,
                        "bytes": 0,
                        "sha256": "",
                    }
                )
                continue
            raw = path.read_bytes()
            rows.append(
                {
                    "utc_date": d.isoformat(),
                    "side": side,
                    "path": str(path),
                    "origin": origin,
                    "exists": True,
                    "bytes": len(raw),
                    "sha256": sha256_bytes(raw),
                    "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "retrieved_at_semantics": "FILE_MTIME_NOT_LIVE_RECEIVE",
                }
            )
    return rows


def inventory_sha256(rows: list[dict[str, Any]]) -> str:
    from research.causal_driver_pb1.identity.ids import sha256_obj

    payload = [
        {
            "utc_date": r.get("utc_date"),
            "side": r.get("side"),
            "origin": r.get("origin"),
            "bytes": r.get("bytes"),
            "sha256": r.get("sha256"),
            "exists": r.get("exists"),
        }
        for r in sorted(rows, key=lambda x: (str(x.get("utc_date")), str(x.get("side"))))
    ]
    return sha256_obj(payload)


def load_side_rows(day: date, side: str) -> list[dict[str, Any]]:
    path, _origin = resolve_candle_path(day, side)
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload:
        return []
    decoded = decode_candles(payload)
    for row in decoded:
        row["side"] = side
        row["utc_file_date"] = yyyymmdd(day)
        row["source_path"] = str(path)
    return decoded


def payload_schema_keys(day: date, side: str) -> list[str]:
    path, _origin = resolve_candle_path(day, side)
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    return sorted(payload.keys())
