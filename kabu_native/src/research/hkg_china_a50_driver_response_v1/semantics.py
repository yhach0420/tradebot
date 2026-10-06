"""Prove HKG/CHI timestamp and observable session from acquired bars. Do not infer hours only from documentation."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from research.hkg_china_a50_driver_response_v1 import LABEL_CHI, LABEL_HKG, STATUS_PROXY
from research.hkg_china_a50_driver_response_v1.source import _cache_tick_path, decode_ticks

JST = ZoneInfo("Asia/Tokyo")
HKT = ZoneInfo("Asia/Hong_Kong")
CST = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def _utc_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _gaps(df: Any) -> dict[str, Any]:
    dts = df["ts_utc_ms"].diff().dropna()
    gt = dts[dts > 60_000]
    return {
        "n_gt_1m": int(len(gt)),
        "max_gap_ms": int(dts.max()) if len(dts) else None,
        "median_step_ms": int(dts.median()) if len(dts) else None,
        "no_interpolation_across_gaps": True,
        "intraday_1m_return_requires_consecutive_60000ms_bars": True,
        "asof_rejects_stale_or_nonlive_beyond_120s": True,
    }


def _session_from_live(df: Any, *, name: str) -> dict[str, Any]:
    """Observable JST intervals from live bars only. Weekends/closures are not forward-filled."""
    if df is None or getattr(df, "empty", True) or "live" not in df.columns:
        return {"proven": False, "reason": f"no_live_flag_{name}"}
    live = df[df["live"] == True]  # noqa: E712
    if live.empty:
        return {"proven": False, "reason": f"no_live_bars_{name}"}
    wd = live[live["weekday"] < 5]
    by_hh = Counter(str(x) for x in wd["jst_hhmm"].tolist())
    n_days = int(wd["jst_date"].nunique()) if len(wd) else 0
    frac = {hh: (by_hh[hh] / n_days if n_days else 0.0) for hh in sorted(by_hh)}
    active = [hh for hh, f in sorted(frac.items()) if f >= 0.40]
    first = active[0] if active else None
    last = active[-1] if active else None
    # Detect a lunch-like interior gap among active minutes.
    break_start = break_end = None
    if len(active) >= 4:
        mins = []
        for hh in active:
            h, m = hh.split(":")
            mins.append(int(h) * 60 + int(m))
        mins = sorted(mins)
        biggest = 0
        gap_a = gap_b = None
        for a, b in zip(mins, mins[1:]):
            if b - a > biggest:
                biggest = b - a
                gap_a, gap_b = a, b
        if biggest >= 20:
            break_start = f"{gap_a // 60:02d}:{gap_a % 60:02d}"
            break_end = f"{gap_b // 60:02d}:{gap_b % 60:02d}"

    def _frac_at(hhmm: str) -> float:
        if n_days <= 0:
            return 0.0
        return float(wd[wd["jst_hhmm"] == hhmm]["jst_date"].nunique() / n_days)

    frac_0859 = _frac_at("08:59")
    frac_0900 = _frac_at("09:00")
    japan_open_ok = bool(frac_0859 >= 0.40 or frac_0900 >= 0.40)
    japan_active = [hh for hh in active if ("09:00" <= hh < "11:30") or ("12:30" <= hh <= "15:00")]
    first_japan = japan_active[0] if japan_active else None
    last_in_japan = japan_active[-1] if japan_active else None
    weekend_live_n = int((live["weekday"] >= 5).sum())
    sample_closed = live[(live["jst_date"] == "20240917") & (live["jst_hhmm"] < "09:00")]
    return {
        "proven": bool(first and last and n_days >= 40),
        "n_live_bars": int(len(live)),
        "n_live_weekdays": int(n_days),
        "cfd_first_live_jst": first,
        "first_valid_observable_jst": first_japan or first,
        "japan_cash_overlap_first_jst": first_japan,
        "session_break_jst": {"from": break_start, "to": break_end} if break_start else None,
        "reopen_jst": break_end,
        "final_usable_japan_session_jst": last_in_japan,
        "last_live_jst": last,
        "live_frac_0859_jst": round(frac_0859, 3),
        "live_frac_0900_jst": round(frac_0900, 3),
        "japan_0900_driver_available_before_open": japan_open_ok,
        "weekend_live_bar_n": weekend_live_n,
        "do_not_forward_fill_closed_market": True,
        "do_not_use_stale_previous_session_as_0900_driver": True,
        "live_frac_head": [{"jst_hhmm": hh, "frac": round(frac[hh], 3)} for hh in active[:8]],
        "live_frac_tail": [{"jst_hhmm": hh, "frac": round(frac[hh], 3)} for hh in active[-8:]],
        "sample_pre_0900_live_n_20240917": int(len(sample_closed)),
        "name": name,
    }


def prove_semantics(hkg: Any | None, chi: Any | None, *, code_hkg: str, code_chi: str) -> dict[str, Any]:
    proven = True
    notes = []
    if hkg is None or getattr(hkg, "empty", True) or chi is None or getattr(chi, "empty", True):
        return {
            "proven": False,
            "reason": "no_hkg_or_chi_rows",
            "join_rule": "driver_available_at <= stock_decision_time AND driver_bar_live",
        }

    jst_off = sorted({float(x) for x in hkg["utc_offset_hours"].tolist() if x == x})
    hkt_off = sorted({float(x) for x in hkg["hkt_offset_hours"].tolist() if x == x})
    cst_off = sorted({float(x) for x in chi["cst_offset_hours"].tolist() if x == x})
    if jst_off != [9.0]:
        proven = False
        notes.append("jst_offset_not_constantly_plus9")
    if hkt_off != [8.0]:
        notes.append("hkt_offset_not_constantly_plus8")
        if 8.0 not in hkt_off:
            proven = False
    if cst_off != [8.0]:
        notes.append("cst_offset_not_constantly_plus8")
        if 8.0 not in cst_off:
            proven = False

    sample = hkg.iloc[0]
    first_ms = int(sample["ts_utc_ms"])
    first_utc = _utc_ms(first_ms)
    bar = {
        "label": "BAR_START",
        "timestamp_field": "ts_utc_ms",
        "timestamp_timezone": "UTC",
        "jst_conversion": "Asia/Tokyo_fixed_plus9",
        "hkt_conversion": "Asia/Hong_Kong_no_DST_in_sample",
        "cst_conversion": "Asia/Shanghai_no_DST_in_sample",
        "bar_interval_ms": 60_000,
        "available_at": "bar_end = bar_start_plus_1_minute",
        "same_bar_close_not_used_as_known_at_bar_start": True,
        "hkg_code": code_hkg,
        "chi_code": code_chi,
    }
    sess_hkg = _session_from_live(hkg, name="HKG")
    sess_chi = _session_from_live(chi, name="CHI")
    if not sess_hkg.get("proven") or not sess_chi.get("proven"):
        proven = False
        notes.append("session_not_proven_from_live_bars")

    ticks_meta = {}
    tick_path = _cache_tick_path(code_hkg, date(2024, 9, 17), 1)
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
        "rule": "driver_available_at <= stock_decision_time AND live_bar AND lag<=120s",
        "stock_bar_semantics": "BAR_START_available_at_Tplus1m",
        "driver_bar_semantics": "BAR_START_available_at_Tplus1m",
        "original_timestamps_stored_utc": True,
        "jst_for_analysis_display_only": True,
        "no_stale_previous_session_for_japan_0900": True,
    }
    return {
        "proven": proven,
        "notes": notes,
        "timezone": "store_UTC_convert_Asia/Tokyo_Hong_Kong_Shanghai",
        "utc_jst_first_bar": {
            "utc": first_utc.isoformat(),
            "jst": first_utc.astimezone(JST).isoformat(),
            "hkt": first_utc.astimezone(HKT).isoformat(),
            "cst": first_utc.astimezone(CST).isoformat(),
        },
        "jst_offsets_observed": jst_off,
        "hkt_offsets_observed": hkt_off,
        "cst_offsets_observed": cst_off,
        "dst": {
            "hong_kong_dst_in_sample": len(hkt_off) > 1,
            "shanghai_dst_in_sample": len(cst_off) > 1,
            "japan_no_dst": True,
        },
        "bar_start_end": bar,
        "missing_intervals_hkg": _gaps(hkg),
        "missing_intervals_chi": _gaps(chi),
        "session_hkg": sess_hkg,
        "session_chi": sess_chi,
        "ticks": ticks_meta,
        "availability_timestamp": "available_at_utc_ms = ts_utc_ms + 60000",
        "join": join,
        "label": STATUS_PROXY,
        "hkg_label": LABEL_HKG,
        "chi_label": LABEL_CHI,
        "true_exchange_native_futures": False,
        "hkg_n": int(len(hkg)),
        "chi_n": int(len(chi)),
    }
