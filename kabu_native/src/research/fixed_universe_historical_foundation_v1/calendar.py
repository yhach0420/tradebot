"""JST-canonical session calendar specification. No invented holidays for unknown years."""
from __future__ import annotations

from typing import Any

from research.e1_x29_prospective import JPX_HOLIDAYS_2026

CANONICAL_TZ = "Asia/Tokyo"

# TSE cash session hours are versioned. Do not collapse 15:00-close history into 15:30.
TSE_SESSION_RULES = (
    {
        "from": "2011-11-21",
        "to": "2024-11-04",
        "am": "09:00-11:30",
        "pm": "12:30-15:00",
        "closing_auction": "legacy_close_1500",
        "note": "pre_Nov2024_close",
    },
    {
        "from": "2024-11-05",
        "to": None,
        "am": "09:00-11:30",
        "pm": "12:30-15:30",
        "closing_auction": "15:25-15:30",
        "note": "TSE_closing_auction_extension_to_1530",
    },
)

# OSE derivatives hours changed across years. Panel build must version this table
# from official OSE notices; these are the current-era labels, not a 2012 backcast.
OSE_SESSION_CURRENT_ERA = {
    "day_session_id": "999",
    "night_session_id": "003",
    "day_typical": "08:45-15:45_product_dependent",
    "night_typical": "16:30-06:00_next_calendar_product_dependent",
    "trade_date_definition": (
        "weekday_night_open_through_next_weekday_day_close_is_one_Trade_Date"
    ),
    "holiday_trading_included_from": "2022-09",
    "flex_futures_excluded_from_datacube_1min": True,
    "do_not_backcast_current_hours_to_2012": True,
}

US_DST = {
    "zone": "America/New_York",
    "cme_globex": "exchange_schedule_almost_24h_with_daily_break_use_UTC",
    "rule": "never_store_naive_ET; convert_exchange_UTC_to_JST",
    "spring_forward_fall_back": "encoded_via_zoneinfo_America/New_York_on_US_cash_only",
}

ASIA_ZONES = {
    "Korea": "Asia/Seoul_no_DST",
    "HongKong": "Asia/Hong_Kong_no_DST",
    "China": "Asia/Shanghai_no_DST",
    "Singapore": "Asia/Singapore_no_DST",
}


def session_calendar_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "calendar_id": "CANONICAL",
            "timezone": CANONICAL_TZ,
            "japan_dst": False,
            "source": "J-Quants_/v2/markets/calendar",
            "covers": "TSE_business_days_OSE_holiday_trading_flag",
        },
        {
            "calendar_id": "TSE_CASH",
            "timezone": CANONICAL_TZ,
            "versioned_hours": True,
            "half_days": "not_regular_except_disaster_shorten",
            "source": "JPX_notices_plus_TSE_SESSION_RULES",
        },
        {
            "calendar_id": "OSE_FUTURES",
            "timezone": CANONICAL_TZ,
            "day_night": True,
            "holiday_trading_from": "2022-09",
            "source": "DataCube_Session_ID_plus_J-Quants_calendar_HolDiv",
        },
        {
            "calendar_id": "US_DST",
            "timezone": "America/New_York_for_cash_hours_labels_only",
            "storage": "UTC",
            "source": "IANA_zoneinfo",
        },
        {
            "calendar_id": "ASIA_MARKETS",
            "timezone": "local_no_DST_listed",
            "source": "IANA",
            "status": "labels_ready_data_gap",
        },
    ]
    for d in sorted(JPX_HOLIDAYS_2026):
        rows.append(
            {
                "calendar_id": "JPX_HOLIDAY_2026_PINNED",
                "date": d,
                "timezone": CANONICAL_TZ,
                "source": "e1_x29_JPX_HOLIDAYS_2026_local_pin",
                "note": "Other years must come from J-Quants calendar after subscribe. Do not invent.",
            }
        )
    return rows


def dst_plan() -> dict[str, Any]:
    return {
        "canonical": CANONICAL_TZ,
        "japan_dst": False,
        "store_external_in": "UTC",
        "join_in": "JST_availability_time",
        "us_dst": "zoneinfo_America/New_York_only_for_US_cash_session_labels",
        "cme": "use_exchange_UTC_not_ET_bars",
        "dukascopy": "export_UTC_reject_broker_DST_server_time",
        "korea_hk_china_sg": "no_DST",
        "forbidden": "naive_local_times_and_yahoo_unlabeled_bars",
        "plan_ready": True,
    }


assert CANONICAL_TZ == "Asia/Tokyo"
assert TSE_SESSION_RULES[-1]["from"] == "2024-11-05"
assert OSE_SESSION_CURRENT_ERA["do_not_backcast_current_hours_to_2012"] is True
assert dst_plan()["plan_ready"] is True
assert "20260921" in JPX_HOLIDAYS_2026
