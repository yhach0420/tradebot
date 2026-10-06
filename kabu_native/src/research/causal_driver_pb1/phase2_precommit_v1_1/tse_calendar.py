"""TSE cash trading-day identity. Input-only. No holiday name list. No returns."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST, FV_FIRST
from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.causal_driver_pb1.phase2_precommit_v1_1 import TSE_AM_MIN_SYMBOLS, TSE_AM_WINDOW
from research.causal_driver_pb1.phase2_precommit_v1_1.isolation import CACHE
from research.fixed_daytrade_universe_v1 import ENDPOINT_CALENDAR
from research.fixed_daytrade_universe_v1.jquants_client import request_json
from research.fixed_daytrade_universe_v1.schema import TSE_SESSION_HOL_DIV, yyyymmdd

try:
    import pyarrow.dataset as ds
except Exception:  # pragma: no cover
    ds = None


def _iso(yyyymmdd_s: str) -> str:
    s = yyyymmdd(yyyymmdd_s)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _try_official_jquants_calendar() -> dict[str, Any] | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    body = CACHE / "calendar_20240917_20260421.json"
    meta_p = CACHE / "calendar_20240917_20260421.meta.json"
    if body.is_file() and meta_p.is_file():
        raw = body.read_bytes()
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        if str(meta.get("SHA256") or "") == sha256_bytes(raw):
            payload = json.loads(raw.decode("utf-8"))
            cached_rows = list(payload.get("data") or [])
            cached_dates = []
            for item in cached_rows:
                try:
                    cached_dates.append(yyyymmdd(item.get("Date")))
                except Exception:
                    continue
            span_ok = (
                bool(cached_dates)
                and min(cached_dates) <= DEV_FIRST
                and max(cached_dates) >= C1_LAST
                and len(cached_rows) >= 400
            )
            if span_ok:
                return {"rows": cached_rows, "meta": meta, "from_cache": True}
    attempts = (
        {"from": DEV_FIRST, "to": C1_LAST},
        {"from": _iso(DEV_FIRST), "to": _iso(C1_LAST)},
    )
    rows: list[dict[str, Any]] = []
    pages = 0
    last_reason = None
    for params in attempts:
        rows = []
        page_params = dict(params)
        pages = 0
        while True:
            pages += 1
            got = request_json(path=ENDPOINT_CALENDAR, params=page_params)
            if not got.get("ok") or not isinstance(got.get("payload"), dict):
                last_reason = got.get("reason")
                rows = []
                break
            payload = got["payload"]
            data = payload.get("data")
            if not isinstance(data, list):
                last_reason = "jquants_calendar_data_not_list"
                rows = []
                break
            rows.extend([item for item in data if isinstance(item, dict)])
            nxt = payload.get("pagination_key")
            if not nxt:
                last_reason = None
                break
            page_params["pagination_key"] = str(nxt)
            time.sleep(0.15)
            if pages > 50:
                last_reason = "jquants_calendar_pagination_guard"
                rows = []
                break
        if rows:
            break
    if not rows:
        return None if last_reason else None
    envelope = {"data": rows}
    raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    dates = []
    for item in rows:
        try:
            dates.append(yyyymmdd(item.get("Date")))
        except Exception:
            continue
    meta = {
        "endpoint": ENDPOINT_CALENDAR,
        "from": DEV_FIRST,
        "to": C1_LAST,
        "SHA256": sha256_bytes(raw),
        "row_n": len(rows),
        "pages": pages,
        "min_date": min(dates) if dates else None,
        "max_date": max(dates) if dates else None,
        "fv_requested": False,
        "ref_jquants_mutated": False,
    }
    span_ok = bool(dates) and min(dates) <= DEV_FIRST and max(dates) >= C1_LAST and len(rows) >= 400
    if not span_ok:
        return None
    body.write_bytes(raw)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rows": rows, "meta": meta, "from_cache": False}


def _presence_calendar(symbols: tuple[str, ...]) -> dict[str, Any]:
    if ds is None:
        raise RuntimeError("pyarrow_required_for_presence_calendar")
    lo, hi = TSE_AM_WINDOW
    by_date: dict[str, set[str]] = defaultdict(set)
    for i, sym in enumerate(symbols, start=1):
        path = minute_parquet_path(sym)
        if not path.is_file():
            continue
        dataset = ds.dataset(str(path), format="parquet")
        filt = (
            (ds.field("date") >= DEV_FIRST)
            & (ds.field("date") <= C1_LAST)
            & (ds.field("time_label") >= lo)
            & (ds.field("time_label") <= hi)
        )
        table = dataset.to_table(columns=["date"], filter=filt)
        for day in set(str(x) for x in table.column("date").to_pylist()):
            if day >= FV_FIRST:
                continue
            by_date[day].add(sym)
        if i % 21 == 0:
            print(f"TSE_PRESENCE {i}/{len(symbols)}", flush=True)
    rows = []
    trading = []
    for day in sorted(by_date):
        n = len(by_date[day])
        is_tse = n >= TSE_AM_MIN_SYMBOLS
        rows.append(
            {
                "date": day,
                "is_tse_cash_trading_day": is_tse,
                "am_symbol_n": n,
                "am_window": f"{lo}-{hi}",
                "threshold": TSE_AM_MIN_SYMBOLS,
            }
        )
        if is_tse:
            trading.append(day)
    return {
        "method": "INPUT_PRESENCE_AM_1M_90_OF_105",
        "source_identity": "JQUANTS_EQUITY_MINUTE_105 AM bars 09:00-11:30; date+time_label only; close/return unread",
        "trading_days": trading,
        "rows": rows,
        "universe_n": len(symbols),
        "close_field_read": False,
        "future_return_read": False,
    }


def build_tse_cash_calendar() -> dict[str, Any]:
    uni = load_research_observation_universe()
    official = _try_official_jquants_calendar()
    method = None
    trading: list[str] = []
    rows: list[dict[str, Any]] = []
    source_identity = ""
    source_sha = ""
    if official and official.get("rows"):
        for raw in official["rows"]:
            day = yyyymmdd(raw.get("Date"))
            if day < DEV_FIRST or day > C1_LAST:
                continue
            if day >= FV_FIRST:
                continue
            hol_raw = raw.get("HolDiv")
            if hol_raw is None:
                hol_raw = raw.get("HolidayDivision")
            hol = str(hol_raw or "").strip()
            if hol and hol[0] in "0123":
                hol = hol[0]
            is_tse = hol in TSE_SESSION_HOL_DIV
            rows.append(
                {
                    "date": day,
                    "is_tse_cash_trading_day": is_tse,
                    "HolDiv": hol,
                    "source_identity": "JQUANTS_/v2/markets/calendar",
                }
            )
            if is_tse:
                trading.append(day)
        if len(set(trading)) < 40:
            official = None
            trading = []
            rows = []
    if official and official.get("rows") and trading:
        method = "JQUANTS_MARKETS_CALENDAR_HOL_DIV_1_2"
        source_identity = "JQUANTS_/v2/markets/calendar HolDiv in {1,2}; range 20240917-20260421; FV not requested"
        source_sha = str((official.get("meta") or {}).get("SHA256") or sha256_obj(rows))
    else:
        pres = _presence_calendar(uni.ordered_symbols)
        method = pres["method"]
        source_identity = pres["source_identity"]
        trading = list(pres["trading_days"])
        rows = list(pres["rows"])
        source_sha = sha256_obj(
            [{"date": r["date"], "is_tse_cash_trading_day": r["is_tse_cash_trading_day"], "am_symbol_n": r.get("am_symbol_n")} for r in rows]
        )
    trading = sorted(set(trading))
    if any(d >= FV_FIRST for d in trading):
        raise RuntimeError("tse_calendar_opened_fv")
    identity_examples = ("20240923", "20250102", "20250103")
    identity_hits = [d for d in identity_examples if d in trading]
    if identity_hits:
        # Parent-precommit examples of FX-only non-cash weekdays. Not an exclusion list.
        # If the calendar classifies them as TSE cash, identity is unresolved.
        raise RuntimeError("tse_calendar_identity_unresolved:" + ",".join(identity_hits))
    return {
        "pass": len(trading) >= 40,
        "method": method,
        "source_identity": source_identity,
        "source_sha256": source_sha,
        "trading_days": trading,
        "trading_n": len(trading),
        "rows": rows,
        "hardcoded_holiday_names": False,
        "named_date_exclusions": [],
        "identity_examples_absent_from_trading": list(identity_examples),
        "fv_opened": False,
        "outcome_used": False,
    }
