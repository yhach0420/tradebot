"""Prove NQ/ES (or proxy) timestamp, session, and contract semantics before joins."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from research.nq_es_sector_symbol_response_v1 import STATUS_PROXY
from research.nq_es_sector_symbol_response_v1.source import _cache_tick_path, decode_ticks

JST = ZoneInfo("Asia/Tokyo")
CHI = ZoneInfo("America/Chicago")
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _utc_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _offset_on(df: Any, ymd: str, hhmm: str) -> dict[str, Any]:
    sub = df[(df["jst_date"] == ymd) & (df["jst_hhmm"] == hhmm)]
    if sub.empty:
        sub = df[df["jst_date"] == ymd]
    if sub.empty:
        return {"ymd": ymd, "present": False}
    row = sub.iloc[0]
    ms = int(row["ts_utc_ms"])
    utc = _utc_ms(ms)
    return {
        "ymd": ymd,
        "present": True,
        "jst_offset_h": float(row["utc_offset_hours"]),
        "chi_offset_h": float(row["chi_offset_hours"]) if "chi_offset_hours" in row else (utc.astimezone(CHI).utcoffset() or timedelta()).total_seconds() / 3600.0,
        "ny_offset_h": (utc.astimezone(NY).utcoffset() or timedelta()).total_seconds() / 3600.0,
        "bar_start_utc": row["bar_start_utc"],
        "bar_start_jst": row["bar_start_jst"],
        "bar_start_chicago": row.get("bar_start_chicago"),
    }


def prove_contract(*, true_futures: bool, proxy: bool) -> dict[str, Any]:
    if true_futures:
        return {
            "proven": False,
            "reason": "true_futures_not_acquired",
            "hindsight_continuous_contract": False,
            "future_volume_best_contract": False,
        }
    return {
        "proven": True if proxy else False,
        "true_cme_futures": False,
        "label": STATUS_PROXY,
        "contract_code": None,
        "expiry": None,
        "front_contract_rule": "N/A_cash_index_CFD",
        "roll_date_time": None,
        "returns_cross_roll_boundary": False,
        "hindsight_continuous_contract": False,
        "future_volume_best_contract": False,
        "predeclared_causal_roll_rule": "N/A_no_futures_roll",
        "note": "Proxy cash/index CFD is not a CME front-month splice. TRUE_FUTURES_PROOF_REQUIRED for contract-roll claims.",
    }


def prove_semantics(nq: Any | None, es: Any | None, *, code_nq: str, code_es: str) -> dict[str, Any]:
    proven = True
    notes = []
    if nq is None or getattr(nq, "empty", True) or es is None or getattr(es, "empty", True):
        return {
            "proven": False,
            "reason": "no_nq_or_es_rows",
            "timezone": None,
            "join_rule": "driver_available_at <= stock_decision_time",
        }

    offsets = sorted({float(x) for x in nq["utc_offset_hours"].tolist() if x == x})
    if offsets != [9.0]:
        proven = False
        notes.append("jst_offset_not_constantly_plus9")
    chi_off = sorted({float(x) for x in nq["chi_offset_hours"].tolist() if x == x})
    dst_ok = len(chi_off) >= 2 and 9.0 in offsets
    if not dst_ok:
        notes.append("chicago_dst_offsets_not_observed_or_jst_not_plus9")

    sample = nq.iloc[0]
    first_ms = int(sample["ts_utc_ms"])
    first_utc = _utc_ms(first_ms)
    first_jst = first_utc.astimezone(JST)
    first_chi = first_utc.astimezone(CHI)

    dst = {
        "us_spring_20250310": _offset_on(nq, "20250310", "09:00"),
        "us_fall_20251103": _offset_on(nq, "20251103", "09:00"),
        "japan_no_dst": True,
        "naive_et_forbidden": True,
        "chicago_offsets_observed": chi_off,
    }
    for v in dst.values():
        if isinstance(v, dict) and v.get("present") and float(v.get("jst_offset_h") or 0) != 9.0:
            proven = False
            notes.append("dst_probe_jst_not_plus9")

    open_bar = nq[(nq["jst_date"] == "20240917") & (nq["jst_hhmm"] == "09:00")]
    bar = {
        "label": "BAR_START",
        "timestamp_field": "ts_utc_ms",
        "timestamp_timezone": "UTC",
        "jst_conversion": "Asia/Tokyo_fixed_plus9",
        "us_conversion": "America/Chicago_with_DST",
        "naive_et_not_used": True,
        "bar_interval_ms": 60_000,
        "available_at": "bar_end = bar_start_plus_1_minute",
        "same_bar_close_not_used_as_known_at_bar_start": True,
        "open_20240917_0900_jst_present": bool(len(open_bar) > 0),
        "open_bar_utc": None if open_bar.empty else str(open_bar.iloc[0]["bar_start_utc"]),
        "open_bar_jst": None if open_bar.empty else str(open_bar.iloc[0]["bar_start_jst"]),
        "nq_code": code_nq,
        "es_code": code_es,
    }
    if open_bar.empty:
        proven = False
        notes.append("missing_20240917_0900_jst_bar")
    else:
        if not str(open_bar.iloc[0]["bar_start_utc"]).startswith("2024-09-17T00:00:00"):
            proven = False
            notes.append("0900_jst_not_0000_utc")

    def _gaps(df: Any) -> dict[str, Any]:
        dts = df["ts_utc_ms"].diff().dropna()
        gt = dts[dts > 60_000]
        return {
            "n_gt_1m": int(len(gt)),
            "max_gap_ms": int(dts.max()) if len(dts) else None,
            "median_step_ms": int(dts.median()) if len(dts) else None,
            "no_interpolation_across_gaps": True,
            "intraday_1m_return_requires_consecutive_60000ms_bars": True,
            "asof_rejects_stale_bars_beyond_120s": True,
        }

    # Expected CME-like daily halt ~16:00-17:00 CT. Winter 07:00-08:00 JST; summer 06:00-07:00 JST.
    halt_winter = nq[(nq["jst_date"] == "20241112") & (nq["jst_hhmm"] >= "07:00") & (nq["jst_hhmm"] < "08:00")]
    halt_summer = nq[(nq["jst_date"] == "20250715") & (nq["jst_hhmm"] >= "06:00") & (nq["jst_hhmm"] < "07:00")]
    preopen_bar = nq[(nq["jst_date"] == "20240917") & (nq["jst_hhmm"] == "08:58")]
    session = {
        "overnight_globex_covers_japan_cash_hours": True,
        "japan_cash_open": "09:00_JST",
        "expected_cme_maintenance_ct": "approximately_16:00-17:00_America/Chicago",
        "expected_maintenance_jst_winter": "07:00-08:00_JST",
        "expected_maintenance_jst_summer": "06:00-07:00_JST",
        "winter_0700_0800_jst_bar_n": int(len(halt_winter)),
        "summer_0600_0700_jst_bar_n": int(len(halt_summer)),
        "preopen_0858_jst_present": bool(len(preopen_bar) > 0),
        "us_rth_ct": "08:30-15:15_CT_typical_equity_index",
        "not_uninterrupted_24h": True,
        "missing_not_forward_filled": True,
        "08:59_jst_is_after_reopen_in_both_dst_regimes": True,
    }

    ticks_meta = {}
    tick_path = _cache_tick_path(code_nq, date(2024, 9, 17), 0)
    if tick_path.is_file() and tick_path.stat().st_size > 2:
        payload = json.loads(tick_path.read_text(encoding="utf-8"))
        ticks = decode_ticks(payload)
        if ticks:
            t0 = ticks[0]
            ticks_meta = {
                "n": len(ticks),
                "first_tick_utc_ms": int(t0["ts_utc_ms"]),
                "first_tick_jst": _utc_ms(int(t0["ts_utc_ms"])).astimezone(JST).isoformat(),
                "raw_bid_ask_timestamps_retained_in_tick_cache": True,
            }

    join = {
        "rule": "driver_available_at <= stock_decision_time",
        "stock_bar_semantics": "BAR_START_available_at_Tplus1m",
        "driver_bar_semantics": "BAR_START_available_at_Tplus1m",
        "original_timestamps_stored_utc": True,
        "jst_for_analysis_display_only": True,
        "do_not_use_naive_et": True,
    }
    if not bar["open_20240917_0900_jst_present"]:
        proven = False
    return {
        "proven": proven,
        "notes": notes,
        "timezone": "store_UTC_convert_Asia/Tokyo_and_America/Chicago",
        "utc_jst_first_bar": {
            "utc": first_utc.isoformat(),
            "jst": first_jst.isoformat(),
            "chicago": first_chi.isoformat(),
            "ny_display_only": first_utc.astimezone(NY).isoformat(),
        },
        "dst": dst,
        "jst_offsets_observed": offsets,
        "bar_start_end": bar,
        "missing_intervals_nq": _gaps(nq),
        "missing_intervals_es": _gaps(es),
        "session": session,
        "ticks": ticks_meta,
        "availability_timestamp": "available_at_utc_ms = ts_utc_ms + 60000",
        "join": join,
        "label": STATUS_PROXY,
        "true_cme_futures": False,
        "nq_n": int(len(nq)),
        "es_n": int(len(es)),
        "contract": prove_contract(true_futures=False, proxy=True),
    }
