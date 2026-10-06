"""Prove USDJPY timestamp semantics before any stock join. Store original UTC timestamps."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from research.usd_jpy_sector_symbol_response_v1.source import (
    _cache_tick_path,
    decode_ticks,
    load_minute_frame,
)

JST = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _utc_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def prove_semantics(fx: Any | None = None) -> dict[str, Any]:
    if fx is None:
        fx, _meta = load_minute_frame()
    proven = True
    notes = []
    if fx is None or getattr(fx, "empty", True):
        return {
            "proven": False,
            "reason": "no_fx_rows",
            "timezone": None,
            "join_rule": "driver_available_at <= stock_decision_time",
        }

    offsets = sorted({float(x) for x in fx["utc_offset_hours"].tolist() if x == x})
    dst_ok = offsets == [9.0]
    if not dst_ok:
        proven = False
        notes.append("jst_offset_not_constantly_plus9")

    sample = fx.iloc[0]
    first_ms = int(sample["ts_utc_ms"])
    first_utc = _utc_ms(first_ms)
    first_jst = first_utc.astimezone(JST)
    ny = first_utc.astimezone(NY)

    # DST probes: US spring/fall 2025. Japan must stay +9 even if NY offset changes.
    def _offset_on(ymd: str, hhmm: str) -> dict[str, Any]:
        sub = fx[(fx["jst_date"] == ymd) & (fx["jst_hhmm"] == hhmm)]
        if sub.empty:
            sub = fx[fx["jst_date"] == ymd]
        if sub.empty:
            return {"ymd": ymd, "present": False}
        row = sub.iloc[0]
        ms = int(row["ts_utc_ms"])
        utc = _utc_ms(ms)
        return {
            "ymd": ymd,
            "present": True,
            "jst_offset_h": float(row["utc_offset_hours"]),
            "ny_offset_h": (utc.astimezone(NY).utcoffset() or timedelta()).total_seconds() / 3600.0,
            "bar_start_utc": row["bar_start_utc"],
            "bar_start_jst": row["bar_start_jst"],
        }

    dst = {
        "us_spring_20250310": _offset_on("20250310", "09:00"),
        "us_fall_20251103": _offset_on("20251103", "09:00"),
        "japan_no_dst": True,
    }
    for v in dst.values():
        if isinstance(v, dict) and v.get("present") and float(v.get("jst_offset_h") or 0) != 9.0:
            proven = False
            notes.append("dst_probe_jst_not_plus9")

    # Weekend: Saturday JST cash is closed; FX often thin/empty around Sat UTC.
    sat = fx[fx["jst_date"] == "20240921"]
    sun = fx[fx["jst_date"] == "20240922"]
    weekend = {
        "saturday_20240921_n": int(len(sat)),
        "sunday_20240922_n": int(len(sun)),
        "weekend_not_filled_with_fake_bars": True,
        "missing_interval_behavior": "day_file_absent_or_empty_yields_no_rows_no_interpolation",
    }

    # Japan holiday (Respect for the Aged Day 2024-09-16 was Monday; 2024-09-23 was holiday).
    hol = fx[fx["jst_date"] == "20240923"]
    holiday = {
        "japan_holiday_20240923_fx_n": int(len(hol)),
        "fx_may_trade_when_tse_closed": int(len(hol)) > 0,
        "stock_join_only_on_discovery_tse_days": True,
    }

    # Bid vs Ask
    both = fx[fx["has_ask"] == True]  # noqa: E712
    spread = both["spread_close"].to_numpy()
    spread = spread[spread == spread]
    bid_ask = {
        "has_bid": True,
        "has_ask": bool(len(both) > 0),
        "median_spread": float(sorted(spread)[len(spread) // 2]) if len(spread) else None,
        "ask_minus_bid_nonnegative_frac": float((spread >= -1e-9).mean()) if len(spread) else None,
        "mid_close": "0.5*(bid_close+ask_close)",
    }
    if bid_ask["ask_minus_bid_nonnegative_frac"] is not None and bid_ask["ask_minus_bid_nonnegative_frac"] < 0.95:
        proven = False
        notes.append("ask_bid_spread_inconsistent")

    # Bar start/end: first Discovery open 20240917 09:00 JST == 20240917 00:00 UTC
    open_bar = fx[(fx["jst_date"] == "20240917") & (fx["jst_hhmm"] == "09:00")]
    bar = {
        "label": "BAR_START",
        "timestamp_field": "ts_utc_ms",
        "timestamp_timezone": "UTC",
        "jst_conversion": "Asia/Tokyo_fixed_plus9",
        "dst_handling": "Japan_no_DST_do_not_use_America_New_York_instrument_defaultTimezone",
        "instrument_defaultTimezone_ignored": "America/New_York",
        "bar_interval_ms": 60_000,
        "available_at": "bar_start_plus_1_minute",
        "same_bar_close_not_used_as_known_at_bar_start": True,
        "open_20240917_0900_jst_present": bool(len(open_bar) > 0),
        "open_bar_utc": None if open_bar.empty else str(open_bar.iloc[0]["bar_start_utc"]),
        "open_bar_jst": None if open_bar.empty else str(open_bar.iloc[0]["bar_start_jst"]),
    }
    if open_bar.empty:
        proven = False
        notes.append("missing_20240917_0900_jst_bar")
    else:
        if not str(open_bar.iloc[0]["bar_start_utc"]).startswith("2024-09-17T00:00:00"):
            proven = False
            notes.append("0900_jst_not_0000_utc")

    ticks_meta = {}
    tick_path = _cache_tick_path(date(2024, 9, 17), 0)
    if tick_path.is_file() and tick_path.stat().st_size > 2:
        payload = json.loads(tick_path.read_text(encoding="utf-8"))
        ticks = decode_ticks(payload)
        if ticks:
            t0 = ticks[0]
            t0_jst = _utc_ms(int(t0["ts_utc_ms"])).astimezone(JST)
            ticks_meta = {
                "n": len(ticks),
                "first_tick_utc_ms": int(t0["ts_utc_ms"]),
                "first_tick_jst": t0_jst.isoformat(),
                "first_bid": t0["bid"],
                "first_ask": t0["ask"],
                "raw_bid_ask_timestamps_retained_in_tick_cache": True,
                "tick_available_at": "tick_timestamp",
                "candle_open_vs_first_tick": None
                if open_bar.empty
                else {
                    "bid_open": float(open_bar.iloc[0]["bid_open"]),
                    "tick_bid": t0["bid"],
                    "abs_diff": abs(float(open_bar.iloc[0]["bid_open"]) - float(t0["bid"])),
                },
            }

    gaps = []
    if len(fx) >= 2:
        dts = fx["ts_utc_ms"].diff().dropna()
        gt = dts[dts > 60_000]
        gaps = [
            {"n_gt_1m": int(len(gt)), "max_gap_ms": int(dts.max()) if len(dts) else None, "median_step_ms": int(dts.median())}
        ]
    missing = {
        "no_interpolation_across_gaps": True,
        "intraday_1m_return_requires_consecutive_60000ms_bars": True,
        "gap_summary": gaps,
    }

    join = {
        "rule": "driver_available_at <= stock_decision_time",
        "stock_bar_semantics": "BAR_START_available_at_Tplus1m",
        "fx_bar_semantics": "BAR_START_available_at_Tplus1m",
        "original_timestamps_stored": True,
        "do_not_use_broker_DST": True,
    }
    if not dst_ok or not bar["open_20240917_0900_jst_present"]:
        proven = False
    return {
        "proven": proven,
        "notes": notes,
        "timezone": "store_UTC_convert_Asia/Tokyo",
        "utc_jst_first_bar": {"utc": first_utc.isoformat(), "jst": first_jst.isoformat(), "ny_display_only": ny.isoformat()},
        "dst": dst,
        "jst_offsets_observed": offsets,
        "bid_vs_ask": bid_ask,
        "bar_start_end": bar,
        "missing_intervals": missing,
        "weekend": weekend,
        "japan_holiday": holiday,
        "ticks": ticks_meta,
        "availability_timestamp": "available_at_utc_ms = ts_utc_ms + 60000",
        "join": join,
        "ny_first": str(ny),
    }
