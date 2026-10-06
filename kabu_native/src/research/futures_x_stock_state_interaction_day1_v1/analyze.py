"""Frozen interaction grid. No threshold search. No ENTRY/EXIT. No ranking tape."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1 import HORIZON_LABEL, HORIZONS_SEC
from research.futures_x_stock_state_interaction_day1_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CONTEXT_SPECS,
    KIND,
    MATERIAL_BPS,
    MIN_CONTEXT_CLOCK_N,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    PARENT_ID,
    SELECTORS,
    TERCILE_N,
    TRADING_DATE,
    UNIVERSE_N,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
)
from research.futures_x_stock_state_interaction_day1_v1.engine import (
    build_interaction_table,
    context_bucket_of,
    mean_of,
    median_of,
)
from research.futures_x_stock_state_interaction_day1_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")
CONC_CLOCKS = {"09:30", "09:50"}


def _same_sign(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    if a == 0 or b == 0:
        return False
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def _clock_metric(clock: dict[str, Any], selector: str, horizon: str, metric: str) -> Optional[float]:
    pack = ((clock.get("selectors") or {}).get(selector) or {}).get("horizons") or {}
    cell = (pack.get(horizon) or {}).get(metric) or {}
    v = cell.get("spread")
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clock_top_abs(clock: dict[str, Any], selector: str, horizon: str, metric: str, stat: str) -> Optional[float]:
    pack = ((clock.get("selectors") or {}).get(selector) or {}).get("horizons") or {}
    cell = (pack.get(horizon) or {}).get(metric) or {}
    top = cell.get("top") or {}
    v = top.get(stat)
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clock_bottom_abs(clock: dict[str, Any], selector: str, horizon: str, metric: str, stat: str) -> Optional[float]:
    pack = ((clock.get("selectors") or {}).get(selector) or {}).get("horizons") or {}
    cell = (pack.get(horizon) or {}).get(metric) or {}
    bot = cell.get("bottom") or {}
    v = bot.get(stat)
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _market_mean(clock: dict[str, Any], horizon: str, metric: str) -> Optional[float]:
    pack = ((clock.get("market") or {}).get(horizon) or {}).get(metric) or {}
    v = pack.get("mean")
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _valid_selector_clocks(clocks: list[dict[str, Any]], selector: str, horizon: str) -> list[dict[str, Any]]:
    out = []
    for c in clocks:
        if _clock_metric(c, selector, horizon, "MID_RETURN_BPS") is None:
            continue
        ranked_n = int(((c.get("selectors") or {}).get(selector) or {}).get("ranked_n") or 0)
        if ranked_n < 2 * TERCILE_N:
            continue
        out.append(c)
    return out


def _agg_spreads(clocks: list[dict[str, Any]], selector: str, horizon: str) -> dict[str, Any]:
    mid = [_clock_metric(c, selector, horizon, "MID_RETURN_BPS") for c in clocks]
    lng = [_clock_metric(c, selector, horizon, "LONG_EXEC_MARKOUT_BPS") for c in clocks]
    sh = [_clock_metric(c, selector, horizon, "SHORT_EXEC_MARKOUT_BPS") for c in clocks]
    top_mid = [_clock_top_abs(c, selector, horizon, "MID_RETURN_BPS", "mean") for c in clocks]
    bot_mid = [_clock_bottom_abs(c, selector, horizon, "MID_RETURN_BPS", "mean") for c in clocks]
    top_long_m = [_clock_top_abs(c, selector, horizon, "LONG_EXEC_MARKOUT_BPS", "mean") for c in clocks]
    top_short_m = [_clock_top_abs(c, selector, horizon, "SHORT_EXEC_MARKOUT_BPS", "mean") for c in clocks]
    return {
        "n": len(clocks),
        "mid_spread": mean_of(mid),
        "long_spread": mean_of(lng),
        "short_spread": mean_of(sh),
        "top_mid": mean_of(top_mid),
        "bottom_mid": mean_of(bot_mid),
        "top_long_mean": mean_of(top_long_m),
        "top_long_median": median_of(top_long_m),
        "top_short_mean": mean_of(top_short_m),
        "top_short_median": median_of(top_short_m),
        "clock_mid_spreads": {c["clock"]: _clock_metric(c, selector, horizon, "MID_RETURN_BPS") for c in clocks},
    }


def _drop_clocks(clocks: list[dict[str, Any]], drop: set[str]) -> list[dict[str, Any]]:
    return [c for c in clocks if c.get("clock") not in drop]


def _top_contrib_clocks(clocks: list[dict[str, Any]], selector: str, horizon: str, k: int) -> list[str]:
    scored: list[tuple[float, str]] = []
    for c in clocks:
        v = _clock_metric(c, selector, horizon, "MID_RETURN_BPS")
        if v is None:
            continue
        scored.append((float(v), str(c["clock"])))
    mean_v = mean_of([s[0] for s in scored])
    if mean_v is not None and mean_v < 0:
        scored.sort(key=lambda x: x[0])
    else:
        scored.sort(key=lambda x: -x[0])
    return [name for _v, name in scored[: int(k)]]


def _symbol_contrib(clocks: list[dict[str, Any]], selector: str, horizon: str) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}
    for c in clocks:
        sel = (c.get("selectors") or {}).get(selector) or {}
        members = {m["symbol"]: m for m in c.get("members") or []}
        for row in sel.get("top") or []:
            m = members.get(row["symbol"])
            if m is None:
                continue
            v = ((m.get(horizon) or {}).get("MID_RETURN_BPS"))
            if v is None:
                continue
            acc[row["symbol"]] = acc.get(row["symbol"], 0.0) + float(v)
        for row in sel.get("bottom") or []:
            m = members.get(row["symbol"])
            if m is None:
                continue
            v = ((m.get(horizon) or {}).get("MID_RETURN_BPS"))
            if v is None:
                continue
            acc[row["symbol"]] = acc.get(row["symbol"], 0.0) - float(v)
    items = sorted(acc.items(), key=lambda kv: -abs(kv[1]))
    return items


def _recompute_spread_excluding_symbols(
    clocks: list[dict[str, Any]], selector: str, horizon: str, drop: set[str]
) -> Optional[float]:
    spreads: list[float] = []
    for c in clocks:
        members = {m["symbol"]: m for m in c.get("members") or []}
        sel = (c.get("selectors") or {}).get(selector) or {}
        top_xs: list[float] = []
        bot_xs: list[float] = []
        for row in sel.get("top") or []:
            if row["symbol"] in drop:
                continue
            v = ((members.get(row["symbol"]) or {}).get(horizon) or {}).get("MID_RETURN_BPS")
            if v is not None:
                top_xs.append(float(v))
        for row in sel.get("bottom") or []:
            if row["symbol"] in drop:
                continue
            v = ((members.get(row["symbol"]) or {}).get(horizon) or {}).get("MID_RETURN_BPS")
            if v is not None:
                bot_xs.append(float(v))
        if not top_xs or not bot_xs:
            continue
        spreads.append(sum(top_xs) / len(top_xs) - sum(bot_xs) / len(bot_xs))
    return mean_of(spreads)


def _eval_row(
    *,
    selector: str,
    family: str,
    bucket: str,
    horizon: str,
    all_clocks: list[dict[str, Any]],
    ctx_clocks_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    base_clocks = _valid_selector_clocks(all_clocks, selector, horizon)
    ctx_clocks = _valid_selector_clocks(ctx_clocks_raw, selector, horizon)
    base = _agg_spreads(base_clocks, selector, horizon)
    inter = _agg_spreads(ctx_clocks, selector, horizon)
    market = [_market_mean(c, horizon, "MID_RETURN_BPS") for c in ctx_clocks]
    lift = None
    if inter["mid_spread"] is not None and base["mid_spread"] is not None:
        lift = float(inter["mid_spread"]) - float(base["mid_spread"])
    long_lift = None
    if inter["long_spread"] is not None and base["long_spread"] is not None:
        long_lift = float(inter["long_spread"]) - float(base["long_spread"])
    short_lift = None
    if inter["short_spread"] is not None and base["short_spread"] is not None:
        short_lift = float(inter["short_spread"]) - float(base["short_spread"])
    exec_ok = False
    if lift is not None:
        if long_lift is not None and _same_sign(lift, long_lift):
            exec_ok = True
        if short_lift is not None and _same_sign(lift, short_lift):
            exec_ok = True
    early = _valid_selector_clocks([c for c in ctx_clocks if c.get("block") == "EARLY"], selector, horizon)
    late = _valid_selector_clocks([c for c in ctx_clocks if c.get("block") == "LATE"], selector, horizon)
    early_sp = _agg_spreads(early, selector, horizon)["mid_spread"]
    late_sp = _agg_spreads(late, selector, horizon)["mid_spread"]
    internal_hold = _same_sign(early_sp, late_sp)
    drop1_names = _top_contrib_clocks(ctx_clocks, selector, horizon, 1)
    drop2_names = _top_contrib_clocks(ctx_clocks, selector, horizon, 2)
    drop1 = _agg_spreads(_drop_clocks(ctx_clocks, set(drop1_names)), selector, horizon)
    drop2 = _agg_spreads(_drop_clocks(ctx_clocks, set(drop2_names)), selector, horizon)
    drop1_lift = None
    if drop1["mid_spread"] is not None and base["mid_spread"] is not None:
        drop1_lift = float(drop1["mid_spread"]) - float(base["mid_spread"])
    drop2_lift = None
    if drop2["mid_spread"] is not None and base["mid_spread"] is not None:
        drop2_lift = float(drop2["mid_spread"]) - float(base["mid_spread"])
    remain_conc = _valid_selector_clocks(_drop_clocks(ctx_clocks, CONC_CLOCKS), selector, horizon)
    conc_agg = _agg_spreads(remain_conc, selector, horizon)
    conc_lift = None
    if conc_agg["mid_spread"] is not None and base["mid_spread"] is not None:
        conc_lift = float(conc_agg["mid_spread"]) - float(base["mid_spread"])
    only_conc = bool(ctx_clocks) and all(c.get("clock") in CONC_CLOCKS for c in ctx_clocks)
    conc_fail = bool(
        only_conc
        or (
            any(c.get("clock") in CONC_CLOCKS for c in ctx_clocks)
            and (
                conc_agg["n"] < MIN_CONTEXT_CLOCK_N
                or (inter["mid_spread"] is not None and conc_agg["mid_spread"] is not None and not _same_sign(inter["mid_spread"], conc_agg["mid_spread"]))
                or (lift is not None and (conc_lift is None or conc_lift < MATERIAL_BPS))
            )
        )
    )
    contrib = _symbol_contrib(ctx_clocks, selector, horizon)
    top_sym = [name for name, _v in contrib[:1]]
    top2_sym = [name for name, _v in contrib[:2]]
    drop_sym1 = _recompute_spread_excluding_symbols(ctx_clocks, selector, horizon, set(top_sym))
    drop_sym2 = _recompute_spread_excluding_symbols(ctx_clocks, selector, horizon, set(top2_sym))
    clock_support = int(len(ctx_clocks_raw))
    ranking_support = int(inter["n"])
    insufficient = clock_support < MIN_CONTEXT_CLOCK_N or ranking_support < MIN_CONTEXT_CLOCK_N
    material = bool(lift is not None and float(lift) >= MATERIAL_BPS)
    clock_ex_ok = bool(
        inter["mid_spread"] is not None
        and drop1["mid_spread"] is not None
        and drop2["mid_spread"] is not None
        and _same_sign(inter["mid_spread"], drop1["mid_spread"])
        and _same_sign(inter["mid_spread"], drop2["mid_spread"])
        and drop1["n"] >= 1
        and drop2["n"] >= 1
    )
    sym_ex_ok = bool(
        inter["mid_spread"] is not None
        and drop_sym1 is not None
        and drop_sym2 is not None
        and _same_sign(inter["mid_spread"], drop_sym1)
        and _same_sign(inter["mid_spread"], drop_sym2)
    )
    abs_long_pos = bool(inter["top_long_mean"] is not None and float(inter["top_long_mean"]) > 0)
    abs_short_pos = bool(inter["top_short_mean"] is not None and float(inter["top_short_mean"]) > 0)
    abs_exec_pos = bool(abs_long_pos or abs_short_pos)
    strong_abs = bool(
        (inter["top_long_mean"] is not None and inter["top_long_median"] is not None and float(inter["top_long_mean"]) > 0 and float(inter["top_long_median"]) > 0)
        or (inter["top_short_mean"] is not None and inter["top_short_median"] is not None and float(inter["top_short_mean"]) > 0 and float(inter["top_short_median"]) > 0)
    )
    candidate = bool(
        (not insufficient)
        and material
        and exec_ok
        and internal_hold
        and clock_ex_ok
        and (not conc_fail)
        and sym_ex_ok
    )
    return {
        "selector": selector,
        "family": family,
        "bucket": bucket,
        "horizon": horizon,
        "context_clock_n": clock_support,
        "ranking_clock_n": ranking_support,
        "insufficient_clock_support": insufficient,
        "base_a_mid_spread": base["mid_spread"],
        "base_a_long_spread": base["long_spread"],
        "base_a_short_spread": base["short_spread"],
        "base_b_market_mid": mean_of(market),
        "interaction_mid_spread": inter["mid_spread"],
        "interaction_long_spread": inter["long_spread"],
        "interaction_short_spread": inter["short_spread"],
        "top_mid": inter["top_mid"],
        "bottom_mid": inter["bottom_mid"],
        "lift_mid": lift,
        "lift_long": long_lift,
        "lift_short": short_lift,
        "executable_improvement": exec_ok,
        "top_long_mean": inter["top_long_mean"],
        "top_long_median": inter["top_long_median"],
        "top_short_mean": inter["top_short_mean"],
        "top_short_median": inter["top_short_median"],
        "early_n": len(early),
        "late_n": len(late),
        "early_spread": early_sp,
        "late_spread": late_sp,
        "internal_hold": internal_hold,
        "drop_top_clock": drop1_names,
        "drop_top_clock_spread": drop1["mid_spread"],
        "drop_top_clock_lift": drop1_lift,
        "drop_top_clock_n": drop1["n"],
        "drop_top2_clocks": drop2_names,
        "drop_top2_clock_spread": drop2["mid_spread"],
        "drop_top2_clock_lift": drop2_lift,
        "drop_top2_clock_n": drop2["n"],
        "clock_exclusion_hold": clock_ex_ok,
        "drop_0930_0950_n": conc_agg["n"],
        "drop_0930_0950_spread": conc_agg["mid_spread"],
        "drop_0930_0950_lift": conc_lift,
        "concentrated_0930_0950": conc_fail,
        "top_symbol": top_sym,
        "top2_symbols": top2_sym,
        "drop_top_symbol_spread": drop_sym1,
        "drop_top2_symbols_spread": drop_sym2,
        "symbol_exclusion_hold": sym_ex_ok,
        "absolute_executable_mean_positive": abs_exec_pos,
        "strong_absolute": strong_abs,
        "material_lift": material,
        "candidate": candidate,
        "clock_mid_spreads": inter["clock_mid_spreads"],
        "ctx_clocks": [c["clock"] for c in ctx_clocks],
    }


def _bucket_clocks(clocks: list[dict[str, Any]], family: str, bucket: str) -> list[dict[str, Any]]:
    return [c for c in clocks if context_bucket_of(c, family) == bucket]


def _context_counts(clocks: list[dict[str, Any]]) -> dict[str, Any]:
    nk_up = [c for c in clocks if c.get("nk_sign") == "UP"]
    nk_down = [c for c in clocks if c.get("nk_sign") == "DOWN"]
    agr = {k: [c for c in clocks if c.get("agreement_180") == k] for k in ("BOTH_UP", "MIXED", "BOTH_DOWN")}
    aln = {k: [c for c in clocks if c.get("alignment_180") == k] for k in ("ALIGNED", "DISAGREED")}
    return {
        "NK180_UP": len(nk_up),
        "NK180_DOWN": len(nk_down),
        "AGREEMENT_BOTH_UP": len(agr["BOTH_UP"]),
        "AGREEMENT_MIXED": len(agr["MIXED"]),
        "AGREEMENT_BOTH_DOWN": len(agr["BOTH_DOWN"]),
        "ALIGN_ALIGNED": len(aln["ALIGNED"]),
        "ALIGN_DISAGREED": len(aln["DISAGREED"]),
        "clocks": [
            {
                "clock": c["clock"],
                "block": c["block"],
                "nk_ret_180": c.get("nk_ret_180"),
                "tx_ret_180": c.get("tx_ret_180"),
                "cash_ew_past_ret_180": c.get("cash_ew_past_ret_180"),
                "nk_sign": c.get("nk_sign"),
                "agreement_180": c.get("agreement_180"),
                "alignment_180": c.get("alignment_180"),
            }
            for c in clocks
        ],
    }


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cands = [r for r in rows if r.get("candidate")]
    pool = cands if cands else rows
    def key(r: dict[str, Any]) -> tuple[float, float, float]:
        lift = float(r["lift_mid"]) if r.get("lift_mid") is not None else -1e18
        spr = abs(float(r["interaction_mid_spread"])) if r.get("interaction_mid_spread") is not None else -1.0
        lng = float(r["top_long_mean"]) if r.get("top_long_mean") is not None else -1e18
        return (lift, spr, lng)
    return max(pool, key=key) if pool else {}


def _mechanism(best: dict[str, Any], case: str) -> str:
    sel = best.get("selector")
    fam = best.get("family")
    bkt = best.get("bucket")
    hz = best.get("horizon")
    lift = best.get("lift_mid")
    base = best.get("base_a_mid_spread")
    inter = best.get("interaction_mid_spread")
    top = best.get("top_mid")
    bot = best.get("bottom_mid")
    mkt = best.get("base_b_market_mid")
    if case == CASE_C:
        return (
            f"On 20260911, frozen stock selectors do not gain a material TOP-BOTTOM spread "
            f"inside frozen futures context versus selector-only BASE A. Closest row is "
            f"{sel} × {fam}={bkt} at {hz}: interaction {inter} bps vs BASE A {base} bps "
            f"(lift {lift} bps). Market-wide move inside the bucket is {mkt} bps and is not "
            f"treated as selection edge."
        )
    return (
        f"At the same frozen futures state {fam}={bkt}, ranking 48 stocks by {sel} "
        f"separates later {hz} MID: TOP {top} bps minus BOTTOM {bot} bps = {inter} bps, "
        f"which is {lift} bps above the all-clock selector BASE A of {base} bps. "
        f"The 48-stock market mean in that context is {mkt} bps, so the result is the "
        f"cross-sectional gap, not the market move."
    )


def run_interaction_check(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    table = build_interaction_table(native_root=root, day=day)
    clocks = table["clocks"]
    counts = _context_counts(clocks)
    rows: list[dict[str, Any]] = []
    for selector in SELECTORS:
        for family, buckets in CONTEXT_SPECS:
            for bucket in buckets:
                ctx = _bucket_clocks(clocks, family, bucket)
                for h in HORIZONS_SEC:
                    rows.append(
                        _eval_row(
                            selector=selector,
                            family=family,
                            bucket=bucket,
                            horizon=HORIZON_LABEL[h],
                            all_clocks=clocks,
                            ctx_clocks_raw=ctx,
                        )
                    )
    best = _pick_best(rows)
    passing = [r for r in rows if r.get("candidate")]
    exec_pass = [r for r in passing if r.get("absolute_executable_mean_positive")]
    if exec_pass:
        case = CASE_A
        verdict = VERDICT_A
        nxt = NEXT_A
        best = _pick_best(exec_pass)
    elif passing:
        case = CASE_B
        verdict = VERDICT_B
        nxt = NEXT_B
        best = _pick_best(passing)
    else:
        case = CASE_C
        verdict = VERDICT_C
        nxt = NEXT_C
    orders = live_order_counts()
    thesis = bool(case in (CASE_A, CASE_B) and best.get("candidate"))
    answers = {
        "1_clock_n": table["anchor_clock_n"],
        "2_stock_n": table["symbol_n"],
        "3_leakage_n": table["future_leakage_n"],
        "4_NK180_UP_clock_n": counts["NK180_UP"],
        "5_NK180_DOWN_clock_n": counts["NK180_DOWN"],
        "6_agreement_bucket_ns": {
            "BOTH_UP": counts["AGREEMENT_BOTH_UP"],
            "MIXED": counts["AGREEMENT_MIXED"],
            "BOTH_DOWN": counts["AGREEMENT_BOTH_DOWN"],
        },
        "7_alignment_bucket_ns": {
            "ALIGNED": counts["ALIGN_ALIGNED"],
            "DISAGREED": counts["ALIGN_DISAGREED"],
        },
        "8_selectors_tested": list(SELECTORS),
        "9_total_frozen_comparisons": len(rows),
        "10_best_interaction_selector": best.get("selector"),
        "11_best_futures_context": f"{best.get('family')}={best.get('bucket')}",
        "12_best_horizon": best.get("horizon"),
        "13_TOP_basket_MID": best.get("top_mid"),
        "14_BOTTOM_basket_MID": best.get("bottom_mid"),
        "15_TOP_BOTTOM_MID_spread": best.get("interaction_mid_spread"),
        "16_selector_only_BASE_spread": best.get("base_a_mid_spread"),
        "17_incremental_interaction_lift": best.get("lift_mid"),
        "18_LONG_TOP_absolute_mean_median": {
            "mean": best.get("top_long_mean"),
            "median": best.get("top_long_median"),
        },
        "19_SHORT_TOP_absolute_mean_median": {
            "mean": best.get("top_short_mean"),
            "median": best.get("top_short_median"),
        },
        "20_executable_improvement": best.get("executable_improvement"),
        "21_EARLY_spread": best.get("early_spread"),
        "22_LATE_spread": best.get("late_spread"),
        "23_internal_hold": best.get("internal_hold"),
        "24_drop_top_clock_result": {
            "dropped": best.get("drop_top_clock"),
            "spread": best.get("drop_top_clock_spread"),
            "lift": best.get("drop_top_clock_lift"),
            "n": best.get("drop_top_clock_n"),
        },
        "25_drop_top2_clocks_result": {
            "dropped": best.get("drop_top2_clocks"),
            "spread": best.get("drop_top2_clock_spread"),
            "lift": best.get("drop_top2_clock_lift"),
            "n": best.get("drop_top2_clock_n"),
        },
        "26_drop_top_symbol_result": {
            "dropped": best.get("top_symbol"),
            "spread": best.get("drop_top_symbol_spread"),
        },
        "27_drop_top2_symbols_result": {
            "dropped": best.get("top2_symbols"),
            "spread": best.get("drop_top2_symbols_spread"),
        },
        "28_0930_0950_concentration": best.get("concentrated_0930_0950"),
        "29_context_clock_support_sufficient": (not bool(best.get("insufficient_clock_support"))),
        "30_exact_mechanism_sentence": _mechanism(best, case),
        "31_ENTRY_thesis_plausible": thesis,
        "32_ENTRY_built": False,
        "33_EXIT_built": False,
        "34_Runtime_changed": False,
        "35_Paper_changed": False,
        "36_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "37_CASE": case,
        "38_VERDICT": verdict,
        "39_NEXT": nxt,
    }
    decision = {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "candidate_n": len(passing),
        "executable_candidate_n": len(exec_pass),
        "best": {k: v for k, v in best.items() if k != "clock_mid_spreads"},
        "ENTRY_built": False,
        "EXIT_built": False,
        "Runtime_changed": False,
        "Paper_changed": False,
        "submit_cancel_live": answers["36_submit_cancel_live"],
        "true_oos": False,
        "certified": False,
        "used_ranking": False,
        "used_breadth": False,
        "futures_only_retest": False,
    }
    return {
        "analysis_id": ANALYSIS_ID,
        "parent_id": PARENT_ID,
        "kind": KIND,
        "trading_date": day,
        "built_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "universe_n": UNIVERSE_N,
        "tercile_n": TERCILE_N,
        "material_bps": MATERIAL_BPS,
        "selectors": list(SELECTORS),
        "context_families": [name for name, _b in CONTEXT_SPECS],
        "future_leakage_n": table["future_leakage_n"],
        "anchor_clock_n": table["anchor_clock_n"],
        "symbol_n": table["symbol_n"],
        "stock_anchor_n": table["stock_anchor_n"],
        "context_counts": counts,
        "grid": rows,
        "passing": passing,
        "best": best,
        "answers": answers,
        "decision": decision,
        "clocks": counts["clocks"],
        "symbols": table["symbols"],
    }
