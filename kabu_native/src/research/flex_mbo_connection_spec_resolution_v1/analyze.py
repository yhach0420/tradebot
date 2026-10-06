"""Connection-spec access gate. No protocol inference. No purchase. No strategy."""
from __future__ import annotations

from typing import Any

from research.flex_mbo_connection_spec_resolution_v1 import (
    CASE_C,
    MICROTIMING_RESEARCH_ALLOWED,
    NETWORK_LATENCY_ALPHA_ALLOWED,
    NEXT_C,
    PUBLIC_WEB_PROTOCOL_RESOLUTION_EXPECTED,
    REQUIRED_FEASIBILITY_SPEC_SHA256,
    REQUIRED_POLICY_SHA256,
    REQUIRED_SELECTED_SOURCE,
    RESEARCH_METHOD_FROZEN,
)
from research.flex_mbo_connection_spec_resolution_v1.search import search_local_specs
from research.flex_mbo_connection_spec_resolution_v1.spec import freeze_sha

NOT_EVALUATED = "NOT_EVALUATED_SPEC_UNAVAILABLE"


def access_boundary() -> dict[str, Any]:
    return {
        "REQUIRED_DOCUMENTS": [
            "FLEX Connection Specifications",
            "FLEX Market by Order Specifications",
        ],
        "OFFICIAL_LOCATION": "JPX System Documents Site / arrowhead/ToSTNeT/FLEX > Connection Specifications (FLEX) Related",
        "NORMAL_ACCESS": "Target JPX Site ID or arrowface ID",
        "LEGACY_ACCESS": "existing dedicated ID only",
        "PUBLIC_WEB_PROTOCOL_RESOLUTION_EXPECTED": bool(PUBLIC_WEB_PROTOCOL_RESOLUTION_EXPECTED),
        "PUBLIC_WEB_SEARCHED_THIS_RUN": False,
        "ACCOUNT_CREATION_ALLOWED_THIS_RUN": False,
        "PROVIDER_CONTACT_ALLOWED_THIS_RUN": False,
    }


def _unavailable_catalog() -> dict[str, Any]:
    keys = (
        "ADD_NEW_ORDER",
        "MODIFY",
        "CANCEL",
        "EXECUTE",
        "INVALIDATION",
        "EXECUTION_SUMMARY",
        "SNAPSHOT",
        "RECOVERY_RETRANSMISSION",
        "SESSION_PHASE",
        "REFERENCE_ISSUE",
    )
    fields = (
        "MESSAGE_TYPE_CODE",
        "FIELD_LAYOUT",
        "ORDER_ID_FIELD",
        "MATCH_ID_FIELD",
        "PRICE_FIELD",
        "QUANTITY_FIELD",
        "FLAGS",
        "ORDER_CONDITION",
        "ISSUE_IDENTIFIER",
        "TIMESTAMP_FIELD",
        "SEQUENCE_FIELD",
    )
    out: dict[str, Any] = {"EXACT_MESSAGE_CATALOG_RESOLVED": False}
    for k in keys:
        out[k] = {f: None for f in fields}
        out[k]["RESOLVED"] = False
    return out


def _unavailable_order() -> dict[str, Any]:
    return {
        "UDP_PACKET_SEQUENCE": None,
        "MESSAGE_SEQUENCE": None,
        "MESSAGES_WITHIN_ONE_PACKET": None,
        "MULTIPLE_MULTICAST_GROUPS": None,
        "SAME_TIMESTAMP_ORDERING": None,
        "RETRANSMITTED_MESSAGES": None,
        "SNAPSHOT_MESSAGES": None,
        "DUPLICATE_PACKETS_MESSAGES": None,
        "SEQUENCE_WRAP": None,
        "SEQUENCE_GAP": None,
        "SESSION_RESTART": None,
        "EVENT_ORDER_SOURCE": "NOT_RESOLVED_SPEC_UNAVAILABLE",
        "DETERMINISTIC_COMPARATOR": None,
        "EVENT_ORDER_REPLAYABLE": False,
        "PACKET_SEQUENCE_RESOLVED": False,
        "MESSAGE_SEQUENCE_RESOLVED": False,
        "SAME_PACKET_ORDER_RESOLVED": False,
        "CROSS_CHANNEL_ORDERING_RESOLVED": False,
        "DUPLICATE_SEMANTICS_RESOLVED": False,
        "GAP_DETECTION_RESOLVED": False,
        "RECOVERY_ORDERING_RESOLVED": False,
        "SESSION_RESTART_RESOLVED": False,
    }


def _unavailable_gap() -> dict[str, Any]:
    return {
        "UDP_DROP_DETECTION": None,
        "TCP_INDIVIDUAL_RETRANSMISSION": None,
        "BACKUP_REQUEST": None,
        "SNAPSHOT_REQUEST": None,
        "RECOVERY_LIMIT": None,
        "RECOVERY_SEQUENCING": None,
        "SNAPSHOT_SUPERSEDES_INCREMENTALS": None,
        "DUPLICATE_SUPPRESSION": None,
        "POST_RECOVERY_CONTINUATION": None,
        "HISTORICAL_REPLAY_DISTINGUISHES_RECOVERY_TRANSPORT": None,
        "SNAPSHOT_RECOVERY_RESOLVED": False,
        "NOTE": NOT_EVALUATED,
    }


def _unavailable_pcap() -> dict[str, Any]:
    return {
        "PRIMARY_UDP_MULTICAST_ONLY": None,
        "BACKUP_CHANNEL": None,
        "TCP_RECOVERY": None,
        "SNAPSHOT_TRAFFIC": None,
        "DUPLICATE_MULTICAST_PATHS": None,
        "REFERENCE_DATA_STREAM": None,
        "ALL_MULTICAST_GROUPS": None,
        "NOTE": NOT_EVALUATED,
    }


def _unavailable_bootstrap() -> dict[str, Any]:
    return {
        "SESSION_BOOTSTRAP_RESOLVED": False,
        "START_OF_DAY_EMPTY_BOOK": None,
        "INITIAL_SNAPSHOT_REQUIRED": None,
        "PRE_OPEN_ORDERS_DISTRIBUTED": None,
        "BASE_REFERENCE_STATE_REQUIRED": None,
        "WHEN_INCREMENTAL_PROCESSING_VALID": None,
        "AM_OPEN_RESET": None,
        "PM_RESET": None,
        "HALT_RESUME": None,
        "END_OF_SESSION_CLEAR": None,
        "NOTE": "Do not assume empty state. Spec unavailable.",
    }


def _unavailable_lifecycle() -> dict[str, Any]:
    return {
        "LIFECYCLE_APPLY_RULES_RESOLVED": False,
        "NEW": None,
        "MODIFY": None,
        "CANCEL": None,
        "PARTIAL_EXECUTION": None,
        "FULL_EXECUTION": None,
        "INVALIDATION": None,
        "MODIFY_SAME_ORDER_ID": None,
        "MODIFY_NEW_ORDER_ID": None,
        "PRIORITY_RESET_SEMANTICS": None,
        "QUANTITY_REPLACEMENT_OR_DELTA": None,
        "NOTE": NOT_EVALUATED,
    }


def _unavailable_book() -> dict[str, Any]:
    return {
        "DETERMINISTIC_BOOK_RECONSTRUCTION_POSSIBLE": False,
        "INITIAL_STATE": None,
        "MESSAGE_APPLY": None,
        "ORDER_KEY": None,
        "PRICE_LEVEL_STATE": None,
        "EXECUTION_APPLICATION": None,
        "CANCEL_APPLICATION": None,
        "MODIFY_APPLICATION": None,
        "GAP_STATE": None,
        "RECOVERY_STATE": None,
        "SESSION_RESET": None,
        "ALGORITHM_DEFINED": False,
        "NOTE": "No specification-level reconstruction algorithm: official specs not locally available.",
    }


def _unavailable_replay() -> dict[str, Any]:
    return {
        "REPLAY_CONTRACT_DEFINED": False,
        "RAW_PACKET": None,
        "DECODE": None,
        "CHANNEL_ORDER_SEQUENCING": None,
        "DEDUPLICATION": None,
        "GAP_HANDLING": None,
        "RECOVERY_HANDLING": None,
        "MESSAGE_APPLICATION": None,
        "CAUSAL_EVENT_STREAM": None,
        "EVENT_ORDER_REPLAYABLE": False,
        "FUTURE_SOT_IF_RESOLVED": "NATIVE_EVENT_ORDER",
        "NOTE": NOT_EVALUATED,
    }


def _timestamp_boundary() -> dict[str, Any]:
    return {
        "EXCHANGE_MESSAGE_TIMESTAMP": None,
        "PACKET_SEQUENCE": None,
        "JPX_HISTORICAL_PCAP_RECEIVE_TIMESTAMP": "Known from parent public material: JPX capture FPGA PTP ns. Not re-proven here.",
        "FUTURE_RUNTIME_RECEIVE_TIMESTAMP": None,
        "FIELDS_MERGED": False,
        "TIMING_SEMANTIC_EQUIVALENCE": "UNPROVEN",
        "TIMING_EQUIVALENCE_PROVEN": False,
        "NETWORK_LATENCY_ALPHA_ALLOWED": False,
        "MICROTIMING_RESEARCH_ALLOWED": False,
        "SOURCE_CAPTURE_TIMESTAMP_FIELD": "SOURCE_CAPTURE_TIMESTAMP",
        "LOCAL_RECEIVE_TIMESTAMP_FIELD": "LOCAL_RECEIVE_TIMESTAMP",
        "REQUIRE_EQUAL_VALUES": False,
    }


def _legal_boundary() -> dict[str, Any]:
    return {
        "SPEC_ACCESS_DOES_NOT_PROVE_F12_F13": True,
        "F12_INTERNAL_ANALYTICAL_USE_RESOLVED": False,
        "F13_STORAGE_REPLAY_RIGHTS_RESOLVED": False,
        "F12": False,
        "F13": False,
        "INHERITED_FROM_PARENT": True,
        "PROVIDER_CONFIRMATION_REQUIRED": True,
        "LICENSE_TEXT_SELF_INTERPRETED": False,
        "NOTE": "No authorized specs were opened. F12/F13 remain inherited unresolved.",
    }


def decide(*, eligible_n: int) -> dict[str, Any]:
    if int(eligible_n) == 0:
        return {
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_C,
            "STRATEGY_RESEARCH_STATUS": "PAUSED",
            "RESTART_ALLOWED": False,
            "CONNECTION_SPEC_AVAILABLE": False,
            "PROTOCOL_RESOLUTION_POSSIBLE": False,
            "INTERPRETATION": (
                "No authorized official FLEX Connection Specifications or FLEX Market by Order "
                "Specifications are already accessible locally. Protocol/replay gaps cannot be "
                "closed without those documents. This run does not create a JPX account, contact "
                "JPX, guess the binary protocol, switch to futures, or restart strategy research. "
                "NEXT is a USER-level decision about obtaining legitimate JPX System Documents access."
            ),
        }
    raise RuntimeError("ELIGIBLE_SPEC_N_NONZERO_READER_NOT_INVOKED")


def build_report_body(*, parent: dict[str, Any], search: dict[str, Any] | None = None) -> dict[str, Any]:
    found = search if search is not None else search_local_specs()
    eligible_n = int(found.get("ELIGIBLE_OFFICIAL_SPEC_N") or 0)
    decision = decide(eligible_n=eligible_n)
    catalog = _unavailable_catalog()
    ev = _unavailable_order()
    gap = _unavailable_gap()
    pcap = _unavailable_pcap()
    boot = _unavailable_bootstrap()
    life = _unavailable_lifecycle()
    book = _unavailable_book()
    replay = _unavailable_replay()
    ts = _timestamp_boundary()
    legal = _legal_boundary()
    f8 = False
    f9 = False
    freeze = freeze_sha(
        {
            "ANALYSIS_ID": "FLEX_MBO_CONNECTION_SPEC_RESOLUTION_V1",
            "CASE": decision["CASE"],
            "ELIGIBLE_OFFICIAL_SPEC_N": eligible_n,
            "CONNECTION_SPEC_AVAILABLE": False,
            "F8": f8,
            "F9": f9,
            "SELECTED_SOURCE_FAMILY_ID": REQUIRED_SELECTED_SOURCE,
        }
    )
    decision["RESOLUTION_SPEC_SHA256"] = freeze
    decision["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"] = REQUIRED_POLICY_SHA256
    decision["FEASIBILITY_SPEC_SHA256"] = REQUIRED_FEASIBILITY_SPEC_SHA256
    decision["F8"] = f8
    decision["F9"] = f9
    decision["F12"] = False
    decision["F13"] = False
    return {
        "parent": parent,
        "access_boundary": access_boundary(),
        "local_spec_search": found,
        "document_identity": {
            "LOCAL_CANDIDATE_DOC_N": found.get("LOCAL_CANDIDATE_DOC_N"),
            "ELIGIBLE_OFFICIAL_SPEC_N": eligible_n,
            "DOCUMENT_IDS": found.get("DOCUMENT_IDS") or [],
            "DOCUMENT_VERSIONS": found.get("DOCUMENT_VERSIONS") or [],
            "DOCUMENT_DATES": found.get("DOCUMENT_DATES") or [],
            "DOCUMENT_HASHES": found.get("DOCUMENT_HASHES") or [],
            "DOCS_OPENED": False,
            "DOCS_REDISTRIBUTED": False,
        },
        "message_catalog": catalog,
        "event_order": ev,
        "gap_recovery": gap,
        "historical_pcap": pcap,
        "session_bootstrap": boot,
        "order_lifecycle": life,
        "book_reconstruction": book,
        "replay_contract": replay,
        "timestamp_boundary": ts,
        "F8_F9": {
            "F8_event_ordering_replayable": f8,
            "F9_deterministic_book_reconstruction_possible": f9,
            "F8_ALL_SEQUENCE_RULES_RESOLVED": False,
            "F9_ALL_APPLY_RULES_RESOLVED": False,
            "WHY_F8_FALSE": "Official specs not locally available; sequencing rules not proven.",
            "WHY_F9_FALSE": "Official specs not locally available; bootstrap/lifecycle/recovery apply rules not proven.",
        },
        "legal_boundary": legal,
        "decision": decision,
        "RESEARCH_METHOD_FROZEN": bool(RESEARCH_METHOD_FROZEN),
        "NETWORK_LATENCY_ALPHA_ALLOWED": bool(NETWORK_LATENCY_ALPHA_ALLOWED),
        "MICROTIMING_RESEARCH_ALLOWED": bool(MICROTIMING_RESEARCH_ALLOWED),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    parent = dict(report.get("parent") or {})
    d = dict(report.get("decision") or {})
    ident = dict(report.get("document_identity") or {})
    cat = dict(report.get("message_catalog") or {})
    ev = dict(report.get("event_order") or {})
    gap = dict(report.get("gap_recovery") or {})
    boot = dict(report.get("session_bootstrap") or {})
    life = dict(report.get("order_lifecycle") or {})
    book = dict(report.get("book_reconstruction") or {})
    ts = dict(report.get("timestamp_boundary") or {})
    legal = dict(report.get("legal_boundary") or {})
    available = bool(d.get("CONNECTION_SPEC_AVAILABLE"))
    return {
        "1_parent_verdict_pinned": bool(parent.get("ok")),
        "2_strategy_status": "PAUSED",
        "3_RESTART_ALLOWED": False,
        "4_official_FLEX_specs_publicly_accessible": False,
        "5_authorized_official_spec_found_locally": ident.get("ELIGIBLE_OFFICIAL_SPEC_N", 0) > 0,
        "6_eligible_official_spec_N": ident.get("ELIGIBLE_OFFICIAL_SPEC_N"),
        "7_document_IDs": ident.get("DOCUMENT_IDS") or [],
        "8_versions": ident.get("DOCUMENT_VERSIONS") or [],
        "9_hashes": ident.get("DOCUMENT_HASHES") or [],
        "10_connection_spec_available": available,
        "11_protocol_resolution_possible": False,
        "12_account_created": False,
        "13_provider_contacted": False,
        "14_exact_message_catalog_resolved": bool(cat.get("EXACT_MESSAGE_CATALOG_RESOLVED")),
        "15_add_message_resolved": bool((cat.get("ADD_NEW_ORDER") or {}).get("RESOLVED")),
        "16_modify_resolved": bool((cat.get("MODIFY") or {}).get("RESOLVED")),
        "17_cancel_resolved": bool((cat.get("CANCEL") or {}).get("RESOLVED")),
        "18_execute_resolved": bool((cat.get("EXECUTE") or {}).get("RESOLVED")),
        "19_invalidation_resolved": bool((cat.get("INVALIDATION") or {}).get("RESOLVED")),
        "20_snapshot_recovery_resolved": bool(gap.get("SNAPSHOT_RECOVERY_RESOLVED")),
        "21_packet_sequence_resolved": bool(ev.get("PACKET_SEQUENCE_RESOLVED")),
        "22_message_sequence_resolved": bool(ev.get("MESSAGE_SEQUENCE_RESOLVED")),
        "23_same_packet_order_resolved": bool(ev.get("SAME_PACKET_ORDER_RESOLVED")),
        "24_cross_channel_ordering_resolved": bool(ev.get("CROSS_CHANNEL_ORDERING_RESOLVED")),
        "25_duplicate_semantics_resolved": bool(ev.get("DUPLICATE_SEMANTICS_RESOLVED")),
        "26_gap_detection_resolved": bool(ev.get("GAP_DETECTION_RESOLVED")),
        "27_recovery_ordering_resolved": bool(ev.get("RECOVERY_ORDERING_RESOLVED")),
        "28_session_restart_resolved": bool(ev.get("SESSION_RESTART_RESOLVED")),
        "29_EVENT_ORDER_SOURCE": ev.get("EVENT_ORDER_SOURCE"),
        "30_EVENT_ORDER_REPLAYABLE": bool(ev.get("EVENT_ORDER_REPLAYABLE")),
        "31_session_bootstrap_resolved": bool(boot.get("SESSION_BOOTSTRAP_RESOLVED")),
        "32_lifecycle_apply_rules_resolved": bool(life.get("LIFECYCLE_APPLY_RULES_RESOLVED")),
        "33_deterministic_reconstruction_possible": bool(book.get("DETERMINISTIC_BOOK_RECONSTRUCTION_POSSIBLE")),
        "34_F8": False,
        "35_F9": False,
        "36_timing_equivalence_proven": bool(ts.get("TIMING_EQUIVALENCE_PROVEN")),
        "37_network_latency_alpha_allowed": False,
        "38_microtiming_research_allowed": False,
        "39_F12_resolved": bool(legal.get("F12_INTERNAL_ANALYTICAL_USE_RESOLVED")),
        "40_F13_resolved": bool(legal.get("F13_STORAGE_REPLAY_RIGHTS_RESOLVED")),
        "41_provider_confirmation_still_required": bool(legal.get("PROVIDER_CONFIRMATION_REQUIRED")),
        "42_market_data_downloaded": False,
        "43_market_data_purchased": False,
        "44_collector_implemented": False,
        "45_replay_engine_implemented": False,
        "46_NEW_DEV_dates_assigned": False,
        "47_TRUE_OOS_dates_assigned": False,
        "48_20260907_plus_read": False,
        "49_research_method_frozen": False,
        "50_RESTART_ALLOWED": False,
        "51_Runtime_changed": False,
        "52_Capture_changed": False,
        "53_submit_cancel_live": "0/0/0",
        "54_TRUE_OOS": False,
        "55_CERTIFIED": False,
        "56_SIZING": False,
        "57_VERDICT": d.get("VERDICT"),
        "58_NEXT": d.get("NEXT"),
    }
