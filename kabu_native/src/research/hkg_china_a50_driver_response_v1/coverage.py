"""105-name driver coverage, evidence quality, tech missing-data ledger. PLAYBOOK_READY stays false."""
from __future__ import annotations

from typing import Any

from research.hkg_china_a50_driver_response_v1 import (
    ES_BROAD_PROXY_LEADS,
    FX_INTRADAY_DIRECT,
    FX_OVERNIGHT_SECTORS,
    NQ_MARKET_MEDIATED,
    TECH_FOCUS,
    US_BROAD_RISK_PREOPEN_SECTORS,
)

USABLE_CHINA_CLASSES = ("HKG_DIRECT_LEAD", "CHINA_A50_DIRECT_LEAD", "GREATER_CHINA_COMMON_LEAD")


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def preserved_driver_map(bind: dict[str, Any]) -> dict[str, Any]:
    fx = dict(bind.get("fx_response_map_v1") or {})
    return {
        "FX_OVERNIGHT_OPEN": {"primary_preserved": list(FX_OVERNIGHT_SECTORS), "source": "FX_RESPONSE_MAP_V1"},
        "FX_INTRADAY_DIRECT": {"symbols": list(FX_INTRADAY_DIRECT), "source": "FX_RESPONSE_MAP_V1"},
        "ES_BROAD_RISK_PROXY_LEADS": {"symbols": list(ES_BROAD_PROXY_LEADS), "evidence_class": "PROXY_CAUSAL_SUPPORTED", "not": "TRUE_CME_FUTURES_PROVEN"},
        "US_BROAD_RISK_PREOPEN": {"sectors": list(US_BROAD_RISK_PREOPEN_SECTORS), "label": "PROXY_NOT_CME_FUTURES"},
        "NQ_MARKET_MEDIATED": {"symbols": list(NQ_MARKET_MEDIATED), "role": "DESCRIPTIVE_ONLY"},
        "korea_not_tested": True,
        "korea_not_economic_null": True,
        "not_all_16_equally_proven": True,
        "fx_map_id": fx.get("ANALYSIS_ID"),
        "TECH_DRIVER_BLOCKER_OPEN": True,
    }


def tech_missing_ledger() -> list[dict[str, Any]]:
    return [
        {"driver": "KOSPI200", "source": "KRX", "status": "PAID_BLOCKED", "purchase": False},
        {"driver": "Samsung_005930", "source": "KRX", "status": "PAID_BLOCKED", "purchase": False},
        {"driver": "SK_hynix_000660", "source": "KRX", "status": "PAID_BLOCKED", "purchase": False},
        {
            "driver": "Taiwan_native_semiconductor",
            "source": "TWSE_Data_EShop_paid_OpenAPI_insufficient",
            "status": "HISTORICAL_INTRADAY_NOT_CURRENTLY_AVAILABLE_FREE",
            "tsm_us_adr_not_used": True,
            "purchase": False,
        },
        {"driver": "True_CME_NQ_ES", "source": "Databento_or_exchange", "status": "HISTORICAL_TRUE_FUTURES_NOT_ENTITLED", "purchase": False},
        {"driver": "NK225mini_TOPIX", "source": "prospective_capture", "status": "ACCUMULATING_SUBTRACK", "purchase": False},
    ]


def coverage_105(bind: dict[str, Any], china_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sector_of = _sector_of(bind)
    nq_cls = dict(bind.get("nqes_stock_classes") or {})
    china = {str(r.get("symbol")): r for r in china_rows}
    fx_direct = set(FX_INTRADAY_DIRECT)
    es_proxy = set(ES_BROAD_PROXY_LEADS)
    rows = []
    for sym in list(bind.get("symbols") or sorted(sector_of)):
        sec = sector_of.get(sym) or (nq_cls.get(sym) or {}).get("sector") or ""
        nq = dict(nq_cls.get(sym) or {})
        ch = dict(china.get(sym) or {})
        overnight = sec in FX_OVERNIGHT_SECTORS
        fx_intraday = sym in fx_direct
        es = sym in es_proxy
        china_usable = bool(ch.get("usable_before_stock_move") and ch.get("class") in USABLE_CHINA_CLASSES)
        china_class = str(ch.get("class") or "")
        if fx_intraday:
            primary, quality, timing, minutes, source, native = "USDJPY", "NATIVE_CAUSAL_PROVEN", "INTRADAY", 1, "JETTA_USDJPY", "NATIVE"
            secondary = "ES_PROXY" if es else (ch.get("class") if china_usable else None)
        elif china_usable:
            primary, quality, timing, minutes, source, native = china_class, "PROXY_CAUSAL_SUPPORTED", "INTRADAY", 1, "JETTA_HKG_CHI_PROXY", "PROXY"
            secondary = "ES_PROXY" if es else ("USDJPY_OVERNIGHT" if overnight else None)
        elif es:
            primary, quality, timing, minutes, source, native = "ES_PROXY", "PROXY_CAUSAL_SUPPORTED", "INTRADAY", 1, "JETTA_USA500_PROXY", "PROXY"
            secondary = "USDJPY_OVERNIGHT" if overnight else None
        elif overnight:
            primary, quality, timing, minutes, source, native = "USDJPY", "OVERNIGHT_CAUSAL_PROVEN", "OVERNIGHT", None, "JETTA_USDJPY", "NATIVE"
            secondary = None
        elif china_class == "SIMULTANEOUS_COMMON_NEWS" or str(nq.get("class") or "") == "SIMULTANEOUS_ONLY":
            primary, quality, timing, minutes, source, native = "NONE", "SIMULTANEOUS_ONLY", "INTRADAY", None, None, None
            secondary = None
        elif sym in TECH_FOCUS:
            primary, quality, timing, minutes, source, native = "UNKNOWN", "SOURCE_UNAVAILABLE", None, None, "KOREA_TAIWAN_CME_BLOCKED", None
            secondary = None
        else:
            primary, quality, timing, minutes, source, native = "UNKNOWN", "UNEXPLAINED", None, None, None, None
            secondary = None
        usable = bool(fx_intraday or es or overnight or china_usable)
        rows.append(
            {
                "symbol": sym,
                "sector": sec,
                "tech_focus": sym in TECH_FOCUS,
                "primary_driver": primary,
                "secondary_driver": secondary,
                "driver_source": source,
                "native_or_proxy": native,
                "overnight_or_intraday": timing,
                "strict_lead": bool(china_usable or fx_intraday or es),
                "lead_minutes": minutes,
                "market_mediated": china_class == "JAPAN_MARKET_MEDIATED" or nq.get("class") == "GLOBAL_MARKET_MEDIATED",
                "sector_mediated": china_class == "JAPAN_SECTOR_MEDIATED" or nq.get("class") == "SECTOR_MEDIATED",
                "D1_D4": ch.get("d1_d4_hkg") if china_usable else (True if fx_intraday or es or overnight else None),
                "evidence_quality": quality,
                "unresolved_reason": None if usable else (quality if quality in ("SOURCE_UNAVAILABLE", "SIMULTANEOUS_ONLY", "UNEXPLAINED") else "UNEXPLAINED"),
                "PLAYBOOK_READY": False,
                "usable_external_driver": usable,
                "china_class": china_class or None,
                "nqes_class": nq.get("class"),
            }
        )
    return rows


def quality_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(str(r.get("evidence_quality") or ""), []).append(str(r.get("symbol")))
    usable = [r for r in rows if r.get("usable_external_driver")]
    return {
        "usable_n": len(usable),
        "pool_n": len(rows),
        "NATIVE_CAUSAL_PROVEN": by.get("NATIVE_CAUSAL_PROVEN") or [],
        "PROXY_CAUSAL_SUPPORTED": by.get("PROXY_CAUSAL_SUPPORTED") or [],
        "OVERNIGHT_CAUSAL_PROVEN": by.get("OVERNIGHT_CAUSAL_PROVEN") or [],
        "SIMULTANEOUS_ONLY": by.get("SIMULTANEOUS_ONLY") or [],
        "SOURCE_UNAVAILABLE": by.get("SOURCE_UNAVAILABLE") or [],
        "UNEXPLAINED": by.get("UNEXPLAINED") or [],
        "not_all_usable_equally_proven": True,
        "PLAYBOOK_READY_any": False,
    }
