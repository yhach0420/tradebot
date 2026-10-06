"""Acquire Discovery-only USDJPY from Dukascopy Jetta. Free source. No purchase. No yfinance. No Confirmation/Validation fetch."""
from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.usd_jpy_sector_symbol_response_v1 import (
    DISCOVERY_FX_FROM,
    DISCOVERY_FX_TO,
    STATUS_ACCESSIBLE,
    STATUS_BLOCKED,
    STATUS_LOGIN,
)
from research.usd_jpy_sector_symbol_response_v1.isolation import CACHE

JST = ZoneInfo("Asia/Tokyo")
UTC = timezone.utc
UA = "kabu_native-usdjpy-causal-research/1"
JETTA = "https://jetta.dukascopy.com/v1"
INSTRUMENT = "USD-JPY"
DATAFEED_TICK = "http://datafeed.dukascopy.com/datafeed/USDJPY/2024/08/17/00h_ticks.bi5"
DATAFEED_TICK_HTTPS = "https://datafeed.dukascopy.com/datafeed/USDJPY/2024/08/17/00h_ticks.bi5"
WIDGET = "https://widgets.dukascopy.com/en/historical-data-export"
EXPORT_PAGE = "https://www.dukascopy.com/swiss/english/marketwatch/historical/"
TIMEOUT = 30
WORKERS = 8
CONFIRMATION_START = "20251127"
VALIDATION_START = "20260422"


def _day_iter(start: str, end: str) -> list[date]:
    a = datetime.strptime(start, "%Y%m%d").date()
    b = datetime.strptime(end, "%Y%m%d").date()
    out = []
    cur = a
    while cur <= b:
        out.append(cur)
        cur += timedelta(days=1)
    return out


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
    rows = []
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


def decode_ticks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    times = list(payload.get("times") or [])
    if not times:
        return []
    bids = list(payload.get("bids") or [0] * len(times))
    asks = list(payload.get("asks") or [0] * len(times))
    bvols = list(payload.get("bidVolumes") or [0] * len(times))
    avols = list(payload.get("askVolumes") or [0] * len(times))
    mult = float(payload.get("multiplier") or 1.0)
    ts = int(payload.get("timestamp") or 0)
    bid = float(payload.get("bid") or 0.0)
    ask = float(payload.get("ask") or 0.0)
    rnd = _nt(mult)
    rows = []
    for i, dt in enumerate(times):
        ts = int(ts + int(dt))
        bid = _k(bid, float(bids[i]), mult, rnd)
        ask = _k(ask, float(asks[i]), mult, rnd)
        rows.append(
            {
                "ts_utc_ms": ts,
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2.0,
                "spread": ask - bid,
                "bid_volume": float(bvols[i]) if i < len(bvols) else float("nan"),
                "ask_volume": float(avols[i]) if i < len(avols) else float("nan"),
            }
        )
    return rows


def _k(prev: float, delta: float, mult: float, rnd: float) -> float:
    return float(round((prev + delta * mult) * rnd) / rnd)


def _nt(mult: float) -> float:
    if not mult:
        return 1.0
    e = math.floor(math.log10(abs(mult)))
    return float(mult) if e > 0 else float(10 ** abs(e))


def candle_url(day: date, side: str) -> str:
    return f"{JETTA}/candles/minute/{INSTRUMENT}/{side}/{day.year}/{day.month}/{day.day}"


def tick_url(day: date, hour: int) -> str:
    return f"{JETTA}/ticks/{INSTRUMENT}/{day.year}/{day.month}/{day.day}/{int(hour)}"


def _cache_candle_path(day: date, side: str):
    root = CACHE / "jetta" / "candles_minute"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"USD-JPY_{side}_{day.year}_{day.month}_{day.day}.json"


def _cache_tick_path(day: date, hour: int):
    root = CACHE / "jetta" / "ticks"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"USD-JPY_{day.year}_{day.month}_{day.day}_{int(hour)}.json"


def fetch_json_cached(path, url: str, *, retries: int = 3) -> dict[str, Any]:
    if path.is_file() and path.stat().st_size > 2:
        try:
            return {"ok": True, "cached": True, "status": 200, "payload": json.loads(path.read_text(encoding="utf-8")), "url": url}
        except Exception:
            pass
    last = {}
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
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "cached": False, "status": got.get("status"), "payload": payload, "url": url}
        if got.get("status") in (404, 204):
            path.write_text("{}", encoding="utf-8")
            return {"ok": True, "cached": False, "empty": True, "status": got.get("status"), "payload": {}, "url": url}
        time.sleep(0.4 * (i + 1))
    return {"ok": False, "cached": False, "status": last.get("status"), "reason": last.get("reason"), "url": url, "payload": {}}


def probe_sources() -> dict[str, Any]:
    print("USDJPY_SOURCE_PROBE", flush=True)
    inst = _http_get(f"{JETTA}/instruments/{INSTRUMENT}")
    sample = _http_get(candle_url(date(2024, 9, 17), "BID"))
    tick = _http_get(tick_url(date(2024, 9, 17), 0))
    feed_http = _http_get(DATAFEED_TICK, timeout=15)
    feed_https = _http_get(DATAFEED_TICK_HTTPS, timeout=20)
    widget = _http_get("https://widgets.dukascopy.com/config.json", timeout=15)
    page = _http_get(EXPORT_PAGE, timeout=20)
    inst_ok = bool(inst.get("ok"))
    sample_ok = bool(sample.get("ok") and sample.get("body") and b"timestamp" in sample.get("body", b""))
    tick_ok = bool(tick.get("ok") and tick.get("body") and b"timestamp" in tick.get("body", b""))
    if inst_ok and sample_ok:
        status = STATUS_ACCESSIBLE
        reason = "jetta_public_historical_api_no_login"
    elif page.get("ok") and not sample_ok:
        status = STATUS_LOGIN if (feed_https.get("status") in (401, 403) or inst.get("status") in (401, 403)) else STATUS_BLOCKED
        reason = "official_export_page_visible_but_history_api_failed"
    else:
        status = STATUS_BLOCKED
        reason = "jetta_and_datafeed_unavailable"
    return {
        "status": status,
        "reason": reason,
        "free_or_paid": "free",
        "purchase": False,
        "additional_purchase_required": False,
        "yfinance_used": False,
        "login_credentials_present": False,
        "official_export_page": EXPORT_PAGE,
        "official_widget": WIDGET,
        "jetta_base": JETTA,
        "instrument": INSTRUMENT,
        "instrument_http": inst.get("status"),
        "sample_candle_http": sample.get("status"),
        "sample_tick_http": tick.get("status"),
        "legacy_datafeed_http": {"status": feed_http.get("status"), "reason": feed_http.get("reason")},
        "legacy_datafeed_https": {"status": feed_https.get("status"), "reason": feed_https.get("reason")},
        "widget_config_http": widget.get("status"),
        "export_page_http": page.get("status"),
        "legacy_datafeed_not_used_for_history": True,
        "did_not_purchase": True,
        "sample_ok": sample_ok,
        "tick_ok": tick_ok,
    }


def acquire_discovery_history() -> dict[str, Any]:
    if DISCOVERY_FX_TO >= CONFIRMATION_START:
        raise RuntimeError("fx_range_must_not_include_confirmation")
    days = _day_iter(DISCOVERY_FX_FROM, DISCOVERY_FX_TO)
    jobs = [(d, side) for d in days for side in ("BID", "ASK")]
    print(f"USDJPY_DOWNLOAD days={len(days)} files={len(jobs)} range={DISCOVERY_FX_FROM}-{DISCOVERY_FX_TO}", flush=True)
    ok_n = 0
    empty_n = 0
    fail = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_json_cached, _cache_candle_path(d, side), candle_url(d, side)): (d, side) for d, side in jobs}
        done = 0
        for fut in as_completed(futs):
            d, side = futs[fut]
            got = fut.result()
            done += 1
            if got.get("ok") and got.get("payload"):
                ok_n += 1
            elif got.get("ok") and got.get("empty"):
                empty_n += 1
            elif got.get("ok") and not got.get("payload"):
                empty_n += 1
            else:
                fail.append({"date": d.isoformat(), "side": side, "status": got.get("status"), "reason": got.get("reason")})
            if done % 100 == 0:
                print(f"USDJPY_DOWNLOAD {done}/{len(jobs)} ok={ok_n} empty={empty_n} fail={len(fail)}", flush=True)
    tick_probe = [
        (date(2024, 9, 17), 0),
        (date(2024, 9, 21), 0),
        (date(2025, 3, 9), 0),
        (date(2025, 11, 2), 0),
        (date(2024, 9, 23), 0),
    ]
    ticks = {}
    for d, h in tick_probe:
        if d.strftime("%Y%m%d") > DISCOVERY_FX_TO:
            continue
        got = fetch_json_cached(_cache_tick_path(d, h), tick_url(d, h))
        ticks[f"{d.isoformat()}_{h:02d}"] = {
            "ok": bool(got.get("ok") and got.get("payload")),
            "status": got.get("status"),
            "n": len(decode_ticks(got.get("payload") or {})) if got.get("payload") else 0,
            "url": got.get("url"),
        }
    return {
        "days_requested": len(days),
        "files_requested": len(jobs),
        "ok_n": ok_n,
        "empty_n": empty_n,
        "fail_n": len(fail),
        "fail_head": fail[:8],
        "range": {"from": DISCOVERY_FX_FROM, "to": DISCOVERY_FX_TO},
        "confirmation_fetched": False,
        "frozen_validation_fetched": False,
        "tick_probe": ticks,
        "purchase": False,
    }


def build_minute_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    days = _day_iter(DISCOVERY_FX_FROM, DISCOVERY_FX_TO)
    recs: list[dict[str, Any]] = []
    n_bid = n_ask = 0
    shift_ms = None
    for d in days:
        bid_p = _cache_candle_path(d, "BID")
        ask_p = _cache_candle_path(d, "ASK")
        bid_rows = decode_candles(json.loads(bid_p.read_text(encoding="utf-8"))) if bid_p.is_file() and bid_p.stat().st_size > 2 else []
        ask_rows = decode_candles(json.loads(ask_p.read_text(encoding="utf-8"))) if ask_p.is_file() and ask_p.stat().st_size > 2 else []
        if bid_rows:
            n_bid += 1
            shift_ms = bid_rows[0].get("shift_ms")
        if ask_rows:
            n_ask += 1
        by_ask = {int(r["ts_utc_ms"]): r for r in ask_rows}
        for r in bid_rows:
            ts = int(r["ts_utc_ms"])
            a = by_ask.get(ts)
            utc_dt = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
            jst_dt = utc_dt.astimezone(JST)
            avail_utc = utc_dt + timedelta(minutes=1)
            avail_jst = jst_dt + timedelta(minutes=1)
            bid_c = float(r["close"])
            ask_c = float(a["close"]) if a else float("nan")
            mid = (bid_c + ask_c) / 2.0 if a is not None else bid_c
            recs.append(
                {
                    "ts_utc_ms": ts,
                    "bar_start_utc": utc_dt.isoformat(),
                    "bar_start_jst": jst_dt.isoformat(),
                    "available_at_utc": avail_utc.isoformat(),
                    "available_at_jst": avail_jst.isoformat(),
                    "available_at_utc_ms": int(avail_utc.timestamp() * 1000),
                    "jst_date": jst_dt.strftime("%Y%m%d"),
                    "jst_hhmm": jst_dt.strftime("%H:%M"),
                    "utc_offset_hours": (jst_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                    "bid_open": float(r["open"]),
                    "bid_high": float(r["high"]),
                    "bid_low": float(r["low"]),
                    "bid_close": bid_c,
                    "ask_open": float(a["open"]) if a else float("nan"),
                    "ask_high": float(a["high"]) if a else float("nan"),
                    "ask_low": float(a["low"]) if a else float("nan"),
                    "ask_close": ask_c,
                    "mid_close": float(mid),
                    "spread_close": float(ask_c - bid_c) if a is not None else float("nan"),
                    "has_ask": a is not None,
                    "bar_semantics": "BAR_START_utc_epoch_ms",
                }
            )
        if not bid_rows and ask_rows:
            n_ask += 0
            for r in ask_rows:
                ts = int(r["ts_utc_ms"])
                utc_dt = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
                jst_dt = utc_dt.astimezone(JST)
                avail_utc = utc_dt + timedelta(minutes=1)
                recs.append(
                    {
                        "ts_utc_ms": ts,
                        "bar_start_utc": utc_dt.isoformat(),
                        "bar_start_jst": jst_dt.isoformat(),
                        "available_at_utc": avail_utc.isoformat(),
                        "available_at_jst": (jst_dt + timedelta(minutes=1)).isoformat(),
                        "available_at_utc_ms": int(avail_utc.timestamp() * 1000),
                        "jst_date": jst_dt.strftime("%Y%m%d"),
                        "jst_hhmm": jst_dt.strftime("%H:%M"),
                        "utc_offset_hours": (jst_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                        "bid_open": float("nan"),
                        "bid_high": float("nan"),
                        "bid_low": float("nan"),
                        "bid_close": float("nan"),
                        "ask_open": float(r["open"]),
                        "ask_high": float(r["high"]),
                        "ask_low": float(r["low"]),
                        "ask_close": float(r["close"]),
                        "mid_close": float(r["close"]),
                        "spread_close": float("nan"),
                        "has_ask": True,
                        "bar_semantics": "BAR_START_utc_epoch_ms",
                    }
                )
    df = pd.DataFrame.from_records(recs)
    if not df.empty:
        df = df.sort_values("ts_utc_ms").drop_duplicates("ts_utc_ms", keep="last").reset_index(drop=True)
        df = df[df["jst_date"] < CONFIRMATION_START].reset_index(drop=True)
    out_path = CACHE / "usdjpy_1m_bidask_20240916_20251126.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        df.to_parquet(out_path, index=False)
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest() if out_path.is_file() else ""
    meta = {
        "n_rows": int(len(df)),
        "n_bid_days": int(n_bid),
        "n_ask_days": int(n_ask),
        "shift_ms": shift_ms,
        "parquet": str(out_path),
        "sha256": sha,
        "bid_ask": True,
        "ohlc": True,
        "tick_raw_not_full_history": True,
        "bar_semantics": "BAR_START_timestamp_is_minute_start_UTC",
        "available_at": "bar_start_plus_1_minute",
        "timezone_stored": "UTC_epoch_ms_plus_JST_conversion",
        "broker_dst_not_used": True,
        "range": {"from": DISCOVERY_FX_FROM, "to": DISCOVERY_FX_TO},
        "min_ts": None if df.empty else int(df["ts_utc_ms"].iloc[0]),
        "max_ts": None if df.empty else int(df["ts_utc_ms"].iloc[-1]),
        "confirmation_in_file": False if df.empty else bool((df["jst_date"] >= CONFIRMATION_START).any()),
        "validation_in_file": False if df.empty else bool((df["jst_date"] >= VALIDATION_START).any()),
    }
    if meta["confirmation_in_file"] or meta["validation_in_file"]:
        raise RuntimeError("fx_file_contains_confirmation_or_validation")
    return df, meta


def load_minute_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    path = CACHE / "usdjpy_1m_bidask_20240916_20251126.parquet"
    if path.is_file():
        df = pd.read_parquet(path)
        if not df.empty and str(df["jst_date"].max()) < CONFIRMATION_START:
            return df, {
                "n_rows": int(len(df)),
                "parquet": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "loaded_existing": True,
                "confirmation_in_file": False,
                "validation_in_file": False,
            }
    return build_minute_frame()
