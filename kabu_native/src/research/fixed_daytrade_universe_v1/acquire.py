"""Acquire official J-Quants daily/master/calendar. No yfinance. No 20260914 selection data."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_v1 import (
    CASE_FETCH,
    CASE_KEY_REQUIRED,
    CASE_NO_VA,
    CASE_SCHEMA,
    ENDPOINT_CALENDAR,
    ENDPOINT_DAILY,
    ENDPOINT_MASTER,
    EXPECTED_WINDOW_FIRST,
    FORBIDDEN_DATA_FROM,
    LAST_COMPLETE_TSE_SESSION,
    TRAIL_SESSIONS,
)
from research.fixed_daytrade_universe_v1.calendar_window import build_official_window
from research.fixed_daytrade_universe_v1.jquants_auth import resolve_api_key_meta
from research.fixed_daytrade_universe_v1.jquants_client import fetch_paginated
from research.fixed_daytrade_universe_v1.schema import (
    DAILY_REQUIRED_RAW,
    daily_schema_status,
    finite_number,
    map_daily_row,
    map_master_row,
    missing_required,
    yyyymmdd,
)
from research.fixed_universe_historical_foundation_v1.universe import CANDIDATES

CALENDAR_FROM = "20260501"
CALENDAR_TO = "20260913"  # may include extra days; selection drops > 20260911
BAR_FROM = EXPECTED_WINDOW_FIRST
BAR_TO = LAST_COMPLETE_TSE_SESSION


def _iso(yyyymmdd_s: str) -> str:
    s = yyyymmdd(yyyymmdd_s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def candidate_codes() -> list[str]:
    return [c[0] for c in CANDIDATES]


def _index_master(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in rows:
        mapped = map_master_row(raw)
        out[mapped["symbol"]] = mapped
    return out


def acquire_official_daily(*, fetch=fetch_paginated) -> dict[str, Any]:
    cred = resolve_api_key_meta()
    base: dict[str, Any] = {
        "available": False,
        "credentials": cred,
        "yfinance_used": False,
        "kabu_board_used": False,
        "scraping_used": False,
        "did_not_fetch_network": not cred["present"],
        "forbidden_from": FORBIDDEN_DATA_FROM,
        "last_complete_tse_session": LAST_COMPLETE_TSE_SESSION,
        "trail_sessions_required": int(TRAIL_SESSIONS),
        "source": "jquants_api_v2",
    }
    if not cred["present"]:
        return {
            **base,
            "CASE_HINT": CASE_KEY_REQUIRED,
            "reason": "jquants_api_key_absent",
        }

    source_audit: list[dict[str, Any]] = []
    try:
        cal = fetch(
            path=ENDPOINT_CALENDAR,
            params={"from": CALENDAR_FROM, "to": CALENDAR_TO},
            kind="calendar",
            stem=f"calendar_{CALENDAR_FROM}_{CALENDAR_TO}",
        )
    except Exception as exc:
        return {**base, "CASE_HINT": CASE_FETCH, "reason": str(exc), "did_not_fetch_network": False}
    source_audit.append(cal["meta"])
    window = build_official_window(list(cal.get("rows") or []))
    if not window.get("max_date_ok"):
        return {
            **base,
            "CASE_HINT": CASE_FETCH,
            "reason": "calendar_max_date_after_cutoff",
            "window": window,
            "did_not_fetch_network": False,
        }
    if window.get("mismatch"):
        return {
            **base,
            "CASE_HINT": window["verdict_if_mismatch"],
            "reason": "official_calendar_window_mismatch",
            "window": window,
            "source_audit": source_audit,
            "did_not_fetch_network": False,
        }

    first = window["first"]
    last = window["last"]
    try:
        master_end = fetch(
            path=ENDPOINT_MASTER,
            params={"date": _iso(last)},
            kind="listed_master",
            stem=f"master_asof_{last}",
        )
        master_start = fetch(
            path=ENDPOINT_MASTER,
            params={"date": _iso(first)},
            kind="listed_master",
            stem=f"master_asof_{first}",
        )
    except Exception as exc:
        return {
            **base,
            "CASE_HINT": CASE_FETCH,
            "reason": str(exc),
            "window": window,
            "did_not_fetch_network": False,
        }
    source_audit.append(master_end["meta"])
    source_audit.append(master_start["meta"])
    if master_end.get("sample"):
        miss_m = missing_required(master_end["sample"], ("Date", "Code", "ProdCat"))
        if miss_m:
            return {
                **base,
                "CASE_HINT": CASE_SCHEMA,
                "reason": f"master_schema_mismatch:{miss_m}",
                "window": window,
                "source_audit": source_audit,
                "did_not_fetch_network": False,
            }

    codes = candidate_codes()
    daily_by_symbol: dict[str, list[dict[str, Any]]] = {}
    sample_daily: dict[str, Any] | None = None
    dropped_after_cutoff_n = 0
    for code in codes:
        try:
            pack = fetch(
                path=ENDPOINT_DAILY,
                params={"code": code, "from": _iso(first), "to": _iso(last)},
                kind="daily_bars",
                stem=f"daily_{code}_{first}_{last}",
            )
        except Exception as exc:
            return {
                **base,
                "CASE_HINT": CASE_FETCH,
                "reason": f"{code}:{exc}",
                "window": window,
                "source_audit": source_audit,
                "did_not_fetch_network": False,
            }
        source_audit.append(pack["meta"])
        if sample_daily is None and pack.get("sample"):
            sample_daily = pack["sample"]
            sch = daily_schema_status(sample_daily)
            if not sch["trading_value_field_available"]:
                return {
                    **base,
                    "CASE_HINT": CASE_NO_VA,
                    "reason": "Va_field_absent_in_sample",
                    "daily_schema": sch,
                    "window": window,
                    "source_audit": source_audit,
                    "did_not_fetch_network": False,
                    "raw_sample_keys": sch["raw_keys"],
                }
            if not sch["ok"]:
                return {
                    **base,
                    "CASE_HINT": CASE_SCHEMA,
                    "reason": f"daily_schema_mismatch:{sch['missing_required']}",
                    "daily_schema": sch,
                    "window": window,
                    "source_audit": source_audit,
                    "did_not_fetch_network": False,
                }
        mapped_rows: list[dict[str, Any]] = []
        for raw in pack.get("rows") or []:
            row = map_daily_row(raw)
            if row["date"] > LAST_COMPLETE_TSE_SESSION:
                dropped_after_cutoff_n += 1
                continue
            if row["date"] < first or row["date"] > last:
                continue
            mapped_rows.append(row)
        daily_by_symbol[code] = mapped_rows

    all_dates: list[str] = []
    for rows in daily_by_symbol.values():
        all_dates.extend(r["date"] for r in rows)
    max_bar = max(all_dates) if all_dates else None
    if max_bar and max_bar > LAST_COMPLETE_TSE_SESSION:
        return {
            **base,
            "CASE_HINT": CASE_FETCH,
            "reason": "daily_max_date_after_cutoff",
            "max_date": max_bar,
            "did_not_fetch_network": False,
        }

    return {
        **base,
        "available": True,
        "CASE_HINT": None,
        "reason": "official_60d_loaded",
        "did_not_fetch_network": False,
        "window": window,
        "daily_by_symbol": daily_by_symbol,
        "master_asof_cutoff": _index_master(list(master_end.get("rows") or [])),
        "master_asof_window_start": _index_master(list(master_start.get("rows") or [])),
        "daily_schema": daily_schema_status(sample_daily),
        "raw_sample_keys": daily_schema_status(sample_daily)["raw_keys"],
        "source_audit": source_audit,
        "dropped_after_cutoff_n": dropped_after_cutoff_n,
        "loaded_series_n": len(daily_by_symbol),
        "candidate_codes": codes,
        "max_date": max_bar,
        "assert_max_date_le_cutoff": (max_bar is None) or (max_bar <= LAST_COMPLETE_TSE_SESSION),
        "required_daily_raw_fields": list(DAILY_REQUIRED_RAW),
        "close_times_volume_not_used_as_va": True,
    }


assert CALENDAR_TO < FORBIDDEN_DATA_FROM
assert BAR_TO == LAST_COMPLETE_TSE_SESSION
assert len(candidate_codes()) == 45
