"""Historical/live access, license, cost, storage. Public docs only. No purchase."""
from __future__ import annotations

from typing import Any


def live_access_paths() -> list[dict[str, Any]]:
    return [
        {
            "PATH_ID": "L1_DIRECT_TSE_RAW_FLEX_MBO",
            "AVAILABLE": True,
            "INDIVIDUAL_OR_SMALL_USER_ACCESS_POSSIBLE": None,
            "CONTRACT_REQUIRED": True,
            "DEDICATED_LINE_REQUIRED": True,
            "COLOCATION_REQUIRED": None,
            "INTERNET_DELIVERY_POSSIBLE": False,
            "RAW_ORDER_ID_PRESERVED": True,
            "MATCH_ID_PRESERVED": True,
            "EVENT_TYPE_PRESERVED": True,
            "SEQUENCE_PRESERVED": None,
            "SOURCE_TIMESTAMP_PRESERVED": None,
            "VENDOR_TIMESTAMP_ADDED": False,
            "NORMALIZATION_APPLIED": False,
            "MONTHLY_COST_KNOWN": False,
            "INITIAL_COST_KNOWN": False,
            "MINIMUM_TERM_KNOWN": False,
            "PUBLIC_PRICE_AVAILABLE": False,
            "LOCAL_STORAGE_ALLOWED_KNOWN": None,
            "NOTE": "TSE dedicated connection; Information Provision and License Agreement; line/system fees extra.",
        },
        {
            "PATH_ID": "L2_RAW_TSE_FORMAT_VENDOR",
            "AVAILABLE": True,
            "INDIVIDUAL_OR_SMALL_USER_ACCESS_POSSIBLE": None,
            "CONTRACT_REQUIRED": True,
            "DEDICATED_LINE_REQUIRED": False,
            "COLOCATION_REQUIRED": False,
            "INTERNET_DELIVERY_POSSIBLE": None,
            "RAW_ORDER_ID_PRESERVED": True,
            "MATCH_ID_PRESERVED": True,
            "EVENT_TYPE_PRESERVED": True,
            "SEQUENCE_PRESERVED": None,
            "SOURCE_TIMESTAMP_PRESERVED": None,
            "VENDOR_TIMESTAMP_ADDED": None,
            "NORMALIZATION_APPLIED": False,
            "MONTHLY_COST_KNOWN": False,
            "INITIAL_COST_KNOWN": False,
            "MINIMUM_TERM_KNOWN": False,
            "PUBLIC_PRICE_AVAILABLE": False,
            "LOCAL_STORAGE_ALLOWED_KNOWN": None,
            "NOTE": "TSE page: vendors may supply raw data in the same format as TSE raw data. Lossless sequence/timestamp still vendor-specific UNKNOWN.",
        },
        {
            "PATH_ID": "L3_VENDOR_NORMALIZED_MBO",
            "AVAILABLE": True,
            "INDIVIDUAL_OR_SMALL_USER_ACCESS_POSSIBLE": None,
            "CONTRACT_REQUIRED": True,
            "DEDICATED_LINE_REQUIRED": False,
            "COLOCATION_REQUIRED": False,
            "INTERNET_DELIVERY_POSSIBLE": None,
            "RAW_ORDER_ID_PRESERVED": None,
            "MATCH_ID_PRESERVED": None,
            "EVENT_TYPE_PRESERVED": None,
            "SEQUENCE_PRESERVED": None,
            "SOURCE_TIMESTAMP_PRESERVED": None,
            "VENDOR_TIMESTAMP_ADDED": True,
            "NORMALIZATION_APPLIED": True,
            "MONTHLY_COST_KNOWN": False,
            "INITIAL_COST_KNOWN": False,
            "MINIMUM_TERM_KNOWN": False,
            "PUBLIC_PRICE_AVAILABLE": False,
            "LOCAL_STORAGE_ALLOWED_KNOWN": None,
            "NOTE": "Acceptable only if lossless Order ID, event type, price, size, Match ID, causal order, needed timestamp. Not documented per vendor in this run.",
        },
    ]


def historical_access() -> dict[str, Any]:
    return {
        "HISTORICAL_FLEX_MBO_AVAILABLE": True,
        "MBO_ERA_EARLIEST_DATE": "20241105",
        "LATEST_AVAILABLE_DATE": "Official: Regular last 30 days rolling; All-period entire period; daily file update 24:00 JST. Exact latest calendar day: UNKNOWN without login.",
        "REGULAR_DELIVERY_WINDOW": "last 30 days",
        "SPOT_PURCHASE_AVAILABILITY": True,
        "ALL_PERIOD_AVAILABILITY": True,
        "MINIMUM_PURCHASE_UNIT": "Regular: contract one month or more; Spot: one month specified by user; All-period: two months or more",
        "PER_DAY_PER_MONTH_UNIT": "Spot priced per month of data; Regular/All-period monthly access",
        "PRICE_IF_PUBLIC": {
            "FLEX_MBO_Historical_Regular_single_entity_JPY_month_ex_tax": 165000,
            "FLEX_MBO_Historical_All_period_single_entity_JPY_month_ex_tax": 495000,
            "FLEX_MBO_Historical_Spot_per_month_JPY_ex_tax": 66000,
            "source": "JPX FLEX Historical public fee table / JPXI Terms Attachment 1",
        },
        "DELIVERY_MECHANISM": "Regular: HTTPS Web-API download URL; All-period/Spot: AWS S3 copy (user AWS account)",
        "DATA_SIZE_OFFICIAL": "Example: ~2.5 Gbytes compressed daily FLEX MBO Historical pcap at 60 million orders",
        "CONTRACT_LICENSE_REQUIREMENT": True,
        "LOCAL_ANALYTICAL_STORAGE_RIGHTS": None,
        "INTERNAL_RESEARCH_USE_RIGHTS": "Internal-use option exists; whether algorithm research is internal use requires JPXI determination if unclear (Terms Art. 12.3-4)",
        "REPLAY_RIGHTS": None,
        "BACKFILL_POSSIBLE": True,
    }


def licensing() -> dict[str, Any]:
    return {
        "HISTORICAL_INTERNAL_RESEARCH_ALLOWED": None,
        "HISTORICAL_INTERNAL_RESEARCH_NOTE": (
            "JPXI Terms define Internal use and Academic Use. FLEX Historical public page allows "
            "detailed analysis and backtesting as a service description. Whether TradeBot algorithm "
            "research is Internal use is not self-judged: Art. 12.3 requires inquiry if unclear."
        ),
        "LOCAL_STORAGE_ALLOWED": None,
        "LOCAL_STORAGE_NOTE": "Delivery is download/S3 copy to customer environment; retention/derived-state rules not fully explicit.",
        "DERIVED_DATA_STORAGE_ALLOWED": None,
        "LIVE_INTERNAL_USE_ALLOWED": None,
        "REPLAY_USE_ALLOWED": None,
        "PROVIDER_CONFIRMATION_REQUIRED": True,
        "DO_NOT_INTERPRET_SILENCE_AS_PERMISSION": True,
        "REDISTRIBUTION": "External distribution is a separate licensed use; vendor redistribution needs TSE permission.",
    }


def cost_access() -> dict[str, Any]:
    return {
        "ACCESS_CLASSIFICATION": "CONTRACTED_BUT_PRACTICAL_HISTORICAL__INSTITUTIONAL_FOR_DIRECT_LIVE",
        "HISTORICAL_COST_KNOWN": True,
        "REALTIME_COST_KNOWN": False,
        "LINE_CONNECTIVITY_COST": None,
        "VENDOR_COST": None,
        "HISTORICAL_PUBLIC_FEES_JPY_EX_TAX": {
            "MBO_regular_single": 165000,
            "MBO_all_period_single": 495000,
            "MBO_spot_per_month": 66000,
        },
        "LIVE_PUBLIC_FEE_EXAMPLE": (
            "TSE realtime fee page example: internal use Full Order Information JPY 150,000 or 200,000 "
            "in one illustrative row; not treated as a complete MBO quote. Direct line fees extra."
        ),
        "NORMALIZED_FEED_ACCEPTABLE": False,
        "NORMALIZED_FEED_RULE": "False unless a named vendor documents lossless Order ID, event type, price, size, Match ID, causal order, needed source timestamp.",
    }


def storage_compute() -> dict[str, Any]:
    return {
        "EXPECTED_DAILY_RAW_SIZE": "approx 2.5 GiB gzip-compressed pcap (official example, 60 million orders)",
        "EXPECTED_30D_RAW_SIZE": "approx 75 GiB compressed under that official example; actual UNKNOWN",
        "EXPECTED_PARSED_SIZE": None,
        "REPLAY_IO_REQUIREMENT": None,
        "CPU_REQUIREMENT_KNOWN": False,
        "RAM_REQUIREMENT_KNOWN": False,
        "CURRENT_STORAGE_FEASIBILITY": "NOT_KNOWN_IMPOSSIBLE",
        "NOTE": "Official size is an example, not a guaranteed bound. No fabricated hardware sizing.",
    }


def historical_live_pair(*, event_order_replayable: bool) -> dict[str, Any]:
    return {
        "HISTORICAL_FLEX_MBO_AVAILABLE": True,
        "LIVE_FLEX_MBO_PATH_AVAILABLE": True,
        "NATIVE_MESSAGE_SEMANTICS_PRESERVED": True,
        "EVENT_ORDERING_REPLAYABLE": bool(event_order_replayable),
        "ORDER_IDENTITY_PRESERVED": True,
        "LICENSING_PERMITS_INTENDED_INTERNAL_USE": None,
        "HISTORICAL_LIVE_PAIR_FEASIBLE": False,
        "WHY": (
            "Product-level historical/live pair exists, but event-order replay is not proven from "
            "public specs and internal-use/replay storage is not confirmed. Timing equivalence is UNPROVEN; "
            "sequence-based research still requires proven event order."
        ),
        "MICROTIMING_RESEARCH_ALLOWED": False,
    }
