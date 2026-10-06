"""Rebuild 105-name driver coverage from preserved FX and US-risk maps. PLAYBOOK_READY stays false."""
from __future__ import annotations

from typing import Any

from research.korea_semiconductor_driver_response_v1 import (
    ES_BROAD_PROXY_LEADS,
    FX_INTRADAY_DIRECT,
    FX_OVERNIGHT_SECTORS,
    NQ_MARKET_MEDIATED,
    TECH_FOCUS,
    US_BROAD_RISK_PREOPEN_SECTORS,
)


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def preserved_driver_map(bind: dict[str, Any]) -> dict[str, Any]:
    fx = dict(bind.get("fx_response_map_v1") or {})
    nq_ans = dict(bind.get("nqes_answers") or {})
    return {
        "FX_OVERNIGHT_OPEN": {
            "primary_preserved": list(FX_OVERNIGHT_SECTORS),
            "source": "FX_RESPONSE_MAP_V1",
            "true_cme_futures": False,
            "native_fx_feed": True,
        },
        "FX_INTRADAY_DIRECT": {
            "symbols": list(FX_INTRADAY_DIRECT),
            "source": "FX_RESPONSE_MAP_V1",
            "ENTRY_rules_built": False,
        },
        "US_BROAD_RISK_PREOPEN": {
            "sectors": list(US_BROAD_RISK_PREOPEN_SECTORS),
            "nq_incremental_to_es_electrical_machinery": False,
            "label": "PROXY_NOT_CME_FUTURES",
            "not_true_cme_proof": True,
        },
        "ES_BROAD_RISK_PROXY_LEADS": {
            "symbols": list(ES_BROAD_PROXY_LEADS),
            "evidence_class": "PROXY_SUPPORTED",
            "not": "TRUE_CME_FUTURES_PROVEN",
        },
        "NQ_MARKET_MEDIATED": {
            "symbols": list(NQ_MARKET_MEDIATED),
            "role": "DESCRIPTIVE_ONLY",
            "causal_intraday_nq_playbook_group": False,
        },
        "parent_nqes_verdict": nq_ans.get("VERDICT"),
        "stocks_with_at_least_one_usable_external_driver_preserved": nq_ans.get(
            "stocks_with_at_least_one_causally_usable_external_driver_n"
        ),
        "not_all_16_equally_proven": True,
        "fx_map_id": fx.get("ANALYSIS_ID"),
    }


def coverage_105(bind: dict[str, Any]) -> list[dict[str, Any]]:
    sector_of = _sector_of(bind)
    nq_cls = dict(bind.get("nqes_stock_classes") or {})
    fx_direct = set(FX_INTRADAY_DIRECT)
    es_proxy = set(ES_BROAD_PROXY_LEADS)
    nq_mkt = set(NQ_MARKET_MEDIATED)
    rows = []
    for sym in list(bind.get("symbols") or sorted(sector_of)):
        sec = sector_of.get(sym) or (nq_cls.get(sym) or {}).get("sector") or ""
        nq = dict(nq_cls.get(sym) or {})
        overnight = sec in FX_OVERNIGHT_SECTORS
        fx_intraday = sym in fx_direct
        es = sym in es_proxy
        nq_desc = sym in nq_mkt
        sim = str(nq.get("class") or "") == "SIMULTANEOUS_ONLY" or str(nq.get("nq_lead_lag") or "").startswith("simultaneous")
        flags = []
        if fx_intraday:
            flags.append("TRUE_DRIVER_PROVEN")
            flags.append("INTRADAY_CAUSAL")
        if overnight:
            flags.append("OVERNIGHT_CAUSAL")
        if es:
            flags.append("PROXY_SUPPORTED_CAUSAL")
            flags.append("INTRADAY_CAUSAL")
        if nq_desc:
            flags.append("SIMULTANEOUS_ONLY")
        if sim and not fx_intraday and not es:
            flags.append("SIMULTANEOUS_ONLY")
        flags = list(dict.fromkeys(flags))
        if not flags:
            flags = ["UNEXPLAINED"]
        if fx_intraday:
            primary, pclass, timing, minutes, native = "USDJPY", "TRUE_DRIVER_PROVEN", "INTRADAY", 1, "NATIVE_FX_JETTA"
        elif es:
            primary, pclass, timing, minutes, native = "ES_PROXY", "PROXY_SUPPORTED_CAUSAL", "INTRADAY", 1, "PROXY_NOT_CME_FUTURES"
        elif overnight:
            primary, pclass, timing, minutes, native = "USDJPY", "OVERNIGHT_CAUSAL", "OVERNIGHT", None, "NATIVE_FX_JETTA"
        elif nq_desc:
            primary, pclass, timing, minutes, native = "US_RISK_PROXY_DESCRIPTIVE", "SIMULTANEOUS_ONLY", "INTRADAY", None, "PROXY_NOT_CME_FUTURES"
        elif sim:
            primary, pclass, timing, minutes, native = "US_RISK_SIMULTANEOUS", "SIMULTANEOUS_ONLY", "INTRADAY", None, "PROXY_NOT_CME_FUTURES"
        else:
            primary, pclass, timing, minutes, native = "UNKNOWN", "UNEXPLAINED", None, None, None
        secondary = None
        if fx_intraday and es:
            secondary = "ES_PROXY"
        elif fx_intraday and overnight:
            secondary = "USDJPY_OVERNIGHT"
        elif es and overnight:
            secondary = "USDJPY_OVERNIGHT"
        usable = bool(fx_intraday or es or overnight)
        rows.append(
            {
                "symbol": sym,
                "sector": sec,
                "tech_focus": sym in TECH_FOCUS,
                "PRIMARY_DRIVER": primary,
                "SECONDARY_DRIVER": secondary,
                "EVIDENCE_CLASS": pclass,
                "EVIDENCE_FLAGS": flags,
                "OVERNIGHT_OR_INTRADAY": timing,
                "LEAD_MINUTES": minutes,
                "MARKET_MEDIATED": nq.get("class") == "GLOBAL_MARKET_MEDIATED",
                "SECTOR_MEDIATED": nq.get("class") == "SECTOR_MEDIATED",
                "DIRECT": bool(fx_intraday or es),
                "PROXY_OR_NATIVE": native,
                "D1_D4_STABLE": bool(fx_intraday or es or overnight),
                "PLAYBOOK_READY": False,
                "usable_external_driver": usable,
                "nqes_class": nq.get("class"),
            }
        )
    return rows


def quality_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [r for r in rows if r.get("usable_external_driver")]

    def with_flag(flag: str) -> list[str]:
        out = []
        for r in rows:
            flags = list(r.get("EVIDENCE_FLAGS") or [])
            if flag in flags:
                out.append(str(r.get("symbol")))
        return out

    by: dict[str, list[str]] = {}
    for r in usable:
        by.setdefault(str(r.get("EVIDENCE_CLASS") or ""), []).append(str(r.get("symbol")))
    fx_direct = with_flag("TRUE_DRIVER_PROVEN")
    es_proxy = with_flag("PROXY_SUPPORTED_CAUSAL")
    return {
        "usable_n": len(usable),
        "pool_n": len(rows),
        "TRUE_DRIVER_PROVEN_intraday_fx": fx_direct,
        "PROXY_SUPPORTED_CAUSAL_es": es_proxy,
        "OVERNIGHT_CAUSAL_fx": with_flag("OVERNIGHT_CAUSAL"),
        "primary_class_TRUE_DRIVER_PROVEN": by.get("TRUE_DRIVER_PROVEN") or [],
        "primary_class_PROXY_SUPPORTED_CAUSAL": by.get("PROXY_SUPPORTED_CAUSAL") or [],
        "overlap_fx_intraday_and_es_proxy": sorted(set(fx_direct) & set(es_proxy)),
        "not_all_16_equally_proven": True,
        "PLAYBOOK_READY_any": any(bool(r.get("PLAYBOOK_READY")) for r in rows),
    }


def unexplained_groups(rows: list[dict[str, Any]], sector_of: dict[str, str]) -> dict[str, Any]:
    unexplained = [r for r in rows if r.get("EVIDENCE_CLASS") == "UNEXPLAINED" or (not r.get("usable_external_driver") and r.get("EVIDENCE_CLASS") in ("UNEXPLAINED", "SIMULTANEOUS_ONLY"))]
    tech_unex = [r["symbol"] for r in rows if r.get("tech_focus") and not r.get("usable_external_driver")]
    banks = [s for s, sec in sector_of.items() if sec in ("銀行業", "保険業") and s in {r["symbol"] for r in unexplained}]
    resource_secs = ("鉱業", "石油･石炭製品", "卸売業", "海運業", "空運業")
    resources = [s for s, sec in sector_of.items() if sec in resource_secs and s in {r["symbol"] for r in unexplained}]
    return {
        "tech_focus_without_usable_korea_or_prior_intraday": tech_unex,
        "tech_still_unexplained_intraday": tech_unex,
        "banks_insurers_unexplained_n": len(banks),
        "resource_trading_unexplained_n": len(resources),
        "simultaneous_or_unexplained_n": len(unexplained),
        "did_not_force_105_coverage": True,
        "UNKNOWN_IDIOSYNCRATIC_valid": True,
    }
