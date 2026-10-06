"""Acquire Discovery-only HKG.IDX-HKD and CHI.IDX-USD from Dukascopy Jetta. Labeled proxies. No purchase."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from research.hkg_china_a50_driver_response_v1 import (
    DISCOVERY_FROM,
    DISCOVERY_TO,
    LABEL_CHI,
    LABEL_HKG,
    PROXY_CHI,
    PROXY_HKG,
    STATUS_BLOCKED,
    STATUS_PROXY,
)
from research.hkg_china_a50_driver_response_v1.isolation import CACHE, NQES_CACHE, USDJPY_CACHE
from research.usd_jpy_sector_symbol_response_v1.source import (
    JETTA,
    _day_iter,
    decode_candles,
    decode_ticks,
    fetch_json_cached,
)

JST = ZoneInfo("Asia/Tokyo")
HKT = ZoneInfo("Asia/Hong_Kong")
CST = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
UA = "kabu_native-hkg-china-a50-causal-research/1"
TIMEOUT = 30
WORKERS = 8
CONFIRMATION_START = "20251127"
VALIDATION_START = "20260422"


def candle_url(code: str, day: date, side: str) -> str:
    return f"{JETTA}/candles/minute/{code}/{side}/{day.year}/{day.month}/{day.day}"


def tick_url(code: str, day: date, hour: int) -> str:
    return f"{JETTA}/ticks/{code}/{day.year}/{day.month}/{day.day}/{int(hour)}"


def _cache_candle_path(code: str, day: date, side: str):
    root = CACHE / "jetta" / "candles_minute" / code.replace("/", "_")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{side}_{day.year}_{day.month}_{day.day}.json"


def _cache_tick_path(code: str, day: date, hour: int):
    root = CACHE / "jetta" / "ticks" / code.replace("/", "_")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{day.year}_{day.month}_{day.day}_{int(hour):02d}.json"


def _http_get_ua(url: str, *, timeout: int = TIMEOUT) -> dict[str, Any]:
    import urllib.error
    import urllib.request

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


def probe_instrument(code: str) -> dict[str, Any]:
    inst = _http_get_ua(f"{JETTA}/instruments/{code}")
    sample = _http_get_ua(candle_url(code, date(2024, 9, 17), "BID"))
    payload = {}
    if inst.get("ok") and inst.get("body"):
        try:
            payload = json.loads(inst["body"].decode("utf-8"))
        except Exception:
            payload = {}
    candles = {}
    if sample.get("ok") and sample.get("body"):
        try:
            candles = json.loads(sample["body"].decode("utf-8"))
        except Exception:
            candles = {}
    n_times = len(list(candles.get("times") or []))
    ts = int(candles.get("timestamp") or 0)
    utc = datetime.fromtimestamp(ts / 1000.0, tz=UTC) if ts else None
    return {
        "code": code,
        "instrument_http": inst.get("status"),
        "sample_http": sample.get("status"),
        "name": payload.get("name") or payload.get("title"),
        "description": payload.get("description"),
        "group": payload.get("group") or payload.get("type"),
        "default_timezone": payload.get("defaultTimezone") or payload.get("timezone"),
        "n_times": n_times,
        "timestamp_utc_ms": ts,
        "timestamp_utc": utc.isoformat() if utc else None,
        "timestamp_jst": utc.astimezone(JST).isoformat() if utc else None,
        "timestamp_hkt": utc.astimezone(HKT).isoformat() if utc else None,
        "ok": bool(inst.get("ok") and n_times > 0),
        "not_korea_proxy": True,
        "not_taiwan_proxy": True,
        "not_cme_futures": True,
        "payload_keys": sorted(payload.keys())[:24] if payload else [],
    }


def probe_sources() -> dict[str, Any]:
    print("HKG_CHI_SOURCE_PROBE", flush=True)
    hkg = probe_instrument(PROXY_HKG)
    chi = probe_instrument(PROXY_CHI)
    proxy_ok = bool(hkg.get("ok") and chi.get("ok"))
    return {
        "status": STATUS_PROXY if proxy_ok else STATUS_BLOCKED,
        "research_mode": "PROXY_DRIVER_RESEARCH",
        "hkg_label": LABEL_HKG,
        "chi_label": LABEL_CHI,
        "not_exchange_native_futures": True,
        "not_korea_substitute": True,
        "not_taiwan_substitute": True,
        "not_nq_es_substitute": True,
        "free_or_paid": "free",
        "purchase": False,
        "any_purchase": False,
        "yfinance_used": False,
        "jetta_base": JETTA,
        "hkg": hkg,
        "chi": chi,
        "proxy_available": proxy_ok,
        "did_not_purchase": True,
        "did_not_combine_hkg_chi_score": True,
        "rows": [
            {
                "source": "Dukascopy_Jetta",
                "instrument": PROXY_HKG,
                "label": LABEL_HKG,
                "history": f"{DISCOVERY_FROM}-{DISCOVERY_TO}_Discovery_adjacent",
                "frequency": "1min_OHLCV",
                "timezone": "store_UTC_convert_JST_and_HKT",
                "bar_semantics": "BAR_START_utc_epoch_ms_available_at_bar_end",
                "cost": "free",
                "purchase_required": False,
                "current_entitlement": bool(hkg.get("ok")),
            },
            {
                "source": "Dukascopy_Jetta",
                "instrument": PROXY_CHI,
                "label": LABEL_CHI,
                "history": f"{DISCOVERY_FROM}-{DISCOVERY_TO}_Discovery_adjacent",
                "frequency": "1min_OHLCV",
                "timezone": "store_UTC_convert_JST_and_CST",
                "bar_semantics": "BAR_START_utc_epoch_ms_available_at_bar_end",
                "cost": "free",
                "purchase_required": False,
                "current_entitlement": bool(chi.get("ok")),
            },
        ],
    }


def acquire_discovery_history(*, codes: tuple[str, str] = (PROXY_HKG, PROXY_CHI)) -> dict[str, Any]:
    if DISCOVERY_TO >= CONFIRMATION_START:
        raise RuntimeError("hkg_chi_range_must_not_include_confirmation")
    days = _day_iter(DISCOVERY_FROM, DISCOVERY_TO)
    jobs = [(code, d, side) for code in codes for d in days for side in ("BID", "ASK")]
    print(f"HKG_CHI_DOWNLOAD codes={codes} days={len(days)} files={len(jobs)} range={DISCOVERY_FROM}-{DISCOVERY_TO}", flush=True)
    ok_n = 0
    empty_n = 0
    fail = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {
            pool.submit(fetch_json_cached, _cache_candle_path(code, d, side), candle_url(code, d, side)): (code, d, side)
            for code, d, side in jobs
        }
        done = 0
        for fut in as_completed(futs):
            code, d, side = futs[fut]
            got = fut.result()
            done += 1
            if got.get("ok") and got.get("payload"):
                ok_n += 1
            elif got.get("ok"):
                empty_n += 1
            else:
                fail.append(
                    {
                        "code": code,
                        "date": d.isoformat(),
                        "side": side,
                        "status": got.get("status"),
                        "reason": got.get("reason"),
                    }
                )
            if done % 200 == 0:
                print(f"HKG_CHI_DOWNLOAD {done}/{len(jobs)} ok={ok_n} empty={empty_n} fail={len(fail)}", flush=True)
    ticks = {}
    for code in codes:
        for d, h in (
            (date(2024, 9, 17), 1),
            (date(2024, 9, 17), 2),
            (date(2025, 3, 17), 1),
            (date(2025, 11, 3), 1),
        ):
            if d.strftime("%Y%m%d") > DISCOVERY_TO:
                continue
            got = fetch_json_cached(_cache_tick_path(code, d, h), tick_url(code, d, h))
            ticks[f"{code}_{d.isoformat()}_{h:02d}"] = {
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
        "range": {"from": DISCOVERY_FROM, "to": DISCOVERY_TO},
        "confirmation_fetched": False,
        "frozen_validation_fetched": False,
        "tick_probe": ticks,
        "purchase": False,
        "proxy_codes": list(codes),
        "label": STATUS_PROXY,
    }


def _mark_live(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["live"] = pd.Series(dtype=bool)
        return out
    out = df.copy()
    mid = pd.to_numeric(out["mid_close"], errors="coerce")
    hi = pd.to_numeric(out["bid_high"], errors="coerce")
    lo = pd.to_numeric(out["bid_low"], errors="coerce")
    vol = pd.to_numeric(out["volume"], errors="coerce") if "volume" in out.columns else pd.Series(0.0, index=out.index)
    prev = mid.shift(1)
    rng = (hi - lo).abs()
    chg = (mid - prev).abs()
    live = (rng.fillna(0) > 0) | (chg.fillna(0) > 0) | (vol.fillna(0) > 0)
    live = live.where(prev.notna(), rng.fillna(0) > 0)
    out["live"] = live.astype(bool)
    out["did_not_forward_fill"] = True
    return out


def _frame_one(code: str, parquet_name: str, label: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    days = _day_iter(DISCOVERY_FROM, DISCOVERY_TO)
    recs: list[dict[str, Any]] = []
    n_bid = n_ask = 0
    shift_ms = None
    for d in days:
        bid_p = _cache_candle_path(code, d, "BID")
        ask_p = _cache_candle_path(code, d, "ASK")
        bid_rows = decode_candles(json.loads(bid_p.read_text(encoding="utf-8"))) if bid_p.is_file() and bid_p.stat().st_size > 2 else []
        ask_rows = decode_candles(json.loads(ask_p.read_text(encoding="utf-8"))) if ask_p.is_file() and ask_p.stat().st_size > 2 else []
        if bid_rows:
            n_bid += 1
            shift_ms = bid_rows[0].get("shift_ms")
        if ask_rows:
            n_ask += 1
        by_ask = {int(r["ts_utc_ms"]): r for r in ask_rows}
        use_rows = bid_rows or ask_rows
        for r in use_rows:
            ts = int(r["ts_utc_ms"])
            a = by_ask.get(ts)
            utc_dt = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
            jst_dt = utc_dt.astimezone(JST)
            hkt_dt = utc_dt.astimezone(HKT)
            cst_dt = utc_dt.astimezone(CST)
            avail_utc = utc_dt + timedelta(minutes=1)
            bid_c = float(r["close"]) if bid_rows else float("nan")
            ask_c = float(a["close"]) if a else float("nan")
            mid = (bid_c + ask_c) / 2.0 if a is not None and bid_rows else (bid_c if bid_rows else float(r["close"]))
            recs.append(
                {
                    "ts_utc_ms": ts,
                    "bar_start_utc": utc_dt.isoformat(),
                    "bar_start_jst": jst_dt.isoformat(),
                    "bar_start_hkt": hkt_dt.isoformat(),
                    "bar_start_cst": cst_dt.isoformat(),
                    "available_at_utc": avail_utc.isoformat(),
                    "available_at_jst": (jst_dt + timedelta(minutes=1)).isoformat(),
                    "available_at_utc_ms": int(avail_utc.timestamp() * 1000),
                    "jst_date": jst_dt.strftime("%Y%m%d"),
                    "jst_hhmm": jst_dt.strftime("%H:%M"),
                    "hkt_hhmm": hkt_dt.strftime("%H:%M"),
                    "cst_hhmm": cst_dt.strftime("%H:%M"),
                    "hkt_offset_hours": (hkt_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                    "cst_offset_hours": (cst_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                    "utc_offset_hours": (jst_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                    "weekday": int(jst_dt.weekday()),
                    "bid_open": float(r["open"]) if bid_rows else float("nan"),
                    "bid_high": float(r["high"]) if bid_rows else float("nan"),
                    "bid_low": float(r["low"]) if bid_rows else float("nan"),
                    "bid_close": bid_c,
                    "ask_close": ask_c,
                    "mid_close": float(mid),
                    "spread_close": float(ask_c - bid_c) if a is not None and bid_rows else float("nan"),
                    "volume": float(r.get("volume") or 0.0),
                    "has_ask": a is not None,
                    "bar_semantics": "BAR_START_utc_epoch_ms",
                    "instrument": code,
                    "label": label,
                }
            )
    df = pd.DataFrame.from_records(recs)
    if not df.empty:
        df = df.sort_values("ts_utc_ms").drop_duplicates("ts_utc_ms", keep="last").reset_index(drop=True)
        df = df[df["jst_date"] < CONFIRMATION_START].reset_index(drop=True)
        df = _mark_live(df)
    out_path = CACHE / parquet_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        df.to_parquet(out_path, index=False)
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest() if out_path.is_file() else ""
    meta = {
        "code": code,
        "label": label,
        "true_exchange_native_futures": False,
        "n_rows": int(len(df)),
        "n_live": int(df["live"].sum()) if not df.empty and "live" in df.columns else 0,
        "n_bid_days": int(n_bid),
        "n_ask_days": int(n_ask),
        "shift_ms": shift_ms,
        "parquet": str(out_path),
        "sha256": sha,
        "bid_ask": True,
        "ohlc": True,
        "bar_semantics": "BAR_START_timestamp_is_minute_start_UTC",
        "available_at": "bar_start_plus_1_minute",
        "timezone_stored": "UTC_epoch_ms_plus_JST_HKT_CST",
        "range": {"from": DISCOVERY_FROM, "to": DISCOVERY_TO},
        "min_ts": None if df.empty else int(df["ts_utc_ms"].iloc[0]),
        "max_ts": None if df.empty else int(df["ts_utc_ms"].iloc[-1]),
        "confirmation_in_file": False if df.empty else bool((df["jst_date"] >= CONFIRMATION_START).any()),
        "validation_in_file": False if df.empty else bool((df["jst_date"] >= VALIDATION_START).any()),
        "did_not_forward_fill": True,
    }
    if meta["confirmation_in_file"] or meta["validation_in_file"]:
        raise RuntimeError("hkg_chi_file_contains_confirmation_or_validation")
    return df, meta


def load_proxy_frames() -> dict[str, Any]:
    hkg_path = CACHE / "hkg_proxy_idx_1m_20240916_20251126.parquet"
    chi_path = CACHE / "chi_proxy_a50_1m_20240916_20251126.parquet"
    out: dict[str, Any] = {}
    if hkg_path.is_file() and chi_path.is_file():
        hkg = pd.read_parquet(hkg_path)
        chi = pd.read_parquet(chi_path)
        if (
            not hkg.empty
            and not chi.empty
            and str(hkg["jst_date"].max()) < CONFIRMATION_START
            and str(chi["jst_date"].max()) < CONFIRMATION_START
            and "live" in hkg.columns
            and "live" in chi.columns
        ):
            out["hkg"] = hkg
            out["chi"] = chi
            out["hkg_meta"] = {
                "n_rows": int(len(hkg)),
                "n_live": int(hkg["live"].sum()),
                "parquet": str(hkg_path),
                "sha256": hashlib.sha256(hkg_path.read_bytes()).hexdigest(),
                "loaded_existing": True,
                "label": LABEL_HKG,
                "code": PROXY_HKG,
                "true_exchange_native_futures": False,
            }
            out["chi_meta"] = {
                "n_rows": int(len(chi)),
                "n_live": int(chi["live"].sum()),
                "parquet": str(chi_path),
                "sha256": hashlib.sha256(chi_path.read_bytes()).hexdigest(),
                "loaded_existing": True,
                "label": LABEL_CHI,
                "code": PROXY_CHI,
                "true_exchange_native_futures": False,
            }
            return out
    hkg, hkg_meta = _frame_one(PROXY_HKG, hkg_path.name, LABEL_HKG)
    chi, chi_meta = _frame_one(PROXY_CHI, chi_path.name, LABEL_CHI)
    out["hkg"] = hkg
    out["chi"] = chi
    out["hkg_meta"] = hkg_meta
    out["chi_meta"] = chi_meta
    return out


def load_fx_frame() -> tuple[pd.DataFrame | None, dict[str, Any]]:
    path = USDJPY_CACHE / "usdjpy_1m_bidask_20240916_20251126.parquet"
    if not path.is_file():
        return None, {"present": False, "path": str(path)}
    df = pd.read_parquet(
        path,
        columns=["ts_utc_ms", "available_at_utc_ms", "mid_close", "spread_close", "jst_date", "jst_hhmm"],
    )
    df = df[df["jst_date"] < CONFIRMATION_START]
    return df, {"present": True, "n_rows": int(len(df)), "path": str(path), "read_only": True}


def load_es_proxy_frame() -> tuple[pd.DataFrame | None, dict[str, Any]]:
    path = NQES_CACHE / "es_proxy_usa500_1m_20240916_20251126.parquet"
    if not path.is_file():
        return None, {"present": False, "path": str(path)}
    cols = ["ts_utc_ms", "available_at_utc_ms", "mid_close", "spread_close", "jst_date", "jst_hhmm"]
    df = pd.read_parquet(path, columns=cols)
    df = df[df["jst_date"] < CONFIRMATION_START]
    return df, {"present": True, "n_rows": int(len(df)), "path": str(path), "read_only": True, "label": "PROXY_NOT_CME_FUTURES"}
