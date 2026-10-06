"""Pair Bid/Ask 1m bars. Preserve both sides. No forward-fill. No silent duplicate drop."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.contracts.time import UTC, bar_start_available_at, to_jst
from research.causal_driver_pb1.phase1 import ALLOWED_JST_FIRST, ALLOWED_JST_LAST, CANONICAL_TIMEZONE, NATIVE_TIMEZONE
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed, day_iter
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase1.jetta import SIDES, load_side_rows


@dataclass(frozen=True, slots=True)
class UsdJpyNormalizedBar:
    ts_utc_ms: int
    bar_start: datetime
    available_at: datetime
    source_timestamp: datetime
    source_timezone: str
    normalized_timestamp_jst: datetime
    bid_open: float | None
    bid_high: float | None
    bid_low: float | None
    bid_close: float | None
    ask_open: float | None
    ask_high: float | None
    ask_low: float | None
    ask_close: float | None
    mid_close: float | None
    spread_close: float | None
    mid_is_raw_source: bool
    source_id: str
    quality_status: QualityStatus
    quality_reason: str
    jst_date: str
    utc_file_dates: tuple[str, ...]


def _ohlc_ok(open_: float, high: float, low: float, close: float) -> bool:
    if not (open_ > 0 and high > 0 and low > 0 and close > 0):
        return False
    if high < low:
        return False
    if high < max(open_, close):
        return False
    if low > min(open_, close):
        return False
    return True


def _side_tuple(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))


def _price_reason(bid: dict[str, Any] | None, ask: dict[str, Any] | None) -> tuple[QualityStatus, str]:
    if bid is None and ask is None:
        return QualityStatus.MISSING, "BOTH_SIDES_MISSING"
    if bid is None:
        return QualityStatus.DEGRADED, "BID_MISSING"
    if ask is None:
        return QualityStatus.DEGRADED, "ASK_MISSING"
    bo, bh, bl, bc = _side_tuple(bid)
    ao, ah, al, ac = _side_tuple(ask)
    if not _ohlc_ok(bo, bh, bl, bc):
        return QualityStatus.REJECTED, "BID_OHLC_SANITY"
    if not _ohlc_ok(ao, ah, al, ac):
        return QualityStatus.REJECTED, "ASK_OHLC_SANITY"
    if ac < bc:
        return QualityStatus.REJECTED, "ASK_LT_BID"
    return QualityStatus.VALID, "PAIRED"


def _record_dup(store: dict[int, list[tuple[float, float, float, float]]], ts: int, tup: tuple[float, float, float, float]) -> None:
    store.setdefault(ts, []).append(tup)


def _dup_stats(store: dict[int, list[tuple[float, float, float, float]]]) -> dict[str, int]:
    duplicate_n = 0
    identical_n = 0
    conflicting_n = 0
    for _ts, rows in store.items():
        if len(rows) <= 1:
            continue
        duplicate_n += len(rows) - 1
        uniq = set(rows)
        if len(uniq) == 1:
            identical_n += len(rows) - 1
        else:
            conflicting_n += 1
    return {
        "duplicate_n": duplicate_n,
        "identical_duplicate_n": identical_n,
        "conflicting_duplicate_n": conflicting_n,
    }


def pair_raw_rows(
    *,
    bid_rows: list[dict[str, Any]],
    ask_rows: list[dict[str, Any]],
    source_id: str,
) -> dict[str, Any]:
    bid_by: dict[int, dict[str, Any]] = {}
    ask_by: dict[int, dict[str, Any]] = {}
    bid_dups: dict[int, list[tuple[float, float, float, float]]] = {}
    ask_dups: dict[int, list[tuple[float, float, float, float]]] = {}
    for row in bid_rows:
        ts = int(row["ts_utc_ms"])
        tup = _side_tuple(row)
        _record_dup(bid_dups, ts, tup)
        bid_by[ts] = row
    for row in ask_rows:
        ts = int(row["ts_utc_ms"])
        tup = _side_tuple(row)
        _record_dup(ask_dups, ts, tup)
        ask_by[ts] = row
    bid_stats = _dup_stats(bid_dups)
    ask_stats = _dup_stats(ask_dups)
    duplicate_n = bid_stats["duplicate_n"] + ask_stats["duplicate_n"]
    identical_duplicate_n = bid_stats["identical_duplicate_n"] + ask_stats["identical_duplicate_n"]
    conflicting_duplicate_n = bid_stats["conflicting_duplicate_n"] + ask_stats["conflicting_duplicate_n"]
    keys = sorted(set(bid_by) | set(ask_by))
    bars: list[UsdJpyNormalizedBar] = []
    dropped_date_n = 0
    for ts in keys:
        bid = bid_by.get(ts)
        ask = ask_by.get(ts)
        utc_dt = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
        jst_dt = to_jst(utc_dt, field="event_time")
        jst_key = jst_dt.strftime("%Y%m%d")
        try:
            assert_ingest_date_allowed(jst_key)
        except (IngestDateDenied, FirewallDenied, ValueError):
            dropped_date_n += 1
            continue
        quality, reason = _price_reason(bid, ask)
        bid_o = bid_h = bid_l = bid_c = None
        ask_o = ask_h = ask_l = ask_c = None
        if bid is not None:
            bid_o, bid_h, bid_l, bid_c = _side_tuple(bid)
        if ask is not None:
            ask_o, ask_h, ask_l, ask_c = _side_tuple(ask)
        mid = None
        spread = None
        if bid_c is not None and ask_c is not None:
            mid = (bid_c + ask_c) / 2.0
            spread = ask_c - bid_c
        files = []
        if bid is not None:
            files.append(str(bid.get("utc_file_date") or ""))
        if ask is not None:
            files.append(str(ask.get("utc_file_date") or ""))
        bars.append(
            UsdJpyNormalizedBar(
                ts_utc_ms=ts,
                bar_start=jst_dt,
                available_at=bar_start_available_at(jst_dt),
                source_timestamp=utc_dt,
                source_timezone=NATIVE_TIMEZONE,
                normalized_timestamp_jst=jst_dt,
                bid_open=bid_o,
                bid_high=bid_h,
                bid_low=bid_l,
                bid_close=bid_c,
                ask_open=ask_o,
                ask_high=ask_h,
                ask_low=ask_l,
                ask_close=ask_c,
                mid_close=mid,
                spread_close=spread,
                mid_is_raw_source=False,
                source_id=source_id,
                quality_status=quality,
                quality_reason=reason,
                jst_date=jst_key,
                utc_file_dates=tuple(sorted(set(x for x in files if x))),
            )
        )
    paired_n = sum(1 for b in bars if b.quality_status == QualityStatus.VALID)
    return {
        "bars": bars,
        "raw_bid_n": len(bid_rows),
        "raw_ask_n": len(ask_rows),
        "paired_bar_n": paired_n,
        "kept_bar_n": len(bars),
        "dropped_restricted_or_out_of_window_n": dropped_date_n,
        "duplicate_n": duplicate_n,
        "identical_duplicate_n": identical_duplicate_n,
        "conflicting_duplicate_n": conflicting_duplicate_n,
        "bid_duplicate": bid_stats,
        "ask_duplicate": ask_stats,
        "canonical_timezone": CANONICAL_TIMEZONE,
        "native_timezone": NATIVE_TIMEZONE,
        "allowed_jst_first": ALLOWED_JST_FIRST,
        "allowed_jst_last": ALLOWED_JST_LAST,
        "available_at_rule": "bar_start_plus_1_minute",
        "forward_fill": False,
        "mid_is_raw_source": False,
        "lunch_dropped": False,
        "pb1_filtered": False,
    }


def load_and_pair(*, utc_first: str, utc_last: str, source_id: str) -> dict[str, Any]:
    bid_rows: list[dict[str, Any]] = []
    ask_rows: list[dict[str, Any]] = []
    for d in day_iter(utc_first, utc_last):
        bid_rows.extend(load_side_rows(d, "BID"))
        ask_rows.extend(load_side_rows(d, "ASK"))
        _ = SIDES
    packed = pair_raw_rows(bid_rows=bid_rows, ask_rows=ask_rows, source_id=source_id)
    packed["utc_first"] = utc_first
    packed["utc_last"] = utc_last
    return packed


def synthetic_side(*, ts_utc_ms: int, open_: float, high: float, low: float, close: float, side: str) -> dict[str, Any]:
    return {
        "ts_utc_ms": ts_utc_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 0.0,
        "shift_ms": 60_000,
        "side": side,
        "utc_file_date": datetime.fromtimestamp(ts_utc_ms / 1000.0, tz=UTC).strftime("%Y%m%d"),
    }


def minutes_between(a: datetime, b: datetime) -> int:
    return int((b - a) / timedelta(minutes=1))
