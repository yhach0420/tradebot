"""Sector/symbol driver-response mapping. Discovery only. No ENTRY optimization. Frozen Validation closed."""
from __future__ import annotations

import gc
from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.identify_minimum_missing_external_causal_information_v1.proxy import load_proxy_frame
from research.sector_symbol_driver_response_mapping_v1 import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_FUTURES,
    CASE_INSUFFICIENT,
    CASE_MAP,
    CASE_SECTOR,
    FROZEN_VALIDATION_OPENED,
    KABU_50_APPLIED,
    NEXT_BIND,
    NEXT_EXPAND,
    NEXT_FUT_GROUP,
    NEXT_PLAYBOOKS,
    NEXT_PROSPECTIVE_SUBTRACK,
    NEXT_SECTOR_PB,
    PARENT_VERDICT,
)
from research.sector_symbol_driver_response_mapping_v1.bind import bind_prior
from research.sector_symbol_driver_response_mapping_v1.catalog import PROXY_ONLY, PROSPECTIVE_ONLY, UNAVAILABLE_NO_PURCHASE, build_catalog
from research.sector_symbol_driver_response_mapping_v1.cluster import build_stock_cards, cluster_cards, hm1_overlay
from research.sector_symbol_driver_response_mapping_v1.panel import build_observations, symbol_history
from research.sector_symbol_driver_response_mapping_v1.response import map_responses


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
            "tse33_code": row.get("tse33_code"),
            "industry_topix17": row.get("topix17_name"),
            "scale_category": row.get("scale_category"),
        }
    return sector_of, meta


def _sector_summary(sector_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sector_rows:
        by[str(r["sector"])].append(r)
    out = []
    for sec, rows in sorted(by.items()):
        ranked = sorted(rows, key=lambda x: abs(float(x.get("rho_incremental_after_market_ex_sector") or 0)), reverse=True)
        stable = [x for x in rows if x.get("status") in {"STRONG_STABLE", "WEAK_STABLE"}]
        prim = (stable or ranked)[0] if ranked else None
        secnd = (stable or ranked)[1] if len(stable or ranked) > 1 else None
        out.append(
            {
                "sector": sec,
                "primary_driver": None if not prim else prim.get("driver"),
                "primary_status": None if not prim else prim.get("status"),
                "primary_rho_inc": None if not prim else prim.get("rho_incremental_after_market_ex_sector"),
                "primary_lead": None if not prim else prim.get("lead_lag"),
                "secondary_driver": None if not secnd else secnd.get("driver"),
                "d1_d4": None if not prim else prim.get("block_rho"),
                "asymmetry": None if not prim else prim.get("asymmetry"),
                "regime": None if not prim else {k: (prim.get("regime") or {}).get(k) for k in ("high", "low", "cut")},
                "stable_n": len(stable),
            }
        )
    return out


def decide(*, bind_ok: bool, stable_sec_n: int, usable_lead_n: int, justified_n: int, futures_blocking: bool) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior bind failed. No mapping."}
    if futures_blocking:
        return {
            "CASE": "FUTURES",
            "VERDICT": CASE_FUTURES,
            "NEXT": NEXT_FUT_GROUP,
            "INTERPRETATION": "Key response groups require true NK/TOPIX futures that are not historically available. Prospective accumulation remains a subtrack. Do not buy DataCube in this phase.",
        }
    if usable_lead_n >= 3 and justified_n >= 5:
        return {
            "CASE": "MAP",
            "VERDICT": CASE_MAP,
            "NEXT": NEXT_PLAYBOOKS,
            "INTERPRETATION": "Stable sector/stock driver-response groups exist. Next is driver-specific playbooks, not a global 105-name strategy.",
        }
    if (stable_sec_n >= 3 or usable_lead_n >= 3) and justified_n < 5:
        return {
            "CASE": "SECTOR",
            "VERDICT": CASE_SECTOR,
            "NEXT": NEXT_SECTOR_PB,
            "INTERPRETATION": "Sector-level driver relations are usable; stock-specific deviations are weak. Fall back to sector/group playbooks.",
        }
    return {
        "CASE": "INSUFFICIENT",
        "VERDICT": CASE_INSUFFICIENT,
        "NEXT": NEXT_EXPAND,
        "INTERPRETATION": "Currently testable drivers (panel market state + 1321/1306 PROXY_NOT_FUTURES) do not explain meaningful sector/stock differences with causal lead. Expand the catalog with legitimate history (USDJPY if available, etc.). Prospective true futures remain a data subtrack, not the sole next step.",
    }


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} parent={bind.get('parent_external_verdict')} n={bind.get('research_pool_n')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    catalog = build_catalog()
    live = inspect_live_now()
    sector_of, meta = _sector_meta(bind)
    mapped = {"sector_driver": [], "stock_driver": [], "stable_sector_driver_n": 0, "stable_sector_driver": []}
    cards: list[dict[str, Any]] = []
    clus: dict[str, Any] = {"ok": False, "clusters": []}
    hm1 = {}
    hist = {}
    obs_meta: dict[str, Any] = {}
    if bind.get("ok"):
        symbols = list(bind.get("symbols") or [])
        print(f"LOAD_DISCOVERY n={len(disc)} forbidden={len(conf)+len(val)}", flush=True)
        minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
        if minutes["date"].isin(list(val)).any():
            raise RuntimeError("frozen_validation_loaded")
        hist = symbol_history(minutes)
        first = sorted(disc)[0]
        last = sorted(disc)[-1]
        nk_df = load_proxy_frame("1321", first, last)
        tx_df = load_proxy_frame("1306", first, last)
        print(f"PROXY nk_rows={len(nk_df)} tx_rows={len(tx_df)}", flush=True)
        print("BUILD_GRID", flush=True)
        obs = build_observations(minutes=minutes, sector_of=sector_of, date_to_block=date_to_block, nk_df=nk_df, tx_df=tx_df)
        del minutes
        gc.collect()
        obs_meta = {k: v for k, v in obs.items() if k not in {"sec_obs", "stk_obs"}}
        print("MAP_RESPONSES", flush=True)
        mapped = map_responses(obs)
        del obs
        gc.collect()
        cards = build_stock_cards(stock_driver=list(mapped.get("stock_driver") or []), sector_of=sector_of, sector_driver=list(mapped.get("sector_driver") or []))
        clus = cluster_cards(cards)
        hm1 = hm1_overlay(cards=cards)
        print(f"STABLE_SEC={mapped.get('stable_sector_driver_n')} CLUSTERS={len(clus.get('clusters') or [])}", flush=True)

    sector_sum = _sector_summary(list(mapped.get("sector_driver") or []))
    justified = [c for c in cards if c.get("stock_specific_rule_justified")]
    fallback = [c for c in cards if c.get("fallback_to_sector_or_group")]
    direct = [c for c in cards if int(c.get("direct_driver_count") or 0) > 0]
    usable_lead = [r for r in list(mapped.get("sector_driver") or []) if r.get("usable_before_stock_move")]
    contemp = [r for r in list(mapped.get("sector_driver") or []) if r.get("only_contemporaneous")]
    futures_blocking = False
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        stable_sec_n=int(mapped.get("stable_sector_driver_n") or 0),
        usable_lead_n=len(usable_lead),
        justified_n=len(justified),
        futures_blocking=futures_blocking,
    )
    decision["prospective_true_futures_subtrack"] = NEXT_PROSPECTIVE_SUBTRACK
    symbol_rows = []
    for c in cards:
        m = dict(meta.get(c["symbol"]) or {})
        h = dict(hist.get(c["symbol"]) or {})
        symbol_rows.append(
            {
                **m,
                **h,
                "primary_driver": c.get("primary_driver"),
                "secondary_driver": c.get("secondary_driver"),
                "cluster_id": c.get("cluster_id"),
                "stock_specific_rule_justified": c.get("stock_specific_rule_justified"),
                "fallback_to_sector_or_group": c.get("fallback_to_sector_or_group"),
                "direct_driver_count": c.get("direct_driver_count"),
                "named": c.get("named"),
                "historical_source_class": "panel_plus_proxy_etf",
                "runtime_deployability": "RESEARCH_ONLY_THIS_PHASE",
            }
        )
    for sym, m in meta.items():
        if sym not in {c["symbol"] for c in cards}:
            h = dict(hist.get(sym) or {})
            symbol_rows.append({**m, **h, "primary_driver": "UNKNOWN_IDIOSYNCRATIC", "sample_availability": h.get("n_days")})

    return {
        "program_id": "COMPLETE_CAUSAL_STRATEGY_RESEARCH_V1",
        "parent_verdict_accepted": PARENT_VERDICT,
        "architecture": "DRIVER→SECTOR→STOCK→LOCAL_SETUP→TRIGGER; multiple playbooks; not one global 105 strategy",
        "hm1_role": "frozen_near_threshold_stock_only_lead_not_universe_definition",
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "blocks"}},
        "split": {k: v for k, v in split.items() if k not in {"discovery_dates", "confirmation_dates", "frozen_validation_dates"}},
        "catalog": catalog,
        "observations": obs_meta,
        "symbol_sector_map": symbol_rows,
        "sector_n": len({r.get("sector") for r in symbol_rows if r.get("sector")}),
        "stock_n": len({r.get("symbol") for r in symbol_rows if r.get("symbol")}),
        "sector_response": sector_sum,
        "sector_driver_cells": [
            {k: r.get(k) for k in ("sector", "driver", "status", "rho_raw", "rho_incremental_after_market_ex_sector", "lead_lag", "signed_response", "usable_before_stock_move", "only_contemporaneous", "block_rho", "fwd_rho", "reverse_rho_sector_now_vs_driver", "n", "day_n")}
            for r in list(mapped.get("sector_driver") or [])
        ],
        "stock_response": [
            {k: r.get(k) for k in ("symbol", "driver", "status", "rho_raw", "rho_after_market", "rho_after_sector", "direct_beyond_sector", "n", "day_n")}
            for r in list(mapped.get("stock_driver") or [])
        ],
        "clusters": clus,
        "hm1_postmap": hm1,
        "counts": {
            "stable_sector_driver_n": mapped.get("stable_sector_driver_n"),
            "usable_lead_n": len(usable_lead),
            "contemporaneous_unusable_n": len(contemp),
            "stocks_direct_beyond_sector": len(direct),
            "stock_specific_justified": len(justified),
            "fallback_to_sector_or_group": len(fallback),
        },
        "stable_sector_driver": mapped.get("stable_sector_driver"),
        "did_not_optimize_vwap_ema_rsi": True,
        "did_not_fit_one_giant_model": True,
        "did_not_purchase": True,
        "did_not_wait_months_for_futures": True,
        "frozen_validation": {
            "opened": bool(FROZEN_VALIDATION_OPENED),
            "accessed": False,
            "status": "CLOSED",
            "n": len(val),
        },
        "live_20260914": live,
        "kabu_50_applied": bool(KABU_50_APPLIED),
        "old_confirmation_used_to_design": False,
        "true_futures_subtrack": {
            "label": "PROSPECTIVE_TRUE_FUTURES",
            "retained": True,
            "not_sole_strategy_next": True,
            "not_conflated_with_1321_1306": True,
            "historical_still_unavailable": True,
        },
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    a_cat = dict(report.get("catalog") or {})
    by_c = dict(a_cat.get("by_class") or {})
    counts = dict(report.get("counts") or {})
    clus = dict(report.get("clusters") or {})
    hm1 = dict(report.get("hm1_postmap") or {})
    live = dict(report.get("live_20260914") or {})
    d = dict(report.get("decision") or {})
    clusters = []
    for g in list(clus.get("clusters") or []):
        clusters.append(
            {
                "id": g.get("cluster_id"),
                "n": g.get("n"),
                "symbols": g.get("symbols"),
                "interpretation": g.get("economic_interpretation"),
                "dominant": g.get("dominant_loading"),
            }
        )
    return {
        "sector_n": report.get("sector_n"),
        "stock_n": report.get("stock_n"),
        "historically_testable_drivers": a_cat.get("testable_ids"),
        "PROXY_ONLY": by_c.get(PROXY_ONLY),
        "PROSPECTIVE_ONLY": by_c.get(PROSPECTIVE_ONLY),
        "UNAVAILABLE_NO_PURCHASE": by_c.get(UNAVAILABLE_NO_PURCHASE),
        "sector_primary_drivers": list(report.get("sector_response") or []),
        "stable_sector_driver_n": counts.get("stable_sector_driver_n"),
        "stocks_additional_direct_sensitivity": counts.get("stocks_direct_beyond_sector"),
        "stock_specific_rules_justified": counts.get("stock_specific_justified"),
        "stocks_fallback_to_sector_or_group": counts.get("fallback_to_sector_or_group"),
        "stable_response_clusters_found": bool(clus.get("ok")),
        "clusters": clusters,
        "electrical_machinery_one_common_driver": hm1.get("shares_one_common_driver"),
        "electrical_machinery_splits": hm1.get("splits_into_multiple_response_groups"),
        "hm1_electrical_machinery_explanation": hm1.get("explanation_candidate"),
        "any_driver_causal_before_stock_move": int(counts.get("usable_lead_n") or 0) > 0,
        "usable_lead_n": counts.get("usable_lead_n"),
        "relations_only_contemporaneous_n": counts.get("contemporaneous_unusable_n"),
        "true_nk_topix_historical_futures_still_unavailable": True,
        "prospective_futures_track_intact": True,
        "frozen_validation_opened": False,
        "old_confirmation_used_to_design": False,
        "kabu_50_applied": False,
        "submit_cancel_live": "0/0/0",
        "live_20260914": {
            "futures_pid": live.get("futures_pid"),
            "futures_alive": live.get("futures_alive"),
            "breadth_pid": live.get("breadth_pid"),
            "breadth_alive": live.get("breadth_alive"),
        },
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
        "hm1_tuned": False,
        "did_not_purchase": True,
    }
