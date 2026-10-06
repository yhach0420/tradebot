"""Public FLEX MBO protocol facts. Login-only connection spec is not used. UNKNOWN if not public."""
from __future__ import annotations

from typing import Any

DOC_URLS = {
    "TSE_FLEX_REALTIME": "https://www.jpx.co.jp/english/markets/paid-info-equities/realtime/index.html",
    "TSE_FLEX_HISTORICAL": "https://www.jpx.co.jp/english/markets/paid-info-equities/historical/01.html",
    "TSE_FLEX_HISTORICAL_SPEC": (
        "https://www.jpx.co.jp/english/markets/paid-info-equities/historical/"
        "b5b4pj0000046hrg-att/e_flexhistorical_spec.pdf"
    ),
    "FAQ_MBO": "https://faqsd.jpx.co.jp/faq/show/17350?category_id=489&site_domain=en_application",
    "FAQ_ORDER_MATCH_ID": "https://faqsd.jpx.co.jp/faq/show/16582",
    "FAQ_MODIFY_FLAG": "https://faqsd.jpx.co.jp/faq/show/19583",
    "FAQ_TCP_RETRANS": "https://faqsd.jpx.co.jp/faq/show/19593?category_id=489&site_domain=en_application",
    "FAQ_SPEC_LOCATION": "https://faqsd.jpx.co.jp/faq/show/5160?site_domain=default",
    "JPXI_TERMS": "https://www.jpx.co.jp/english/markets/paid-info-equities/reference/uorii50000002o2f-att/terms_e.pdf",
}

LOGIN_ONLY_SPEC = (
    "FLEX Market by Order Specifications and FLEX Connection Specifications are posted on "
    "JPX System Documents Site and require Target/arrowface or dedicated ID login. "
    "This run does not create an account and does not retrieve those files."
)


def protocol_messages() -> dict[str, Any]:
    return {
        "CONNECTION_SPEC_PUBLICLY_OBTAINABLE": False,
        "CONNECTION_SPEC_ACCESS": LOGIN_ONLY_SPEC,
        "MESSAGE_TYPE_LIST": None,
        "ORDER_ADD_MESSAGE": None,
        "ORDER_MODIFY_MESSAGE": "Public FAQ: modification flag=1 identifies a modification-triggered distribution; full modify message layout UNKNOWN",
        "ORDER_CANCEL_MESSAGE": None,
        "ORDER_EXECUTE_MESSAGE": "Product outline: executed/invalidation orders are disseminated; exact message type code UNKNOWN",
        "INVALIDATION_MESSAGE": "Product outline lists invalidation orders; exact message type code UNKNOWN",
        "EXECUTION_SUMMARY_MESSAGE": "Product outline lists execution summary; exact message type code UNKNOWN",
        "ORDER_ID_SEMANTICS": (
            "Proven at product/FAQ level: Order ID identifies each order, unique per issue, "
            "not participant-identifying. Binary width/encoding UNKNOWN without connection spec."
        ),
        "ORDER_ID_SEMANTICS_PROVEN": True,
        "MATCH_ID_SEMANTICS": (
            "Proven at FAQ level: Match ID links executions, unique per issue, not participant-identifying. "
            "Full field layout UNKNOWN."
        ),
        "MATCH_ID_SEMANTICS_PROVEN": True,
        "PRICE_SEMANTICS": "Product outline includes price; tick/scale encoding UNKNOWN",
        "QUANTITY_SEMANTICS": "Product outline includes quantity; unit/scale encoding UNKNOWN",
        "ORDER_CONDITION_SEMANTICS": "Product outline lists Order Condition; enumerated values UNKNOWN",
        "ISSUE_CODE_SEMANTICS": None,
        "TRADING_PHASE_MESSAGES": None,
        "SESSION_BOUNDARY_MESSAGES": None,
        "SEQUENCE_NUMBER_SEMANTICS": None,
        "PACKET_SEQUENCE_SEMANTICS": (
            "Historical pcap stores multicast packets per group with PTP-synced capture timestamps. "
            "Intra-packet message order and live UDP sequence field: UNKNOWN without connection spec."
        ),
        "PACKET_SEQUENCE_PROVEN": False,
        "RECOVERY_SEMANTICS": (
            "Public FAQ: live FLEX MBO supports TCP retransmission (50,000 messages/request), "
            "backup, and snapshot after UDP drop. Procedures are in login-only specs. "
            "Whether historical pcap includes recovery/snapshot traffic: UNKNOWN."
        ),
        "RECOVERY_GAP_PROVEN": False,
        "MESSAGE_INTERNAL_TIMESTAMP": None,
        "MESSAGE_TIMESTAMP_RESOLUTION": None,
        "MESSAGE_INTERNAL_TIMESTAMP_PROVEN": False,
        "MESSAGE_TYPES_EXACTLY_DOCUMENTED": False,
    }


def event_order() -> dict[str, Any]:
    return {
        "A_EXPLICIT_MESSAGE_SEQUENCE": None,
        "B_PACKET_SEQUENCE": "Historical pcap preserves packet capture order; live UDP packet sequence UNKNOWN",
        "C_OFFICIAL_MULTICAST_ORDER": "Live source of truth is TSE FLEX MBO multicast; exact intra-channel rules UNKNOWN",
        "D_SEQUENCE_PLUS_PCAP": "Partial: pcap packet order + PTP ns clock documented; message serial UNKNOWN",
        "EVENT_ORDER_SOURCE": "UNPROVEN_COMBINATION_C_AND_PCAP_PACKET_ORDER",
        "EVENT_ORDER_REPLAYABLE": False,
        "WHY_NOT_REPLAYABLE": (
            "Exact causal order cannot be reconstructed from public pages: message sequence number, "
            "same-packet ordering, multicast-channel interleave, retransmission/recovery inclusion, "
            "gap detection, duplicates, and session restart are specified in login-only FLEX MBO specs."
        ),
        "SAME_PACKET_RULE": None,
        "DIFFERENT_PACKET_RULE": "Historical: pcap timestamp order at JPX capture point (not proven equivalent to live user receive)",
        "MULTICAST_CHANNEL_ORDERING": None,
        "RETRANSMISSION_RECOVERY": "Live TCP retrans/snapshot exist; historical inclusion UNKNOWN",
        "SEQUENCE_GAPS": None,
        "DUPLICATE_PACKETS": None,
        "SESSION_RESTART": None,
    }


def timestamp_semantics() -> dict[str, Any]:
    return {
        "MESSAGE_SEMANTIC_EQUIVALENCE": True,
        "TIMING_SEMANTIC_EQUIVALENCE": "UNPROVEN",
        "TIMING_EQUIVALENCE_PROVEN": False,
        "HISTORICAL_PCAP_TIMESTAMP_SOURCE": (
            "PCAP Packet Header receive time: TAP after JPX co-location edge SW, FPGA clock, "
            "PTP-synced to arrowhead, nanoseconds (FLEX Historical connection spec)."
        ),
        "HISTORICAL_TIMESTAMP_TIMEZONE": None,
        "HISTORICAL_TIMESTAMP_RESOLUTION": "nanoseconds (capture clock)",
        "MESSAGE_INTERNAL_MATCHING_ENGINE_TIMESTAMP": None,
        "LIVE_END_USER_NETWORK_DELAY": None,
        "VENDOR_NORMALIZATION_DELAY": None,
        "HISTORICAL_VS_RUNTIME_CAPTURE_POINT_EQUIVALENCE": None,
        "NETWORK_LATENCY_ALPHA_ALLOWED": False,
        "MICROTIMING_RESEARCH_ALLOWED": False,
        "LIVE_TECHNICAL_CALIBRATION_REQUIRED": True,
        "REPLAY_MIN_REACTION_LATENCY": "UNASSIGNED",
    }


def historical_pcap() -> dict[str, Any]:
    return {
        "ACTUAL_FILE_FORMAT": "pcap (official: packet capture format .pcap from 2021-05-24); PCAPNG: UNKNOWN",
        "PACKET_TIMESTAMP_SOURCE": "JPX capture-server FPGA, PTP-synced to arrowhead",
        "TIMESTAMP_TIMEZONE": None,
        "TIMESTAMP_RESOLUTION": "nanoseconds",
        "CHANNEL_STRUCTURE": "pcap files generated per realtime multicast group",
        "MULTICAST_ADDRESS_REPRESENTATION": "Documented as multicast group numbers in Historical spec tables; IP/port values in login-only connection spec",
        "PACKET_PAYLOAD_PRESERVATION": "Official: actual FLEX multicast messages stored (not decoded to CSV)",
        "PACKET_LOSS_SEMANTICS": None,
        "RECOVERY_MESSAGES_INCLUDED": None,
        "REFERENCE_MASTER_DATA_INCLUDED": "Issue/base-price via FLEX Standard path for MBO users; whether inside MBO pcap: UNKNOWN",
        "SYMBOL_MAPPING_REQUIREMENT": None,
        "TRADING_DAY_PARTITIONING": "Daily files; Regular last 30 days; All-period entire history; Spot one month",
        "COMPRESSION_DELIVERY": "GZIP; Regular HTTPS API; All-period/Spot AWS S3 copy",
        "OFFICIAL_DAILY_SIZE_EXAMPLE": "Approx 2.5 Gbytes compressed FLEX MBO Historical pcap in a 60-million-order day",
        "MBO_ERA_EARLIEST_DATE": "20241105",
        "PRE_MBO_NOTE": "If FLEX MBO Historical is selected, FLEX Full Historical is provided for periods prior to 2024-11-01 (different protocol).",
    }


def book_reconstruction() -> dict[str, Any]:
    return {
        "DETERMINISTIC_BOOK_RECONSTRUCTION_POSSIBLE": None,
        "INITIAL_STATE": None,
        "NEW_ORDER": None,
        "MODIFY": "Modification flag documented; full apply rule UNKNOWN",
        "CANCEL": None,
        "EXECUTE": None,
        "INVALIDATION": None,
        "SEQUENCE_GAP": None,
        "RECOVERY": "Live snapshot/backup exist; historical bootstrap from session start UNKNOWN",
        "SESSION_RESET": None,
        "INSTRUMENT_HALT": None,
        "END_OF_SESSION": None,
        "WHY_UNKNOWN": "Exact MBO message catalog and recovery/snapshot payload are in login-only specifications.",
    }


def reference_data() -> list[dict[str, Any]]:
    rows = [
        ("ISSUE_CODE_MAPPING", True, True, False, False, "Needed to bind MBO messages to TradeBot symbols; encoding UNKNOWN"),
        ("TICK_SIZE_TABLE", False, True, True, False, "Required for book/execution sim; not an alpha source"),
        ("TRADING_UNIT", False, True, True, False, "Lot size for quantity interpretation"),
        ("SPECIAL_QUOTE_STATE", False, True, True, False, "May appear as Standard tags; MBO presence UNKNOWN"),
        ("TRADING_HALT", False, True, True, False, "Session/reg state; MBO message UNKNOWN"),
        ("SESSION_STATE", False, True, True, False, "AM/PM/itayose/close; MBO phase messages UNKNOWN"),
        ("CORPORATE_ACTION_MAPPING", False, False, True, False, "Execution simulation across days"),
        ("BASE_PRICE", True, False, False, False, "FLEX Standard provides base price to MBO users per TSE outline"),
        ("MARKET_SEGMENT", False, False, False, False, "Universe filter; not MBO-native"),
    ]
    out = []
    for rid, dec, book, exe, alpha, note in rows:
        out.append(
            {
                "REFERENCE_ID": rid,
                "REQUIRED_FOR_DECODING": dec,
                "REQUIRED_FOR_BOOK_RECONSTRUCTION": book,
                "REQUIRED_FOR_EXECUTION_SIMULATION": exe,
                "ALPHA_INFORMATION": alpha,
                "NOTE": note,
            }
        )
    return out
