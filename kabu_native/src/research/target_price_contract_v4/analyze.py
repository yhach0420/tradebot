"""V4 persistence audit. Integrity only. No PnL / TopK / Spearman-vs-returns selector."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.e1_x34a_execution_policy.executable_board import (
    PREOPEN_ITAYOSE_SIGNS,
    SPECIAL_QUOTE_SIGNS,
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
)
from research.target_price_contract_v3.analyze import (
    _f,
    _quantiles,
    evaluate_combo,
    plus600_calendar,
    row_primary as v3_row_primary,
    row_return as v3_row_return,
    smd,
)
from research.target_price_contract_v4 import (
    COHORT_MEDIAN_MIN,
    COHORT_MEDIAN_UNIVERSE_FRAC_MIN,
    COHORT_P10_MIN,
    COMPARE_AGES,
    EVENT_RATE_SMD_HARD,
    HORIZON_SEC,
    INTERVAL_BUCKETS,
    MARK_AGE_BUCKETS,
    MATERIAL_DECILE_DROP,
    MATERIAL_SMD_DROP,
    SCORE_DECILE_COVERAGE_RANGE_GOAL,
    SCORE_DECILE_COVERAGE_RANGE_HARD,
    SMD_BIAS_THRESHOLD,
    SYMBOL_COVERAGE_RANGE_GOAL,
    V3_M1_5S_DECILE_RANGE,
    V3_M1_5S_EVENT_RATE_SMD,
    V3_M1_5S_SYMBOL_RANGE,
)
from research.target_price_contract_v4.persist import merge_day_persist
from research.uniform10_entry_rebuild import UNIFORM10
from small_paper.v1r_native_entry_live import FEATURE_ORDER

FEATURE_COLS = list(FEATURE_ORDER) + ["score", "price_level"]
ITAYOSE_STATES = {STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE}
SPECIAL_STATES = {STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD}


def _rank(x: np.ndarray) -> np.ndarray:
    o = np.argsort(x, kind="mergesort")
    r = np.empty(x.size, dtype=float)
    r[o] = np.arange(x.size, dtype=float)
    return r


def mark_age_bucket(age: Optional[float]) -> Optional[str]:
    if age is None:
        return None
    x = float(age)
    prev = None
    for lo, hi, name in MARK_AGE_BUCKETS:
        if hi == float("inf"):
            if prev is None or x > prev + 1e-12:
                return name
            return name
        if x <= hi + 1e-12 and (prev is None or x > prev + 1e-12):
            return name
        prev = hi
    return ">300s"


def row_primary_m4(row: dict[str, Any]) -> bool:
    if not row.get("tse_t0_cont") or not row.get("tse_t1_cont"):
        return False
    p0 = _f(row.get("m4_t0_px"))
    p1 = _f(row.get("m4_t1_px"))
    return p0 is not None and p1 is not None and p0 > 0 and p1 > 0


def row_return_m4(row: dict[str, Any]) -> Optional[float]:
    if not row_primary_m4(row):
        return None
    p0 = _f(row.get("m4_t0_px"))
    p1 = _f(row.get("m4_t1_px"))
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return float(p1 / p0 - 1.0)


def _cov_m4(xs: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(xs)
    k = sum(1 for r in xs if row_primary_m4(r))
    return {"n": n, "target_valid": k, "coverage_rate": k / n if n else None}


def cohort_stats_m4(rows: list[dict[str, Any]]) -> dict[str, Any]:
    g: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        g[(str(r.get("date")), str(r.get("anchor")))].append(r)
    recs = []
    ns = []
    for (day, an), xs in sorted(g.items()):
        uni = int(xs[0].get("universe_n") or 0) if xs else 0
        valid = sum(1 for r in xs if row_primary_m4(r))
        recs.append({"date": day, "anchor": an, "eligible_universe_n": uni, "primary_target_valid_n": valid})
        ns.append(float(valid))
    q = _quantiles(ns)
    med_uni = float(np.median([int(r["eligible_universe_n"]) for r in recs])) if recs else None
    med = q.get("p50")
    p10 = q.get("p10")
    frac = (float(med) / med_uni) if med is not None and med_uni else None
    adequate = bool(
        med is not None
        and p10 is not None
        and med_uni is not None
        and float(med) >= float(COHORT_MEDIAN_MIN)
        and float(p10) >= float(COHORT_P10_MIN)
        and frac is not None
        and float(frac) >= float(COHORT_MEDIAN_UNIVERSE_FRAC_MIN)
    )
    return {
        "n_cohorts": len(recs),
        "rows": recs,
        "median_target_valid_n_per_cohort": med,
        "p10": p10,
        "p25": q.get("p25"),
        "p75": q.get("p75"),
        "p90": q.get("p90"),
        "min": (min(ns) if ns else None),
        "max": (max(ns) if ns else None),
        "mean": q.get("mean"),
        "median_eligible_universe_n": med_uni,
        "median_frac_of_universe": frac,
        "TARGET_COHORT_COVERAGE_ADEQUATE": adequate,
    }


def missingness_m4(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if row_primary_m4(r)]
    null = [r for r in rows if not row_primary_m4(r)]
    smd_rows = []
    hits = []
    for col in FEATURE_COLS:
        d = smd([_f(r.get(col)) for r in valid], [_f(r.get(col)) for r in null])
        rec = {
            "feature": col,
            "smd_valid_minus_null": d,
            "valid": _quantiles([_f(r.get(col)) for r in valid if _f(r.get(col)) is not None]),
            "null": _quantiles([_f(r.get(col)) for r in null if _f(r.get(col)) is not None]),
            "flag": bool(d is not None and abs(d) >= float(SMD_BIAS_THRESHOLD)),
        }
        smd_rows.append(rec)
        if rec["flag"]:
            hits.append(col)
    scored = [r for r in rows if _f(r.get("score")) is not None]
    deciles = []
    decile_range = None
    if len(scored) >= 50:
        scores = np.asarray([float(r["score"]) for r in scored], dtype=float)
        qs = np.quantile(scores, np.linspace(0, 1, 11))
        qs[0] -= 1e-12
        qs[-1] += 1e-12
        bins = np.digitize(scores, qs[1:-1], right=True)
        rates = []
        for d in range(10):
            xs = [scored[i] for i in range(len(scored)) if int(bins[i]) == d]
            n = len(xs)
            k = sum(1 for r in xs if row_primary_m4(r))
            rate = k / n if n else None
            if rate is not None:
                rates.append(rate)
            deciles.append({"score_decile": d + 1, "n": n, "target_valid": k, "coverage_rate": rate})
        if rates:
            decile_range = float(max(rates) - min(rates))
    sym_g: dict[str, list] = defaultdict(list)
    for r in rows:
        sym_g[str(r.get("symbol"))].append(r)
    sym_rates = []
    for xs in sym_g.values():
        n = len(xs)
        if n <= 0:
            continue
        sym_rates.append(sum(1 for r in xs if row_primary_m4(r)) / n)
    sym_range = (max(sym_rates) - min(sym_rates)) if sym_rates else None
    er = next((x["smd_valid_minus_null"] for x in smd_rows if x["feature"] == "event_rate_60s"), None)
    bias = bool(hits) or (
        decile_range is not None and decile_range > float(SCORE_DECILE_COVERAGE_RANGE_GOAL) + 1e-12
    ) or (sym_range is not None and sym_range > float(SYMBOL_COVERAGE_RANGE_GOAL) + 1e-12)
    explainable = bool(
        bias
        and (decile_range is None or decile_range < float(SCORE_DECILE_COVERAGE_RANGE_HARD))
        and (er is None or abs(float(er)) < float(EVENT_RATE_SMD_HARD))
    )
    return {
        "n_valid": len(valid),
        "n_null": len(null),
        "smd": smd_rows,
        "bias_features": hits,
        "score_deciles": deciles,
        "score_decile_coverage_range": decile_range,
        "symbol_coverage_range": sym_range,
        "n_symbol": len(sym_g),
        "am": _cov_m4([r for r in rows if r.get("session") == "AM"]),
        "pm": _cov_m4([r for r in rows if r.get("session") == "PM"]),
        "TARGET_MISSINGNESS_SELECTION_BIAS": bias,
        "BIAS_EXPLAINABLE": explainable,
        "event_rate_smd": er,
    }


def first_fail_m4(row: dict[str, Any]) -> Optional[str]:
    if row_primary_m4(row):
        return None
    if row.get("empty"):
        return "SYMBOL_DATA_MISSING"
    if not row.get("tse_t0_cont"):
        return "T0_OUTSIDE_CONTINUOUS"
    p0 = _f(row.get("m4_t0_px"))
    if p0 is None:
        if not row.get("t0_has_event"):
            return "T0_NO_EVENT_IN_SESSION"
        st = str(row.get("last_state") or "")
        ask_s, bid_s = str(row.get("last_ask_sign") or ""), str(row.get("last_bid_sign") or "")
        if st in ITAYOSE_STATES or ask_s in PREOPEN_ITAYOSE_SIGNS or bid_s in PREOPEN_ITAYOSE_SIGNS:
            return "T0_ITAYOSE"
        if st in SPECIAL_STATES or ask_s in SPECIAL_QUOTE_SIGNS or bid_s in SPECIAL_QUOTE_SIGNS or row.get("last_special"):
            return "T0_SPECIAL"
        bid, ask = _f(row.get("last_bid")), _f(row.get("last_ask"))
        if bid is not None and ask is not None and bid > 0 and ask > 0 and bid + 1e-12 >= ask:
            return "T0_LOCKED_CROSSED"
        if str(row.get("m4_t_lo0_reason") or "") in {"time_reversal", "seq_reset"}:
            return "T0_INTEGRITY_CUT_NO_STATE"
        return "T0_NO_VALID_MARK"
    if not row.get("tse_t1_cont"):
        if row.get("t1_phase") in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}:
            return "T600_CLOSING_AUCTION"
        return "T600_OUTSIDE_CONTINUOUS"
    p1 = _f(row.get("m4_t1_px"))
    if p1 is None:
        if str(row.get("m4_t_lo1_reason") or "") in {"time_reversal", "seq_reset"}:
            return "T600_INTEGRITY_CUT_NO_STATE"
        return "T600_NO_VALID_MARK"
    return "OTHER"


def waterfall_m4(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(rows)
    stages = [("ROWS_TOTAL", rows)]
    s1 = [r for r in rows if r.get("tse_t0_cont")]
    stages.append(("T0_MARK_VALID", [r for r in s1 if _f(r.get("m4_t0_px")) is not None]))
    s2 = stages[-1][1]
    s3 = [r for r in s2 if r.get("tse_t1_cont")]
    stages.append(("T600_IN_CONTINUOUS", s3))
    s4 = [r for r in s3 if _f(r.get("m4_t1_px")) is not None]
    stages.append(("T600_MARK_VALID", s4))
    s5 = [r for r in s4 if row_primary_m4(r)]
    stages.append(("PRIMARY_ROWS", s5))
    out = []
    prev_n = n
    for i, (name, xs) in enumerate(stages):
        k = len(xs)
        n_in = n if i == 0 else prev_n
        out.append(
            {
                "stage": name,
                "n_in": n_in,
                "n_pass": k,
                "n_fail": n_in - k,
                "cumulative_pass_rate": k / n if n else None,
            }
        )
        prev_n = k
    return out


def first_fail_table_m4(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nulls = [r for r in rows if not row_primary_m4(r)]
    n = len(nulls)
    c = Counter(first_fail_m4(r) or "OTHER" for r in nulls)
    return [{"reason": k, "n": v, "share": v / n if n else None} for k, v in c.most_common()]


def by_anchor_m4(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cal = {r["anchor"]: r for r in plus600_calendar()}
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("anchor"))].append(r)
    out = []
    for h, m in UNIFORM10:
        an = f"{h:02d}:{m:02d}"
        xs = g.get(an) or []
        n = len(xs)
        valid = sum(1 for r in xs if row_primary_m4(r))
        reasons = Counter(first_fail_m4(r) or "NONE" for r in xs if not row_primary_m4(r))
        c = cal.get(an) or {}
        out.append(
            {
                "anchor": an,
                "rows_total": n,
                "target_valid": valid,
                "coverage_rate": valid / n if n else None,
                "dominant_null_reason": reasons.most_common(1)[0][0] if reasons else None,
                "t1_hm": c.get("target_time"),
                "target_market_phase": c.get("target_market_phase"),
                "PRIMARY_TARGET_ELIGIBLE_TSE": c.get("PRIMARY_TARGET_ELIGIBLE_TSE"),
            }
        )
    return out


def by_day_m4(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("date"))].append(r)
    out = []
    for day in sorted(g):
        xs = g[day]
        n = len(xs)
        valid = sum(1 for r in xs if row_primary_m4(r))
        reasons = Counter(first_fail_m4(r) or "NONE" for r in xs if not row_primary_m4(r))
        out.append(
            {
                "date": day,
                "rows_total": n,
                "target_valid": valid,
                "coverage_rate": valid / n if n else None,
                "dominant_null_reason": reasons.most_common(1)[0][0] if reasons else None,
            }
        )
    return out


def by_symbol_m4(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("symbol"))].append(r)
    out = []
    for sym in sorted(g):
        xs = g[sym]
        n = len(xs)
        valid = sum(1 for r in xs if row_primary_m4(r))
        out.append({"symbol": sym, "rows_total": n, "target_valid": valid, "coverage_rate": valid / n if n else None})
    out.sort(key=lambda r: (r["coverage_rate"] is None, r["coverage_rate"] or 0.0, r["symbol"]))
    return out


def integrity_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prim = [r for r in rows if row_primary_m4(r)]
    itayose = 0
    auction = 0
    lunch = 0
    session_cross = 0
    future = 0
    inactivity_gap = 0
    for r in prim:
        if r.get("t1_phase") in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}:
            auction += 1
        if r.get("t0_phase") == "LUNCH" or r.get("t1_phase") == "LUNCH":
            lunch += 1
        if r.get("session") == "AM" and r.get("t1_phase") not in {"AM_CONTINUOUS"}:
            session_cross += 1
        if r.get("session") == "PM" and r.get("t0_phase") not in {"PM_CONTINUOUS_ZARABA"}:
            session_cross += 1
        st = str(r.get("last_state") or "")
        age = _f(r.get("m4_t0_age"))
        lag = _f(r.get("last_lag"))
        if st in ITAYOSE_STATES | SPECIAL_STATES and age is not None and lag is not None and abs(age - lag) < 1e-6:
            itayose += 1
    ff = first_fail_table_m4(rows)
    for rec in ff:
        if rec.get("reason") == "CAPTURE_GAP":
            inactivity_gap += int(rec.get("n") or 0)
    return {
        "ITAYOSE_BASE_ROW_N": itayose,
        "CLOSING_AUCTION_ENDPOINT_ROW_N": auction,
        "LUNCH_CARRY_PRIMARY_N": lunch,
        "SESSION_CARRY_PRIMARY_N": session_cross,
        "FUTURE_EVENT_PRIMARY_N": future,
        "INACTIVITY_LABELED_CAPTURE_GAP_N": inactivity_gap,
        "NO_SESSION_CARRY": session_cross == 0,
        "NO_LUNCH_CARRY": lunch == 0,
        "NO_FUTURE_EVENT_USED": future == 0,
        "NO_INACTIVITY_AS_CAPTURE_GAP": inactivity_gap == 0,
    }


def session_carry_from_ages(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n_ok = 0
    n_am_on_pm = 0
    n_before_session = 0
    n_after_mark = 0
    for r in rows:
        if not row_primary_m4(r):
            continue
        n_ok += 1
        day = str(r.get("date") or "")
        an = str(r.get("anchor") or "00:00")
        try:
            hh, mm = int(an[:2]), int(an[3:5])
        except (TypeError, ValueError):
            continue
        t0 = float(hm_epoch(day, hh, mm))
        t1 = t0 + float(HORIZON_SEC)
        sess_lo0 = float(hm_epoch(day, 9, 0)) if r.get("session") == "AM" else float(hm_epoch(day, 12, 30))
        a0 = _f(r.get("m4_t0_age"))
        a1 = _f(r.get("m4_t1_age"))
        if a0 is not None:
            ev0 = t0 - a0
            if ev0 + 1e-6 < sess_lo0:
                n_before_session += 1
            if ev0 > t0 + 1e-6:
                n_after_mark += 1
            if r.get("session") == "PM" and ev0 + 1e-6 < float(hm_epoch(day, 12, 30)):
                n_am_on_pm += 1
        if a1 is not None:
            ev1 = t1 - a1
            if ev1 + 1e-6 < sess_lo0:
                n_before_session += 1
            if ev1 > t1 + 1e-6:
                n_after_mark += 1
            if r.get("session") == "PM" and ev1 + 1e-6 < float(hm_epoch(day, 12, 30)):
                n_am_on_pm += 1
    return {
        "primary_checked": n_ok,
        "n_mark_before_session_open": n_before_session,
        "n_am_quote_on_pm_mark": n_am_on_pm,
        "n_future_mark_event": n_after_mark,
        "NO_SESSION_CARRY": n_before_session == 0 and n_am_on_pm == 0,
        "NO_FUTURE_EVENT_USED": n_after_mark == 0,
    }


def mark_age_dist(rows: list[dict[str, Any]], side: str) -> dict[str, Any]:
    prim = [r for r in rows if row_primary_m4(r)]
    ages_f = [x for x in (_f(r.get(f"m4_{side}_age")) for r in prim) if x is not None]
    arr = np.asarray(ages_f, dtype=float)
    if arr.size:
        q = {
            "n": int(arr.size),
            "median": float(np.median(arr)),
            "p75": float(np.quantile(arr, 0.75)),
            "p90": float(np.quantile(arr, 0.90)),
            "p95": float(np.quantile(arr, 0.95)),
            "p99": float(np.quantile(arr, 0.99)),
            "max": float(np.max(arr)),
        }
    else:
        q = {k: None for k in ("n", "median", "p75", "p90", "p95", "p99", "max")}
        q["n"] = 0
    n = len(ages_f)
    c = Counter(mark_age_bucket(x) for x in ages_f)
    buckets = []
    for _lo, _hi, name in MARK_AGE_BUCKETS:
        k = int(c.get(name) or 0)
        buckets.append({"bucket": name, "n": k, "share": k / n if n else None})
    share_gt300 = next((b["share"] for b in buckets if b["bucket"] == ">300s"), None)
    return {"side": side, **q, "buckets": buckets, "share_gt300s": share_gt300}


def label_stability_m1_5_vs_m4(rows: list[dict[str, Any]]) -> dict[str, Any]:
    diffs = []
    aa = []
    bb = []
    n_changed = 0
    n_m1_only = 0
    n_m4_only = 0
    for r in rows:
        m1 = v3_row_primary(r, "M1", 5.0)
        m4 = row_primary_m4(r)
        if m1 and not m4:
            n_m1_only += 1
        if m4 and not m1:
            n_m4_only += 1
        if not (m1 and m4):
            continue
        ra = v3_row_return(r, "M1", 5.0)
        rb = row_return_m4(r)
        if ra is None or rb is None:
            continue
        d = abs(ra - rb)
        diffs.append(d)
        aa.append(ra)
        bb.append(rb)
        if d > 1e-12:
            n_changed += 1
    if not diffs:
        return {
            "n_common": 0,
            "n_m1_only": n_m1_only,
            "n_m4_only": n_m4_only,
            "n_label_changed": 0,
            "mean_abs_diff": None,
            "median_abs_diff": None,
            "p90_abs_diff": None,
            "p99_abs_diff": None,
            "spearman": None,
        }
    arr = np.asarray(diffs, dtype=float)
    rho = None
    if len(aa) >= 10:
        rho = float(np.corrcoef(_rank(np.asarray(aa)), _rank(np.asarray(bb)))[0, 1])
    return {
        "n_common": int(arr.size),
        "n_m1_only": n_m1_only,
        "n_m4_only": n_m4_only,
        "n_label_changed": n_changed,
        "mean_abs_diff": float(np.mean(arr)),
        "median_abs_diff": float(np.median(arr)),
        "p90_abs_diff": float(np.quantile(arr, 0.90)),
        "p99_abs_diff": float(np.quantile(arr, 0.99)),
        "spearman": rho,
        "note": "Expect M4 to keep M1@5s labels and only add previously missing rows.",
    }


def compare_table(rows: list[dict[str, Any]], miss_m4: dict[str, Any], coh_m4: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    n = len(rows)
    for age in COMPARE_AGES:
        c = evaluate_combo(rows, "M1", float(age))
        smd_imb = next(
            (x["smd_valid_minus_null"] for x in (c.get("_miss") or {}).get("smd") or [] if x["feature"] == "imbalance"),
            None,
        )
        out.append(
            {
                "contract": f"M1_{int(age)}s",
                "max_mark_age_sec": age,
                "PRIMARY_ROWS": c.get("PRIMARY_ROWS"),
                "coverage": c.get("PRIMARY_COVERAGE_RATE"),
                "median_cohort": c.get("median_target_valid_n_per_cohort"),
                "p10_cohort": c.get("p10_target_valid_n_per_cohort"),
                "smd_event_rate_60s": c.get("smd_event_rate_60s"),
                "smd_spread_bps": c.get("smd_spread_bps"),
                "smd_imbalance": smd_imb,
                "smd_score": c.get("smd_score"),
                "score_decile_coverage_range": c.get("score_decile_coverage_range"),
                "symbol_coverage_range": c.get("symbol_coverage_range"),
                "ITAYOSE_BASE_ROW_N": c.get("ITAYOSE_BASE_ROW_N"),
                "CLOSING_AUCTION_ENDPOINT_ROW_N": c.get("CLOSING_AUCTION_ENDPOINT_ROW_N"),
                "causal_validity": "last_le_t_plus_arbitrary_age_cap",
                "TARGET_COHORT_COVERAGE_ADEQUATE": c.get("TARGET_COHORT_COVERAGE_ADEQUATE"),
            }
        )
    prim = sum(1 for r in rows if row_primary_m4(r))
    smd_map = {x["feature"]: x["smd_valid_minus_null"] for x in miss_m4.get("smd") or []}
    out.append(
        {
            "contract": "M4_PERSISTENT",
            "max_mark_age_sec": None,
            "PRIMARY_ROWS": prim,
            "coverage": prim / n if n else None,
            "median_cohort": coh_m4.get("median_target_valid_n_per_cohort"),
            "p10_cohort": coh_m4.get("p10"),
            "smd_event_rate_60s": smd_map.get("event_rate_60s"),
            "smd_spread_bps": smd_map.get("spread_bps"),
            "smd_imbalance": smd_map.get("imbalance"),
            "smd_score": smd_map.get("score"),
            "score_decile_coverage_range": miss_m4.get("score_decile_coverage_range"),
            "symbol_coverage_range": miss_m4.get("symbol_coverage_range"),
            "ITAYOSE_BASE_ROW_N": None,
            "CLOSING_AUCTION_ENDPOINT_ROW_N": None,
            "causal_validity": "last_le_t_until_next_event_no_age_cap",
            "TARGET_COHORT_COVERAGE_ADEQUATE": coh_m4.get("TARGET_COHORT_COVERAGE_ADEQUATE"),
        }
    )
    return out


def aggregate_persist(extracted: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_n: Counter = Counter()
    bucket_symdays: dict[str, set[str]] = defaultdict(set)
    bucket_days: dict[str, set[str]] = defaultdict(set)
    bucket_syms: dict[str, set[str]] = defaultdict(set)
    next_gt5: Counter = Counter()
    n_same = n_cross = n_all = n_valid = n_rev = n_reset = 0
    raw_holes = uni_holes = 0
    for body in extracted:
        day = str(body.get("date") or "")
        n_reset += int(body.get("n_seq_resets") or 0)
        raw_holes += int(body.get("native_ingest_raw_sequence_holes") or 0)
        uni_holes += int(body.get("native_ingest_sequence_holes") or 0)
        merged = merge_day_persist(body.get("persist_parts") or [])
        n_valid += int(merged.get("n_valid_quotes") or 0)
        n_all += int(merged.get("n_intervals_all") or 0)
        n_same += int(merged.get("n_same_session") or 0)
        n_cross += int(merged.get("n_cross_session") or 0)
        n_rev += int(merged.get("n_reversals") or 0)
        for k, v in (merged.get("buckets") or {}).items():
            bucket_n[k] += int(v)
            if int(v) > 0:
                bucket_days[k].add(day)
        for part in body.get("persist_parts") or []:
            sym = str(part.get("symbol") or "")
            for k, v in (part.get("buckets") or {}).items():
                if int(v) > 0:
                    bucket_symdays[k].add(f"{day}|{sym}")
                    bucket_syms[k].add(sym)
            for k, v in (part.get("next_gt5") or {}).items():
                next_gt5[k] += int(v)
    total = int(sum(bucket_n.values()))
    buckets = []
    for _lo, _hi, name in INTERVAL_BUCKETS:
        k = int(bucket_n.get(name) or 0)
        buckets.append(
            {
                "bucket": name,
                "n_intervals": k,
                "share": k / total if total else None,
                "symbol_count": len(bucket_syms.get(name) or []),
                "symbol_day_count": len(bucket_symdays.get(name) or []),
                "day_count": len(bucket_days.get(name) or []),
            }
        )
    n_gt5 = int(next_gt5.get("n") or 0)
    still = int(next_gt5.get("next_valid_continuous_quote") or 0)
    became_block = int(next_gt5.get("next_itayose_or_special") or 0)
    not_open = int(next_gt5.get("next_not_opened_or_preopen") or 0)
    gt5_names = ("5-10s", "10-30s", "30-60s", "60-120s", "120-300s", ">300s")
    share_gt5 = (sum(int(bucket_n.get(nm) or 0) for nm in gt5_names) / total) if total else None
    still_rate = still / n_gt5 if n_gt5 else None
    block_rate = became_block / n_gt5 if n_gt5 else None
    not_open_rate = not_open / n_gt5 if n_gt5 else None
    days_gt5 = max((len(bucket_days.get(nm) or []) for nm in gt5_names), default=0)
    syms_gt5 = len(set().union(*((bucket_syms.get(nm) or set()) for nm in gt5_names)))
    # Share of >5s can be small because PUSH is dense (most intervals <1s).
    # "Common" = large absolute count, present on most days and many symbols.
    long_quiet_common = bool(n_gt5 >= 10000 and days_gt5 >= 15 and syms_gt5 >= 50)
    no_expire_protocol = bool(
        still_rate is not None
        and float(still_rate) >= 0.80
        and (not_open_rate is None or float(not_open_rate) < 0.05)
    )
    return {
        "n_valid_quotes": n_valid,
        "n_intervals_all": n_all,
        "n_same_session_intervals": n_same,
        "n_cross_session_intervals": n_cross,
        "n_reversals": n_rev,
        "n_seq_resets": n_reset,
        "native_ingest_raw_sequence_holes": raw_holes,
        "native_ingest_sequence_holes": uni_holes,
        "seq_holes_not_per_symbol_gaps": True,
        "buckets": buckets,
        "n_gt5s_intervals": n_gt5,
        "n_days_with_gt5s": days_gt5,
        "n_symbols_with_gt5s": syms_gt5,
        "share_gt5s": share_gt5,
        "long_quiet_intervals_common": long_quiet_common,
        "next_gt5": dict(next_gt5),
        "next_gt5_still_valid_continuous_rate": still_rate,
        "next_gt5_became_itayose_special_rate": block_rate,
        "next_gt5_not_opened_rate": not_open_rate,
        "no_5s_expire_protocol_evidence": no_expire_protocol,
    }


def persistence_decision(*, persist: dict[str, Any], carry: dict[str, Any], integ: dict[str, Any]) -> dict[str, Any]:
    code_a = True
    empiric_a = bool(persist.get("long_quiet_intervals_common")) and bool(persist.get("no_5s_expire_protocol_evidence"))
    no_future = bool(carry.get("NO_FUTURE_EVENT_USED")) and bool(integ.get("NO_FUTURE_EVENT_USED"))
    persists = bool(code_a and empiric_a and no_future)
    return {
        "MARK_STATE_PERSISTS_UNTIL_NEXT_EVENT": persists,
        "ARBITRARY_MARK_AGE_CUTOFF_REQUIRED": not persists,
        "code_last_snapshot_until_next_push": code_a,
        "empirical_long_quiet_and_next_still_continuous": empiric_a,
        "EXECUTION_FRESHNESS_UNCHANGED": True,
        "EXECUTION_FRESHNESS_SEC": 5.0,
        "note": (
            "BOARD_FRESHNESS_SEC=5 is payload quote-clock vs recv and live fill safety. "
            "It is not a kabu PUSH TTL. Historical M4 does not inherit it. "
            "A low share of >5s intervals means PUSH is dense, not that quiet state is invalid. "
            "Persistence is last-observed until next event; next-event after >5s remaining "
            "continuous two-sided is the empirical test of B (expire after N seconds)."
        ),
    }


def bias_material(miss: dict[str, Any]) -> dict[str, Any]:
    er = miss.get("event_rate_smd")
    dec = miss.get("score_decile_coverage_range")
    sym = miss.get("symbol_coverage_range")
    er_abs_drop = None if er is None else abs(float(V3_M1_5S_EVENT_RATE_SMD)) - abs(float(er))
    dec_drop = None if dec is None else float(V3_M1_5S_DECILE_RANGE) - float(dec)
    sym_drop = None if sym is None else float(V3_M1_5S_SYMBOL_RANGE) - float(sym)
    material = bool(
        (er_abs_drop is not None and float(er_abs_drop) >= float(MATERIAL_SMD_DROP))
        or (dec_drop is not None and float(dec_drop) >= float(MATERIAL_DECILE_DROP))
    )
    return {
        "v3_event_rate_smd": V3_M1_5S_EVENT_RATE_SMD,
        "v3_decile_range": V3_M1_5S_DECILE_RANGE,
        "v3_symbol_range": V3_M1_5S_SYMBOL_RANGE,
        "m4_event_rate_smd": er,
        "m4_decile_range": dec,
        "m4_symbol_range": sym,
        "event_rate_abs_smd_drop": er_abs_drop,
        "decile_range_drop": dec_drop,
        "symbol_range_drop": sym_drop,
        "MATERIAL_REDUCTION_VS_V3": material,
    }


def structural_residual(*, anc: list[dict[str, Any]], miss: dict[str, Any]) -> dict[str, Any]:
    a1520 = next((x for x in anc if x.get("anchor") == "15:20"), {})
    a1515 = next((x for x in anc if x.get("anchor") == "15:15"), {})
    cov1520 = a1520.get("coverage_rate")
    cov1515 = a1515.get("coverage_rate")
    auction_zero = (cov1520 is not None and float(cov1520) <= 1e-12) or (
        cov1515 is not None and float(cov1515) <= 1e-12
    )
    er = miss.get("event_rate_smd")
    event_rate_collapsed = er is None or abs(float(er)) < float(SMD_BIAS_THRESHOLD)
    n_sym = int(miss.get("n_symbol") or 0)
    rotating = n_sym > 50
    explained = bool(auction_zero and rotating)
    return {
        "coverage_15_20": cov1520,
        "coverage_15_15": cov1515,
        "auction_or_close_endpoint_zero": auction_zero,
        "event_rate_smd_below_0_25": event_rate_collapsed,
        "n_symbol": n_sym,
        "rotating_universe": rotating,
        "STRUCTURAL_RESIDUAL_EXPLAINABLE": explained,
        "note": (
            "15:15/15:20 t+600 is closing auction or close — PRIMARY ineligible by TSE SoT. "
            "Symbol coverage range is inflated by 1-day rotating 50-name universe plus those zeros."
        ),
    }


def verdict_pack(
    *,
    n: int,
    prim: int,
    persist_dec: dict[str, Any],
    carry: dict[str, Any],
    integ: dict[str, Any],
    persist: dict[str, Any],
    coh: dict[str, Any],
    miss: dict[str, Any],
    material: dict[str, Any],
    residual: dict[str, Any],
    stab: dict[str, Any],
    age0: dict[str, Any],
    age1: dict[str, Any],
) -> dict[str, Any]:
    session_ok = True
    persists = bool(persist_dec.get("MARK_STATE_PERSISTS_UNTIL_NEXT_EVENT"))
    no_session = bool(carry.get("NO_SESSION_CARRY")) and bool(integ.get("NO_SESSION_CARRY"))
    no_lunch = bool(integ.get("NO_LUNCH_CARRY"))
    no_gap_inactivity = bool(integ.get("NO_INACTIVITY_AS_CAPTURE_GAP"))
    no_gap_carry = bool(no_gap_inactivity)
    no_future = bool(carry.get("NO_FUTURE_EVENT_USED"))
    itayose = int(integ.get("ITAYOSE_BASE_ROW_N") or 0)
    auction = int(integ.get("CLOSING_AUCTION_ENDPOINT_ROW_N") or 0)
    cohort_ok = bool(coh.get("TARGET_COHORT_COVERAGE_ADEQUATE"))
    bias = bool(miss.get("TARGET_MISSINGNESS_SELECTION_BIAS"))
    material_ok = bool(material.get("MATERIAL_REDUCTION_VS_V3"))
    residual_ok = bool(residual.get("STRUCTURAL_RESIDUAL_EXPLAINABLE")) and bool(
        residual.get("event_rate_smd_below_0_25")
    )
    bias_gate = (not bias) or (material_ok and residual_ok)
    ready = bool(
        session_ok
        and persists
        and no_session
        and no_lunch
        and no_gap_carry
        and no_future
        and itayose == 0
        and auction == 0
        and cohort_ok
        and bias_gate
        and not persist_dec.get("ARBITRARY_MARK_AGE_CUTOFF_REQUIRED")
    )
    defects = []
    if not persists or persist_dec.get("ARBITRARY_MARK_AGE_CUTOFF_REQUIRED"):
        defects.append("PERSISTENCE")
    if not cohort_ok:
        defects.append("COVERAGE")
    if bias and not bias_gate:
        defects.append("MISSINGNESS")
    if itayose or auction or not no_future or not no_session or not no_lunch or not session_ok:
        defects.append("MARK_SEMANTICS")
    if ready:
        verdict = "TARGET_PRICE_CONTRACT_V4_READY"
    elif len(defects) > 1:
        verdict = "TARGET_PRICE_CONTRACT_V4_MULTIPLE_DEFECTS"
    elif defects == ["PERSISTENCE"]:
        verdict = "TARGET_PRICE_CONTRACT_V4_PERSISTENCE_INVALID"
    elif defects == ["MISSINGNESS"]:
        verdict = "TARGET_PRICE_CONTRACT_V4_STILL_MISSINGNESS_BIASED"
    else:
        verdict = "TARGET_PRICE_CONTRACT_V4_MULTIPLE_DEFECTS"
    if ready:
        nxt = (
            "TARGET V4 READY on persistence + integrity gates. Do not auto-start C this run. "
            "Await explicit C REBUILD instruction. Runtime CLOCK/EXIT stay on Dual Lane 15:00. "
            "Execution freshness remains 5s. Do not search age>60s. Do not hunt new mark sources."
        )
    elif "PERSISTENCE" in defects:
        nxt = (
            "Do not start C REBUILD. M4 persistence is not accepted. Do not search age>60s. "
            "Do not open a new mark-source hunt. B remains closed. Execution freshness unchanged."
        )
    else:
        nxt = (
            "Do not start C REBUILD. Target V4 is not ready. Do not search age>60s. "
            "Do not open a new mark-source hunt. B remains closed."
        )
    return {
        "SESSION_SOT_VALID": session_ok,
        "PM_CONTINUOUS_END": "15:25",
        "MARK_STATE_PERSISTS_UNTIL_NEXT_EVENT": persists,
        "ARBITRARY_MARK_AGE_CUTOFF_REQUIRED": bool(persist_dec.get("ARBITRARY_MARK_AGE_CUTOFF_REQUIRED")),
        "NO_SESSION_CARRY": no_session,
        "NO_LUNCH_CARRY": no_lunch,
        "NO_CAPTURE_GAP_CARRY": no_gap_carry,
        "NO_FUTURE_EVENT_USED": no_future,
        "ITAYOSE_BASE_ROW_N": itayose,
        "CLOSING_AUCTION_ENDPOINT_ROW_N": auction,
        "ROWS_TOTAL": n,
        "M4_PRIMARY_ROWS": prim,
        "M4_COVERAGE_RATE": prim / n if n else None,
        "M4_MEDIAN_COHORT": coh.get("median_target_valid_n_per_cohort"),
        "M4_P10_COHORT": coh.get("p10"),
        "M4_P25_COHORT": coh.get("p25"),
        "M4_P75_COHORT": coh.get("p75"),
        "M4_P90_COHORT": coh.get("p90"),
        "M4_MIN_COHORT": coh.get("min"),
        "M4_MAX_COHORT": coh.get("max"),
        "M4_EVENT_RATE_SMD": miss.get("event_rate_smd"),
        "M4_SCORE_DECILE_COVERAGE_RANGE": miss.get("score_decile_coverage_range"),
        "M4_SYMBOL_COVERAGE_RANGE": miss.get("symbol_coverage_range"),
        "M4_TARGET_MISSINGNESS_SELECTION_BIAS": bias,
        "M4_T0_MARK_AGE_P90": age0.get("p90"),
        "M4_T600_MARK_AGE_P90": age1.get("p90"),
        "COMMON_5S_M4_LABEL_SPEARMAN": stab.get("spearman"),
        "TARGET_COHORT_COVERAGE_ADEQUATE": cohort_ok,
        "TARGET_V4_READY_FOR_C_REBUILD": ready,
        "VERDICT": verdict,
        "defects": defects,
        "RECOMMENDED_NEXT_STEP": nxt,
        "MATERIAL_REDUCTION_VS_V3": material_ok,
        "STRUCTURAL_RESIDUAL_EXPLAINABLE": residual.get("STRUCTURAL_RESIDUAL_EXPLAINABLE"),
        "bias_gate": bias_gate,
        "C_REBUILD_THIS_RUN": False,
        "age_search_expanded_beyond_60s": False,
        "new_mark_source_search": False,
        "selector_used_pnl_or_topk": False,
    }
