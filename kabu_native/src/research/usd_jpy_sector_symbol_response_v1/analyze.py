"""USDJPY sector/stock causal response. Discovery only. No ENTRY optimization. Frozen Validation closed."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.usd_jpy_sector_symbol_response_v1 import (
    CASE_BIND,
    CASE_BLOCKED,
    CASE_GROUPS,
    CASE_NO_LEAD,
    CASE_SECTOR,
    FOCUS_SECTORS,
    FOCUS_STOCKS,
    NEXT_BIND,
    NEXT_BY_GROUPS,
    NEXT_PRESERVE,
    NEXT_RESOLVE,
    NEXT_UNEXPLAINED,
    PRIOR_RESIDUAL_FLAGS,
    STATUS_ACCESSIBLE,
)
from research.usd_jpy_sector_symbol_response_v1.bind import bind_prior
from research.usd_jpy_sector_symbol_response_v1.panel import build_observations
from research.usd_jpy_sector_symbol_response_v1.reconcile import freeze_fx_response_map, reconcile_stock_rows, usable_direct_causal_leads
from research.usd_jpy_sector_symbol_response_v1.response import map_preopen, map_sector, map_stock
from research.usd_jpy_sector_symbol_response_v1.semantics import prove_semantics
from research.usd_jpy_sector_symbol_response_v1.source import acquire_discovery_history, load_minute_frame, probe_sources


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
    fx_n: int,
    sector_lead_n: int,
    stable_group_n: int,
    preopen_stable_n: int = 0,
) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior bind failed."}
    if source_status != STATUS_ACCESSIBLE or not semantics_ok or fx_n <= 0:
        return {
            "CASE": "BLOCKED",
            "VERDICT": CASE_BLOCKED,
            "NEXT": NEXT_RESOLVE,
            "INTERPRETATION": "USDJPY history was not obtained with proven timestamp semantics. Do not fake a no-lead result. Resolve free Dukascopy/Jetta access.",
        }
    if stable_group_n >= 3 and sector_lead_n >= 2:
        return {
            "CASE": "GROUPS",
            "VERDICT": CASE_GROUPS,
            "NEXT": NEXT_UNEXPLAINED,
            "INTERPRETATION": "USDJPY leads stable response groups. Freeze the driver-response map. Do not build playbooks yet. Map the next driver for unexplained groups.",
        }
    if sector_lead_n >= 1 or preopen_stable_n >= 1:
        return {
            "CASE": "SECTOR",
            "VERDICT": CASE_SECTOR,
            "NEXT": NEXT_PRESERVE,
            "INTERPRETATION": "USDJPY shows a sector-specific causal lead (intraday residual and/or overnight/preopen state). Preserve that FX group and choose the next driver family from remaining unexplained groups. Do not treat this as a global 105-name rule or a playbook.",
        }
    return {
        "CASE": "NO_LEAD",
        "VERDICT": CASE_NO_LEAD,
        "NEXT": NEXT_BY_GROUPS,
        "INTERPRETATION": "USDJPY does not provide a usable causal lead after market-mode removal at 1-minute resolution. This does not mean external drivers fail. Choose the next family from unresolved groups.",
    }


def _groups(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[str]] = defaultdict(list)
    for r in stocks:
        if r.get("class") == "NO_STABLE_RESPONSE":
            continue
        key = f"{r.get('class')}|{r.get('lead_lag')}|{r.get('sector')}"
        by[key].append(str(r.get("symbol")))
    genuine = [{"profile": k, "n": len(v), "symbols": v} for k, v in by.items() if len(v) >= 3]
    return {
        "did_not_reuse_c1_c5": True,
        "new_groups_from_usdjpy_profiles": genuine,
        "response_based_groups_change": bool(genuine),
        "n_profile_groups": len(genuine),
    }


def _next_family(unexplained_sectors: list[str], unexplained_stocks: list[str], sector_of: dict[str, str]) -> dict[str, Any]:
    names = " ".join(unexplained_sectors)
    elec = "電気機器" in unexplained_sectors or any(sector_of.get(s) == "電気機器" for s in unexplained_stocks)
    bank = any(x in names for x in ("銀行", "保険"))
    resource = any(x in names for x in ("鉱業", "石油", "卸売", "海運", "空運"))
    if elec:
        fam, why = "NQ_ES", "electrical-machinery and residual tech-sensitive names remain unexplained after USDJPY"
    elif bank:
        fam, why = "JGB_US_RATES", "rate-sensitive groups remain unexplained"
    elif resource:
        fam, why = "WTI_COMMODITIES", "resource/trading-house/transport-cost groups remain unexplained"
    else:
        fam, why = "NQ_ES", "broad unexplained residual after USDJPY; global risk is the next single family"
    return {
        "family": fam,
        "why": why,
        "do_not_test_all_simultaneously": True,
        "true_futures_remain_prospective_subtrack": True,
        "do_not_buy_datacube": True,
    }


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} parent={bind.get('parent_mapping_verdict')} n={bind.get('research_pool_n')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("validation_dates") or []))
    probe = probe_sources()
    acq = {"ok_n": 0, "fail_n": 0, "confirmation_fetched": False, "frozen_validation_fetched": False}
    fx = None
    fx_meta: dict[str, Any] = {}
    semantics: dict[str, Any] = {"proven": False}
    sector_rows: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []
    pre_sec: list[dict[str, Any]] = []
    pre_stk: list[dict[str, Any]] = []
    obs_meta: dict[str, Any] = {}
    sector_of, meta = _sector_meta(bind)
    if bind.get("ok") and probe.get("status") == STATUS_ACCESSIBLE:
        acq = acquire_discovery_history()
        fx, fx_meta = load_minute_frame()
        semantics = prove_semantics(fx)
        if semantics.get("proven") and fx is not None and not fx.empty:
            symbols = list(bind.get("symbols") or [])
            print(f"LOAD_MINUTES n={len(symbols)} discovery={len(disc)}", flush=True)
            minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
            if minutes["date"].isin(list(conf | val)).any():
                raise RuntimeError("confirmation_or_validation_loaded")
            date_to_block = dict(blocks.get("date_to_block") or {})
            obs = build_observations(minutes=minutes, fx=fx, sector_of=sector_of, date_to_block=date_to_block)
            obs_meta = {k: obs.get(k) for k in ("n_clocks", "n_ok", "n_days", "resolution_min", "used_five_minute_grid")}
            sector_rows = map_sector(obs.get("sector") or {})
            stock_rows = reconcile_stock_rows(map_stock(obs.get("stock") or {}, sector_of))
            pre_sec = map_preopen(obs.get("preopen_sector") or {}, residual=True)
            pre_stk = map_preopen(obs.get("preopen_stock") or {}, residual=False)

    lead_secs = [r for r in sector_rows if r.get("usable_before_stock_move")]
    sim_secs = [r for r in sector_rows if r.get("only_simultaneous")]
    preopen_stable = []
    for r in pre_sec:
        o = dict(r.get("open") or {})
        m15 = dict(r.get("m15") or {})
        rho_o = o.get("rho")
        rho_15 = m15.get("rho")
        if (o.get("stable") and rho_o is not None and abs(float(rho_o)) >= 0.04) or (
            m15.get("stable") and rho_15 is not None and abs(float(rho_15)) >= 0.04
        ):
            preopen_stable.append(
                {
                    "sector": r.get("name"),
                    "open_rho": rho_o,
                    "open_stable": o.get("stable"),
                    "open_d1_d4": o.get("d1_d4_agree"),
                    "m15_rho": rho_15,
                    "m15_stable": m15.get("stable"),
                    "m15_d1_d4": m15.get("d1_d4_agree"),
                }
            )
    weak_up = [r["sector"] for r in sector_rows if r.get("jpy_weakening_sector_up")]
    strong_up = [r["sector"] for r in sector_rows if r.get("jpy_strengthening_sector_up")]
    asym = [r["sector"] for r in sector_rows if r.get("asymmetric")]
    direct = [r for r in stock_rows if r.get("direct_sensitivity")]
    usable_direct = usable_direct_causal_leads(stock_rows)
    mkt_med = [r for r in stock_rows if r.get("class") == "MARKET_MEDIATED"]
    sec_med = [r for r in stock_rows if r.get("class") == "SECTOR_MEDIATED"]
    flags = list(bind.get("prior_residual_flags") or PRIOR_RESIDUAL_FLAGS)
    flag_map = {r["symbol"]: r for r in stock_rows}
    flags_explained = []
    flags_remain = []
    for s in flags:
        row = flag_map.get(s) or {}
        if row.get("class") in ("DIRECT_STOCK_SENSITIVITY", "SECTOR_MEDIATED", "MARKET_MEDIATED") and row.get("usable_before_stock_move"):
            flags_explained.append({"symbol": s, "class": row.get("class"), "lead_lag": row.get("lead_lag")})
        else:
            flags_remain.append({"symbol": s, "class": row.get("class") or "NOT_TESTED", "lead_lag": row.get("lead_lag")})
    groups = _groups(stock_rows)
    unexplained_sec = [r["sector"] for r in sector_rows if not r.get("usable_before_stock_move")]
    unexplained_stk = [r["symbol"] for r in stock_rows if r.get("class") == "NO_STABLE_RESPONSE"]
    nxt = _next_family(unexplained_sec, unexplained_stk, sector_of)
    elec = [r for r in stock_rows if r.get("sector") == "電気機器"]
    elec_classes = sorted({r.get("class") for r in elec})
    focus_out = {}
    for fam, syms in FOCUS_STOCKS.items():
        focus_out[fam] = [
            {
                "symbol": s,
                "sector": sector_of.get(s),
                "class": (flag_map.get(s) or {}).get("class"),
                "lead_lag": (flag_map.get(s) or {}).get("lead_lag"),
                "beta10_sec": ((flag_map.get(s) or {}).get("sector_adj_1m") or {}).get("beta_per_10bps_fx"),
                "rho_sec": ((flag_map.get(s) or {}).get("sector_adj_1m") or {}).get("rho"),
            }
            for s in syms
            if s in sector_of
        ]
    focus_sec_out = [r for r in sector_rows if r.get("sector") in FOCUS_SECTORS]
    live = inspect_live_now()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        source_status=str(probe.get("status") or ""),
        semantics_ok=bool(semantics.get("proven")),
        fx_n=int((fx_meta or {}).get("n_rows") or (0 if fx is None else len(fx))),
        sector_lead_n=len(lead_secs),
        stable_group_n=int(groups.get("n_profile_groups") or 0),
        preopen_stable_n=len(preopen_stable),
    )
    return {
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "parent_verdict_accepted": PARENT_OK(bind),
        "architecture": "DRIVER → SECTOR → STOCK → LOCAL SETUP → ENTRY/EXIT",
        "japan_internal_drivers_not_mined_further": True,
        "current_result_accepted": "CURRENT_DRIVER_SET_INSUFFICIENT_V1",
        "does_not_mean_no_external_driver": True,
        "prior_residual_flags_are_not_playbooks": True,
        "source_probe": probe,
        "acquisition": acq,
        "fx_meta": fx_meta,
        "timestamp_semantics": semantics,
        "observations": obs_meta,
        "sector_raw_and_residual": sector_rows,
        "stock_response": stock_rows,
        "preopen_sector": pre_sec,
        "preopen_stock_head": pre_stk[:30],
        "focus_sectors": focus_sec_out,
        "focus_stocks": focus_out,
        "groups": groups,
        "prior_residual_flags": {
            "n": len(flags),
            "explained_by_usdjpy": flags_explained,
            "remain_unexplained": flags_remain,
        },
        "unexplained": {
            "sectors": unexplained_sec,
            "stock_n": len(unexplained_stk),
            "stocks_head": unexplained_stk[:40],
        },
        "next_driver": nxt,
        "electrical_machinery": {
            "n": len(elec),
            "classes": elec_classes,
            "heterogeneity_explained_by_usdjpy": len(elec_classes) == 1 and elec_classes[0] != "NO_STABLE_RESPONSE",
            "direct_n": sum(1 for r in elec if r.get("class") == "DIRECT_STOCK_SENSITIVITY"),
        },
        "counts": {
            "sector_causal_lead_n": len(lead_secs),
            "sector_simultaneous_n": len(sim_secs),
            "preopen_stable_n": len(preopen_stable),
            "preopen_stable_sectors": preopen_stable,
            "jpy_weakening_sectors": weak_up,
            "jpy_strengthening_sectors": strong_up,
            "asymmetric_sectors": asym,
            "direct_after_market_and_sector_n": len(direct),
            "USABLE_DIRECT_CAUSAL_LEAD_N": len(usable_direct),
            "usable_direct_causal_lead_symbols": [r.get("symbol") for r in usable_direct],
            "market_mediated_n": len(mkt_med),
            "sector_mediated_n": len(sec_med),
            "direct_symbols": [r.get("symbol") for r in direct],
            "direct_but_not_usable": [r.get("symbol") for r in direct if not r.get("usable_before_stock_move")],
        },
        "fx_response_map_v1": freeze_fx_response_map(
            report={
                "stock_response": stock_rows,
                "counts": {"preopen_stable_sectors": preopen_stable},
            }
        ),
        "symbol_meta_n": len(meta),
        "true_futures_subtrack": {
            "label": "PROSPECTIVE_TRUE_FUTURES_SUBTRACK",
            "do_not_substitute_1321_1306": True,
            "datacube_purchased": False,
        },
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "entry_rules_optimized": False,
        "decision": decision,
    }


def PARENT_OK(bind: dict[str, Any]) -> bool:
    return bool(bind.get("ok")) and str(bind.get("parent_mapping_verdict") or "") == "CURRENT_DRIVER_SET_INSUFFICIENT_V1"


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    a = dict(report.get("counts") or {})
    p = dict(report.get("source_probe") or {})
    sem = dict(report.get("timestamp_semantics") or {})
    acq = dict(report.get("acquisition") or {})
    fx = dict(report.get("fx_meta") or {})
    d = dict(report.get("decision") or {})
    flags = dict(report.get("prior_residual_flags") or {})
    nxt = dict(report.get("next_driver") or {})
    elec = dict(report.get("electrical_machinery") or {})
    focus = dict(report.get("focus_stocks") or {})
    groups = dict(report.get("groups") or {})
    live = dict(report.get("live_20260914") or {})
    direct = list(a.get("direct_symbols") or [])
    stocks = {r.get("symbol"): r for r in list(report.get("stock_response") or [])}
    return {
        "USDJPY_source_obtained": bool(p.get("status") == STATUS_ACCESSIBLE and int(fx.get("n_rows") or 0) > 0),
        "free_or_paid": p.get("free_or_paid"),
        "any_purchase": False,
        "history_range": (fx.get("range") or acq.get("range")),
        "bid_ask_or_ohlc": "1-minute Bid and Ask OHLC from Jetta; ticks sampled for semantics",
        "timestamp_semantics_proven": bool(sem.get("proven")),
        "sectors_with_USDJPY_causal_lead": a.get("sector_causal_lead_n"),
        "sectors_with_only_simultaneous_association": a.get("sector_simultaneous_n"),
        "preopen_stable_n": a.get("preopen_stable_n"),
        "preopen_stable_sectors": a.get("preopen_stable_sectors"),
        "sectors_respond_JPY_weakening": a.get("jpy_weakening_sectors"),
        "sectors_respond_JPY_strengthening": a.get("jpy_strengthening_sectors"),
        "asymmetric_relationships": a.get("asymmetric_sectors"),
        "stocks_direct_after_market_and_sector_n": a.get("direct_after_market_and_sector_n"),
        "USABLE_DIRECT_CAUSAL_LEAD_N": a.get("USABLE_DIRECT_CAUSAL_LEAD_N"),
        "usable_direct_causal_lead_symbols": a.get("usable_direct_causal_lead_symbols"),
        "direct_but_not_usable": a.get("direct_but_not_usable"),
        "stocks_direct": [
            {
                "symbol": s,
                "lead_time_min": (stocks.get(s) or {}).get("lead_time_min"),
                "lead_lag": (stocks.get(s) or {}).get("lead_lag"),
                "direct_sensitivity": (stocks.get(s) or {}).get("direct_sensitivity"),
                "causal_lead": (stocks.get(s) or {}).get("causal_lead"),
                "usable_before_stock_move": (stocks.get(s) or {}).get("usable_before_stock_move"),
                "simultaneous_common_news": (stocks.get(s) or {}).get("simultaneous_common_news"),
                "beta_per_10bps_fx": ((stocks.get(s) or {}).get("sector_adj_1m") or {}).get("beta_per_10bps_fx"),
                "mean_response_bps": ((stocks.get(s) or {}).get("sector_adj_1m") or {}).get("mean_y"),
                "d1_d4_agree": (stocks.get(s) or {}).get("d1_d4_agree"),
                "d1_d4_stable": int((stocks.get(s) or {}).get("d1_d4_agree") or 0) >= 3,
            }
            for s in direct
        ],
        "prior_12_explained_by_USDJPY": flags.get("explained_by_usdjpy"),
        "prior_12_remain": flags.get("remain_unexplained"),
        "response_based_groups_change": groups.get("response_based_groups_change"),
        "did_not_reuse_c1_c5": True,
        "USDJPY_explains_electrical_machinery_heterogeneity": elec.get("heterogeneity_explained_by_usdjpy"),
        "autos": focus.get("autos"),
        "machinery": focus.get("machinery"),
        "trading_houses": focus.get("trading_houses"),
        "any_relation_usable_before_stock_move": int(a.get("sector_causal_lead_n") or 0) > 0
        or int(a.get("USABLE_DIRECT_CAUSAL_LEAD_N") or 0) > 0
        or int(a.get("preopen_stable_n") or 0) > 0,
        "unresolved_groups_for_next_driver": dict(report.get("unexplained") or {}),
        "next_driver_family": nxt.get("family"),
        "next_driver_why": nxt.get("why"),
        "frozen_validation_opened": False,
        "old_confirmation_used_to_design": False,
        "kabu_50": False,
        "entry_rules_optimized": False,
        "submit_cancel_live": "0/0/0",
        "live_20260914_note": live.get("note") or live.get("status"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
