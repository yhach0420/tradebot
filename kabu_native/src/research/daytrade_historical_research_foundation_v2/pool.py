"""Prime common-stock research pool. Liquidity top80 ∪ activity top40. No PnL. V1 is seed-only."""
from __future__ import annotations

from typing import Any

from research.aligned_historical_panel_v1.universe_bind import bind_frozen_universe
from research.daytrade_historical_research_foundation_v2 import (
    ACTIVITY_TOP_N,
    LIQUIDITY_TOP_N,
    PRIME_MKT,
    V1_1_ADOPTED,
    V1_1_REJECTED_ID,
    WINDOW_FIRST,
    WINDOW_LAST,
    WINDOW_N,
)
from research.daytrade_historical_research_foundation_v2.fetch_cache import _iso, fetch_pages
from research.daytrade_historical_research_foundation_v2.isolation import REF
from research.fixed_daytrade_universe_v1 import ENDPOINT_DAILY, MIN_ACTIVE_SESSION_N, MIN_MEDIAN_VA_JPY, MIN_P20_VA_JPY, MIN_SESSION_COVERAGE
from research.fixed_daytrade_universe_v1.jquants_client import try_load_cache
from research.fixed_daytrade_universe_v1.liquidity import liquidity_pass, metrics_aligned_to_sessions
from research.fixed_daytrade_universe_v1.schema import finite_number, map_daily_row, map_master_row, symbol4, yyyymmdd


def _master_by_symbol(pack: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not pack:
        return out
    for raw in pack.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        try:
            m = map_master_row(raw)
        except Exception:
            continue
        if not m.get("common_stock_domestic"):
            continue
        if "preferred" in str(m.get("name_en") or "").lower():
            continue
        out[m["symbol"]] = m
    return out


def listing_continuity(*, start: dict[str, dict[str, Any]], end: dict[str, dict[str, Any]], symbol: str) -> bool:
    a = start.get(symbol) or {}
    b = end.get(symbol) or {}
    return bool(a.get("common_stock_domestic") and b.get("common_stock_domestic") and str(b.get("market") or "") == PRIME_MKT)


def range_bps(prev_close: float | None, high: float | None, low: float | None) -> float | None:
    if prev_close in {None, 0.0} or high is None or low is None:
        return None
    return (high - low) / prev_close * 10_000.0


def load_session_days() -> list[str]:
    from research.fixed_daytrade_universe_v1.calendar_window import build_official_window

    cal = try_load_cache(kind="calendar", stem="calendar_20260501_20260913")
    if cal is None:
        return []
    win = build_official_window(list(cal.get("rows") or []))
    return list(win.get("days") or []) if win.get("ok") else []


def fetch_daily_by_date(session_days: list[str]) -> dict[str, Any]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    sample_keys: list[str] = []
    from_cache_n = 0
    net_n = 0
    for day in session_days:
        cache = REF / "daily_by_date" / f"daily_{day}.json"
        got = fetch_pages(path=ENDPOINT_DAILY, params={"date": _iso(day)}, cache_path=cache)
        if not got.get("ok"):
            return {"ok": False, "reason": got.get("reason"), "status": got.get("status"), "day": day, "by_symbol": by_symbol}
        if got.get("from_cache"):
            from_cache_n += 1
        else:
            net_n += 1
        print(
            f"DAILY_DATE {day} cache={got.get('from_cache')} rows={len(got.get('rows') or [])}",
            flush=True,
        )
        if not sample_keys:
            rows0 = list(got.get("rows") or [])
            if rows0:
                sample_keys = list(rows0[0].keys())
        for raw in got.get("rows") or []:
            try:
                mapped = map_daily_row(raw)
            except Exception:
                continue
            by_symbol.setdefault(mapped["symbol"], []).append(mapped)
    return {
        "ok": True,
        "by_symbol": by_symbol,
        "symbol_n": len(by_symbol),
        "from_cache_days": from_cache_n,
        "network_days": net_n,
        "sample_keys": sample_keys,
        "yfinance_used": False,
    }


def build_pool(*, session_days: list[str], daily: dict[str, Any]) -> dict[str, Any]:
    v1 = bind_frozen_universe()
    v1_syms = set(v1.get("symbols") or [])
    start = _master_by_symbol(try_load_cache(kind="listed_master", stem=f"master_asof_{WINDOW_FIRST}"))
    end = _master_by_symbol(try_load_cache(kind="listed_master", stem=f"master_asof_{WINDOW_LAST}"))
    expected_n = len(session_days)
    eligible: list[dict[str, Any]] = []
    for sym, rows in (daily.get("by_symbol") or {}).items():
        m_end = end.get(sym) or {}
        if str(m_end.get("market") or "") != PRIME_MKT:
            continue
        if not listing_continuity(start=start, end=end, symbol=sym):
            continue
        rows_sorted = sorted(rows, key=lambda r: r["date"])
        aligned = metrics_aligned_to_sessions(session_days=session_days, rows=rows_sorted)
        ok, reasons = liquidity_pass(aligned)
        if not ok:
            continue
        ranges: list[float] = []
        prev = None
        closes: list[float] = []
        for r in rows_sorted:
            c = finite_number(r.get("close"))
            h = finite_number(r.get("high"))
            l = finite_number(r.get("low"))
            if prev is not None:
                rb = range_bps(prev, h, l)
                if rb is not None:
                    ranges.append(rb)
            if c is not None:
                prev = c
                closes.append(c)
        med_range = float(sorted(ranges)[len(ranges) // 2]) if ranges else None
        last_close = closes[-1] if closes else None
        eligible.append(
            {
                "symbol": sym,
                "name_en": m_end.get("name_en"),
                "name_ja": m_end.get("name_ja"),
                "tse33_name": m_end.get("sector33_name"),
                "tse33_code": m_end.get("sector33"),
                "topix17_name": m_end.get("sector17_name"),
                "scale_category": m_end.get("scale_category"),
                "median_va": aligned.get("median_daily_trading_value_60d"),
                "p20_va": aligned.get("p20_daily_trading_value_60d"),
                "median_vo": aligned.get("median_daily_volume_60d"),
                "session_coverage": aligned.get("session_coverage"),
                "active_session_n": aligned.get("active_session_n"),
                "median_range_bps": med_range,
                "last_close": last_close,
                "notional_100": (last_close * 100.0) if last_close is not None else None,
                "in_v1_seed": sym in v1_syms,
                "special_excluded_9983_6861": False,
                "liquidity_pass": True,
                "listing_continuity_pass": True,
            }
        )
    eligible.sort(key=lambda r: (-(r.get("median_va") or 0.0), r["symbol"]))
    liq_set = eligible[: int(LIQUIDITY_TOP_N)]
    by_range = sorted([r for r in eligible if r.get("median_range_bps") is not None], key=lambda r: (-r["median_range_bps"], r["symbol"]))
    act_set = by_range[: int(ACTIVITY_TOP_N)]
    union_map: dict[str, dict[str, Any]] = {}
    for r in liq_set:
        rec = dict(r)
        rec["in_liquidity_top80"] = True
        rec["in_activity_top40"] = False
        union_map[r["symbol"]] = rec
    for r in act_set:
        if r["symbol"] in union_map:
            union_map[r["symbol"]]["in_activity_top40"] = True
        else:
            rec = dict(r)
            rec["in_liquidity_top80"] = False
            rec["in_activity_top40"] = True
            union_map[r["symbol"]] = rec
    union = sorted(union_map.values(), key=lambda r: (-(r.get("median_va") or 0.0), r["symbol"]))
    return {
        "v1_seed": {
            "verified": bool(v1.get("verified")),
            "symbol_n": v1.get("symbol_n"),
            "identity_sha256": v1.get("identity_sha256"),
            "treated_as_final_trade_universe": False,
            "treated_as_runtime_universe": False,
            "treated_as_kabu_registration_set": False,
            "kept_as_baseline_seed": True,
            "modified": False,
        },
        "v1_1_candidate": {
            "id": V1_1_REJECTED_ID,
            "adopted": bool(V1_1_ADOPTED),
            "reason": "trade_names_were_confounded_with_context_sensors",
        },
        "eligible_before_ranking_n": len(eligible),
        "liquidity_top80_n": len(liq_set),
        "activity_top40_n": len(act_set),
        "union_n": len(union),
        "did_not_force_n": True,
        "did_not_special_exclude_9983_6861": True,
        "9983_in_union": any(r["symbol"] == "9983" for r in union),
        "6861_in_union": any(r["symbol"] == "6861" for r in union),
        "v1_seed_in_union_n": sum(1 for r in union if r.get("in_v1_seed")),
        "eligible": eligible,
        "liquidity_set": liq_set,
        "activity_set": act_set,
        "union": union,
        "floors": {
            "min_active_session_n": MIN_ACTIVE_SESSION_N,
            "min_session_coverage": MIN_SESSION_COVERAGE,
            "min_median_va": MIN_MEDIAN_VA_JPY,
            "min_p20_va": MIN_P20_VA_JPY,
            "prime_mkt": PRIME_MKT,
        },
        "panel_conditioned_as_of_202609": True,
        "claim_selectable_in_2025": False,
        "window": {"first": WINDOW_FIRST, "last": WINDOW_LAST, "n": WINDOW_N},
        "expected_n": expected_n,
    }


assert symbol4("99830") == "9983"
assert yyyymmdd("2026-09-11") == "20260911"
assert V1_1_ADOPTED is False
