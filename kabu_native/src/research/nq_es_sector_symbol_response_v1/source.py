"""Audit and acquire NQ/ES. Prefer true CME futures. No purchase. Proxy allowed and labeled."""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from research.nq_es_sector_symbol_response_v1 import (
    DISCOVERY_FROM,
    DISCOVERY_TO,
    PROXY_ES,
    PROXY_ES_ROLE,
    PROXY_NQ,
    PROXY_NQ_ROLE,
    STATUS_ACCESSIBLE,
    STATUS_BLOCKED,
    STATUS_PAID_BLOCKED,
    STATUS_PROXY,
)
from research.nq_es_sector_symbol_response_v1.isolation import CACHE, DATACUBE, NATIVE, USDJPY_CACHE
from research.usd_jpy_sector_symbol_response_v1.source import (
    JETTA,
    _day_iter,
    decode_candles,
    decode_ticks,
    fetch_json_cached,
)

JST = ZoneInfo("Asia/Tokyo")
CHI = ZoneInfo("America/Chicago")
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
UA = "kabu_native-nq-es-causal-research/1"
TIMEOUT = 30
WORKERS = 8
CONFIRMATION_START = "20251127"
VALIDATION_START = "20260422"
DATABENTO_DOCS = "https://databento.com/docs/quickstart"
CME_GLOBEX = "https://www.cmegroup.com/trading/equity-index/us-index/e-mini-nasdaq-100.html"


def _env(*names: str) -> bool:
    return any(bool(str(os.environ.get(n) or "").strip()) for n in names)


def _local_n(rel: str) -> int:
    root = NATIVE / rel
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


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
        "group": payload.get("group") or payload.get("type"),
        "default_timezone": (payload.get("defaultTimezone") or payload.get("timezone")),
        "session": payload.get("session") or payload.get("tradingHours") or payload.get("hours"),
        "n_times": n_times,
        "open": candles.get("open"),
        "timestamp_utc_ms": ts,
        "timestamp_utc": utc.isoformat() if utc else None,
        "timestamp_jst": utc.astimezone(JST).isoformat() if utc else None,
        "ok": bool(inst.get("ok") and n_times > 0),
        "payload_keys": sorted(payload.keys())[:24] if payload else [],
    }


def probe_sources() -> dict[str, Any]:
    print("NQ_ES_SOURCE_PROBE", flush=True)
    databento_key = _env("DATABENTO_API_KEY", "DATABENTO_KEY")
    dukas = _env("DUKASCOPY_USER", "DUKASCOPY_PASSWORD")
    local_fut = _local_n("data/reference/historical_panel/us_futures")
    datacube_n = sum(1 for p in DATACUBE.rglob("*") if p.is_file()) if DATACUBE.exists() else 0
    nq_p = probe_instrument(PROXY_NQ)
    es_p = probe_instrument(PROXY_ES)
    hyphen_nq = probe_instrument("USATECH-IDX-USD")
    hyphen_es = probe_instrument("USA500-IDX-USD")
    proxy_ok = bool(nq_p.get("ok") and es_p.get("ok"))
    true_local = local_fut > 0
    if databento_key:
        true_status = "CREDENTIALS_PRESENT_PULL_NOT_APPROVED"
        true_reason = "DATABENTO_API_KEY_present_but_no_explicit_user_approval_to_pull_or_purchase"
    elif true_local:
        true_status = "LOCAL_FILES_PRESENT"
        true_reason = "local_us_futures_files_exist"
    else:
        true_status = STATUS_PAID_BLOCKED
        true_reason = "true_CME_NQ_ES_not_local_and_no_databento_key_no_purchase"
    if true_local:
        overall = STATUS_ACCESSIBLE
        research_mode = "TRUE_FUTURES"
    elif proxy_ok:
        overall = STATUS_PROXY
        research_mode = "PROXY_MECHANISM_RESEARCH"
    else:
        overall = STATUS_BLOCKED
        research_mode = "BLOCKED"
    return {
        "status": overall,
        "research_mode": research_mode,
        "true_futures_status": true_status,
        "true_futures_reason": true_reason,
        "true_cme_nq_es_available": bool(true_local),
        "proxy_available": proxy_ok,
        "proxy_label": STATUS_PROXY,
        "free_or_paid_true_futures": "paid_if_databento_or_exchange_license",
        "free_or_paid_proxy": "free",
        "purchase": False,
        "any_purchase": False,
        "additional_purchase_required": not true_local,
        "yfinance_used": False,
        "login_credentials_present": bool(dukas or databento_key),
        "databento_credentials_present": bool(databento_key),
        "databento_pull_attempted": False,
        "databento_docs": DATABENTO_DOCS,
        "databento_dataset": "GLBX.MDP3",
        "databento_schema_candidate": "ohlcv-1m",
        "cme_page": CME_GLOBEX,
        "local_us_futures_file_n": local_fut,
        "datacube_file_n": datacube_n,
        "datacube_purchased": False,
        "jetta_base": JETTA,
        "nq_proxy": nq_p,
        "es_proxy": es_p,
        "hyphen_nq_rejected_if_worse": hyphen_nq,
        "hyphen_es_rejected_if_worse": hyphen_es,
        "never_treat_nasdaq100_cash_as_nq_futures": True,
        "never_treat_sp500_cash_as_es_futures": True,
        "did_not_purchase": True,
        "do_not_combine_nq_es_into_one_score": True,
        "rows": [
            {
                "source": "local_data/reference/historical_panel/us_futures",
                "instrument": "true_CME_NQ_ES",
                "history": "none" if local_fut == 0 else "local_files",
                "frequency": "unknown",
                "timezone": None,
                "bar_semantics": "unproven",
                "contract_identity": "missing",
                "roll_handling": "unproven",
                "cost": "existing_if_present",
                "current_credentials": False,
                "current_entitlement": False,
                "purchase_required": local_fut == 0,
                "file_n": local_fut,
            },
            {
                "source": "Databento",
                "instrument": "GLBX.MDP3 NQ / ES",
                "history": "multi_year_if_entitled",
                "frequency": "ohlcv-1m",
                "timezone": "UTC_nanosecond",
                "bar_semantics": "ts_event_must_be_proven_before_join",
                "contract_identity": "front_month_must_be_predeclared",
                "roll_handling": "no_hindsight_continuous_no_future_volume_best_contract",
                "cost": "metered_USD_plus_exchange_constraints",
                "current_credentials": bool(databento_key),
                "current_entitlement": False,
                "purchase_required": not databento_key,
                "pull_without_approval": False,
            },
            {
                "source": "Dukascopy_Jetta",
                "instrument": PROXY_NQ,
                "role": PROXY_NQ_ROLE,
                "history": f"{DISCOVERY_FROM}-{DISCOVERY_TO}_Discovery_adjacent",
                "frequency": "1min_OHLCV",
                "timezone": "store_UTC_convert_JST_session_America_Chicago",
                "bar_semantics": "BAR_START_utc_epoch_ms_available_at_bar_end",
                "contract_identity": "IDX_CASH_CFD_not_CME_NQ",
                "roll_handling": "N/A_cash_index_no_futures_roll",
                "cost": "free",
                "current_credentials": False,
                "current_entitlement": bool(nq_p.get("ok")),
                "purchase_required": False,
                "label": STATUS_PROXY,
            },
            {
                "source": "Dukascopy_Jetta",
                "instrument": PROXY_ES,
                "role": PROXY_ES_ROLE,
                "history": f"{DISCOVERY_FROM}-{DISCOVERY_TO}_Discovery_adjacent",
                "frequency": "1min_OHLCV",
                "timezone": "store_UTC_convert_JST_session_America_Chicago",
                "bar_semantics": "BAR_START_utc_epoch_ms_available_at_bar_end",
                "contract_identity": "IDX_CASH_CFD_not_CME_ES",
                "roll_handling": "N/A_cash_index_no_futures_roll",
                "cost": "free",
                "current_credentials": False,
                "current_entitlement": bool(es_p.get("ok")),
                "purchase_required": False,
                "label": STATUS_PROXY,
            },
        ],
    }


def acquire_discovery_history(*, codes: tuple[str, str] = (PROXY_NQ, PROXY_ES)) -> dict[str, Any]:
    if DISCOVERY_TO >= CONFIRMATION_START:
        raise RuntimeError("nq_es_range_must_not_include_confirmation")
    days = _day_iter(DISCOVERY_FROM, DISCOVERY_TO)
    jobs = [(code, d, side) for code in codes for d in days for side in ("BID", "ASK")]
    print(f"NQ_ES_DOWNLOAD codes={codes} days={len(days)} files={len(jobs)} range={DISCOVERY_FROM}-{DISCOVERY_TO}", flush=True)
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
                print(f"NQ_ES_DOWNLOAD {done}/{len(jobs)} ok={ok_n} empty={empty_n} fail={len(fail)}", flush=True)
    ticks = {}
    for code in codes:
        for d, h in (
            (date(2024, 9, 17), 0),
            (date(2024, 9, 21), 0),
            (date(2025, 3, 9), 0),
            (date(2025, 11, 2), 0),
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
        "true_cme_futures_pulled": False,
        "proxy_codes": list(codes),
        "label": STATUS_PROXY,
    }


def _frame_one(code: str, parquet_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
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
            chi_dt = utc_dt.astimezone(CHI)
            ny_dt = utc_dt.astimezone(NY)
            avail_utc = utc_dt + timedelta(minutes=1)
            bid_c = float(r["close"]) if bid_rows else float("nan")
            ask_c = float(a["close"]) if a else float("nan")
            mid = (bid_c + ask_c) / 2.0 if a is not None and bid_rows else (bid_c if bid_rows else float(r["close"]))
            recs.append(
                {
                    "ts_utc_ms": ts,
                    "bar_start_utc": utc_dt.isoformat(),
                    "bar_start_jst": jst_dt.isoformat(),
                    "bar_start_chicago": chi_dt.isoformat(),
                    "bar_start_ny_display_only": ny_dt.isoformat(),
                    "available_at_utc": avail_utc.isoformat(),
                    "available_at_jst": (jst_dt + timedelta(minutes=1)).isoformat(),
                    "available_at_utc_ms": int(avail_utc.timestamp() * 1000),
                    "jst_date": jst_dt.strftime("%Y%m%d"),
                    "jst_hhmm": jst_dt.strftime("%H:%M"),
                    "chi_hhmm": chi_dt.strftime("%H:%M"),
                    "chi_offset_hours": (chi_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                    "utc_offset_hours": (jst_dt.utcoffset() or timedelta()).total_seconds() / 3600.0,
                    "bid_open": float(r["open"]) if bid_rows else float("nan"),
                    "bid_high": float(r["high"]) if bid_rows else float("nan"),
                    "bid_low": float(r["low"]) if bid_rows else float("nan"),
                    "bid_close": bid_c,
                    "ask_open": float(a["open"]) if a else float("nan"),
                    "ask_high": float(a["high"]) if a else float("nan"),
                    "ask_low": float(a["low"]) if a else float("nan"),
                    "ask_close": ask_c,
                    "mid_close": float(mid),
                    "spread_close": float(ask_c - bid_c) if a is not None and bid_rows else float("nan"),
                    "has_ask": a is not None,
                    "bar_semantics": "BAR_START_utc_epoch_ms",
                    "instrument": code,
                    "label": STATUS_PROXY,
                }
            )
    df = pd.DataFrame.from_records(recs)
    if not df.empty:
        df = df.sort_values("ts_utc_ms").drop_duplicates("ts_utc_ms", keep="last").reset_index(drop=True)
        df = df[df["jst_date"] < CONFIRMATION_START].reset_index(drop=True)
    out_path = CACHE / parquet_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        df.to_parquet(out_path, index=False)
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest() if out_path.is_file() else ""
    meta = {
        "code": code,
        "label": STATUS_PROXY,
        "true_cme_futures": False,
        "n_rows": int(len(df)),
        "n_bid_days": int(n_bid),
        "n_ask_days": int(n_ask),
        "shift_ms": shift_ms,
        "parquet": str(out_path),
        "sha256": sha,
        "bid_ask": True,
        "ohlc": True,
        "bar_semantics": "BAR_START_timestamp_is_minute_start_UTC",
        "available_at": "bar_start_plus_1_minute",
        "timezone_stored": "UTC_epoch_ms_plus_JST_and_Chicago_conversion",
        "naive_et_not_used": True,
        "range": {"from": DISCOVERY_FROM, "to": DISCOVERY_TO},
        "min_ts": None if df.empty else int(df["ts_utc_ms"].iloc[0]),
        "max_ts": None if df.empty else int(df["ts_utc_ms"].iloc[-1]),
        "confirmation_in_file": False if df.empty else bool((df["jst_date"] >= CONFIRMATION_START).any()),
        "validation_in_file": False if df.empty else bool((df["jst_date"] >= VALIDATION_START).any()),
        "contract_identity": "IDX_CASH_CFD",
        "roll": "N/A_no_futures_contract",
    }
    if meta["confirmation_in_file"] or meta["validation_in_file"]:
        raise RuntimeError("nq_es_file_contains_confirmation_or_validation")
    return df, meta


def load_proxy_frames() -> dict[str, Any]:
    nq_path = CACHE / "nq_proxy_usatech_1m_20240916_20251126.parquet"
    es_path = CACHE / "es_proxy_usa500_1m_20240916_20251126.parquet"
    out: dict[str, Any] = {}
    if nq_path.is_file() and es_path.is_file():
        nq = pd.read_parquet(nq_path)
        es = pd.read_parquet(es_path)
        if not nq.empty and not es.empty and str(nq["jst_date"].max()) < CONFIRMATION_START and str(es["jst_date"].max()) < CONFIRMATION_START:
            out["nq"] = nq
            out["es"] = es
            out["nq_meta"] = {
                "n_rows": int(len(nq)),
                "parquet": str(nq_path),
                "sha256": hashlib.sha256(nq_path.read_bytes()).hexdigest(),
                "loaded_existing": True,
                "label": STATUS_PROXY,
                "true_cme_futures": False,
                "code": PROXY_NQ,
            }
            out["es_meta"] = {
                "n_rows": int(len(es)),
                "parquet": str(es_path),
                "sha256": hashlib.sha256(es_path.read_bytes()).hexdigest(),
                "loaded_existing": True,
                "label": STATUS_PROXY,
                "true_cme_futures": False,
                "code": PROXY_ES,
            }
            return out
    nq, nq_meta = _frame_one(PROXY_NQ, nq_path.name)
    es, es_meta = _frame_one(PROXY_ES, es_path.name)
    out["nq"] = nq
    out["es"] = es
    out["nq_meta"] = nq_meta
    out["es_meta"] = es_meta
    return out


def load_fx_overnight_frame() -> tuple[pd.DataFrame | None, dict[str, Any]]:
    path = USDJPY_CACHE / "usdjpy_1m_bidask_20240916_20251126.parquet"
    if not path.is_file():
        return None, {"present": False, "path": str(path)}
    df = pd.read_parquet(
        path,
        columns=["ts_utc_ms", "available_at_utc_ms", "mid_close", "spread_close", "jst_date", "jst_hhmm"],
    )
    df = df[df["jst_date"] < CONFIRMATION_START]
    return df, {"present": True, "n_rows": int(len(df)), "path": str(path), "read_only": True}
