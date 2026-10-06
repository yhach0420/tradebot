"""Composition decision. V1 immutable. No strategy. No PnL. No future-return selection."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.aligned_historical_panel_v1.universe_bind import bind_frozen_universe
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.fixed_daytrade_universe_composition_audit_v1 import (
    ANALYSIS_ID,
    CASE_APPROVED,
    CASE_DAILY,
    CASE_EXPAND,
    CASE_GAPS,
    CASE_REDESIGN,
    CASE_UNIVERSE,
    CORR_REDUNDANT,
    FUTURE_RETURN_USED,
    LIVE_20260914_CHANGED,
    NEXT_PANEL,
    NEXT_REDESIGN,
    NEXT_V11,
    PAPER_CHANGED,
    PAPER_RESULT_USED,
    PNL_USED,
    RUNTIME_CHANGED,
    STRATEGY_SEARCH_STARTED,
    UNIVERSE_CHANGED,
    V1_1_ID,
    V1_REWRITTEN,
    WINDOW_FIRST,
    WINDOW_LAST,
)
from research.fixed_daytrade_universe_composition_audit_v1.expansion import propose_v1_1
from research.fixed_daytrade_universe_composition_audit_v1.factors import (
    CORE_BOTH_SIDES_REQUIRED,
    MISSING_ROLE_SPECS,
    ROLES,
    side_coverage,
)
from research.fixed_daytrade_universe_composition_audit_v1.load_daily import (
    candidate_meta,
    load_master_index,
    load_topix,
    load_universe_daily,
)
from research.fixed_daytrade_universe_composition_audit_v1.proxies import series_proxies
from research.fixed_daytrade_universe_composition_audit_v1.redundancy import (
    aligned_returns,
    pair_correlations,
    redundancy_clusters,
    sector_relative_returns,
    topix_betas,
)
from research.fixed_daytrade_universe_composition_audit_v1.stats import median, percentile
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE


def decide(*, universe_ok: bool, daily_ok: bool, coverage: list[dict[str, Any]], necessary_missing: list) -> dict[str, Any]:
    if not universe_ok:
        return {
            "CASE": "UNIVERSE",
            "VERDICT": CASE_UNIVERSE,
            "NEXT": NEXT_REDESIGN,
            "reason": "universe_not_frozen",
            "INTERPRETATION": "Frozen universe artifacts were not verified. Composition audit does not invent a universe.",
        }
    if not daily_ok:
        return {
            "CASE": "DAILY",
            "VERDICT": CASE_DAILY,
            "NEXT": NEXT_PANEL,
            "reason": "official_60d_daily_unavailable",
            "INTERPRETATION": "Official trailing 60d daily cache/fetch failed. No yfinance substitute.",
        }
    core = [r for r in coverage if r["factor"] in CORE_BOTH_SIDES_REQUIRED]
    core_missing = [r for r in core if not r["both_sides"]]
    thin = [r for r in core if r["status"] == "BOTH_SIDES_THIN"]
    if core_missing:
        return {
            "CASE": "REDESIGN" if len(core_missing) >= 3 else "EXPAND",
            "VERDICT": CASE_REDESIGN if len(core_missing) >= 3 else CASE_EXPAND,
            "NEXT": NEXT_REDESIGN if len(core_missing) >= 3 else NEXT_V11,
            "reason": "core_factor_missing_side:" + ",".join(r["factor"] for r in core_missing),
            "INTERPRETATION": (
                "A core external driver is missing a positive or negative sleeve in the frozen 45. "
                "V1 is not rewritten. A V1.1 candidate is proposed only if expansion can restore both sides."
            ),
        }
    if necessary_missing:
        return {
            "CASE": "EXPAND",
            "VERDICT": CASE_EXPAND,
            "NEXT": NEXT_V11,
            "reason": "necessary_roles_missing:" + ",".join(r["role"] for r in necessary_missing),
            "INTERPRETATION": (
                "Core factor sides exist, but insurance / non-bank / utility sleeves that distinguish "
                "those drivers from idiosyncratic clusters are absent. V1 stays frozen. V1.1 is proposal-only."
            ),
        }
    if thin:
        return {
            "CASE": "GAPS",
            "VERDICT": CASE_GAPS,
            "NEXT": NEXT_PANEL,
            "reason": "core_sides_thin:" + ",".join(r["factor"] for r in thin),
            "INTERPRETATION": "Both sides exist for core drivers, but some sleeves are thin. V1 remains usable with documented gaps.",
        }
    return {
        "CASE": "APPROVED",
        "VERDICT": CASE_APPROVED,
        "NEXT": NEXT_PANEL,
        "reason": "factor_coverage_sufficient",
        "INTERPRETATION": "Frozen 45 has both-side coverage for core external drivers. Minute add-on remains the panel blocker.",
    }


def build_report_body() -> dict[str, Any]:
    universe = bind_frozen_universe()
    meta = candidate_meta()
    master = load_master_index()
    daily = load_universe_daily(list(universe.get("symbols") or [])) if universe.get("ok") else {"ok": False, "missing": [], "by_symbol": {}}
    topix = load_topix() if daily.get("ok") else {"ok": False, "closes": {}, "reason": "skipped_no_equity_daily"}

    mapping_rows: list[dict[str, Any]] = []
    proxy_rows: list[dict[str, Any]] = []
    ret_map: dict[str, list[tuple[str, float]]] = {}
    va_med: dict[str, float | None] = {}
    sector_of: dict[str, str] = {}
    tse33_counter: Counter[str] = Counter()
    s17_counter: Counter[str] = Counter()

    for sym in list(universe.get("symbols") or []):
        m = dict((master.get("by_symbol") or {}).get(sym) or {})
        c = dict(meta.get(sym) or {})
        s33 = str(m.get("sector33_name") or c.get("tse33_phase0") or "")
        s17 = str(m.get("sector17_name") or "")
        sector_of[sym] = s33
        tse33_counter[s33 or "UNKNOWN"] += 1
        s17_counter[s17 or "UNKNOWN"] += 1
        mapping_rows.append(
            {
                "symbol": sym,
                "name_en": m.get("name_en") or c.get("name_en"),
                "name_ja": m.get("name_ja") or c.get("name_ja"),
                "tse33_code": m.get("sector33"),
                "tse33_name": s33,
                "topix17_code": m.get("sector17"),
                "topix17_name": s17,
                "scale_category": m.get("scale_category"),
                "market_name": m.get("market_name"),
                "sector_bucket_phase0": c.get("sector_bucket"),
                "roles": ROLES.get(sym) or {},
            }
        )
        pack = dict((daily.get("by_symbol") or {}).get(sym) or {})
        px = series_proxies(pack.get("bars") or [])
        sm = dict(px.get("summary") or {})
        va_vals = [v for v in px.get("va") or [] if v is not None]
        va_med[sym] = median(va_vals) if va_vals else None
        proxy_rows.append(
            {
                "symbol": sym,
                "tse33_name": s33,
                "proxy_n": px.get("proxy_n"),
                "median_range_over_prev_close": (sm.get("range_over_prev_close") or {}).get("median"),
                "median_abs_body_over_prev_close": (sm.get("abs_body_over_prev_close") or {}).get("median"),
                "median_gap_over_prev_close": (sm.get("gap_over_prev_close") or {}).get("median"),
                "median_abs_gap": (sm.get("gap_over_prev_close") or {}).get("median_abs"),
                "median_open_to_high": (sm.get("open_to_high_over_prev_close") or {}).get("median"),
                "median_open_to_low": (sm.get("open_to_low_over_prev_close") or {}).get("median"),
                "p80_abs_range": (sm.get("range_over_prev_close") or {}).get("p80_abs"),
                "median_va": va_med[sym],
                "p20_va": percentile(va_vals, 0.20) if va_vals else None,
            }
        )
        ret_map[sym] = list(zip(px.get("dates") or [], px.get("returns_adj") or []))

    coverage = side_coverage(list(universe.get("symbols") or []))
    dates, rets = aligned_returns(ret_map)
    pairs = pair_correlations(rets, sector_of=sector_of) if rets else []
    rel = sector_relative_returns(rets, sector_of=sector_of) if rets else {}
    rel_pairs = pair_correlations(rel, sector_of=sector_of) if rel else []
    rel_lookup = {(r["a"], r["b"]): r.get("corr") for r in rel_pairs}
    for rec in pairs:
        rec["sector_relative_corr"] = rel_lookup.get((rec["a"], rec["b"]))
    clusters = redundancy_clusters(pairs, median_va=va_med)
    betas = topix_betas(rets, dates, dict(topix.get("closes") or {}))
    beta_map = {r["symbol"]: r for r in betas.get("rows") or []}
    for row in proxy_rows:
        b = beta_map.get(row["symbol"]) or {}
        row["topix_beta"] = b.get("topix_beta")
        row["topix_r"] = b.get("topix_r")

    present_s33 = {str(r.get("tse33_code") or "") for r in mapping_rows}
    missing_role_rows = []
    for spec in MISSING_ROLE_SPECS:
        in_u = [r["symbol"] for r in mapping_rows if str(r.get("tse33_code") or "") == str(spec["tse33_code"])]
        missing_role_rows.append(
            {
                **spec,
                "universe_n": len(in_u),
                "universe_symbols": in_u,
                "absent_from_v1": len(in_u) == 0,
            }
        )
    necessary_missing = [r for r in missing_role_rows if r["absent_from_v1"] and r["necessary_for_driver_id"]]
    useful_missing = [r for r in missing_role_rows if r["absent_from_v1"] and not r["necessary_for_driver_id"]]

    decision = decide(
        universe_ok=bool(universe.get("ok")),
        daily_ok=bool(daily.get("ok")),
        coverage=coverage,
        necessary_missing=necessary_missing,
    )
    expand = decision.get("VERDICT") in {CASE_EXPAND, CASE_REDESIGN}
    v11 = propose_v1_1(
        frozen_symbols=list(universe.get("symbols") or []),
        master_by=dict(master.get("by_symbol") or {}),
        necessary_missing=necessary_missing,
        useful_missing=useful_missing if decision.get("VERDICT") == CASE_EXPAND else [],
        redundant_clusters=clusters,
        expand=bool(expand and decision.get("VERDICT") == CASE_EXPAND),
    )
    high_pairs = [p for p in pairs if p.get("corr") is not None and (p["corr"] >= 0.70 or p.get("redundant_flag"))]
    return {
        "universe_bind": universe,
        "window": {"first": WINDOW_FIRST, "last": WINDOW_LAST, "n": 60, "common_return_n": len(dates)},
        "master": {"ok": master.get("ok"), "n": master.get("n"), "from_cache": master.get("from_cache")},
        "daily": {"ok": daily.get("ok"), "missing": daily.get("missing")},
        "topix": {
            "ok": topix.get("ok"),
            "n": topix.get("n"),
            "raw_keys": topix.get("raw_keys"),
            "reason": topix.get("reason"),
            "from_cache": topix.get("from_cache"),
        },
        "tse33_counts": [{"tse33": k, "n": v} for k, v in sorted(tse33_counter.items(), key=lambda kv: (-kv[1], kv[0]))],
        "topix17_counts": [{"topix17": k, "n": v} for k, v in sorted(s17_counter.items(), key=lambda kv: (-kv[1], kv[0]))],
        "mapping": mapping_rows,
        "proxies": proxy_rows,
        "factor_coverage": coverage,
        "missing_roles": missing_role_rows,
        "necessary_missing_roles": necessary_missing,
        "useful_missing_roles": useful_missing,
        "redundancy_pairs_high": high_pairs,
        "redundancy_clusters": clusters,
        "topix_beta": betas,
        "v1_1_candidate": v11,
        "decision": {
            **decision,
            "strategy_search_started": False,
            "pnl_used": False,
            "future_return_used": False,
            "universe_changed": False,
            "v1_rewritten": False,
            "v1_1_id": V1_1_ID if expand else None,
        },
        "live_20260914": {"trading_date": LIVE_TRADING_DATE, "plan_changed": bool(LIVE_20260914_CHANGED)},
        "policy": {
            "corr_redundant": CORR_REDUNDANT,
            "roles_are_a_priori_not_fitted": True,
            "contemporaneous_corr_allowed": True,
            "future_return_forbidden": True,
        },
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    u = dict(report.get("universe_bind") or {})
    d = dict(report.get("decision") or {})
    cov = list(report.get("factor_coverage") or [])
    v11 = dict(report.get("v1_1_candidate") or {})
    live = dict(report.get("live_20260914") or {})
    by_f = {r["factor"]: r for r in cov}
    return {
        "frozen_universe_verified": bool(u.get("verified")),
        "symbol_n": u.get("symbol_n"),
        "universe_sha": u.get("identity_sha256"),
        "v1_rewritten": bool(V1_REWRITTEN),
        "tse33_n": len(report.get("tse33_counts") or []),
        "topix17_n": len(report.get("topix17_counts") or []),
        "rates_both_sides": bool((by_f.get("JAPAN_RATES") or {}).get("both_sides")),
        "oil_both_sides": bool((by_f.get("OIL") or {}).get("both_sides")),
        "jpy_both_sides": bool((by_f.get("USDJPY") or {}).get("both_sides")),
        "us_tech_both_sides": bool((by_f.get("US_TECH_NQ") or {}).get("both_sides")),
        "china_both_sides": bool((by_f.get("CHINA_HK") or {}).get("both_sides")),
        "necessary_missing_roles": [r.get("role") for r in report.get("necessary_missing_roles") or []],
        "v1_1_add_n": v11.get("add_n"),
        "v1_1_adds": [a.get("symbol") for a in v11.get("add") or []],
        "strategy_search_started": bool(STRATEGY_SEARCH_STARTED),
        "pnl_used": bool(PNL_USED),
        "future_return_used": bool(FUTURE_RETURN_USED),
        "paper_result_used": bool(PAPER_RESULT_USED),
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_changed": bool(PAPER_CHANGED),
        "live_20260914_changed": bool(live.get("plan_changed")),
        "submit_cancel_live": "0/0/0",
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "universe_changed": bool(UNIVERSE_CHANGED),
    }


assert ANALYSIS_ID == "AUDIT_FIXED_DAYTRADE_UNIVERSE_COMPOSITION_V1"
assert LIVE_TRADING_DATE == "20260914"
