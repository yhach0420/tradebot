"""Official first-party documentation facts. No market-data download. UNKNOWN if not established."""
from __future__ import annotations

from typing import Any

from research.new_information_source_policy_decision_v1 import CONSOLIDATED_FEED_ID

DOC_URLS = {
    "TSE_FLEX_REALTIME": "https://www.jpx.co.jp/english/markets/paid-info-equities/realtime/index.html",
    "TSE_FLEX_REALTIME_JA": "https://www.jpx.co.jp/markets/paid-info-equities/realtime/index.html",
    "TSE_FLEX_HISTORICAL": "https://www.jpx.co.jp/english/markets/paid-info-equities/historical/01.html",
    "TSE_FLEX_HISTORICAL_SPEC": (
        "https://www.jpx.co.jp/english/markets/paid-info-equities/historical/"
        "b5b4pj0000046hrg-att/e_flexhistorical_spec.pdf"
    ),
    "ARROWHEAD_SYSTEM": "https://www.jpx.co.jp/systems/equities-trading/01.html",
    "FAQ_MBO_VS_STANDARD": "https://faqsd.jpx.co.jp/faq/show/179?category_id=9&site_domain=en_application",
    "FAQ_ORDER_MATCH_ID": "https://faqsd.jpx.co.jp/faq/show/16582",
    "FAQ_MBO_MODIFY_FLAG": "https://faqsd.jpx.co.jp/faq/show/19583",
    "OSE_REALTIME": "https://www.jpx.co.jp/english/markets/paid-info-derivatives/realtime/index.html",
    "JGATE_ALTERNATIVE": "https://www.jpx.co.jp/english/markets/paid-info-alternative/j-gate/index.html",
    "JGATE_ITCH_BINARY_SPEC": "https://www.jpx.co.jp/english/markets/paid-info-alternative/j-gate/SpecBinaryE.pdf",
    "JGATE_ORDER_TRADE_SPEC": "https://www.jpx.co.jp/english/markets/paid-info-alternative/j-gate/SpecOrderE.pdf",
}

P_KEYS = (
    "P1_materially_new_raw_information",
    "P2_not_derivable_from_current_Capture",
    "P3_officially_documented",
    "P4_causal_event_ordering_auditable",
    "P5_Japan_market_scope_relevant",
    "P6_coverage_or_justified_global_context",
    "P7_historical_replay_possible",
    "P8_future_live_runtime_observation_possible",
    "P9_historical_live_semantic_form_compatible",
    "P10_can_form_causal_state",
    "P11_can_form_natural_invalidation",
    "P12_Complete_Full_Causal_compatible",
    "P13_not_execution_only_under_current_architecture",
    "P14_does_not_require_closed_strategy_revival",
    "P15_access_feasibility_not_all_UNKNOWN",
)


def _gates(**vals: bool) -> dict[str, Any]:
    g = {k: bool(vals[k]) for k in P_KEYS}
    failed = [k for k, v in g.items() if v is False]
    return {**g, "FAILED": failed, "POLICY_ELIGIBLE": len(failed) == 0}


def official_documentation_rows() -> list[dict[str, Any]]:
    return [
        {
            "DOC_ID": "TSE_FLEX_REALTIME",
            "URL": DOC_URLS["TSE_FLEX_REALTIME"],
            "ISSUER": "Tokyo Stock Exchange / JPX",
            "USED_FOR": "FLEX Standard vs FLEX MBO live contents; Order ID; executed/invalidation; license path",
        },
        {
            "DOC_ID": "TSE_FLEX_HISTORICAL",
            "URL": DOC_URLS["TSE_FLEX_HISTORICAL"],
            "ISSUER": "JPX Market Innovation & Research",
            "USED_FOR": "FLEX Historical stores realtime FLEX as pcap; FLEX MBO Historical product; receive timestamp",
        },
        {
            "DOC_ID": "TSE_FLEX_HISTORICAL_SPEC",
            "URL": DOC_URLS["TSE_FLEX_HISTORICAL_SPEC"],
            "ISSUER": "JPXI",
            "USED_FOR": "PCAP PTP-synced nanosecond capture clock; FLEX Full replaced by FLEX MBO 2024-11-05",
        },
        {
            "DOC_ID": "FAQ_MBO_VS_STANDARD",
            "URL": DOC_URLS["FAQ_MBO_VS_STANDARD"],
            "ISSUER": "JPX Service Desk FAQ",
            "USED_FOR": "MBO is order-basis all prices; Standard is 10-level price-basis",
        },
        {
            "DOC_ID": "FAQ_ORDER_MATCH_ID",
            "URL": DOC_URLS["FAQ_ORDER_MATCH_ID"],
            "ISSUER": "JPX Service Desk FAQ",
            "USED_FOR": "Order ID and Match ID on MBO; unique per issue; not participant-identifying",
        },
        {
            "DOC_ID": "OSE_REALTIME",
            "URL": DOC_URLS["OSE_REALTIME"],
            "ISSUER": "Osaka Exchange / JPX",
            "USED_FOR": "OSE/J-GATE live auction info, quotes, full-order multicast contract",
        },
        {
            "DOC_ID": "JGATE_ALTERNATIVE",
            "URL": DOC_URLS["JGATE_ALTERNATIVE"],
            "ISSUER": "JPXI",
            "USED_FOR": "ITCH Binary = recorded J-GATE 3.0 ITCH messages; Order/Trade Data; redistribution ban",
        },
        {
            "DOC_ID": "JGATE_ITCH_BINARY_SPEC",
            "URL": DOC_URLS["JGATE_ITCH_BINARY_SPEC"],
            "ISSUER": "JPXI",
            "USED_FOR": "MoldUDP64 sequence number; ITCH message blocks; historical is disseminated ITCH packets",
        },
    ]


def _flex_mbo_shared() -> dict[str, Any]:
    return {
        "OFFICIAL_SOURCE_OR_VENDOR": "Tokyo Stock Exchange; distribution/historical: JPX Market Innovation & Research",
        "PRODUCT_OR_FEED_NAME": "FLEX Market by Order (FLEX MBO / FLEX MBO BC live); FLEX MBO Historical",
        "MARKET_SCOPE": "TSE cash equities (ex straight bonds / TOKYO PRO-BOND); Fukuoka and Sapporo equities (ex bonds)",
        "JAPAN_EQUITY_COMPATIBLE": True,
        "LIVE_AVAILABLE": True,
        "HISTORICAL_AVAILABLE": True,
        "HISTORICAL_LIVE_SCHEMA_EQUIVALENT": True,
        "SAME_FORM_NOTE": (
            "JPXI stores TSE realtime FLEX and provides FLEX Historical as pcap of actual multicast "
            "messages. Since 2024-11-05 FLEX Full was replaced by FLEX MBO; FLEX MBO Historical is "
            "the matching historical product. Pre-2024-11-05 Full vs MBO is a protocol change, not "
            "the MBO-era same-form path."
        ),
        "EVENT_LEVEL_OR_SNAPSHOT": "EVENT_LEVEL",
        "FIELDS_AVAILABLE": [
            "price",
            "quantity",
            "Order ID",
            "Order Condition",
            "modification/cancel",
            "executed/invalidation orders",
            "execution summary",
            "modification flag (FAQ)",
            "Match ID (FAQ)",
        ],
        "ORDER_ID_AVAILABLE": True,
        "TRADE_ID_AVAILABLE": True,
        "TRADE_ID_NOTE": "Match ID documented on JPX FAQ as execution-link identifier, unique per issue.",
        "AGGRESSOR_SIDE_NATIVE": None,
        "PRICE": True,
        "SIZE": True,
        "EVENT_TYPE": True,
        "EXCHANGE_TIMESTAMP_AVAILABLE": None,
        "TIMESTAMP_RESOLUTION": (
            "Historical PCAP capture clock: nanoseconds, FPGA, PTP-synced to arrowhead. "
            "Message-internal matching-engine timestamp: UNKNOWN from public pages (connection spec "
            "is referenced, not fully published without contract)."
        ),
        "RECEIVE_TIMESTAMP_POSSIBLE": True,
        "SEQUENCE_NUMBER_AVAILABLE": None,
        "CAUSAL_EVENT_ORDER_AUDITABLE": True,
        "CAUSAL_CLOCK_NOTE": (
            "Official source event order is the TSE FLEX multicast stream. Historical pcap records "
            "packet receive time at JPX capture with PTP-synced nanosecond clock. Do not use daily "
            "or bar-end stamps. Message serial inside MBO: UNKNOWN from public pages."
        ),
        "SYMBOL_COVERAGE": "All TSE listed issues in scope plus FSE/SSE equities (ex bonds)",
        "SESSION_COVERAGE": "Continuous auction book events as disseminated; MBO pre-open issue/base-price row is '-' vs Standard",
        "PREOPEN_AVAILABLE": None,
        "CONTINUOUS_AVAILABLE": True,
        "HISTORICAL_DEPTH": "Full order-by-order book events (not 10-level snapshots)",
        "HISTORICAL_DATE_RANGE": (
            "FLEX Historical from 2011-01-11 generally; MBO-era from 2024-11-05. "
            "pcap+receive timestamp from 2021-05-24. Regular service last 30 days; all-period and spot exist."
        ),
        "BACKFILL_AVAILABLE": True,
        "REALTIME_RUNTIME_ACCESS_MODEL": (
            "Direct TSE dedicated line (message format) or market-information providers "
            "(raw TSE format or normalized). Information Provision and License Agreement required."
        ),
        "LICENSING_RESTRICTIONS_KNOWN": True,
        "REDISTRIBUTION_RESTRICTIONS_KNOWN": True,
        "LOCAL_STORAGE_ALLOWED_KNOWN": None,
        "REPLAYABLE": True,
        "DOCUMENTATION_CONFIDENCE": "HIGH_PRODUCT_AND_SAME_FORM; MEDIUM_MESSAGE_INTERNAL_CLOCK",
        "INDEPENDENT_PRODUCT": True,
        "CONSOLIDATED_INTO": CONSOLIDATED_FEED_ID,
    }


def source_family_rows() -> list[dict[str, Any]]:
    mbo = _flex_mbo_shared()
    s1 = {
        "SOURCE_FAMILY_ID": "ORDER_LEVEL_ADD_CANCEL_EXECUTE",
        "INDEPENDENT_PRODUCT": True,
        "CONSOLIDATED_INTO": CONSOLIDATED_FEED_ID,
        **mbo,
        "MATERIAL_INFORMATION_GAIN": True,
        "MATERIAL_GAIN_NOTE": (
            "Native add/cancel/modify/execute with Order ID is not reconstructable from kabu 10-level "
            "snapshots. FLEX Standard 10-level is the same information class as current Capture, not this family."
        ),
        "CAN_FORM_CAUSAL_STATE": True,
        "CAN_FORM_NATURAL_INVALIDATION": True,
        "INFORMATION_IS_DIRECTIONAL_OR_CONTEXTUAL": "BOTH",
        "COMPLETE_FULL_CAUSAL_COMPATIBLE": True,
        "NOT_EXECUTION_ONLY": True,
        "GATES": _gates(
            P1_materially_new_raw_information=True,
            P2_not_derivable_from_current_Capture=True,
            P3_officially_documented=True,
            P4_causal_event_ordering_auditable=True,
            P5_Japan_market_scope_relevant=True,
            P6_coverage_or_justified_global_context=True,
            P7_historical_replay_possible=True,
            P8_future_live_runtime_observation_possible=True,
            P9_historical_live_semantic_form_compatible=True,
            P10_can_form_causal_state=True,
            P11_can_form_natural_invalidation=True,
            P12_Complete_Full_Causal_compatible=True,
            P13_not_execution_only_under_current_architecture=True,
            P14_does_not_require_closed_strategy_revival=True,
            P15_access_feasibility_not_all_UNKNOWN=True,
        ),
    }
    s2 = {
        "SOURCE_FAMILY_ID": "EXCHANGE_NATIVE_EXECUTION_TAPE",
        **mbo,
        "INDEPENDENT_PRODUCT": False,
        "CONSOLIDATED_INTO": CONSOLIDATED_FEED_ID,
        "PRODUCT_OR_FEED_NAME": "Not a separate TSE product; executed/invalidation + execution summary are FLEX MBO messages",
        "MATERIAL_INFORMATION_GAIN": True,
        "MATERIAL_GAIN_NOTE": (
            "Native executions with Match ID are on the same FLEX MBO feed as order lifecycle. "
            "Counting S2 separately would double-count one indivisible native market-event feed."
        ),
        "CAN_FORM_CAUSAL_STATE": True,
        "CAN_FORM_NATURAL_INVALIDATION": True,
        "INFORMATION_IS_DIRECTIONAL_OR_CONTEXTUAL": "BOTH",
        "COMPLETE_FULL_CAUSAL_COMPATIBLE": True,
        "NOT_EXECUTION_ONLY": True,
        "GATES": {
            **_gates(
                P1_materially_new_raw_information=True,
                P2_not_derivable_from_current_Capture=True,
                P3_officially_documented=True,
                P4_causal_event_ordering_auditable=True,
                P5_Japan_market_scope_relevant=True,
                P6_coverage_or_justified_global_context=True,
                P7_historical_replay_possible=True,
                P8_future_live_runtime_observation_possible=True,
                P9_historical_live_semantic_form_compatible=True,
                P10_can_form_causal_state=True,
                P11_can_form_natural_invalidation=True,
                P12_Complete_Full_Causal_compatible=True,
                P13_not_execution_only_under_current_architecture=True,
                P14_does_not_require_closed_strategy_revival=True,
                P15_access_feasibility_not_all_UNKNOWN=True,
            ),
            "POLICY_ELIGIBLE": False,
            "FAILED": ["NOT_INDEPENDENT_FEED"],
            "INDEPENDENT_FAMILY": False,
        },
    }
    s3 = {
        "SOURCE_FAMILY_ID": "BROAD_MARKET_FUTURES_CONTEXT",
        "INDEPENDENT_PRODUCT": True,
        "CONSOLIDATED_INTO": None,
        "OFFICIAL_SOURCE_OR_VENDOR": "Osaka Exchange; historical/alternative: JPX Market Innovation & Research",
        "PRODUCT_OR_FEED_NAME": (
            "OSE/J-GATE realtime market data (auction trading information + optional ITCH full-order "
            "multicast); historical pair: J-GATE ITCH Binary Data"
        ),
        "MARKET_SCOPE": "OSE (and TOCOM) listed derivatives including index futures; not TSE cash equities",
        "JAPAN_EQUITY_COMPATIBLE": True,
        "JAPAN_EQUITY_COMPATIBLE_NOTE": "Compatible as independent JP market context for a cash-equity bot, not as a cash-equity order book.",
        "LIVE_AVAILABLE": True,
        "HISTORICAL_AVAILABLE": True,
        "HISTORICAL_LIVE_SCHEMA_EQUIVALENT": True,
        "SAME_FORM_NOTE": (
            "ITCH Binary Data is documented as binary of messages disseminated via J-GATE 3.0 ITCH. "
            "That pairs with live ITCH. Daily OSE PDF reports or OHLC cubes are NOT the same form. "
            "J-GATE 2.0 ITCH Historical CSV is a prior protocol (through 2021-09-17)."
        ),
        "EVENT_LEVEL_OR_SNAPSHOT": "EVENT_LEVEL",
        "FIELDS_AVAILABLE": [
            "J-GATE ITCH full order messages (live/historical binary)",
            "OSE current/open/high/low, volume, best/multiple quotes (broadcast/multicast)",
            "MoldUDP64 sequence number (ITCH binary spec)",
            "Order/Trade Data Bi/Si aggressor exists on a different JPXI alternative product, not proven identical to live ITCH",
        ],
        "ORDER_ID_AVAILABLE": True,
        "TRADE_ID_AVAILABLE": None,
        "AGGRESSOR_SIDE_NATIVE": None,
        "AGGRESSOR_NOTE": (
            "J-GATE Order/Trade Data documents Buyer/Seller Initiated trades. That product is not "
            "established as the same semantic form as live ITCH. ITCH native aggressor: UNKNOWN."
        ),
        "PRICE": True,
        "SIZE": True,
        "EVENT_TYPE": True,
        "EXCHANGE_TIMESTAMP_AVAILABLE": True,
        "TIMESTAMP_RESOLUTION": "ITCH tag T documented as UNIX seconds; finer ITCH timestamps UNKNOWN from public pages; MoldUDP64 sequence documented",
        "RECEIVE_TIMESTAMP_POSSIBLE": True,
        "SEQUENCE_NUMBER_AVAILABLE": True,
        "CAUSAL_EVENT_ORDER_AUDITABLE": True,
        "SYMBOL_COVERAGE": "OSE listed futures/options series (index futures in scope for global context)",
        "SESSION_COVERAGE": "OSE day/night sessions as listed; not TSE cash AM by itself",
        "PREOPEN_AVAILABLE": None,
        "CONTINUOUS_AVAILABLE": True,
        "HISTORICAL_DEPTH": "ITCH Binary from 2021-09-21; ITCH CSV 2016-07-19 to 2021-09-17 (different form)",
        "HISTORICAL_DATE_RANGE": "ITCH Binary 2021-09-21 onward (spot/regular AWS)",
        "BACKFILL_AVAILABLE": True,
        "REALTIME_RUNTIME_ACCESS_MODEL": (
            "OSE Information Provision Agreement; direct Market Information System / J-GATE multicast "
            "full-order line, or OSE information service provider. Redistribution requires OSE permission."
        ),
        "LICENSING_RESTRICTIONS_KNOWN": True,
        "REDISTRIBUTION_RESTRICTIONS_KNOWN": True,
        "LOCAL_STORAGE_ALLOWED_KNOWN": None,
        "REPLAYABLE": True,
        "DOCUMENTATION_CONFIDENCE": "HIGH_PRODUCT_AND_ITCH_SEQUENCE; MEDIUM_ITCH_AGGRESSOR_FIELD",
        "MATERIAL_INFORMATION_GAIN": True,
        "MATERIAL_GAIN_NOTE": "Independent futures/index event state, not reconstructable from current equity-universe cross-section.",
        "CAN_FORM_CAUSAL_STATE": True,
        "CAN_FORM_NATURAL_INVALIDATION": True,
        "INFORMATION_IS_DIRECTIONAL_OR_CONTEXTUAL": "CONTEXTUAL",
        "COMPLETE_FULL_CAUSAL_COMPATIBLE": True,
        "NOT_EXECUTION_ONLY": True,
        "GATES": _gates(
            P1_materially_new_raw_information=True,
            P2_not_derivable_from_current_Capture=True,
            P3_officially_documented=True,
            P4_causal_event_ordering_auditable=True,
            P5_Japan_market_scope_relevant=True,
            P6_coverage_or_justified_global_context=True,
            P7_historical_replay_possible=True,
            P8_future_live_runtime_observation_possible=True,
            P9_historical_live_semantic_form_compatible=True,
            P10_can_form_causal_state=True,
            P11_can_form_natural_invalidation=True,
            P12_Complete_Full_Causal_compatible=True,
            P13_not_execution_only_under_current_architecture=True,
            P14_does_not_require_closed_strategy_revival=True,
            P15_access_feasibility_not_all_UNKNOWN=True,
        ),
    }
    return [s1, s2, s3]


def unique_policy_eligible(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not dict(r.get("GATES") or {}).get("POLICY_ELIGIBLE"):
            continue
        fid = str(r.get("CONSOLIDATED_INTO") or r["SOURCE_FAMILY_ID"])
        if fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out
