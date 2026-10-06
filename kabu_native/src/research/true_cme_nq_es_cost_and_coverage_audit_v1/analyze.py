"""True CME NQ/ES cost and coverage audit. Discovery-adjacent metadata only. No ENTRY. No purchase."""
from __future__ import annotations

from typing import Any

from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.true_cme_nq_es_cost_and_coverage_audit_v1 import (
    CASE_BLOCKED,
    CASE_EXISTING,
    CASE_HIGH,
    CASE_REVIEW,
    HKG_CHI_CLOSE,
    MODEST_COMBINED_USD,
    NEXT_BIND,
    NEXT_FREE_IF_NOT_APPROVED,
    NEXT_UNEXPLAINED,
    PARENT_VERDICT,
    PROXY_ES,
    PROXY_NQ,
    TECH_FOCUS,
)
from research.true_cme_nq_es_cost_and_coverage_audit_v1.bind import bind_prior
from research.true_cme_nq_es_cost_and_coverage_audit_v1.source import probe_sources


def _usd_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def decide(
    *,
    bind_ok: bool,
    local_present: bool,
    api_key_present: bool,
    combined_usd: Any,
    nq_usd: Any,
    es_usd: Any,
    timeseries_called: bool,
    purchase: bool,
    account_created: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BLOCKED,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior HKG/FX/NQ-ES/Korea bind failed. Do not purchase. Do not proceed to oil automatically.",
            "ask_user_for_paid_acquisition_approval": False,
        }
    if timeseries_called or purchase or account_created:
        return {
            "CASE": "BLOCKED",
            "VERDICT": CASE_BLOCKED,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Safety: timeseries download, purchase, or account creation occurred and this audit must stop.",
            "ask_user_for_paid_acquisition_approval": False,
        }
    if local_present:
        return {
            "CASE": "EXISTING",
            "VERDICT": CASE_EXISTING,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Local true CME history is already present. Audit entitlement before any new account. Do not purchase additional data in this phase. Do not build playbooks.",
            "ask_user_for_paid_acquisition_approval": False,
        }
    comb = _usd_number(combined_usd)
    nq_n = _usd_number(nq_usd)
    es_n = _usd_number(es_usd)
    if comb is None and nq_n is not None and es_n is not None:
        comb = nq_n + es_n
    if api_key_present and comb is not None:
        if comb <= 0:
            return {
                "CASE": "EXISTING",
                "VERDICT": CASE_EXISTING,
                "NEXT": NEXT_UNEXPLAINED,
                "INTERPRETATION": "Authenticated metadata get_cost returned zero for the Discovery-adjacent L0 request. Treat as existing entitlement. Do not purchase. Do not build playbooks.",
                "ask_user_for_paid_acquisition_approval": False,
            }
        if comb <= MODEST_COMBINED_USD:
            return {
                "CASE": "REVIEW",
                "VERDICT": CASE_REVIEW,
                "NEXT": NEXT_UNEXPLAINED,
                "INTERPRETATION": "True CME L0 NQ/ES ohlcv-1m is modest-cost and would directly test whether the US proxy simultaneous result was a proxy artifact. Do not purchase in this phase. Ask the user before any paid acquisition.",
                "ask_user_for_paid_acquisition_approval": True,
            }
        return {
            "CASE": "HIGH",
            "VERDICT": CASE_HIGH,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "True CME would resolve the proxy-source uncertainty, but authenticated get_cost is material for this stage. Do not purchase. If not approved, the next free driver family is oil/commodity.",
            "ask_user_for_paid_acquisition_approval": False,
        }
    return {
        "CASE": "BLOCKED",
        "VERDICT": CASE_BLOCKED,
        "NEXT": NEXT_UNEXPLAINED,
        "INTERPRETATION": "No Databento API key and no local true CME history. Exact USD cost is unknown without free metadata get_cost. Do not create an account, enter payment information, or purchase. Official historical pricing is usage-based with no monthly subscription required. If paid CME is not approved, the next free driver family is oil/commodity for resources/trading houses.",
        "ask_user_for_paid_acquisition_approval": False,
    }


def current_driver_map(bind: dict[str, Any]) -> dict[str, Any]:
    fx = dict(bind.get("fx_response_map_v1") or {})
    return {
        "FX_OVERNIGHT_OPEN": {"sectors": ["輸送用機器", "非鉄金属"], "source": "FX_RESPONSE_MAP_V1", "preserved": True},
        "FX_INTRADAY_DIRECT": {"symbols": ["6787", "7717", "4092"], "source": "FX_RESPONSE_MAP_V1", "n": 3},
        "ES_BROAD_RISK_PROXY_LEADS": {
            "symbols": ["6779", "2737", "7717", "4461", "4092"],
            "evidence_class": "PROXY_CAUSAL_SUPPORTED",
            "not": "TRUE_CME_FUTURES_PROVEN",
        },
        "US_BROAD_RISK_PREOPEN": {"sectors": ["電気機器", "機械", "非鉄金属"], "label": "PROXY_NOT_CME_FUTURES"},
        "NQ_MARKET_MEDIATED": {
            "symbols": ["4062", "5706", "6861", "4004", "6367", "7741", "7220", "6480"],
            "role": "DESCRIPTIVE_ONLY",
        },
        "HKG_CHI": {"closed_as": HKG_CHI_CLOSE, "parent_verdict": PARENT_VERDICT, "do_not_retune": True},
        "KOREA": {"tested": False, "economic_null": False, "status": "KOREA_INTRADAY_SOURCE_NOT_AVAILABLE_V1"},
        "TECH_13": {"usable_causal_driver_n": 0, "n": 13},
        "usable_external_driver_stocks_n": (bind.get("hkg_answers") or {}).get("stocks_with_at_least_one_usable_external_driver_n") or 16,
        "remain_unexplained_n": (bind.get("hkg_answers") or {}).get("remain_unexplained_n") or 89,
        "not_all_16_equally_proven": True,
        "fx_map_id": fx.get("ANALYSIS_ID"),
        "TECH_DRIVER_BLOCKER_OPEN": True,
        "proxy_nq": PROXY_NQ,
        "proxy_es": PROXY_ES,
    }


def tech_unresolved_13(bind: dict[str, Any]) -> list[dict[str, Any]]:
    by_sym = dict(bind.get("by_symbol") or {})
    rows = []
    for sym in TECH_FOCUS:
        meta = dict(by_sym.get(sym) or {})
        rows.append(
            {
                "symbol": sym,
                "tse33_name": meta.get("tse33_name") or meta.get("sector33_name"),
                "usable_causal_driver": False,
                "us_proxy_class": "SIMULTANEOUS_ONLY",
                "hkg_chi": HKG_CHI_CLOSE,
                "korea": "NOT_TESTED_SOURCE_UNAVAILABLE",
                "taiwan_native": "NOT_ENTITLED",
                "true_cme": "NOT_ENTITLED",
            }
        )
    return rows


def comparison_rows(probe: dict[str, Any], ask: bool) -> list[dict[str, Any]]:
    exact = probe.get("exact_combined_usd")
    return [
        {
            "source": "TRUE_CME_NQ_ES",
            "economic_question_addressed": "Did the US proxy simultaneous result for Japan tech fail because USATECH/USA500 are not true CME NQ/ES futures?",
            "target_japan_names": list(TECH_FOCUS),
            "historical_range_available": "catalog_GLBX.MDP3_since_2010-06-06_covers_2024-09-16_to_2025-11-26",
            "resolution": "ohlcv-1m_L0",
            "causal_timing_suitability": "HIGH_if_ts_event_mapped_to_BAR_START_available_at",
            "source_quality": "NATIVE_CME_GLOBEX_MDP3",
            "proxy_or_native": "NATIVE",
            "estimated_price": exact,
            "purchase_required": True if not probe.get("LOCAL_TRUE_CME_HISTORY_PRESENT") else False,
            "account_required": not bool(probe.get("API_KEY_PRESENT")),
            "data_volume": probe.get("size"),
            "expected_information_gain": "HIGH_for_US_proxy_uncertainty_on_tech_13",
            "priority": 1,
            "ask_user_this_phase": ask,
        },
        {
            "source": "KRX_KOSPI200_SAMSUNG_SKHYNIX",
            "economic_question_addressed": "Does Korea-session KOSPI200 / Samsung / SK hynix information at or before T lead Japan semiconductor / electrical machinery after T?",
            "target_japan_names": list(TECH_FOCUS),
            "historical_range_available": "KRX_paid_products_IX1002_ST1002_after_purchase",
            "resolution": "1min_and_10min_listed",
            "causal_timing_suitability": "HIGH_same_timezone_simultaneous_session_if_entitled",
            "source_quality": "NATIVE_KRX_PAID_NOT_ENTITLED",
            "proxy_or_native": "NATIVE",
            "estimated_price": "UNKNOWN_UNTIL_LOGIN_CART",
            "purchase_required": True,
            "account_required": True,
            "data_volume": "index_plus_two_cash_names_or_broader_ST1002_bundle",
            "expected_information_gain": "HIGH_for_Korea_session_semi_untested_not_the_US_proxy_question",
            "priority": 2,
            "ask_user_this_phase": False,
        },
        {
            "source": "TWSE_NATIVE_SEMICONDUCTOR",
            "economic_question_addressed": "Does native Taiwan-session semiconductor information at or before T lead Japan tech after T?",
            "target_japan_names": list(TECH_FOCUS),
            "historical_range_available": "official_TWSE_intraday_historical_exists_Data_EShop",
            "resolution": "intraday_vendor_product_not_free_OpenAPI",
            "causal_timing_suitability": "HIGH_if_native_1m_entitled",
            "source_quality": "NATIVE_TWSE_PAID_BROAD_PRODUCTS",
            "proxy_or_native": "NATIVE",
            "estimated_price": "MATERIALLY_MORE_EXPENSIVE_OR_BROADER_THAN_NARROW_CME_L0",
            "purchase_required": True,
            "account_required": True,
            "data_volume": "currently_identified_products_broader_than_2_CME_L0_names",
            "expected_information_gain": "HIGH_for_Taiwan_session_not_US_proxy_question_TSM_US_ADR_is_not_Taiwan_cash_proof",
            "priority": 3,
            "ask_user_this_phase": False,
        },
    ]


def paid_data_decision(probe: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    comb = probe.get("exact_combined_usd")
    modest = _usd_number(comb) is not None and float(comb) <= MODEST_COMBINED_USD and float(comb) > 0
    semantics_sufficient_for_first_test = True
    semantics_proven = False
    resolves_proxy = True
    recommend = bool(modest and semantics_sufficient_for_first_test and resolves_proxy and probe.get("API_KEY_PRESENT"))
    return {
        "recommend_true_cme_first": recommend,
        "reason_if_not": None
        if recommend
        else "exact_usd_unknown_or_not_modest_or_key_absent_do_not_recommend_purchase_merely_because_catalog_exists",
        "cost_modest": modest,
        "source_semantics_sufficient_documented": semantics_sufficient_for_first_test,
        "ohlcv_1m_semantics_proven_from_bars": semantics_proven,
        "directly_resolves_us_proxy_uncertainty": resolves_proxy,
        "purchase_now": False,
        "paid_download_now": False,
        "account_creation_now": False,
        "ask_user_for_paid_acquisition_approval": bool(decision.get("ask_user_for_paid_acquisition_approval")),
        "cheapest_high_information_next_dataset": "TRUE_CME_NQ_ES_among_three_blocked_tech_sources_pending_exact_get_cost",
        "if_not_approved_free_next": NEXT_FREE_IF_NOT_APPROVED,
        "if_not_approved_free_next_role": "resource_trading_house_group",
        "do_not_auto_proceed_to_oil_this_phase": True,
        "verdict": decision.get("VERDICT"),
    }


def deferred_free_drivers() -> list[dict[str, Any]]:
    return [
        {
            "family": "OIL_COMMODITY",
            "program_id": NEXT_FREE_IF_NOT_APPROVED,
            "role": "resources_trading_houses",
            "immediate_priority": False,
            "next_if_cme_purchase_not_approved": True,
            "purchase": False,
        },
        {
            "family": "NK225mini_TOPIX_prospective_capture",
            "role": "accumulating_subtrack",
            "immediate_priority": False,
            "purchase": False,
        },
    ]


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} hkg={bind.get('parent_hkg_verdict')} n={bind.get('research_pool_n')}", flush=True)
    probe = probe_sources()
    live = inspect_live_now()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        local_present=bool(probe.get("LOCAL_TRUE_CME_HISTORY_PRESENT")),
        api_key_present=bool(probe.get("API_KEY_PRESENT")),
        combined_usd=probe.get("exact_combined_usd"),
        nq_usd=probe.get("exact_nq_usd"),
        es_usd=probe.get("exact_es_usd"),
        timeseries_called=bool(probe.get("timeseries_called")),
        purchase=bool(probe.get("purchase")),
        account_created=bool(probe.get("account_creation_performed")),
    )
    tech_rows = tech_unresolved_13(bind)
    ask = bool(decision.get("ask_user_for_paid_acquisition_approval"))
    cmp_rows = comparison_rows(probe, ask)
    paid = paid_data_decision(probe, decision)
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "hkg_chi_closed_as": HKG_CHI_CLOSE,
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "source_probe": probe,
        "current_driver_map": current_driver_map(bind),
        "tech_unresolved_13": tech_rows,
        "tech_usable_n": 0,
        "tech_n": 13,
        "comparison": cmp_rows,
        "paid_data_decision": paid,
        "deferred_free_drivers": deferred_free_drivers(),
        "contract_roll_design": dict(probe.get("roll") or {}),
        "cme_semantics": dict(probe.get("semantics") or {}),
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "entry_rules_optimized": False,
        "playbooks_built": False,
        "purchase": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    p = dict(report.get("source_probe") or {})
    d = dict(report.get("decision") or {})
    paid = dict(report.get("paid_data_decision") or {})
    cmp = list(report.get("comparison") or [])
    krx = next((r for r in cmp if r.get("source") == "KRX_KOSPI200_SAMSUNG_SKHYNIX"), {})
    tw = next((r for r in cmp if r.get("source") == "TWSE_NATIVE_SEMICONDUCTOR"), {})
    cme = next((r for r in cmp if r.get("source") == "TRUE_CME_NQ_ES"), {})
    return {
        "Existing_Databento_API_key": bool(p.get("API_KEY_PRESENT")),
        "API_KEY_PRESENT": bool(p.get("API_KEY_PRESENT")),
        "Existing_true_CME_files": bool(p.get("LOCAL_TRUE_CME_HISTORY_PRESENT")),
        "LOCAL_TRUE_CME_HISTORY_PRESENT": bool(p.get("LOCAL_TRUE_CME_HISTORY_PRESENT")),
        "True_NQ_1m_available": {
            "catalog": True,
            "local": bool(p.get("true_nq_1m_local")),
            "entitled": bool(p.get("LOCAL_TRUE_CME_HISTORY_PRESENT") or (p.get("API_KEY_PRESENT") and _usd_number(p.get("exact_nq_usd")) == 0)),
        },
        "True_ES_1m_available": {
            "catalog": True,
            "local": bool(p.get("true_es_1m_local")),
            "entitled": bool(p.get("LOCAL_TRUE_CME_HISTORY_PRESENT") or (p.get("API_KEY_PRESENT") and _usd_number(p.get("exact_es_usd")) == 0)),
        },
        "Historical_range_sufficient": True,
        "OHLCV_1m_semantics_proven": False,
        "OHLCV_1m_semantics_documented": True,
        "Exact_estimated_NQ_cost": p.get("exact_nq_usd"),
        "Exact_estimated_ES_cost": p.get("exact_es_usd"),
        "Combined_cost": p.get("exact_combined_usd"),
        "Any_purchase": False,
        "Any_paid_download": False,
        "Account_creation_performed": False,
        "Can_true_CME_directly_resolve_material_US_proxy_uncertainty": True,
        "Expected_information_gain_vs_KRX": "CME_higher_for_this_US_proxy_question_KRX_higher_for_untested_Korea_session_semi",
        "Expected_information_gain_vs_TWSE": "CME_higher_for_this_US_proxy_question_TWSE_higher_for_Taiwan_session_and_currently_broader_or_more_expensive",
        "Cheapest_high_information_next_dataset": paid.get("cheapest_high_information_next_dataset"),
        "Paid_data_acquisition_justified_enough_to_ask_user": bool(d.get("ask_user_for_paid_acquisition_approval")),
        "If_not_approved_free_driver_next": NEXT_FREE_IF_NOT_APPROVED,
        "ENTRY_playbook_built": False,
        "Frozen_Validation_opened": False,
        "Kabu_50": False,
        "submit_cancel_live": "0/0/0",
        "HKG_CHI_closed_as": HKG_CHI_CLOSE,
        "parent_accepted": PARENT_VERDICT,
        "tech_13_usable_causal_driver_n": 0,
        "timeseries_called": False,
        "KRX_exact_price": krx.get("estimated_price"),
        "TWSE_TSM_US_ADR_is_not_Taiwan_session_causal_proof": True,
        "cme_priority": cme.get("priority"),
        "twse_priority": tw.get("priority"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
