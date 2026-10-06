"""Korea semiconductor driver response. Discovery only. No ENTRY. No fabricated proxy. No purchase."""
from __future__ import annotations

from typing import Any

from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.korea_semiconductor_driver_response_v1 import (
    CASE_BIND,
    CASE_BLOCKED,
    CASE_BROAD,
    CASE_PROXY,
    CASE_SEMI,
    CASE_SIM,
    FWD_HORIZONS,
    NEXT_BIND,
    NEXT_EXECUTABLE_FREE_FAMILY,
    NEXT_UNEXPLAINED,
    PARENT_VERDICT,
    STATUS_BLOCKED,
    STATUS_NATIVE,
    STATUS_PROXY,
    TECH_FOCUS,
    TECH_MINIMUM_MISSING,
)
from research.korea_semiconductor_driver_response_v1.bind import bind_prior
from research.korea_semiconductor_driver_response_v1.coverage import (
    coverage_105,
    preserved_driver_map,
    quality_split,
    unexplained_groups,
)
from research.korea_semiconductor_driver_response_v1.source import documented_krx_session, probe_sources


def decide(
    *,
    bind_ok: bool,
    source_status: str,
    native_ok: bool,
    proxy_ok: bool,
    semantics_ok: bool,
    kospi_lead: bool,
    samsung_lead: bool,
    sk_lead: bool,
    samsung_after_kospi: bool,
    sk_after_kospi: bool,
    simultaneous_only: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior NQ/ES proxy verdict or FX_RESPONSE_MAP_V1 bind failed. Do not redesign USDJPY or NQ/ES.",
        }
    if source_status == STATUS_BLOCKED or (not native_ok and not proxy_ok) or (source_status in (STATUS_NATIVE, STATUS_PROXY) and not semantics_ok and not (native_ok or proxy_ok)):
        return {
            "CASE": "BLOCKED",
            "VERDICT": CASE_BLOCKED,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": (
                "No valid Korea 1-minute source is available without purchase. "
                "Jetta has no KOSPI200 / Samsung / SK hynix. KRX IX1002/ST1002 are paid. "
                "Do not fabricate a Korea proxy. Preserve FX and US-risk maps. Do not build playbooks."
            ),
        }
    if source_status == STATUS_PROXY and proxy_ok and semantics_ok:
        if samsung_after_kospi or sk_after_kospi or samsung_lead or sk_lead or kospi_lead:
            return {
                "CASE": "PROXY",
                "VERDICT": CASE_PROXY,
                "NEXT": NEXT_UNEXPLAINED,
                "INTERPRETATION": (
                    "A labeled non-KRX Korea proxy shows a response. This is not native KRX proof. "
                    "Preserve FX and US-risk maps. Do not build playbooks."
                ),
            }
        if simultaneous_only:
            return {
                "CASE": "SIM",
                "VERDICT": CASE_SIM,
                "NEXT": NEXT_UNEXPLAINED,
                "INTERPRETATION": "Korea (proxy) association is simultaneous/common-news only. Preserve FX and US-risk maps.",
            }
        return {
            "CASE": "PROXY",
            "VERDICT": CASE_PROXY,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Korea proxy history existed but no stable causal lead was isolated. Native KRX proof still required.",
        }
    if samsung_after_kospi or sk_after_kospi or ((samsung_lead or sk_lead) and not kospi_lead):
        return {
            "CASE": "SEMI",
            "VERDICT": CASE_SEMI,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Korean semiconductor leaders causally lead Japanese tech after broad Korea is removed. Preserve FX and US-risk maps. Do not build playbooks.",
        }
    if kospi_lead and not (samsung_after_kospi or sk_after_kospi):
        return {
            "CASE": "BROAD",
            "VERDICT": CASE_BROAD,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Only broad KOSPI200 response is supported. Preserve FX and US-risk maps. Do not build playbooks.",
        }
    if simultaneous_only:
        return {
            "CASE": "SIM",
            "VERDICT": CASE_SIM,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Korea and Japan semiconductor names move simultaneously. Not a usable intraday lead. Preserve FX and US-risk maps.",
        }
    return {
        "CASE": "BLOCKED",
        "VERDICT": CASE_BLOCKED,
        "NEXT": NEXT_UNEXPLAINED,
        "INTERPRETATION": "Korea minute testing could not be completed. Preserve FX and US-risk maps. Do not build playbooks.",
    }


def _not_tested_row(name: str, *, reason: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "tested": False,
        "reason": reason,
        "class": "NOT_TESTED_SOURCE_UNAVAILABLE",
        "STRICT_CAUSAL_LEAD": False,
        "SIMULTANEOUS_COMMON_NEWS": None,
        "KOREA_LEADS_JAPAN": None,
        "JAPAN_LEADS_KOREA": None,
        "NO_RELATION": None,
    }
    for h in FWD_HORIZONS:
        out[f"fwd_{h}m_spearman"] = None
        out[f"fwd_{h}m_beta"] = None
        out[f"fwd_{h}m_mean_bps"] = None
        out[f"fwd_{h}m_median_bps"] = None
        out[f"fwd_{h}m_direction_p"] = None
        out[f"d1_d4_{h}m"] = None
    return out


def _next_driver(probe: dict[str, Any], groups: dict[str, Any]) -> dict[str, Any]:
    jetta = dict(probe.get("jetta") or {})
    gap = dict(jetta.get("next_gap_catalog") or {})
    hkg = list(gap.get("hkg") or []) or [
        x for x in list(jetta.get("asia_idx") or []) if str(x.get("code") or "").startswith("HKG.")
    ]
    chi = list(gap.get("chi_a50") or []) or [
        x for x in list(jetta.get("asia_idx") or []) if str(x.get("code") or "").startswith("CHI.")
    ]
    taiwan_native = bool(gap.get("taiwan_cash_native_on_jetta"))
    tech_unex = list(groups.get("tech_still_unexplained_intraday") or [])
    return {
        "unresolved_economic_group_that_determines_NEXT": "JAPAN_SEMICONDUCTOR_ELECTRICAL_MACHINERY",
        "tech_focus_still_unexplained": tech_unex,
        "minimum_missing_for_that_group": TECH_MINIMUM_MISSING,
        "korea_native_1m_status": "PAID_KRX_IX1002_ST1002_NOT_PURCHASED",
        "taiwan_cash_native_on_jetta": taiwan_native,
        "tsm_us_adr_is_not_taiwan_cash_session": True,
        "true_cme_nq_es": "UNPROVEN_PAID_NOT_PURCHASED",
        "do_not_purchase": True,
        "do_not_fabricate_korea_proxy": True,
        "do_not_follow_fixed_checklist_blindly": True,
        "banks_insurers_unexplained_n": groups.get("banks_insurers_unexplained_n"),
        "resource_trading_unexplained_n": groups.get("resource_trading_unexplained_n"),
        "next_executable_free_family": NEXT_EXECUTABLE_FREE_FAMILY,
        "next_executable_free_why": (
            "Tech remains the unresolved high-priority group, but native Korea/Taiwan cash and true CME "
            "require purchase or are absent from Jetta. China-sensitive cyclicals also remain unexplained "
            "and HKG.IDX / CHI.IDX exist on the free Jetta catalog. HKG/CHI are not Korea proxies."
        ),
        "hkg_instruments": hkg,
        "chi_instruments": chi,
        "oil_on_jetta_n": len(list(gap.get("oil") or [])),
        "rates_on_jetta_n": len(list(gap.get("rates") or [])),
        "true_futures_subtrack": {
            "NK225mini_TOPIX": "PROSPECTIVE_TRUE_FUTURES_SUBTRACK",
            "true_CME_NQ_ES": "UNPROVEN_HISTORICAL_DRIVERS",
            "datacube_purchased": False,
            "databento_purchased": False,
            "krx_purchased": False,
        },
        "gap_driven": True,
        "family": "HKG_CHINA_A50",
        "why": "Korea 1m blocked without purchase; tech still unexplained; next testable free Asia-session family is HKG/CHI while Taiwan native and true CME remain the minimum missing for tech.",
    }


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} parent={bind.get('parent_nqes_verdict')} n={bind.get('research_pool_n')}", flush=True)
    probe = probe_sources()
    print(f"KOREA_SOURCE status={probe.get('status')} jetta_ok={dict(probe.get('jetta') or {}).get('ok')}", flush=True)
    sector_of: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        sector_of[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    preserved = preserved_driver_map(bind)
    cov_rows = coverage_105(bind)
    quality = quality_split(cov_rows)
    groups = unexplained_groups(cov_rows, sector_of)
    reason = "KOREA_INTRADAY_SOURCE_NOT_AVAILABLE"
    empty_kospi = [_not_tested_row(s, reason=reason) for s in list(bind.get("symbols") or [])]
    empty_tech = [_not_tested_row(s, reason=reason) for s in TECH_FOCUS]
    samsung_rows = [_not_tested_row(s, reason=reason) for s in list(bind.get("symbols") or [])]
    sk_rows = [_not_tested_row(s, reason=reason) for s in list(bind.get("symbols") or [])]
    leader = {
        "samsung_and_sk_tested_separately": True,
        "merged_into_one_score_initially": False,
        "agree": None,
        "one_leads_the_other": None,
        "incremental_japan_information": None,
        "KOREA_SEMI_COMMON_considered": False,
        "tested": False,
        "reason": reason,
    }
    reverse = {
        "tested": False,
        "reason": reason,
        "KOREA_LEADS_JAPAN": None,
        "JAPAN_LEADS_KOREA": None,
        "SIMULTANEOUS_GLOBAL_NEWS": None,
        "NO_RELATION": None,
        "same_minute_correlation_is_not_success": True,
    }
    sector_resp = [
        {
            "sector": sec,
            "tested": False,
            "reason": reason,
            "KOSPI200": None,
            "Samsung": None,
            "SK_hynix": None,
            "class": "NOT_TESTED_SOURCE_UNAVAILABLE",
        }
        for sec in sorted({v for v in sector_of.values() if v})
    ]
    tech_targets = []
    nq_cls = dict(bind.get("nqes_stock_classes") or {})
    cov_by = {r["symbol"]: r for r in cov_rows}
    for s in TECH_FOCUS:
        nq = dict(nq_cls.get(s) or {})
        cov = dict(cov_by.get(s) or {})
        tech_targets.append(
            {
                "symbol": s,
                "sector": sector_of.get(s) or nq.get("sector"),
                "korea_tested": False,
                "korea_class": "NOT_TESTED_SOURCE_UNAVAILABLE",
                "korea_explained": False,
                "us_proxy_class": nq.get("class") or cov.get("nqes_class"),
                "us_usable_before_stock_move": nq.get("usable_before_stock_move"),
                "preserved_EVIDENCE_CLASS": cov.get("EVIDENCE_CLASS"),
                "usable_external_driver": bool(cov.get("usable_external_driver")),
            }
        )
    explained_tech_n = sum(1 for r in tech_targets if r.get("korea_explained") or r.get("usable_external_driver"))
    nxt = _next_driver(probe, groups)
    native_ok = bool(probe.get("native_krx_available"))
    proxy_ok = bool(probe.get("proxy_available"))
    semantics = {
        "proven": False,
        "reason": reason,
        "intended_if_native_krx": {
            "original_source_timestamp": "KRX_native_product",
            "store_utc": True,
            "display_jst_kst": True,
            "jst_equals_kst": True,
            "bar_start": True,
            "available_at": "bar_end_T_plus_1m_for_BAR_START",
            "driver_available_at_le_japan_decision_time": True,
            "no_same_bar_close_leakage": True,
        },
        "did_not_forward_fill_closed_market_bars": True,
        "observations_built": False,
        "used_five_minute_grid": False,
    }
    session = documented_krx_session()
    live = inspect_live_now()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        source_status=str(probe.get("status") or ""),
        native_ok=native_ok,
        proxy_ok=proxy_ok,
        semantics_ok=bool(semantics.get("proven")),
        kospi_lead=False,
        samsung_lead=False,
        sk_lead=False,
        samsung_after_kospi=False,
        sk_after_kospi=False,
        simultaneous_only=False,
    )
    usable_n = int(quality.get("usable_n") or 0)
    remain_n = max(0, int(quality.get("pool_n") or 0) - usable_n)
    return {
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "parent_verdict_accepted": bool(bind.get("ok")) and str(bind.get("parent_nqes_verdict") or "") == PARENT_VERDICT,
        "nq_es_not_reinterpreted_as_true_cme": True,
        "fx_response_map_preserved": True,
        "us_risk_proxy_evidence_preserved": True,
        "architecture": "DRIVER → SECTOR → STOCK → LOCAL SETUP → ENTRY/EXIT",
        "source_probe": probe,
        "acquisition": {
            "downloaded": False,
            "reason": reason,
            "confirmation_fetched": False,
            "frozen_validation_fetched": False,
            "krx_purchased": False,
            "datacube_purchased": False,
            "databento_purchased": False,
        },
        "timestamp_semantics": semantics,
        "session_semantics": session,
        "preserved_driver_map": preserved,
        "kospi200_response": empty_kospi,
        "samsung_response": samsung_rows,
        "skhynix_response": sk_rows,
        "samsung_after_kospi": [_not_tested_row(s, reason=reason) for s in TECH_FOCUS],
        "skhynix_after_kospi": [_not_tested_row(s, reason=reason) for s in TECH_FOCUS],
        "leader_incremental": leader,
        "reverse_causality": reverse,
        "sector_response": sector_resp,
        "stock_response": empty_kospi,
        "japan_tech_targets": tech_targets,
        "us_risk_comparison": {
            "tested": False,
            "reason": reason,
            "korea_adds_after_us_broad_risk": None,
            "did_not_build_combined_strategy": True,
        },
        "fx_comparison": {
            "tested": False,
            "reason": reason,
            "korea_adds_after_usdjpy_where_relevant": None,
            "fx_groups_not_destroyed": True,
        },
        "d1_d4": [{"tested": False, "reason": reason, "did_not_rescue_with_alt_lag_or_subset": True}],
        "coverage_rows": cov_rows,
        "coverage_quality": quality,
        "unexplained": groups,
        "next_driver": nxt,
        "tech_empty": empty_tech,
        "time_of_day_windows_not_applied": ["09:00-10:00", "10:00-11:30", "12:30-13:30", "13:30-14:45"],
        "observations": {"n_clocks": 0, "used_five_minute_grid": False, "built": False},
        "coverage": {
            "pool_n": quality.get("pool_n"),
            "has_at_least_one_causally_usable_external_driver_n": usable_n,
            "remain_unexplained_n": remain_n,
            "tech_with_usable_causal_driver_n": explained_tech_n,
            "quality": quality,
            "not_all_16_equally_proven": True,
        },
        "fx_response_map_v1": dict(bind.get("fx_response_map_v1") or {}),
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
    jetta = dict(p.get("jetta") or {})
    krx = dict(p.get("krx") or {})
    sem = dict(report.get("timestamp_semantics") or {})
    ses = dict(report.get("session_semantics") or {})
    d = dict(report.get("decision") or {})
    nxt = dict(report.get("next_driver") or {})
    cov = dict(report.get("coverage") or {})
    q = dict(cov.get("quality") or report.get("coverage_quality") or {})
    tech = list(report.get("japan_tech_targets") or [])
    by = {r.get("symbol"): r for r in tech}
    live = dict(report.get("live_20260914") or {})
    kospi_found = bool(p.get("native_krx_available") and krx.get("intraday_1m_index_product") and not krx.get("purchase_required"))
    samsung_found = bool(jetta.get("samsung_present") or (p.get("native_krx_available") and not krx.get("purchase_required")))
    sk_found = bool(jetta.get("sk_hynix_present") or (p.get("native_krx_available") and not krx.get("purchase_required")))
    native_or_proxy = "none_available_without_purchase"
    if p.get("native_krx_available"):
        native_or_proxy = "NATIVE_KRX"
    elif p.get("proxy_available"):
        native_or_proxy = "PROXY_NOT_KRX_NATIVE"
    return {
        "KOSPI200_minute_source_found": kospi_found,
        "KOSPI200_native_or_proxy": native_or_proxy,
        "KOSPI200_free_or_paid": "paid_KRX_IX1002" if krx.get("intraday_1m_index_product") else "not_listed_free",
        "purchase": False,
        "Samsung_005930_minute_source_found": samsung_found,
        "SK_hynix_000660_minute_source_found": sk_found,
        "timestamp_semantics_proven": bool(sem.get("proven")),
        "session_semantics_proven": bool(ses.get("proven_from_bars")),
        "Korea_broad_market_leads_Japanese_tech": None,
        "Samsung_leads_Japanese_tech": None,
        "SK_hynix_leads_Japanese_tech": None,
        "Samsung_incremental_after_KOSPI200": None,
        "SK_hynix_incremental_after_KOSPI200": None,
        "strongest_Korea_semiconductor_driver": None,
        "STRICT_CAUSAL_LEAD": [],
        "SIMULTANEOUS_COMMON_NEWS_only": None,
        "explained_6857": bool((by.get("6857") or {}).get("korea_explained")),
        "explained_8035": bool((by.get("8035") or {}).get("korea_explained")),
        "explained_6920": bool((by.get("6920") or {}).get("korea_explained")),
        "explained_285A0": bool((by.get("285A0") or {}).get("korea_explained")),
        "tech_13_with_usable_causal_drivers_n": cov.get("tech_with_usable_causal_driver_n"),
        "Korea_adds_beyond_US_broad_risk": None,
        "Korea_adds_beyond_USDJPY_where_relevant": None,
        "stocks_with_at_least_one_usable_external_driver_n": cov.get("has_at_least_one_causally_usable_external_driver_n"),
        "remain_unexplained_n": cov.get("remain_unexplained_n"),
        "TRUE_DRIVER_PROVEN_n": len(q.get("TRUE_DRIVER_PROVEN_intraday_fx") or []),
        "PROXY_SUPPORTED_CAUSAL_n": len(q.get("PROXY_SUPPORTED_CAUSAL_es") or []),
        "OVERNIGHT_CAUSAL_n": len(q.get("OVERNIGHT_CAUSAL_fx") or []),
        "not_all_16_equally_proven": True,
        "unresolved_economic_group_that_determines_NEXT": nxt.get("unresolved_economic_group_that_determines_NEXT"),
        "next_executable_free_family": nxt.get("next_executable_free_family"),
        "next_driver_family": nxt.get("family"),
        "next_driver_why": nxt.get("why"),
        "minimum_missing_for_tech": nxt.get("minimum_missing_for_that_group"),
        "ENTRY_playbooks_built": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "Kabu_50": False,
        "submit_cancel_live": "0/0/0",
        "source_status": p.get("status"),
        "research_mode": p.get("research_mode"),
        "did_not_fabricate_korea_proxy": bool(p.get("did_not_fabricate_korea_proxy")),
        "krx_ix1002": krx.get("intraday_1m_index_product"),
        "krx_st1002": krx.get("intraday_1m_stock_product"),
        "krx_exact_price_krw": krx.get("exact_price_krw"),
        "jetta_n_instruments": jetta.get("n_instruments"),
        "jetta_kr_country_n": jetta.get("kr_country_n"),
        "live_20260914_note": live.get("note") or live.get("status"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
