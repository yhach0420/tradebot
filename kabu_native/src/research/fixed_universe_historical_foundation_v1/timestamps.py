"""Canonical 1-minute bar semantics. Same-bar close ENTRY forbidden."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")

CANONICAL_TZ = "Asia/Tokyo"
BAR_INTERVAL = "[T, T+1min)"
SAME_BAR_CLOSE_ENTRY = False
FEATURE_AVAILABLE_BEFORE_BAR_END = False


def availability_time_jst(bar_start: datetime) -> datetime:
    """Information in [T, T+1) is first usable at T+1min, not at T."""
    if bar_start.tzinfo is None:
        bar_start = bar_start.replace(tzinfo=JST)
    return bar_start.astimezone(JST) + timedelta(minutes=1)


def earliest_evaluation_clock(bar_start: datetime) -> datetime:
    """Signal from bar close at T+1 uses next available price at/after T+1."""
    return availability_time_jst(bar_start)


def map_provider_label_to_bar_start(*, provider: str, labeled: datetime, mapping: str) -> datetime:
    if mapping == "label_is_start":
        return labeled
    if mapping == "label_is_end":
        return labeled - timedelta(minutes=1)
    raise ValueError(f"unknown mapping {provider} {mapping}")


def semantics_rows() -> list[dict[str, Any]]:
    return [
        {
            "source": "CANONICAL_RESEARCH",
            "label_field": "bar_start_jst",
            "interval": BAR_INTERVAL,
            "availability": "T_plus_1min",
            "same_bar_close_entry": False,
            "timezone": CANONICAL_TZ,
            "fully_known": True,
            "mapping_status": "FROZEN",
            "notes": "All providers must map into this clock or be rejected.",
        },
        {
            "source": "J-Quants_equities_bars_minute",
            "label_field": "Date+Time_HH:mm",
            "interval": "OFFICIAL_UNSPECIFIED_start_vs_end",
            "availability": "CONSERVATIVE_Time_plus_1min_if_Time_is_start",
            "same_bar_close_entry": False,
            "timezone": "JST_implied",
            "fully_known": False,
            "mapping_status": "PENDING_TICK_RECONCILE",
            "notes": (
                "Official spec lists Time HH:mm and omits empty minutes. "
                "Does not say whether 09:00 is [09:00,09:01) or (08:59,09:00]. "
                "Confirm against tick CSV before panel freeze. "
                "If unconfirmable, reject provider."
            ),
        },
        {
            "source": "J-Quants_equities_trades_tick",
            "label_field": "Time_HH:MM:SS.ffffff",
            "interval": "print_time",
            "availability": "print_time_plus_research_lag_zero_for_historical",
            "same_bar_close_entry": False,
            "timezone": "JST",
            "fully_known": True,
            "mapping_status": "FROZEN_FOR_AUDIT",
            "notes": "Audit source for minute bars. Not realtime ENTRY.",
        },
        {
            "source": "DataCube_futures_1min",
            "label_field": "Interval_Time_HHMM",
            "interval": "implied_[Interval, Interval+1min)_from_VWAP_language",
            "availability": "Interval_plus_1min",
            "same_bar_close_entry": False,
            "timezone": "JST",
            "fully_known": True,
            "mapping_status": "PROVISIONAL_FROM_SPEC_TEXT",
            "notes": (
                "Spec: VWAP from interval start to end for that one minute. "
                "Trade_Date is night-start through next day session. "
                "Execution_Date from 2022-09. Session_ID 999/003. "
                "Still confirm on sample file."
            ),
        },
        {
            "source": "Databento_ohlcv_1m",
            "label_field": "ts_event",
            "interval": "MUST_READ_SCHEMA",
            "availability": "bar_end_exclusive",
            "same_bar_close_entry": False,
            "timezone": "UTC",
            "fully_known": False,
            "mapping_status": "PENDING_SCHEMA",
            "notes": "Reject if UTC mapping cannot be made exact. Convert UTC -> JST after mapping.",
        },
        {
            "source": "Dukascopy_USDJPY",
            "label_field": "vendor_bar_time",
            "interval": "MUST_EXPORT_UTC",
            "availability": "T_plus_1min_after_UTC_map",
            "same_bar_close_entry": False,
            "timezone": "UTC_required",
            "fully_known": False,
            "mapping_status": "PENDING_EXPORT_SETTINGS",
            "notes": "Do not ingest broker DST server time. Reject if DST-ambiguous.",
        },
        {
            "source": "PAPER_RUNTIME",
            "label_field": "received_at",
            "interval": "event",
            "availability": "actual_received_at",
            "same_bar_close_entry": False,
            "timezone": "JST_or_capture_clock",
            "fully_known": True,
            "mapping_status": "FROZEN_FOR_PAPER",
            "notes": "Paper substitutes Ask ENTRY / Bid EXIT / spread / fill. Historical 1m is mechanism evidence only.",
        },
    ]


def leakage_protection() -> dict[str, Any]:
    return {
        "SPEC_READY": True,
        "APPLIED_TO_PANEL": False,
        "SAME_BAR_CLOSE_ENTRY_ALLOWED": False,
        "FEATURE_FROM_BAR_T_AVAILABLE_AT": "T_plus_1min",
        "EVALUATION_PRICE": "next_available_bar_or_price",
        "PAPER_SUBSTITUTION": "received_at_Ask_ENTRY_Bid_EXIT",
        "EXTERNAL_SAME_RULE": True,
        "EXAMPLE_FORBIDDEN": "use_1030_minute_bar_close_to_enter_at_103000",
        "EXAMPLE_ALLOWED": "1030_bar_close_feature_evaluates_from_103100_or_later",
        "EMPTY_MINUTE_RULE": "missing_bar_is_not_forward_filled_for_signals",
        "HISTORICAL_NOT_FILL_PROOF": True,
        "HISTORICAL_NOT_SPREAD_PROOF": True,
    }


def fully_known() -> bool:
    return all(r["fully_known"] is True for r in semantics_rows() if r["source"] != "PAPER_RUNTIME")


assert SAME_BAR_CLOSE_ENTRY is False
assert FEATURE_AVAILABLE_BEFORE_BAR_END is False
assert availability_time_jst(datetime(2026, 9, 14, 10, 30, tzinfo=JST)) == datetime(
    2026, 9, 14, 10, 31, tzinfo=JST
)
assert fully_known() is False
