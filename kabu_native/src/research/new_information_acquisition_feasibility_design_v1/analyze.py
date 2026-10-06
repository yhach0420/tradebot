"""Acquisition feasibility decision. No purchase. No strategy. No PnL."""
from __future__ import annotations

from typing import Any

from research.new_information_acquisition_feasibility_design_v1 import (
    CASE_C,
    LIVE_TECHNICAL_CALIBRATION_REQUIRED,
    MICROTIMING_RESEARCH_ALLOWED,
    MIN_REQUIRED_RESEARCH_DAYS,
    NETWORK_LATENCY_ALPHA_ALLOWED,
    NEXT_C,
    REQUIRED_POLICY_SHA256,
    REQUIRED_SELECTED_SOURCE,
    RESEARCH_METHOD_FROZEN,
)
from research.new_information_acquisition_feasibility_design_v1.access import (
    cost_access,
    historical_access,
    historical_live_pair,
    licensing,
    live_access_paths,
    storage_compute,
)
from research.new_information_acquisition_feasibility_design_v1.date_exposure import build_ledger
from research.new_information_acquisition_feasibility_design_v1.protocol import (
    book_reconstruction,
    event_order,
    historical_pcap,
    protocol_messages,
    reference_data,
    timestamp_semantics,
)
from research.new_information_acquisition_feasibility_design_v1.spec import freeze_sha


def source_boundary() -> dict[str, Any]:
    return {
        "SELECTED_SOURCE_FAMILY_ID": REQUIRED_SELECTED_SOURCE,
        "EQUALS": "TSE FLEX Market by Order (FLEX MBO / FLEX MBO BC realtime) plus FLEX MBO Historical",
        "ALLOWED_RAW": [
            "Order ID",
            "price",
            "quantity",
            "event type",
            "new/add",
            "modify",
            "cancel",
            "execute",
            "invalidation",
            "execution summary",
            "Match ID where documented",
            "native multicast event ordering",
        ],
        "FORBIDDEN_SUBSTITUTES": [
            "kabu Buy1..10 / Sell1..10",
            "FLEX Standard 10-level",
            "JPXI 10-level Order Book Historical",
            "CurrentPrice / TradingVolume deltas",
            "derived aggressor from L1",
            "OHLC bars",
            "vendor-derived order-flow score",
        ],
        "SOURCE_SEMANTIC_BOUNDARY_CHANGED": False,
    }


def feasibility_gates(*, parent: dict[str, Any], ev: dict[str, Any], proto: dict[str, Any], book: dict[str, Any], ledger: dict[str, Any], lic: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    f = {
        "F1_selected_policy_SHA_exact": str(parent.get("EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256") or "") == REQUIRED_POLICY_SHA256,
        "F2_historical_FLEX_MBO_obtainable": True,
        "F3_live_FLEX_MBO_obtainable": True,
        "F4_historical_live_native_semantics_compatible": True,
        "F5_Order_ID_preserved": True,
        "F6_execute_invalidation_semantics_preserved": True,
        "F7_Match_ID_sufficient_or_optional": True,
        "F8_event_ordering_replayable": bool(ev.get("EVENT_ORDER_REPLAYABLE")),
        "F9_deterministic_book_reconstruction_possible": book.get("DETERMINISTIC_BOOK_RECONSTRUCTION_POSSIBLE") is True,
        "F10_at_least_30_clean_research_days_can_be_sourced": bool(ledger.get("AT_LEAST_30_CLEAN_DAYS_STRUCTURALLY_POSSIBLE")),
        "F11_previously_exposed_dates_can_be_excluded": True,
        "F12_internal_analytical_use_legally_feasible": False,
        "F13_local_or_replay_storage_legal_or_confirmable": False,
        "F14_technical_access_without_changing_Paper_Runtime": True,
        "F15_current_hardware_storage_not_known_impossible": store.get("CURRENT_STORAGE_FEASIBILITY") == "NOT_KNOWN_IMPOSSIBLE",
        "F16_network_microtiming_uncertainty_isolated": (not NETWORK_LATENCY_ALPHA_ALLOWED) and (not MICROTIMING_RESEARCH_ALLOWED),
        "F17_no_strategy_outcome_needed_for_feasibility": True,
    }
    failed = [k for k, v in f.items() if v is False]
    return {
        **f,
        "FAILED": failed,
        "ALL_PASS": len(failed) == 0,
        "PROTOCOL_GAPS": [
            "MESSAGE_TYPE_LIST",
            "SEQUENCE_NUMBER_SEMANTICS",
            "MESSAGE_INTERNAL_TIMESTAMP",
            "RECOVERY_GAP_IN_HISTORICAL_PCAP",
            "DETERMINISTIC_BOOK_RECONSTRUCTION",
            "EVENT_ORDER_REPLAYABLE",
        ],
        "LEGAL_CONFIRMATION_REMAINING": bool(lic.get("PROVIDER_CONFIRMATION_REQUIRED")),
        "ORDER_ID_PROVEN": bool(proto.get("ORDER_ID_SEMANTICS_PROVEN")),
        "MATCH_ID_PROVEN": bool(proto.get("MATCH_ID_SEMANTICS_PROVEN")),
    }


def restart_gate() -> dict[str, Any]:
    return {
        "RG1_LEGACY_DEV_retired": True,
        "RG2_closed_architectures_remain_closed": True,
        "RG3_source_acquired_and_validated": False,
        "RG4_method_capacity_frozen_before_outcomes": False,
        "RG5_NEW_DEV_date_selection_rule_frozen": True,
        "RG6_NEW_DEV_unread": True,
        "RG7_future_use_policy_explicitly_permits": False,
        "RESTART_ALLOWED": False,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESEARCH_METHOD_FROZEN": False,
        "NOTE": "Feasibility result does not restart research. RG4/RG7 remain false. Source not acquired.",
    }


def decide(*, gates: dict[str, Any]) -> dict[str, Any]:
    # Protocol gaps (F8/F9 and exact message catalog) block CASE A.
    # Legal confirmation also remains, but CASE C is the binding documentation gap.
    return {
        "CASE": "C",
        "VERDICT": CASE_C,
        "NEXT": NEXT_C,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": False,
        "ALL_FEASIBILITY_GATES_PASS": False,
        "INTERPRETATION": (
            "FLEX MBO is a documented TSE/JPXI product with historical pcap and live paths, "
            "but exact message/event/replay semantics cannot yet be proven from publicly obtainable "
            "documentation because FLEX Market by Order Specifications require a JPX System Documents "
            "login. This does not say MBO has no research value. It says acquisition cannot proceed "
            "until the connection spec is resolved without purchasing market data. Do not contact "
            "the provider automatically. Do not switch to futures. Strategy research remains paused."
        ),
        "FAILED_GATES": list(gates.get("FAILED") or []),
    }


def build_report_body(*, parent: dict[str, Any]) -> dict[str, Any]:
    proto = protocol_messages()
    ev = event_order()
    ts = timestamp_semantics()
    hist_pcap = historical_pcap()
    hist = historical_access()
    live = live_access_paths()
    lic = licensing()
    cost = cost_access()
    store = storage_compute()
    book = book_reconstruction()
    refs = reference_data()
    ledger = build_ledger()
    pair = historical_live_pair(event_order_replayable=bool(ev.get("EVENT_ORDER_REPLAYABLE")))
    gates = feasibility_gates(parent=parent, ev=ev, proto=proto, book=book, ledger=ledger, lic=lic, store=store)
    decision = decide(gates=gates)
    freeze = freeze_sha(
        {
            "SELECTED_SOURCE_FAMILY_ID": REQUIRED_SELECTED_SOURCE,
            "CASE": decision["CASE"],
            "FAILED_GATES": decision["FAILED_GATES"],
            "EVENT_ORDER_REPLAYABLE": ev["EVENT_ORDER_REPLAYABLE"],
            "MESSAGE_SEMANTIC_EQUIVALENCE": ts["MESSAGE_SEMANTIC_EQUIVALENCE"],
            "TIMING_SEMANTIC_EQUIVALENCE": ts["TIMING_SEMANTIC_EQUIVALENCE"],
        }
    )
    decision["FEASIBILITY_SPEC_SHA256"] = freeze
    return {
        "parent": parent,
        "source_boundary": source_boundary(),
        "protocol_messages": proto,
        "event_order": ev,
        "historical_access": hist,
        "historical_pcap": hist_pcap,
        "live_access": live,
        "historical_live_pair": pair,
        "timestamp_semantics": ts,
        "date_exposure": {k: v for k, v in ledger.items() if k != "ROWS"},
        "date_exposure_rows": ledger.get("ROWS") or [],
        "clean_history": {
            "CLEAN_HISTORICAL_PERIOD_EXISTS": ledger["CLEAN_HISTORICAL_PERIOD_EXISTS"],
            "AT_LEAST_30_CLEAN_DAYS_STRUCTURALLY_POSSIBLE": ledger["AT_LEAST_30_CLEAN_DAYS_STRUCTURALLY_POSSIBLE"],
            "MIN_REQUIRED_RESEARCH_DAYS": int(MIN_REQUIRED_RESEARCH_DAYS),
            "NOTE": ledger["CLEAN_HISTORICAL_NOTE"],
            "DATES_ASSIGNED": False,
        },
        "reference_data": refs,
        "book_reconstruction": book,
        "storage_compute": store,
        "licensing": lic,
        "cost_access": cost,
        "provider_normalization": {
            "NORMALIZED_FEED_ACCEPTABLE": False,
            "L3_NOTE": live[2]["NOTE"],
        },
        "feasibility_gates": gates,
        "restart_gate": restart_gate(),
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    parent = dict(report.get("parent") or {})
    d = dict(report.get("decision") or {})
    proto = dict(report.get("protocol_messages") or {})
    ev = dict(report.get("event_order") or {})
    ts = dict(report.get("timestamp_semantics") or {})
    hist = dict(report.get("historical_access") or {})
    live = list(report.get("live_access") or [])
    pair = dict(report.get("historical_live_pair") or {})
    ledger = dict(report.get("date_exposure") or {})
    clean = dict(report.get("clean_history") or {})
    book = dict(report.get("book_reconstruction") or {})
    refs = list(report.get("reference_data") or [])
    lic = dict(report.get("licensing") or {})
    store = dict(report.get("storage_compute") or {})
    cost = dict(report.get("cost_access") or {})
    gates = dict(report.get("feasibility_gates") or {})
    l1 = live[0] if live else {}
    l2 = live[1] if len(live) > 1 else {}
    return {
        "1_parent_verdict_pinned": bool(parent.get("ok")),
        "2_selected_source_family": REQUIRED_SELECTED_SOURCE,
        "3_source_policy_SHA_exact": bool(gates.get("F1_selected_policy_SHA_exact")),
        "4_strategy_research_status": "PAUSED",
        "5_RESTART_ALLOWED": False,
        "6_message_semantics_historical_live_equivalent": True,
        "7_timing_semantics_historical_live_equivalent": "UNPROVEN",
        "8_timing_equivalence_proven": False,
        "9_exact_MBO_message_types_documented": False,
        "10_Order_ID_semantics_proven": True,
        "11_Match_ID_semantics_proven": True,
        "12_message_sequence_number_proven": False,
        "13_packet_sequence_semantics_proven": False,
        "14_message_internal_timestamp_proven": False,
        "15_recovery_gap_semantics_proven": False,
        "16_causal_event_order_replayable": False,
        "17_EVENT_ORDER_SOURCE": ev.get("EVENT_ORDER_SOURCE"),
        "18_historical_FLEX_MBO_available": True,
        "19_MBO_era_earliest_date": "20241105",
        "20_historical_PCAP_timestamp_source": ts.get("HISTORICAL_PCAP_TIMESTAMP_SOURCE"),
        "21_historical_delivery_unit": hist.get("MINIMUM_PURCHASE_UNIT"),
        "22_spot_backfill_possible": True,
        "23_direct_live_TSE_path_available": bool(l1.get("AVAILABLE")),
        "24_raw_vendor_path_available": bool(l2.get("AVAILABLE")),
        "25_normalized_vendor_path_acceptable": False,
        "26_historical_live_pair_feasible": bool(pair.get("HISTORICAL_LIVE_PAIR_FEASIBLE")),
        "27_project_exposed_date_N": ledger.get("EXPOSED_DATE_N"),
        "28_project_exposed_date_range": f"{ledger.get('EARLIEST_EXPOSED_DATE')}..{ledger.get('LATEST_EXPOSED_DATE')}",
        "29_exposed_date_reuse_with_new_MBO_allowed": False,
        "30_clean_historical_period_exists": clean.get("CLEAN_HISTORICAL_PERIOD_EXISTS"),
        "31_at_least_30_clean_days_structurally_possible": clean.get("AT_LEAST_30_CLEAN_DAYS_STRUCTURALLY_POSSIBLE"),
        "32_network_latency_alpha_allowed": False,
        "33_microtiming_research_allowed": False,
        "34_live_technical_calibration_required": True,
        "35_replay_minimum_reaction_latency": "UNASSIGNED",
        "36_deterministic_book_reconstruction_possible": book.get("DETERMINISTIC_BOOK_RECONSTRUCTION_POSSIBLE"),
        "37_required_reference_data_IDs": [r["REFERENCE_ID"] for r in refs if r.get("REQUIRED_FOR_DECODING") or r.get("REQUIRED_FOR_BOOK_RECONSTRUCTION")],
        "38_historical_internal_research_use_allowed": lic.get("HISTORICAL_INTERNAL_RESEARCH_ALLOWED"),
        "39_local_storage_allowed": lic.get("LOCAL_STORAGE_ALLOWED"),
        "40_replay_use_allowed": lic.get("REPLAY_USE_ALLOWED"),
        "41_provider_confirmation_required": True,
        "42_expected_daily_raw_size": store.get("EXPECTED_DAILY_RAW_SIZE"),
        "43_expected_30d_raw_size": store.get("EXPECTED_30D_RAW_SIZE"),
        "44_current_storage_feasibility": store.get("CURRENT_STORAGE_FEASIBILITY"),
        "45_access_classification": cost.get("ACCESS_CLASSIFICATION"),
        "46_historical_cost_known": True,
        "47_realtime_cost_known": False,
        "48_F1": gates.get("F1_selected_policy_SHA_exact"),
        "49_F2": gates.get("F2_historical_FLEX_MBO_obtainable"),
        "50_F3": gates.get("F3_live_FLEX_MBO_obtainable"),
        "51_F4": gates.get("F4_historical_live_native_semantics_compatible"),
        "52_F5": gates.get("F5_Order_ID_preserved"),
        "53_F6": gates.get("F6_execute_invalidation_semantics_preserved"),
        "54_F7": gates.get("F7_Match_ID_sufficient_or_optional"),
        "55_F8": gates.get("F8_event_ordering_replayable"),
        "56_F9": gates.get("F9_deterministic_book_reconstruction_possible"),
        "57_F10": gates.get("F10_at_least_30_clean_research_days_can_be_sourced"),
        "58_F11": gates.get("F11_previously_exposed_dates_can_be_excluded"),
        "59_F12": gates.get("F12_internal_analytical_use_legally_feasible"),
        "60_F13": gates.get("F13_local_or_replay_storage_legal_or_confirmable"),
        "61_F14": gates.get("F14_technical_access_without_changing_Paper_Runtime"),
        "62_F15": gates.get("F15_current_hardware_storage_not_known_impossible"),
        "63_F16": gates.get("F16_network_microtiming_uncertainty_isolated"),
        "64_F17": gates.get("F17_no_strategy_outcome_needed_for_feasibility"),
        "65_all_feasibility_gates_pass": False,
        "66_market_data_purchased": False,
        "67_historical_data_downloaded": False,
        "68_live_feed_started": False,
        "69_contract_signed": False,
        "70_account_created": False,
        "71_collector_implemented": False,
        "72_strategy_created": False,
        "73_ENTRY_created": False,
        "74_EXIT_created": False,
        "75_model_created": False,
        "76_NEW_DEV_dates_assigned": False,
        "77_TRUE_OOS_dates_assigned": False,
        "78_20260907_plus_read": False,
        "79_RESEARCH_METHOD_FROZEN": bool(RESEARCH_METHOD_FROZEN),
        "80_RESTART_ALLOWED": False,
        "81_Runtime_changed": False,
        "82_Capture_changed": False,
        "83_submit_cancel_live": "0/0/0",
        "84_TRUE_OOS": False,
        "85_CERTIFIED": False,
        "86_SIZING": False,
        "87_VERDICT": d.get("VERDICT"),
        "88_NEXT": d.get("NEXT"),
        "LIVE_TECHNICAL_CALIBRATION_REQUIRED": bool(LIVE_TECHNICAL_CALIBRATION_REQUIRED),
    }
