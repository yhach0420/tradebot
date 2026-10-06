"""HKG / China A50 causal response. Discovery only. No ENTRY. No fabricated Korea/Taiwan/CME substitute."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.hkg_china_a50_driver_response_v1 import (
    CASE_A50,
    CASE_BIND,
    CASE_BLOCKED,
    CASE_GREATER,
    CASE_HKG,
    CASE_SIM,
    CHINA_INTEREST_SECTORS,
    NEXT_BIND,
    NEXT_UNEXPLAINED,
    PARENT_VERDICT,
    STATUS_BLOCKED,
    STATUS_PROXY,
    TECH_FOCUS,
)
from research.hkg_china_a50_driver_response_v1.bind import bind_prior
from research.hkg_china_a50_driver_response_v1.coverage import coverage_105, preserved_driver_map, quality_split, tech_missing_ledger
from research.hkg_china_a50_driver_response_v1.panel import build_observations
from research.hkg_china_a50_driver_response_v1.response import (
    map_foreign_open,
    map_incremental_sector,
    map_incremental_stock,
    map_one_driver_sector,
)
from research.hkg_china_a50_driver_response_v1.semantics import prove_semantics
from research.hkg_china_a50_driver_response_v1.source import (
    acquire_discovery_history,
    load_es_proxy_frame,
    load_fx_frame,
    load_proxy_frames,
    probe_sources,
)
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now


def decide(
    *,
    bind_ok: bool,
    source_status: str,
    semantics_ok: bool,
    hkg_n: int,
    chi_n: int,
    greater_n: int,
    hkg_n_lead: int,
    chi_n_lead: int,
    sim_only: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior Korea/FX/NQ-ES bind failed."}
    if source_status != STATUS_PROXY or not semantics_ok or hkg_n <= 0 or chi_n <= 0:
        return {
            "CASE": "BLOCKED",
            "VERDICT": CASE_BLOCKED,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "HKG/CHI proxy history or timestamp/session semantics could not support valid research.",
        }
    if greater_n >= 2:
        return {
            "CASE": "GREATER",
            "VERDICT": CASE_GREATER,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "A labeled Greater-China proxy shows a stable causal response group. Not Korea/Taiwan/CME. Preserve FX and US-risk maps. Tech blocker remains open unless directly resolved. Do not build playbooks.",
        }
    if chi_n_lead >= 1 and hkg_n_lead == 0:
        return {
            "CASE": "A50",
            "VERDICT": CASE_A50,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "China A50 proxy shows a stable causal group after HKG is removed. Preserve FX and US-risk maps. Do not build playbooks.",
        }
    if hkg_n_lead >= 1 and chi_n_lead == 0:
        return {
            "CASE": "HKG",
            "VERDICT": CASE_HKG,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Hong Kong index proxy shows a stable causal group after A50 is removed. Preserve FX and US-risk maps. Do not build playbooks.",
        }
    if sim_only or (hkg_n_lead == 0 and chi_n_lead == 0 and greater_n == 0):
        return {
            "CASE": "SIM",
            "VERDICT": CASE_SIM,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "HKG/CHI associations are simultaneous/common-news or unstable. Preserve FX and US-risk maps. Tech blocker remains open. Do not build playbooks.",
        }
    return {
        "CASE": "GREATER",
        "VERDICT": CASE_GREATER,
        "NEXT": NEXT_UNEXPLAINED,
        "INTERPRETATION": "Both HKG and A50 show related Japan response. Preserve FX and US-risk maps. Do not build playbooks.",
    }


def _sector_meta(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def _compact_sum(rec: dict[str, Any] | None) -> dict[str, Any]:
    r = dict(rec or {})
    keys = ("n", "rho", "beta", "beta_per_10bps_fx", "mean_y", "median_y", "direction_prob_same_sign", "day_n", "d1_d4_agree", "stable", "rho_fx_up", "rho_fx_down", "mean_y_fx_up", "mean_y_fx_down", "asym_mean_diff")
    return {k: r.get(k) for k in keys}


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} korea={bind.get('parent_korea_verdict')} n={bind.get('research_pool_n')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("validation_dates") or []))
    probe = probe_sources()
    acq = {"ok_n": 0, "fail_n": 0, "confirmation_fetched": False, "frozen_validation_fetched": False}
    hkg = chi = None
    hkg_meta: dict[str, Any] = {}
    chi_meta: dict[str, Any] = {}
    semantics: dict[str, Any] = {"proven": False}
    hkg_sec: list[dict[str, Any]] = []
    chi_sec: list[dict[str, Any]] = []
    inc_sec: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []
    open_rows: list[dict[str, Any]] = []
    obs_meta: dict[str, Any] = {}
    fx_meta: dict[str, Any] = {}
    es_meta: dict[str, Any] = {}
    sector_of = _sector_meta(bind)
    if bind.get("ok") and probe.get("status") == STATUS_PROXY:
        acq = acquire_discovery_history()
        frames = load_proxy_frames()
        hkg, chi = frames.get("hkg"), frames.get("chi")
        hkg_meta, chi_meta = dict(frames.get("hkg_meta") or {}), dict(frames.get("chi_meta") or {})
        semantics = prove_semantics(hkg, chi, code_hkg=str(hkg_meta.get("code") or ""), code_chi=str(chi_meta.get("code") or ""))
        if semantics.get("proven") and hkg is not None and chi is not None and not hkg.empty and not chi.empty:
            symbols = list(bind.get("symbols") or [])
            print(f"LOAD_MINUTES n={len(symbols)} discovery={len(disc)}", flush=True)
            minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
            if minutes["date"].isin(list(conf | val)).any():
                raise RuntimeError("confirmation_or_validation_loaded")
            fx, fx_meta = load_fx_frame()
            es, es_meta = load_es_proxy_frame()
            date_to_block = dict(blocks.get("date_to_block") or {})
            obs = build_observations(
                minutes=minutes,
                hkg=hkg,
                chi=chi,
                fx=fx,
                es=es,
                sector_of=sector_of,
                date_to_block=date_to_block,
            )
            obs_meta = {k: obs.get(k) for k in ("n_clocks", "n_ok", "n_skip_no_live", "n_skip_0900_stale", "n_days", "resolution_min", "used_five_minute_grid", "asof_max_lag_ms", "did_not_test_0900_without_live_driver")}
            hkg_sec = map_one_driver_sector(obs.get("sector") or {}, xkey="hkg1", driver="hkg")
            chi_sec = map_one_driver_sector(obs.get("sector") or {}, xkey="chi1", driver="chi")
            inc_sec = map_incremental_sector(obs.get("sector") or {})
            stock_rows = map_incremental_stock(obs.get("stock") or {}, sector_of)
            open_rows = map_foreign_open(obs.get("foreign_open_sector") or {})

    hkg_leads = [r for r in stock_rows if r.get("class") == "HKG_DIRECT_LEAD" and r.get("usable_before_stock_move")]
    chi_leads = [r for r in stock_rows if r.get("class") == "CHINA_A50_DIRECT_LEAD" and r.get("usable_before_stock_move")]
    greater = [r for r in stock_rows if r.get("class") == "GREATER_CHINA_COMMON_LEAD" and r.get("usable_before_stock_move")]
    sim = [r for r in stock_rows if r.get("class") in ("SIMULTANEOUS_COMMON_NEWS", "UNUSABLE_NEAR_CONTEMPORANEOUS")]
    tech_rows = [r for r in stock_rows if r.get("tech_focus")]
    tech_explained = [r for r in tech_rows if r.get("usable_before_stock_move")]
    interest = []
    for sec in CHINA_INTEREST_SECTORS:
        rec = next((r for r in inc_sec if r.get("sector") == sec), {})
        interest.append(
            {
                "sector": sec,
                "hkg_leads": rec.get("hkg_leads"),
                "chi_leads": rec.get("chi_leads"),
                "hkg_rho": (rec.get("hkg_residual_1m") or {}).get("rho") if rec else None,
                "chi_rho": (rec.get("chi_residual_1m") or {}).get("rho") if rec else None,
                "hkg_incremental_after_chi": rec.get("hkg_incremental_after_chi"),
                "chi_incremental_after_hkg": rec.get("chi_incremental_after_hkg"),
            }
        )
    cov_rows = coverage_105(bind, stock_rows)
    quality = quality_split(cov_rows)
    usable_n = int(quality.get("usable_n") or 0)
    remain_n = max(0, int(quality.get("pool_n") or 0) - usable_n)
    unexplained_syms = [r["symbol"] for r in cov_rows if not r.get("usable_external_driver")]
    banks_n = sum(1 for s, sec in sector_of.items() if sec in ("銀行業", "保険業") and s in unexplained_syms)
    resource_n = sum(1 for s, sec in sector_of.items() if sec in ("鉱業", "石油･石炭製品", "卸売業", "海運業") and s in unexplained_syms)
    tech_still = [s for s in TECH_FOCUS if s in unexplained_syms or s in {r["symbol"] for r in tech_rows if not r.get("usable_before_stock_move")}]
    nxt = {
        "unresolved_economic_group_that_determines_NEXT": "JAPAN_SEMICONDUCTOR_ELECTRICAL_MACHINERY" if tech_still else ("BANKS_INSURERS" if banks_n else ("RESOURCES_TRADING" if resource_n else "RESIDUAL_IDIOSYNCRATIC")),
        "tech_still_highest_value_unresolved": bool(tech_still),
        "TECH_DRIVER_BLOCKER_OPEN": True,
        "korea_not_tested": True,
        "do_not_treat_hkg_chi_as_korea_taiwan_cme": True,
        "banks_insurers_unexplained_n": banks_n,
        "resource_trading_unexplained_n": resource_n,
        "next_executable_free_if_user_continues_without_purchase": "OIL_COMMODITY" if resource_n else ("RATES" if banks_n else None),
        "paid_review": {
            "A_free_data_response_groups_n": int(len(hkg_leads) >= 1) + int(len(chi_leads) >= 1) + int(len(greater) >= 1) + 2,
            "A_note": "FX overnight/intraday and ES proxy groups were already found on free Jetta data. This phase adds HKG/CHI proxy groups only if usable leads exist.",
            "B_tech_economic_importance": "The 13 unresolved semiconductor/electrical-machinery names remain the highest-value unexplained cluster.",
            "C_would_one_paid_dataset_resolve_high_value_group": True,
            "C_candidates": ["native_KRX_1m_IX1002_ST1002", "TWSE_native_intraday", "true_CME_NQ_ES"],
            "purchase": False,
            "do_not_purchase_now": True,
        },
        "family": "SELECT_NEXT_DRIVER_FOR_UNEXPLAINED_GROUPS_V1",
        "why": "Tech remains the highest-value unresolved group but is source-blocked (Korea paid, Taiwan free OpenAPI insufficient, true CME unentitled). HKG/CHI are not substitutes. Do not auto-walk every remaining indicator.",
        "gap_driven": True,
    }
    live = inspect_live_now()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        source_status=str(probe.get("status") or ""),
        semantics_ok=bool(semantics.get("proven")),
        hkg_n=int(hkg_meta.get("n_rows") or 0),
        chi_n=int(chi_meta.get("n_rows") or 0),
        greater_n=len(greater),
        hkg_n_lead=len(hkg_leads),
        chi_n_lead=len(chi_leads),
        sim_only=bool(len(hkg_leads) == 0 and len(chi_leads) == 0 and len(greater) == 0),
    )
    return {
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "parent_verdict_accepted": bool(bind.get("ok")) and str(bind.get("parent_korea_verdict") or "") == PARENT_VERDICT,
        "korea_not_tested": True,
        "korea_not_economic_null": True,
        "do_not_claim_korea_has_no_causal_lead": True,
        "TECH_DRIVER_BLOCKER_OPEN": True,
        "architecture": "DRIVER → SECTOR → STOCK → LOCAL SETUP → ENTRY/EXIT",
        "source_probe": probe,
        "acquisition": acq,
        "hkg_meta": hkg_meta,
        "chi_meta": chi_meta,
        "fx_meta": fx_meta,
        "es_meta": es_meta,
        "timestamp_semantics": semantics,
        "session_semantics": {
            "hkg": dict((semantics.get("session_hkg") or {})),
            "chi": dict((semantics.get("session_chi") or {})),
        },
        "observations": obs_meta,
        "hkg_sector": hkg_sec,
        "chi_sector": chi_sec,
        "incremental_sector": inc_sec,
        "stock_response": stock_rows,
        "foreign_open": open_rows,
        "interest_sectors": interest,
        "tech_name_response": [
            {
                "symbol": r.get("symbol"),
                "class": r.get("class"),
                "usable": r.get("usable_before_stock_move"),
                "hkg_lead_lag": r.get("hkg_lead_lag"),
                "chi_lead_lag": r.get("chi_lead_lag"),
            }
            for r in tech_rows
        ],
        "groups": {
            "HKG_DIRECT_LEAD": [r["symbol"] for r in hkg_leads],
            "CHINA_A50_DIRECT_LEAD": [r["symbol"] for r in chi_leads],
            "GREATER_CHINA_COMMON_LEAD": [r["symbol"] for r in greater],
            "SIMULTANEOUS_OR_NEAR": [r["symbol"] for r in sim][:40],
        },
        "coverage_rows": cov_rows,
        "coverage_quality": quality,
        "coverage": {
            "pool_n": quality.get("pool_n"),
            "has_at_least_one_causally_usable_external_driver_n": usable_n,
            "remain_unexplained_n": remain_n,
            "tech_explained_by_hkg_chi_n": len(tech_explained),
            "quality": quality,
        },
        "tech_missing_driver_data": tech_missing_ledger(),
        "unexplained": {
            "stock_n": remain_n,
            "tech_still": list(TECH_FOCUS) if tech_still else [],
            "banks_insurers_n": banks_n,
            "resource_trading_n": resource_n,
        },
        "next_driver": nxt,
        "preserved_driver_map": preserved_driver_map(bind),
        "fx_response_map_v1": dict(bind.get("fx_response_map_v1") or {}),
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "entry_rules_optimized": False,
        "playbooks_built": False,
        "purchase": False,
        "decision": decision,
        "stock_compact": [
            {
                "symbol": r.get("symbol"),
                "sector": r.get("sector"),
                "class": r.get("class"),
                "usable": r.get("usable_before_stock_move"),
                "hkg_sec": _compact_sum(r.get("hkg_sector_adj_1m") if isinstance(r.get("hkg_sector_adj_1m"), dict) else {}),
                "chi_sec": _compact_sum(r.get("chi_sector_adj_1m") if isinstance(r.get("chi_sector_adj_1m"), dict) else {}),
                "hkg_after_chi": _compact_sum(r.get("hkg_after_chi_1m") if isinstance(r.get("hkg_after_chi_1m"), dict) else {}),
                "chi_after_hkg": _compact_sum(r.get("chi_after_hkg_1m") if isinstance(r.get("chi_after_hkg_1m"), dict) else {}),
            }
            for r in stock_rows
        ],
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    p = dict(report.get("source_probe") or {})
    sem = dict(report.get("timestamp_semantics") or {})
    sess = dict(report.get("session_semantics") or {})
    sh = dict(sess.get("hkg") or {})
    sc = dict(sess.get("chi") or {})
    d = dict(report.get("decision") or {})
    nxt = dict(report.get("next_driver") or {})
    cov = dict(report.get("coverage") or {})
    q = dict(cov.get("quality") or report.get("coverage_quality") or {})
    g = dict(report.get("groups") or {})
    interest = {r.get("sector"): r for r in list(report.get("interest_sectors") or [])}
    tech = list(report.get("tech_name_response") or [])
    hkg_m = dict(report.get("hkg_meta") or {})
    chi_m = dict(report.get("chi_meta") or {})
    live = dict(report.get("live_20260914") or {})
    inc = list(report.get("incremental_sector") or [])
    mkt_surv = [r.get("sector") for r in inc if (r.get("hkg_leads") or r.get("chi_leads"))]
    return {
        "HKG_source_acquired": bool(int(hkg_m.get("n_rows") or 0) > 0),
        "CHI_A50_source_acquired": bool(int(chi_m.get("n_rows") or 0) > 0),
        "free": True,
        "purchase": False,
        "timestamp_semantics_proven": bool(sem.get("proven")),
        "actual_observable_session_from_bars": bool(sh.get("proven") and sc.get("proven")),
        "Japan_times_that_may_use_HKG": {
            "first": sh.get("first_valid_observable_jst"),
            "break": sh.get("session_break_jst"),
            "final_in_japan_session": sh.get("final_usable_japan_session_jst"),
            "japan_0900_ok": sh.get("japan_0900_driver_available_before_open"),
        },
        "Japan_times_that_may_use_CHI": {
            "first": sc.get("first_valid_observable_jst"),
            "break": sc.get("session_break_jst"),
            "final_in_japan_session": sc.get("final_usable_japan_session_jst"),
            "japan_0900_ok": sc.get("japan_0900_driver_available_before_open"),
        },
        "HKG_leads_any_japan_sector": any(bool(r.get("usable_before_stock_move") and str(r.get("lead_lag") or "").startswith("hkg_leads")) for r in list(report.get("hkg_sector") or [])),
        "CHI_leads_any_japan_sector": any(bool(r.get("usable_before_stock_move") and str(r.get("lead_lag") or "").startswith("chi_leads")) for r in list(report.get("chi_sector") or [])),
        "survives_japan_market_removal_sectors": mkt_surv,
        "STRICT_causal_stock_leads_n": len(g.get("HKG_DIRECT_LEAD") or []) + len(g.get("CHINA_A50_DIRECT_LEAD") or []) + len(g.get("GREATER_CHINA_COMMON_LEAD") or []),
        "STRICT_causal_symbols": {
            "HKG_DIRECT_LEAD": g.get("HKG_DIRECT_LEAD"),
            "CHINA_A50_DIRECT_LEAD": g.get("CHINA_A50_DIRECT_LEAD"),
            "GREATER_CHINA_COMMON_LEAD": g.get("GREATER_CHINA_COMMON_LEAD"),
        },
        "Greater_China_common_group": g.get("GREATER_CHINA_COMMON_LEAD"),
        "HKG_specific_group": g.get("HKG_DIRECT_LEAD"),
        "A50_specific_group": g.get("CHINA_A50_DIRECT_LEAD"),
        "explains_trading_houses": (interest.get("卸売業") or {}).get("hkg_leads") or (interest.get("卸売業") or {}).get("chi_leads"),
        "explains_machinery": (interest.get("機械") or {}).get("hkg_leads") or (interest.get("機械") or {}).get("chi_leads"),
        "explains_nonferrous": (interest.get("非鉄金属") or {}).get("hkg_leads") or (interest.get("非鉄金属") or {}).get("chi_leads"),
        "explains_steel": (interest.get("鉄鋼") or {}).get("hkg_leads") or (interest.get("鉄鋼") or {}).get("chi_leads"),
        "explains_shipping": (interest.get("海運業") or {}).get("hkg_leads") or (interest.get("海運業") or {}).get("chi_leads"),
        "explains_chemicals": (interest.get("化学") or {}).get("hkg_leads") or (interest.get("化学") or {}).get("chi_leads"),
        "explains_any_of_13_tech": any(bool(r.get("usable")) for r in tech),
        "stocks_with_at_least_one_usable_external_driver_n": cov.get("has_at_least_one_causally_usable_external_driver_n"),
        "evidence_quality_breakdown": {
            "NATIVE_CAUSAL_PROVEN_n": len(q.get("NATIVE_CAUSAL_PROVEN") or []),
            "PROXY_CAUSAL_SUPPORTED_n": len(q.get("PROXY_CAUSAL_SUPPORTED") or []),
            "OVERNIGHT_CAUSAL_PROVEN_n": len(q.get("OVERNIGHT_CAUSAL_PROVEN") or []),
            "SIMULTANEOUS_ONLY_n": len(q.get("SIMULTANEOUS_ONLY") or []),
            "SOURCE_UNAVAILABLE_n": len(q.get("SOURCE_UNAVAILABLE") or []),
            "UNEXPLAINED_n": len(q.get("UNEXPLAINED") or []),
        },
        "remain_unexplained_n": cov.get("remain_unexplained_n"),
        "tech_still_highest_value_unresolved": nxt.get("tech_still_highest_value_unresolved"),
        "paid_taiwan_korea_cme_would_address_material_missing_group": True,
        "do_not_purchase": True,
        "ENTRY_playbooks_built": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used": False,
        "Kabu_50": False,
        "submit_cancel_live": "0/0/0",
        "source_status": p.get("status"),
        "korea_not_tested": True,
        "TECH_DRIVER_BLOCKER_OPEN": True,
        "live_20260914_note": live.get("note") or live.get("status"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
