"""NQ/ES sector/stock causal response. Discovery only. No ENTRY optimization. Frozen Validation closed."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.nq_es_sector_symbol_response_v1 import (
    CASE_BIND,
    CASE_BLOCKED,
    CASE_ES_BROAD,
    CASE_NO_LEAD,
    CASE_NQ_TECH,
    CASE_PROXY,
    FX_OVERNIGHT_SECTORS,
    NEXT_BIND,
    NEXT_RESOLVE,
    NEXT_UNEXPLAINED,
    PARENT_VERDICT,
    STATUS_ACCESSIBLE,
    STATUS_PROXY,
    TECH_FOCUS,
    TECH_SECTORS,
)
from research.nq_es_sector_symbol_response_v1.bind import bind_prior
from research.nq_es_sector_symbol_response_v1.panel import build_observations
from research.nq_es_sector_symbol_response_v1.response import (
    map_es_sectors,
    map_incremental_sector,
    map_incremental_stock,
    map_nq_sectors,
    map_preopen,
    map_preopen_incremental,
)
from research.nq_es_sector_symbol_response_v1.semantics import prove_semantics
from research.nq_es_sector_symbol_response_v1.source import (
    acquire_discovery_history,
    load_fx_overnight_frame,
    load_proxy_frames,
    probe_sources,
)


def _sector_meta(bind: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    sector_of: dict[str, str] = {}
    meta: dict[str, dict[str, Any]] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        sec = str(row.get("tse33_name") or row.get("sector33_name") or "")
        sector_of[str(sym)] = sec
        meta[str(sym)] = {
            "symbol": str(sym),
            "name_ja": row.get("name_ja"),
            "name_en": row.get("name_en"),
            "sector": sec,
        }
    return sector_of, meta


def decide(
    *,
    bind_ok: bool,
    source_status: str,
    semantics_ok: bool,
    nq_n: int,
    es_n: int,
    true_futures: bool,
    nq_specific_n: int,
    es_broad_n: int,
    nq_sector_lead: bool,
    proxy_mechanism: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior USDJPY/FX-map bind failed."}
    if source_status not in (STATUS_ACCESSIBLE, STATUS_PROXY) or not semantics_ok or nq_n <= 0 or es_n <= 0:
        return {
            "CASE": "BLOCKED",
            "VERDICT": CASE_BLOCKED,
            "NEXT": NEXT_RESOLVE,
            "INTERPRETATION": "Neither true CME NQ/ES history nor a legitimate labeled proxy could support valid research.",
        }
    proxy = (not true_futures) and source_status == STATUS_PROXY
    if proxy and proxy_mechanism:
        return {
            "CASE": "PROXY",
            "VERDICT": CASE_PROXY,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": (
                "A labeled US-tech/index proxy shows a credible driver-response mechanism. "
                "This is not proof from true CME NQ/ES futures. Preserve confirmed USDJPY groups. Do not build playbooks."
            ),
        }
    if nq_specific_n >= 1 or nq_sector_lead:
        return {
            "CASE": "NQ_TECH",
            "VERDICT": CASE_NQ_TECH,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "NQ specifically leads stable tech/response groups after ES residualization. Preserve USDJPY groups. Do not build playbooks.",
        }
    if es_broad_n >= 1:
        return {
            "CASE": "ES_BROAD",
            "VERDICT": CASE_ES_BROAD,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "Only broad ES/global-risk response is supported. NQ is not incremental. Preserve USDJPY groups. Do not build playbooks.",
        }
    return {
        "CASE": "NO_LEAD",
        "VERDICT": CASE_NO_LEAD,
        "NEXT": NEXT_UNEXPLAINED,
        "INTERPRETATION": "NQ/ES (or proxy) associations are mostly simultaneous/common-news or unstable. Preserve USDJPY groups. Do not build playbooks.",
    }


def _preopen_stable(rows: list[dict[str, Any]], *, keys: tuple[str, ...] = ("open", "m5", "m15")) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        hit = {}
        ok = False
        for k in keys:
            rec = dict(r.get(k) or {})
            rho = rec.get("rho")
            if rec.get("stable") and rho is not None and abs(float(rho)) >= 0.04:
                ok = True
            hit[k] = {"rho": rho, "stable": rec.get("stable"), "d1_d4": rec.get("d1_d4_agree")}
        if ok:
            out.append({"name": r.get("name") or r.get("sector"), "driver": r.get("driver"), **hit})
    return out


def _groups(stocks: list[dict[str, Any]], sector_of: dict[str, str], fx_map: dict[str, Any]) -> dict[str, Any]:
    nq_direct = [r["symbol"] for r in stocks if r.get("class") in ("NQ_SPECIFIC_AFTER_ES", "DIRECT_NQ_SENSITIVITY") and r.get("usable_before_stock_move")]
    nq_mkt = [r["symbol"] for r in stocks if r.get("class") == "GLOBAL_MARKET_MEDIATED"]
    es_broad = [r["symbol"] for r in stocks if r.get("class") == "DIRECT_ES_SENSITIVITY" and r.get("usable_before_stock_move")]
    fx_names = [s for s, sec in sector_of.items() if sec in FX_OVERNIGHT_SECTORS]
    assigned = set(nq_direct) | set(es_broad)
    unknown = [r["symbol"] for r in stocks if r["symbol"] not in assigned and r.get("class") in ("NO_STABLE_RESPONSE", "SIMULTANEOUS_ONLY", "SECTOR_MEDIATED")]
    labels = []
    if len(nq_direct) >= 2:
        labels.append({"group": "NQ_DIRECT", "n": len(nq_direct), "symbols": nq_direct})
    if len(nq_mkt) >= 3:
        labels.append({"group": "NQ_MARKET_MEDIATED", "n": len(nq_mkt), "symbols": nq_mkt})
    if len(es_broad) >= 2:
        labels.append({"group": "ES_BROAD_RISK", "n": len(es_broad), "symbols": es_broad})
    labels.append({"group": "FX_OVERNIGHT_EXPORTER", "n": len(fx_names), "symbols": fx_names, "preserved_from": "FX_RESPONSE_MAP_V1"})
    return {
        "did_not_reuse_c1_c5": True,
        "did_not_force_tse33": True,
        "groups": labels,
        "unknown_n": len(unknown),
        "unknown_head": unknown[:40],
        "nq_direct_n": len(nq_direct),
        "es_broad_n": len(es_broad),
        "fx_overnight_n": len(fx_names),
        "new_stable_driver_response_groups_n": sum(1 for g in labels if g["group"] != "FX_OVERNIGHT_EXPORTER" and int(g.get("n") or 0) >= 2),
        "fx_map_id": fx_map.get("ANALYSIS_ID"),
    }


def _next_family(unexplained_sec: list[str], unexplained_stk: list[str], sector_of: dict[str, str], nq_explained_tech: bool) -> dict[str, Any]:
    names = " ".join(unexplained_sec)
    elec_left = (not nq_explained_tech) and (
        "電気機器" in unexplained_sec or any(sector_of.get(s) == "電気機器" for s in unexplained_stk)
    )
    bank = any(x in names for x in ("銀行", "保険")) or any(sector_of.get(s) in ("銀行業", "保険業") for s in unexplained_stk)
    resource = any(x in names for x in ("鉱業", "石油", "卸売", "海運", "空運"))
    if elec_left:
        fam, why = "KOSPI_KOSPI200", "Asia semiconductor / electrical-machinery names remain unexplained after USDJPY and NQ/ES"
    elif bank:
        fam, why = "JGB_US_RATES", "banks / insurers remain unexplained"
    elif resource:
        fam, why = "WTI_COMMODITIES", "resource / INPEX / trading-house groups remain unexplained"
    else:
        fam, why = "CHINA_HK", "remaining cyclicals/unassigned groups; China/HK is the next gap-driven family unless a tighter residual cluster appears"
    return {
        "family": fam,
        "why": why,
        "do_not_follow_fixed_checklist_blindly": True,
        "true_futures_remain_prospective_subtrack": True,
        "do_not_buy_datacube": True,
        "gap_driven": True,
    }


def _tech_rows(stocks: list[dict[str, Any]], sector_of: dict[str, str]) -> list[dict[str, Any]]:
    by = {r["symbol"]: r for r in stocks}
    extra = [s for s, sec in sector_of.items() if sec in TECH_SECTORS]
    names = list(TECH_FOCUS) + [s for s in extra if s not in TECH_FOCUS]
    out = []
    for s in names:
        r = by.get(s) or {}
        out.append(
            {
                "symbol": s,
                "sector": sector_of.get(s),
                "focus": s in TECH_FOCUS,
                "class": r.get("class"),
                "nq_lead_lag": r.get("nq_lead_lag"),
                "es_lead_lag": r.get("es_lead_lag"),
                "nq_after_es_lead_lag": r.get("nq_after_es_lead_lag"),
                "nq_rho_sec": ((r.get("nq_sector_adj_1m") or {}).get("rho")),
                "es_rho_sec": ((r.get("es_sector_adj_1m") or {}).get("rho")),
                "nq_after_es_rho": ((r.get("nq_after_es_1m") or {}).get("rho")),
                "usable_before_stock_move": r.get("usable_before_stock_move"),
                "simultaneous_common_news": r.get("simultaneous_common_news"),
            }
        )
    return out


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} parent={bind.get('parent_usdjpy_verdict')} n={bind.get('research_pool_n')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("validation_dates") or []))
    probe = probe_sources()
    acq = {"ok_n": 0, "fail_n": 0, "confirmation_fetched": False, "frozen_validation_fetched": False, "true_cme_futures_pulled": False}
    nq = es = None
    nq_meta: dict[str, Any] = {}
    es_meta: dict[str, Any] = {}
    semantics: dict[str, Any] = {"proven": False}
    nq_sec: list[dict[str, Any]] = []
    es_sec: list[dict[str, Any]] = []
    inc_sec: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []
    pre_nq: list[dict[str, Any]] = []
    pre_es: list[dict[str, Any]] = []
    pre_inc: list[dict[str, Any]] = []
    obs_meta: dict[str, Any] = {}
    fx_meta: dict[str, Any] = {}
    sector_of, meta = _sector_meta(bind)
    fx_map = dict(bind.get("fx_response_map_v1") or {})
    if bind.get("ok") and probe.get("status") in (STATUS_ACCESSIBLE, STATUS_PROXY):
        acq = acquire_discovery_history()
        frames = load_proxy_frames()
        nq, es = frames.get("nq"), frames.get("es")
        nq_meta, es_meta = dict(frames.get("nq_meta") or {}), dict(frames.get("es_meta") or {})
        semantics = prove_semantics(nq, es, code_nq=str(nq_meta.get("code") or ""), code_es=str(es_meta.get("code") or ""))
        if semantics.get("proven") and nq is not None and es is not None and not nq.empty and not es.empty:
            symbols = list(bind.get("symbols") or [])
            print(f"LOAD_MINUTES n={len(symbols)} discovery={len(disc)}", flush=True)
            minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
            if minutes["date"].isin(list(conf | val)).any():
                raise RuntimeError("confirmation_or_validation_loaded")
            fx, fx_meta = load_fx_overnight_frame()
            date_to_block = dict(blocks.get("date_to_block") or {})
            obs = build_observations(
                minutes=minutes,
                nq=nq,
                es=es,
                fx=fx,
                sector_of=sector_of,
                date_to_block=date_to_block,
            )
            obs_meta = {k: obs.get(k) for k in ("n_clocks", "n_ok", "n_stale_skip", "n_days", "resolution_min", "used_five_minute_grid", "asof_max_lag_ms")}
            nq_sec = map_nq_sectors(obs.get("sector") or {})
            es_sec = map_es_sectors(obs.get("sector") or {})
            inc_sec = map_incremental_sector(obs.get("sector") or {})
            stock_rows = map_incremental_stock(obs.get("stock") or {}, sector_of)
            pre_nq = map_preopen(obs.get("preopen_sector") or {}, xkey="nq_ov", residual_open=True)
            pre_es = map_preopen(obs.get("preopen_sector") or {}, xkey="es_ov", residual_open=True)
            pre_inc = map_preopen_incremental(obs.get("preopen_sector") or {})

    nq_specific = [r for r in stock_rows if r.get("class") == "NQ_SPECIFIC_AFTER_ES" and r.get("usable_before_stock_move")]
    nq_direct = [r for r in stock_rows if r.get("class") == "DIRECT_NQ_SENSITIVITY" and r.get("usable_before_stock_move")]
    es_direct = [r for r in stock_rows if r.get("class") == "DIRECT_ES_SENSITIVITY" and r.get("usable_before_stock_move")]
    sim_only = [r for r in stock_rows if r.get("class") == "SIMULTANEOUS_ONLY"]
    none = [r for r in stock_rows if r.get("class") == "NO_STABLE_RESPONSE"]
    elec_inc = [r for r in inc_sec if r.get("sector") == "電気機器"]
    nq_leads_elec = bool(elec_inc and elec_inc[0].get("nq_incremental_after_es"))
    es_leads_elec = bool(elec_inc and (elec_inc[0].get("es_residual_1m") or {}).get("stable") and str((elec_inc[0].get("es_lead_lag") or "")).startswith("es_leads"))
    nq_inc_after_es = bool(elec_inc and elec_inc[0].get("nq_incremental_after_es")) or bool(nq_specific)
    pre_nq_stable = _preopen_stable(pre_nq)
    pre_es_stable = _preopen_stable(pre_es)
    pre_inc_stable = []
    for r in pre_inc:
        rec = dict(r.get("nq_after_fx_open") or {})
        rho = rec.get("rho")
        if rec.get("stable") and rho is not None and abs(float(rho)) >= 0.04:
            pre_inc_stable.append({"name": r.get("name"), "nq_after_fx_rho": rho, "d1_d4": rec.get("d1_d4_agree")})
    fx_changes = []
    for r in pre_inc:
        nq_o = dict(r.get("nq_open") or {})
        nq_fx = dict(r.get("nq_after_fx_open") or {})
        if nq_o.get("stable") and not nq_fx.get("stable") and r.get("name") in FX_OVERNIGHT_SECTORS:
            fx_changes.append({"name": r.get("name"), "nq_open_rho": nq_o.get("rho"), "nq_after_fx_rho": nq_fx.get("rho"), "attribution": "overnight_FX_absorbs_NQ"})
        elif nq_fx.get("stable") and r.get("name") in list(FX_OVERNIGHT_SECTORS) + list(TECH_SECTORS):
            fx_changes.append({"name": r.get("name"), "nq_after_fx_rho": nq_fx.get("rho"), "attribution": "NQ_remains_incremental_after_overnight_FX"})
    groups = _groups(stock_rows, sector_of, fx_map)
    tech = _tech_rows(stock_rows, sector_of)
    tech_focus = [r for r in tech if r.get("focus")]
    nq_explains = {s: next((r.get("class") for r in tech_focus if r.get("symbol") == s), None) for s in ("6857", "8035", "6920", "285A0")}
    nq_explained_tech = any(
        r.get("usable_before_stock_move") and r.get("class") in ("NQ_SPECIFIC_AFTER_ES", "DIRECT_NQ_SENSITIVITY") for r in tech_focus
    )
    unexplained_sec = [
        r["sector"]
        for r in inc_sec
        if not r.get("nq_incremental_after_es") and not str(r.get("es_lead_lag") or "").startswith("es_leads")
    ]
    unexplained_stk = [r["symbol"] for r in stock_rows if not r.get("usable_before_stock_move")]
    nxt = _next_family(unexplained_sec, unexplained_stk, sector_of, nq_explained_tech)
    fx_usable = list((fx_map.get("FX_INTRADAY_DIRECT_CANDIDATES") or {}).get("symbols") or [])
    fx_ov_syms = [s for s, sec in sector_of.items() if sec in FX_OVERNIGHT_SECTORS]
    nq_es_usable = [r["symbol"] for r in stock_rows if r.get("usable_before_stock_move")]
    has_driver = sorted(set(fx_usable) | set(fx_ov_syms) | set(nq_es_usable))
    remain = sorted(set(sector_of) - set(has_driver))
    proxy_mechanism = bool(
        nq_specific or nq_direct or es_direct or pre_nq_stable or pre_es_stable or nq_leads_elec or es_leads_elec
    )
    live = inspect_live_now()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        source_status=str(probe.get("status") or ""),
        semantics_ok=bool(semantics.get("proven")),
        nq_n=int(nq_meta.get("n_rows") or 0),
        es_n=int(es_meta.get("n_rows") or 0),
        true_futures=False,
        nq_specific_n=len(nq_specific),
        es_broad_n=len(es_direct),
        nq_sector_lead=nq_leads_elec,
        proxy_mechanism=proxy_mechanism,
    )
    return {
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "parent_verdict_accepted": bool(bind.get("ok")) and str(bind.get("parent_usdjpy_verdict") or "") == PARENT_VERDICT,
        "architecture": "DRIVER → SECTOR → STOCK → LOCAL SETUP → ENTRY/EXIT",
        "usdjpy_preserved": True,
        "usdjpy_not_redesigned": True,
        "do_not_combine_nq_es_score": True,
        "source_probe": probe,
        "acquisition": acq,
        "nq_meta": nq_meta,
        "es_meta": es_meta,
        "fx_overnight_meta": fx_meta,
        "timestamp_semantics": semantics,
        "contract_semantics": dict((semantics.get("contract") or {})),
        "observations": obs_meta,
        "nq_sector": nq_sec,
        "es_sector": es_sec,
        "incremental_sector": inc_sec,
        "stock_response": stock_rows,
        "preopen_nq": pre_nq,
        "preopen_es": pre_es,
        "preopen_incremental": pre_inc,
        "tech_name_response": tech,
        "groups": groups,
        "unexplained": {"sectors": unexplained_sec, "stock_n": len(unexplained_stk), "stocks_head": unexplained_stk[:40]},
        "next_driver": nxt,
        "electrical_machinery": {
            "nq_leads": nq_leads_elec,
            "es_leads": es_leads_elec,
            "nq_incremental_after_es": nq_inc_after_es,
            "row": elec_inc[0] if elec_inc else {},
        },
        "focus_tech": {
            "6857": nq_explains.get("6857"),
            "8035": nq_explains.get("8035"),
            "6920": nq_explains.get("6920"),
            "285A0": nq_explains.get("285A0"),
            "rows": tech_focus,
        },
        "fx_interaction": {
            "overnight_NQ_after_USDJPY": fx_changes,
            "preopen_nq_stable": pre_nq_stable,
            "preopen_es_stable": pre_es_stable,
            "nq_incremental_after_fx_stable": pre_inc_stable,
        },
        "coverage": {
            "pool_n": len(sector_of),
            "fx_usable_direct": fx_usable,
            "fx_overnight_symbols_n": len(fx_ov_syms),
            "nq_es_usable": nq_es_usable,
            "has_at_least_one_causally_usable_external_driver_n": len(has_driver),
            "remain_unexplained_n": len(remain),
            "remain_head": remain[:40],
        },
        "counts": {
            "nq_specific_n": len(nq_specific),
            "nq_direct_n": len(nq_direct),
            "es_direct_n": len(es_direct),
            "simultaneous_only_n": len(sim_only),
            "no_stable_n": len(none),
            "preopen_nq_stable_n": len(pre_nq_stable),
            "preopen_es_stable_n": len(pre_es_stable),
        },
        "fx_response_map_v1": fx_map,
        "usdjpy_label_reconciliation": dict(bind.get("usdjpy_label_reconciliation") or {}),
        "symbol_meta_n": len(meta),
        "true_futures_subtrack": {
            "label": "PROSPECTIVE_TRUE_FUTURES_SUBTRACK",
            "do_not_substitute_1321_1306": True,
            "datacube_purchased": False,
            "nk225mini_topix_accumulated_prospectively": True,
        },
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "entry_rules_optimized": False,
        "playbooks_built": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    a = dict(report.get("counts") or {})
    p = dict(report.get("source_probe") or {})
    sem = dict(report.get("timestamp_semantics") or {})
    con = dict(report.get("contract_semantics") or {})
    nq_m = dict(report.get("nq_meta") or {})
    es_m = dict(report.get("es_meta") or {})
    d = dict(report.get("decision") or {})
    nxt = dict(report.get("next_driver") or {})
    elec = dict(report.get("electrical_machinery") or {})
    focus = dict(report.get("focus_tech") or {})
    groups = dict(report.get("groups") or {})
    cov = dict(report.get("coverage") or {})
    fxr = dict(report.get("fx_response_map_v1") or {})
    rec = dict(report.get("usdjpy_label_reconciliation") or {})
    fx_int = dict(report.get("fx_interaction") or {})
    tech = list(report.get("tech_name_response") or [])
    focus_rows = [r for r in tech if r.get("focus")]
    strict = [r["symbol"] for r in focus_rows if str(r.get("nq_lead_lag") or "").startswith("nq_leads") and r.get("usable_before_stock_move")]
    sim = [r["symbol"] for r in focus_rows if r.get("class") == "SIMULTANEOUS_ONLY" or r.get("simultaneous_common_news")]
    none = [r["symbol"] for r in focus_rows if r.get("class") == "NO_STABLE_RESPONSE"]
    live = dict(report.get("live_20260914") or {})
    return {
        "USDJPY_labels_reconciled": bool(rec.get("USABLE_DIRECT_CAUSAL_LEAD_N") == 3 or fxr.get("USABLE_DIRECT_CAUSAL_LEAD_N") == 3),
        "USABLE_DIRECT_CAUSAL_LEAD_N": rec.get("USABLE_DIRECT_CAUSAL_LEAD_N") or fxr.get("USABLE_DIRECT_CAUSAL_LEAD_N"),
        "usable_direct_fx_symbols": rec.get("usable_direct_causal_lead_symbols") or (fxr.get("FX_INTRADAY_DIRECT_CANDIDATES") or {}).get("symbols"),
        "FX_overnight_group_preserved": True,
        "FX_overnight_sectors": ["輸送用機器", "非鉄金属"],
        "NQ_historical_source_available": bool(int(nq_m.get("n_rows") or 0) > 0),
        "NQ_true_futures_or_proxy": "PROXY_NOT_CME_FUTURES" if not nq_m.get("true_cme_futures") else "TRUE_CME_FUTURES",
        "NQ_free_or_paid": "free_proxy",
        "ES_historical_source_available": bool(int(es_m.get("n_rows") or 0) > 0),
        "ES_true_futures_or_proxy": "PROXY_NOT_CME_FUTURES" if not es_m.get("true_cme_futures") else "TRUE_CME_FUTURES",
        "any_purchase": False,
        "timestamp_semantics_proven": bool(sem.get("proven")),
        "contract_roll_semantics_proven": bool(con.get("proven")),
        "NQ_leads_electrical_machinery": elec.get("nq_leads"),
        "ES_leads_electrical_machinery": elec.get("es_leads"),
        "NQ_incremental_after_ES": elec.get("nq_incremental_after_es"),
        "NQ_explains_6857": focus.get("6857"),
        "NQ_explains_8035": focus.get("8035"),
        "NQ_explains_6920": focus.get("6920"),
        "NQ_explains_285A0": focus.get("285A0"),
        "tech_STRICT_CAUSAL_LEAD": strict,
        "tech_SIMULTANEOUS_ONLY": sim,
        "tech_NO_STABLE_RESPONSE": none,
        "preopen_NQ_relationship": bool(int(a.get("preopen_nq_stable_n") or 0) > 0),
        "preopen_nq_stable": fx_int.get("preopen_nq_stable"),
        "overnight_USDJPY_interaction_changes_attribution": list(fx_int.get("overnight_NQ_after_USDJPY") or []),
        "new_stable_driver_response_groups_n": groups.get("new_stable_driver_response_groups_n"),
        "stocks_with_at_least_one_causally_usable_external_driver_n": cov.get("has_at_least_one_causally_usable_external_driver_n"),
        "remain_unexplained_n": cov.get("remain_unexplained_n"),
        "next_driver_family": nxt.get("family"),
        "next_driver_why": nxt.get("why"),
        "ENTRY_playbooks_built": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "Kabu_50": False,
        "submit_cancel_live": "0/0/0",
        "source_status": p.get("status"),
        "research_mode": p.get("research_mode"),
        "nq_specific_n": a.get("nq_specific_n"),
        "es_direct_n": a.get("es_direct_n"),
        "live_20260914_note": live.get("note") or live.get("status"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
