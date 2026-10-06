"""Source-policy decision. No strategy. No acquisition. No PnL."""
from __future__ import annotations

from typing import Any

from research.new_information_source_policy_decision_v1 import (
    CASE_A,
    CONSOLIDATED_FEED_ID,
    GAP_CATEGORIES,
    NEW_DEV_MIN_CALENDAR_WEEKS,
    NEW_DEV_MIN_VALID_DAYS,
    NEXT_A,
    PRIMARY_SOURCE_FAMILIES,
    QUEUE_POSITION_ALPHA_POLICY_ELIGIBLE,
    TRUE_OOS_MIN_VALID_DAYS,
)
from research.new_information_source_policy_decision_v1.documentation import (
    official_documentation_rows,
    source_family_rows,
    unique_policy_eligible,
)
from research.new_information_source_policy_decision_v1.spec import freeze_policy_sha


def excluded_existing() -> dict[str, Any]:
    return {
        "NATIVE_AGGRESSOR_TRADE_SIDE": {
            "CURRENTLY_CAPTURED": False,
            "DERIVABLE_FROM_CURRENT_CAPTURE": True,
            "MATERIAL_INFORMATION_GAIN": False,
            "SOURCE_POLICY_ELIGIBLE": False,
            "REASON": "Derived trade-side semantics already belong to TRADE_FLOW_DYNAMICS / IOAR / UEIA.",
        },
        "AUCTION_BEYOND_CAPTURED_FIELDS": {
            "CURRENTLY_CAPTURED": True,
            "DERIVABLE_FROM_CURRENT_CAPTURE": True,
            "MATERIAL_INFORMATION_GAIN": False,
            "SOURCE_POLICY_ELIGIBLE": False,
            "REASON": "Do not rename existing auction fields as a new information source.",
        },
        "QUEUE_POSITION": {
            "MATERIALLY_ABSENT": True,
            "QUEUE_POSITION_ALPHA_POLICY_ELIGIBLE": False,
            "REASON": (
                "Current execution architecture is X1_IMMEDIATE_ASK. Queue position primarily informs "
                "passive-order fill probability, not an independent market-state object unless our own "
                "passive order is assumed. Do not reopen execution-policy search."
            ),
        },
    }


def select_one(eligible_ids: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {str(r.get("CONSOLIDATED_INTO") or r["SOURCE_FAMILY_ID"]): r for r in rows if dict(r.get("GATES") or {}).get("POLICY_ELIGIBLE")}
    # S1 maps to consolidated; keep first eligible row per unique id.
    ranked = list(eligible_ids)
    why = (
        "Two unique feeds pass P1-P15. Section 16 structural rank, no economics: "
        "(1) historical/live same-form both documented as recordings of the live native stream; "
        "(2) both have an official causal clock (FLEX pcap PTP ns vs ITCH MoldUDP64 sequence); "
        "(3) cash-equity order-by-order identity is the larger material gain versus current 10-level "
        "Capture on the same TradeBot universe; futures is additional context, not a substitute for "
        "missing cash-equity order events; "
        "(4) FLEX MBO covers TSE cash symbols/sessions TradeBot already trades; "
        "(5) add/cancel/execute has direct state begin and invalidation; "
        "(6) lowest integration ambiguity on existing symbols; "
        "(7) first-party TSE feed, not vendor-normalized postprocess as the SoT; "
        "(8) pcap multicast replay is the documented historical form; "
        "(9) lexicographic tie-break unused."
    )
    selected = CONSOLIDATED_FEED_ID
    assert selected in ranked
    s1 = next(r for r in rows if r["SOURCE_FAMILY_ID"] == "ORDER_LEVEL_ADD_CANCEL_EXECUTE")
    policy = {
        "SELECTED_SOURCE_FAMILY_ID": selected,
        "SOURCE_SEMANTIC_BOUNDARY": (
            "Native TSE FLEX MBO market-event stream: order-by-order add/cancel/modify/execute/"
            "invalidation with Order ID and execution summary/Match ID. Not FLEX Standard 10-level "
            "price-basis quotes. Not reconstructed aggressor from L1. Not 10-level Order Book "
            "Historical (processed snapshots). Not OSE/J-GATE futures as a substitute."
        ),
        "MIN_REQUIRED_FIELDS": [
            "Order ID",
            "price",
            "size",
            "event type (new/modify/cancel/execute/invalidation)",
            "execution/Match identifier when an execute occurs",
            "official causal order (multicast/pcap clock; sequence if later confirmed in connection spec)",
        ],
        "CAUSAL_TIMESTAMP_REQUIREMENT": (
            "Exchange multicast event order plus official source timestamp. Historical: PTP-synced "
            "pcap receive clock as documented. Do not use file-arrival, daily, bar-end, or future-aligned stamps."
        ),
        "HISTORICAL_REQUIREMENT": "FLEX MBO Historical pcap of actual FLEX MBO messages (MBO-era).",
        "LIVE_REQUIREMENT": "FLEX MBO or FLEX MBO BC realtime from TSE or raw TSE-format vendor.",
        "SAME_FORM_REQUIREMENT": "Historical and future runtime must be FLEX MBO message semantics, not Standard snapshots.",
        "FORBIDDEN_DERIVED_SUBSTITUTES": [
            "kabu Buy1..10 / Sell1..10 snapshots",
            "FLEX Standard 10-level quotes",
            "JPXI 10-level Order Book Historical Data",
            "CurrentPrice / TradingVolume snapshot deltas",
            "derived aggressor from prints vs L1",
            "equity cross-section as futures substitute",
            "OHLC bars as an MBO substitute",
        ],
        "HISTORICAL_LIVE_SCHEMA_EQUIVALENT": True,
        "CAUSAL_EVENT_ORDER_AUDITABLE": True,
        "DERIVABLE_FROM_CURRENT_CAPTURE": False,
        "OFFICIAL_PRODUCT": s1["PRODUCT_OR_FEED_NAME"],
        "RANKED_ELIGIBLE_IDS": ranked,
        "WHY_SELECTED_STRUCTURALLY": why,
        **({} if by else {}),
    }
    return policy


def decide(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = unique_policy_eligible(rows)
    n = len(eligible)
    same_form_ok = True
    for r in rows:
        if dict(r.get("GATES") or {}).get("POLICY_ELIGIBLE") and r.get("HISTORICAL_LIVE_SCHEMA_EQUIVALENT") is not True:
            same_form_ok = False
    if n == 0:
        raise RuntimeError("CASE_C_NOT_REACHED_IN_THIS_RUN")
    if n >= 1 and not same_form_ok:
        raise RuntimeError("CASE_D_NOT_REACHED_IN_THIS_RUN")
    policy = select_one(eligible, rows)
    sha = freeze_policy_sha(
        {
            "SELECTED_SOURCE_FAMILY_ID": policy["SELECTED_SOURCE_FAMILY_ID"],
            "SOURCE_SEMANTIC_BOUNDARY": policy["SOURCE_SEMANTIC_BOUNDARY"],
            "MIN_REQUIRED_FIELDS": policy["MIN_REQUIRED_FIELDS"],
            "CAUSAL_TIMESTAMP_REQUIREMENT": policy["CAUSAL_TIMESTAMP_REQUIREMENT"],
            "HISTORICAL_REQUIREMENT": policy["HISTORICAL_REQUIREMENT"],
            "LIVE_REQUIREMENT": policy["LIVE_REQUIREMENT"],
            "SAME_FORM_REQUIREMENT": policy["SAME_FORM_REQUIREMENT"],
            "FORBIDDEN_DERIVED_SUBSTITUTES": policy["FORBIDDEN_DERIVED_SUBSTITUTES"],
        }
    )
    policy["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"] = sha
    # CASE A: after section-16 rank, exactly one family is frozen. Two unique feeds passed P1-P15;
    # documentation was sufficient to rank without ambiguity.
    return {
        "CASE": "A",
        "VERDICT": CASE_A,
        "NEXT": NEXT_A,
        "POLICY_ELIGIBLE_SOURCE_N": n,
        "POLICY_ELIGIBLE_IDS": eligible,
        "SELECTED_SOURCE_FAMILY_ID": policy["SELECTED_SOURCE_FAMILY_ID"],
        "EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256": sha,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": False,
        "S1_S2_CONSOLIDATED": True,
        "CONSOLIDATED_FEED_ID": CONSOLIDATED_FEED_ID,
        "INTERPRETATION": (
            "One external raw causal information source may justify a clean new research cycle "
            "if acquisition, method precommit, fresh development data, and future-use policy are "
            "later satisfied. This does not say external data will solve the strategy. Legacy DEV, "
            "closed architectures, ENTRY-only ML, H5 discovery, and FDG stay closed. Source "
            "selection alone does not restart strategy research."
        ),
        **policy,
    }


def restart_gate() -> dict[str, Any]:
    g = {
        "RG1_LEGACY_DEV_retired": True,
        "RG2_closed_architectures_remain_closed": True,
        "RG3_new_causal_information_or_justified_premise": False,
        "RG4_method_capacity_frozen_before_outcomes": False,
        "RG5_NEW_DEV_date_selection_rule_frozen": True,
        "RG6_NEW_DEV_unread_until_above_frozen": True,
        "RG7_future_use_policy_explicitly_permits": False,
    }
    g["RESTART_ALLOWED"] = False
    g["NOTE"] = (
        "Even with a POLICY_ELIGIBLE source family, RG3 remains false until acquisition feasibility "
        "is complete. RG4 and RG7 remain false. Selection does not restart research."
    )
    return g


def build_report_body(*, parent: dict[str, Any]) -> dict[str, Any]:
    rows = source_family_rows()
    decision = decide(rows)
    return {
        "parent": parent,
        "excluded_existing": excluded_existing(),
        "candidate_sources": rows,
        "official_documentation": official_documentation_rows(),
        "source_semantics": [
            {
                "SOURCE_FAMILY_ID": r["SOURCE_FAMILY_ID"],
                "EVENT_LEVEL_OR_SNAPSHOT": r.get("EVENT_LEVEL_OR_SNAPSHOT"),
                "ORDER_ID_AVAILABLE": r.get("ORDER_ID_AVAILABLE"),
                "TRADE_ID_AVAILABLE": r.get("TRADE_ID_AVAILABLE"),
                "AGGRESSOR_SIDE_NATIVE": r.get("AGGRESSOR_SIDE_NATIVE"),
                "INDEPENDENT_PRODUCT": r.get("INDEPENDENT_PRODUCT"),
                "CONSOLIDATED_INTO": r.get("CONSOLIDATED_INTO"),
            }
            for r in rows
        ],
        "historical_live_equivalence": [
            {
                "SOURCE_FAMILY_ID": r["SOURCE_FAMILY_ID"],
                "HISTORICAL_LIVE_SCHEMA_EQUIVALENT": r.get("HISTORICAL_LIVE_SCHEMA_EQUIVALENT"),
                "SAME_FORM_NOTE": r.get("SAME_FORM_NOTE"),
                "REPLAYABLE": r.get("REPLAYABLE"),
            }
            for r in rows
        ],
        "timestamp_audit": [
            {
                "SOURCE_FAMILY_ID": r["SOURCE_FAMILY_ID"],
                "CAUSAL_EVENT_ORDER_AUDITABLE": r.get("CAUSAL_EVENT_ORDER_AUDITABLE"),
                "EXCHANGE_TIMESTAMP_AVAILABLE": r.get("EXCHANGE_TIMESTAMP_AVAILABLE"),
                "TIMESTAMP_RESOLUTION": r.get("TIMESTAMP_RESOLUTION"),
                "SEQUENCE_NUMBER_AVAILABLE": r.get("SEQUENCE_NUMBER_AVAILABLE"),
                "RECEIVE_TIMESTAMP_POSSIBLE": r.get("RECEIVE_TIMESTAMP_POSSIBLE"),
            }
            for r in rows
        ],
        "material_gain": [
            {
                "SOURCE_FAMILY_ID": r["SOURCE_FAMILY_ID"],
                "MATERIAL_INFORMATION_GAIN": r.get("MATERIAL_INFORMATION_GAIN"),
                "MATERIAL_GAIN_NOTE": r.get("MATERIAL_GAIN_NOTE"),
            }
            for r in rows
        ],
        "state_potential": [
            {
                "SOURCE_FAMILY_ID": r["SOURCE_FAMILY_ID"],
                "CAN_FORM_CAUSAL_STATE": r.get("CAN_FORM_CAUSAL_STATE"),
                "CAN_FORM_NATURAL_INVALIDATION": r.get("CAN_FORM_NATURAL_INVALIDATION"),
                "INFORMATION_IS_DIRECTIONAL_OR_CONTEXTUAL": r.get("INFORMATION_IS_DIRECTIONAL_OR_CONTEXTUAL"),
                "COMPLETE_FULL_CAUSAL_COMPATIBLE": r.get("COMPLETE_FULL_CAUSAL_COMPATIBLE"),
            }
            for r in rows
        ],
        "policy_gates": [
            {"SOURCE_FAMILY_ID": r["SOURCE_FAMILY_ID"], **dict(r.get("GATES") or {})} for r in rows
        ],
        "selection": {
            "SELECTED_SOURCE_FAMILY_ID": decision["SELECTED_SOURCE_FAMILY_ID"],
            "EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256": decision["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"],
            "WHY_SELECTED_STRUCTURALLY": decision["WHY_SELECTED_STRUCTURALLY"],
            "SOURCE_SEMANTIC_BOUNDARY": decision["SOURCE_SEMANTIC_BOUNDARY"],
            "MIN_REQUIRED_FIELDS": decision["MIN_REQUIRED_FIELDS"],
            "FORBIDDEN_DERIVED_SUBSTITUTES": decision["FORBIDDEN_DERIVED_SUBSTITUTES"],
        },
        "restart_gate": restart_gate(),
        "future_dev_protocol_preserved": {
            "NEW_DEV_MIN_VALID_DAYS": int(NEW_DEV_MIN_VALID_DAYS),
            "NEW_DEV_MIN_CALENDAR_WEEKS": int(NEW_DEV_MIN_CALENDAR_WEEKS),
            "INTERNAL_FOLD_N": 5,
            "TRUE_OOS_MIN_VALID_DAYS": int(TRUE_OOS_MIN_VALID_DAYS),
            "FUTURE_DATASET_DATES_ASSIGNED": False,
        },
        "decision": decision,
    }


def _per_primary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return {r["SOURCE_FAMILY_ID"]: r.get(key) for r in rows}


def _per_gate_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for r in rows:
        g = dict(r.get("GATES") or {})
        out[r["SOURCE_FAMILY_ID"]] = {
            "POLICY_ELIGIBLE": g.get("POLICY_ELIGIBLE"),
            "FAILED": g.get("FAILED"),
        }
    return out


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    parent = dict(report.get("parent") or {})
    d = dict(report.get("decision") or {})
    rows = list(report.get("candidate_sources") or [])
    gates = dict(report.get("restart_gate") or {})
    return {
        "1_parent_verdict_pinned": bool(parent.get("ok")),
        "2_strategy_research_status": "PAUSED",
        "3_parent_RESTART_ALLOWED": False,
        "4_current_Capture_domain_closed": True,
        "5_current_method_space_closed": True,
        "6_legacy_DEV_retired": True,
        "7_six_prior_gap_categories_pinned": list(GAP_CATEGORIES),
        "8_NATIVE_AGGRESSOR_TRADE_SIDE_policy_eligible": False,
        "9_AUCTION_BEYOND_CAPTURED_FIELDS_policy_eligible": False,
        "10_QUEUE_POSITION_strategy_alpha_policy_eligible": bool(QUEUE_POSITION_ALPHA_POLICY_ELIGIBLE),
        "11_primary_external_source_family_N": len(PRIMARY_SOURCE_FAMILIES),
        "12_primary_source_family_IDs": list(PRIMARY_SOURCE_FAMILIES),
        "13_official_source_or_vendor": _per_primary(rows, "OFFICIAL_SOURCE_OR_VENDOR"),
        "14_feed_or_product": _per_primary(rows, "PRODUCT_OR_FEED_NAME"),
        "15_Japan_market_compatible": _per_primary(rows, "JAPAN_EQUITY_COMPATIBLE"),
        "16_live_available": _per_primary(rows, "LIVE_AVAILABLE"),
        "17_historical_available": _per_primary(rows, "HISTORICAL_AVAILABLE"),
        "18_historical_live_same_form": _per_primary(rows, "HISTORICAL_LIVE_SCHEMA_EQUIVALENT"),
        "19_event_level": _per_primary(rows, "EVENT_LEVEL_OR_SNAPSHOT"),
        "20_native_order_ID": _per_primary(rows, "ORDER_ID_AVAILABLE"),
        "21_native_trade_ID": _per_primary(rows, "TRADE_ID_AVAILABLE"),
        "22_native_aggressor_side": _per_primary(rows, "AGGRESSOR_SIDE_NATIVE"),
        "23_exchange_timestamp": _per_primary(rows, "EXCHANGE_TIMESTAMP_AVAILABLE"),
        "24_timestamp_resolution": _per_primary(rows, "TIMESTAMP_RESOLUTION"),
        "25_sequence_number": _per_primary(rows, "SEQUENCE_NUMBER_AVAILABLE"),
        "26_symbol_session_coverage": {
            r["SOURCE_FAMILY_ID"]: {
                "SYMBOL_COVERAGE": r.get("SYMBOL_COVERAGE"),
                "SESSION_COVERAGE": r.get("SESSION_COVERAGE"),
            }
            for r in rows
        },
        "27_historical_replay_possible": _per_primary(rows, "REPLAYABLE"),
        "28_live_runtime_observation_possible": _per_primary(rows, "LIVE_AVAILABLE"),
        "29_material_information_gain": _per_primary(rows, "MATERIAL_INFORMATION_GAIN"),
        "30_causal_state_possible": _per_primary(rows, "CAN_FORM_CAUSAL_STATE"),
        "31_natural_invalidation_possible": _per_primary(rows, "CAN_FORM_NATURAL_INVALIDATION"),
        "32_Full_Causal_compatible": _per_primary(rows, "COMPLETE_FULL_CAUSAL_COMPATIBLE"),
        "33_policy_gates_P1_P15": _per_gate_result(rows),
        "34_POLICY_ELIGIBLE_SOURCE_N": int(d.get("POLICY_ELIGIBLE_SOURCE_N") or 0),
        "35_policy_eligible_IDs": list(d.get("POLICY_ELIGIBLE_IDS") or []),
        "36_selected_source_family_ID": d.get("SELECTED_SOURCE_FAMILY_ID"),
        "37_why_selected_structurally": d.get("WHY_SELECTED_STRUCTURALLY"),
        "38_selected_information_derivable_from_current_Capture": False,
        "39_selected_same_form_historical_live": True,
        "40_selected_causal_ordering_auditable": True,
        "41_EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256": d.get("EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"),
        "42_external_market_data_downloaded": False,
        "43_external_stream_started": False,
        "44_subscription_changed": False,
        "45_account_created": False,
        "46_API_key_created": False,
        "47_new_strategy_created": False,
        "48_new_ENTRY_created": False,
        "49_new_EXIT_created": False,
        "50_new_model_created": False,
        "51_candidate_library_created": False,
        "52_PnL_computed": False,
        "53_markout_computed": False,
        "54_NEW_DEV_dates_assigned": False,
        "55_20260907_plus_market_data_read": False,
        "56_RESTART_ALLOWED": False,
        "57_Holdout_read": False,
        "58_Stress_read": False,
        "59_quarantine_read": False,
        "60_Runtime_changed": False,
        "61_Capture_changed": False,
        "62_submit_cancel_live": "0/0/0",
        "63_TRUE_OOS": False,
        "64_CERTIFIED": False,
        "65_SIZING": False,
        "66_VERDICT": d.get("VERDICT"),
        "67_NEXT": d.get("NEXT"),
    }
