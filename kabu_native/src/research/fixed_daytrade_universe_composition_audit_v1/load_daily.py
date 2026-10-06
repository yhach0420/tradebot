"""Load frozen-universe official daily cache, listing master, optional TOPIX and expansion dailies."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_v1 import ENDPOINT_DAILY, ENDPOINT_MASTER
from research.fixed_daytrade_universe_v1.jquants_client import fetch_paginated, try_load_cache
from research.fixed_daytrade_universe_v1.schema import finite_number, map_daily_row, map_master_row, symbol4, yyyymmdd
from research.fixed_daytrade_universe_composition_audit_v1 import ENDPOINT_TOPIX, WINDOW_FIRST, WINDOW_LAST
from research.fixed_universe_historical_foundation_v1.universe import CANDIDATES


def _iso(yyyymmdd_s: str) -> str:
    s = str(yyyymmdd_s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def candidate_meta() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for code, en, ja, bucket, tse33, liq, px in CANDIDATES:
        out[code] = {
            "name_en": en,
            "name_ja": ja,
            "sector_bucket": bucket,
            "tse33_phase0": tse33,
            "liquidity_class": liq,
            "price_range_class": px,
        }
    return out


def load_master_index() -> dict[str, Any]:
    pack = try_load_cache(kind="listed_master", stem="master_asof_20260911")
    if pack is None:
        return {"ok": False, "reason": "master_cache_missing", "by_symbol": {}}
    by: dict[str, dict[str, Any]] = {}
    for raw in pack.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        try:
            mapped = map_master_row(raw)
        except Exception:
            continue
        if not mapped.get("common_stock_domestic"):
            continue
        name_en = str(mapped.get("name_en") or "")
        if "preferred" in name_en.lower():
            continue
        by[mapped["symbol"]] = mapped
    return {"ok": True, "n": len(by), "by_symbol": by, "meta": pack.get("meta"), "from_cache": True}


def load_symbol_daily(symbol: str) -> dict[str, Any]:
    stem = f"daily_{symbol}_{WINDOW_FIRST}_{WINDOW_LAST}"
    pack = try_load_cache(kind="daily_bars", stem=stem)
    from_cache = True
    if pack is None:
        try:
            pack = fetch_paginated(
                path=ENDPOINT_DAILY,
                params={"code": symbol, "from": _iso(WINDOW_FIRST), "to": _iso(WINDOW_LAST)},
                kind="daily_bars",
                stem=stem,
            )
            from_cache = bool(pack.get("from_cache"))
        except Exception as exc:
            return {"symbol": symbol, "ok": False, "from_cache": False, "bars": [], "n": 0, "reason": f"{type(exc).__name__}"}
    bars: list[dict[str, Any]] = []
    for raw in pack.get("rows") or []:
        mapped = map_daily_row(raw)
        if mapped["date"] < WINDOW_FIRST or mapped["date"] > WINDOW_LAST:
            continue
        mapped["adj_close"] = finite_number(raw.get("AdjC"))
        if mapped["adj_close"] is None:
            mapped["adj_close"] = mapped["close"]
        bars.append(mapped)
    bars.sort(key=lambda r: r["date"])
    return {
        "symbol": symbol,
        "ok": bool(bars),
        "from_cache": from_cache,
        "bars": bars,
        "n": len(bars),
        "meta": pack.get("meta"),
    }


def load_universe_daily(symbols: list[str]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for sym in symbols:
        got = load_symbol_daily(sym)
        if not got["ok"]:
            missing.append(sym)
        by[sym] = got
    return {"by_symbol": by, "missing": missing, "ok": not missing}


def load_topix() -> dict[str, Any]:
    stem = f"topix_daily_{WINDOW_FIRST}_{WINDOW_LAST}"
    try:
        pack = fetch_paginated(
            path=ENDPOINT_TOPIX,
            params={"from": _iso(WINDOW_FIRST), "to": _iso(WINDOW_LAST)},
            kind="indices_topix",
            stem=stem,
        )
    except Exception as exc:
        return {"ok": False, "reason": f"topix_fetch:{type(exc).__name__}", "closes": {}}
    sample = pack.get("sample") if isinstance(pack.get("sample"), dict) else None
    keys = list(sample.keys()) if sample else []
    required = {"Date", "C"}
    if sample and not required <= set(keys):
        return {"ok": False, "reason": f"topix_schema_mismatch:{keys}", "raw_keys": keys, "closes": {}}
    closes: dict[str, float] = {}
    for raw in pack.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        try:
            d = yyyymmdd(raw["Date"])
        except Exception:
            continue
        c = finite_number(raw.get("C"))
        if c is None:
            continue
        closes[d] = c
    return {
        "ok": len(closes) >= 20,
        "raw_keys": keys,
        "n": len(closes),
        "closes": closes,
        "from_cache": pack.get("from_cache"),
        "meta": pack.get("meta"),
        "reason": None if len(closes) >= 20 else "topix_too_short",
    }


assert ENDPOINT_MASTER.endswith("master")
assert symbol4("72030") == "7203"
