"""V3 mark selection by data integrity only. No PnL / Spearman / TopK in the selector."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.executable_board import (
    PREOPEN_ITAYOSE_SIGNS,
    SPECIAL_QUOTE_SIGNS,
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
)
from research.executable_target_v2_coverage_audit.session_sot import plus_sec_hm, tse_phase_hm
from research.target_price_contract_v3 import (
    AGE_BUCKETS,
    AGE_TOLERANCES_SEC,
    COHORT_MEDIAN_MIN,
    COHORT_MEDIAN_UNIVERSE_FRAC_MIN,
    COHORT_P10_MIN,
    EVENT_RATE_SMD_HARD,
    HORIZON_SEC,
    MARK_CANDIDATES,
    QTY_SITES,
    SCORE_DECILE_COVERAGE_RANGE_GOAL,
    SCORE_DECILE_COVERAGE_RANGE_HARD,
    SMD_BIAS_THRESHOLD,
    SYMBOL_COVERAGE_RANGE_GOAL,
    TIE_BREAK_MARK_ORDER,
)
from research.uniform10_entry_rebuild import UNIFORM10
from small_paper.v1r_native_entry_live import FEATURE_ORDER

FEATURE_COLS = list(FEATURE_ORDER) + ["score", "price_level"]
HIGHLIGHT_ANCHORS = ("14:40", "14:50", "15:00", "15:10", "15:20")
ITAYOSE_STATES = {STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE}
SPECIAL_STATES = {STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _quantiles(xs: list[float]) -> dict[str, Optional[float]]:
    arr = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if arr.size == 0:
        return {k: None for k in ("n", "mean", "p10", "p25", "p50", "p75", "p90")}
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "p10": float(np.quantile(arr, 0.10)),
        "p25": float(np.quantile(arr, 0.25)),
        "p50": float(np.quantile(arr, 0.50)),
        "p75": float(np.quantile(arr, 0.75)),
        "p90": float(np.quantile(arr, 0.90)),
    }


def smd(a: list[float], b: list[float]) -> Optional[float]:
    aa = np.asarray([x for x in a if x is not None and np.isfinite(x)], dtype=float)
    bb = np.asarray([x for x in b if x is not None and np.isfinite(x)], dtype=float)
    if aa.size < 10 or bb.size < 10:
        return None
    na, nb = float(aa.size), float(bb.size)
    va = float(aa.var(ddof=1)) if aa.size > 1 else 0.0
    vb = float(bb.var(ddof=1)) if bb.size > 1 else 0.0
    sp = np.sqrt(((na - 1.0) * va + (nb - 1.0) * vb) / max(na + nb - 2.0, 1.0))
    if sp < 1e-12:
        return 0.0
    return float((aa.mean() - bb.mean()) / sp)


def _age_ok(age: Any, tol: float) -> bool:
    x = _f(age)
    return x is not None and x <= float(tol) + 1e-12


def mark_side(row: dict[str, Any], side: str, mark: str, tol: float) -> tuple[Optional[float], Optional[float], Optional[str]]:
    if mark == "M3":
        px, age, src = mark_side(row, side, "M1", tol)
        if px is not None:
            return px, age, src
        return mark_side(row, side, "M2", tol)
    key = "m1" if mark == "M1" else "m2"
    px = _f(row.get(f"{key}_{side}_px"))
    age = _f(row.get(f"{key}_{side}_age"))
    if px is not None and _age_ok(age, tol):
        return px, age, mark
    return None, None, None


def row_primary(row: dict[str, Any], mark: str, tol: float) -> bool:
    if not row.get("tse_t0_cont") or not row.get("tse_t1_cont"):
        return False
    p0, _, _ = mark_side(row, "t0", mark, tol)
    p1, _, _ = mark_side(row, "t1", mark, tol)
    return p0 is not None and p1 is not None and p0 > 0 and p1 > 0


def row_return(row: dict[str, Any], mark: str, tol: float) -> Optional[float]:
    if not row_primary(row, mark, tol):
        return None
    p0, _, _ = mark_side(row, "t0", mark, tol)
    p1, _, _ = mark_side(row, "t1", mark, tol)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return float(p1 / p0 - 1.0)


def plus600_calendar() -> list[dict[str, Any]]:
    rows = []
    for h, m in UNIFORM10:
        lab = f"{h:02d}:{m:02d}"
        h1, m1 = plus_sec_hm(h, m, HORIZON_SEC)
        p0 = tse_phase_hm(h, m)
        p1 = tse_phase_hm(h1, m1)
        tse = p1 in {"AM_CONTINUOUS", "PM_CONTINUOUS_ZARABA"}
        rows.append(
            {
                "anchor": lab,
                "t0_market_phase": p0,
                "target_time": f"{h1:02d}:{m1:02d}",
                "target_market_phase": p1,
                "PRIMARY_TARGET_ELIGIBLE_TSE": bool(tse),
                "highlight": lab in HIGHLIGHT_ANCHORS,
            }
        )
    return rows


def _bucket(age: Optional[float]) -> Optional[str]:
    if age is None:
        return None
    x = float(age)
    if x <= 1.0 + 1e-12:
        return "0-1s"
    if x <= 5.0 + 1e-12:
        return "1-5s"
    if x <= 10.0 + 1e-12:
        return "5-10s"
    if x <= 15.0 + 1e-12:
        return "10-15s"
    if x <= 30.0 + 1e-12:
        return "15-30s"
    if x <= 60.0 + 1e-12:
        return "30-60s"
    if x <= 120.0 + 1e-12:
        return "60-120s"
    return ">120s"


def age_dist(rows: list[dict[str, Any]], px_key: str, age_key: str) -> list[dict[str, Any]]:
    cont = [r for r in rows if r.get("tse_t0_cont")]
    n = len(cont)
    c = Counter()
    missing = 0
    for r in cont:
        if _f(r.get(px_key)) is None:
            missing += 1
            continue
        c[_bucket(_f(r.get(age_key))) or "other"] += 1
    out = []
    for _, _, name in AGE_BUCKETS:
        k = int(c.get(name) or 0)
        out.append({"bucket": name, "n": k, "coverage": k / n if n else None, "missing_n": missing, "n_in": n})
    for sess in ("AM", "PM"):
        xs = [r for r in cont if r.get("session") == sess]
        ns = len(xs)
        cs = Counter(_bucket(_f(r.get(age_key))) for r in xs if _f(r.get(px_key)) is not None)
        for _, _, name in AGE_BUCKETS:
            k = int(cs.get(name) or 0)
            out.append({"bucket": name, "session": sess, "n": k, "coverage": k / ns if ns else None, "n_in": ns})
    for an in HIGHLIGHT_ANCHORS:
        xs = [r for r in cont if r.get("anchor") == an]
        ns = len(xs)
        cs = Counter(_bucket(_f(r.get(age_key))) for r in xs if _f(r.get(px_key)) is not None)
        for _, _, name in AGE_BUCKETS:
            k = int(cs.get(name) or 0)
            out.append({"bucket": name, "anchor": an, "n": k, "coverage": k / ns if ns else None, "n_in": ns})
    return out


def cohort_stats(rows: list[dict[str, Any]], mark: str, tol: float) -> dict[str, Any]:
    g: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        g[(str(r.get("date")), str(r.get("anchor")))].append(r)
    recs = []
    ns = []
    for (day, an), xs in sorted(g.items()):
        uni = int(xs[0].get("universe_n") or 0) if xs else 0
        valid = sum(1 for r in xs if row_primary(r, mark, tol))
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


def missingness(rows: list[dict[str, Any]], mark: str, tol: float) -> dict[str, Any]:
    valid = [r for r in rows if row_primary(r, mark, tol)]
    null = [r for r in rows if not row_primary(r, mark, tol)]
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
            k = sum(1 for r in xs if row_primary(r, mark, tol))
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
        sym_rates.append(sum(1 for r in xs if row_primary(r, mark, tol)) / n)
    sym_range = (max(sym_rates) - min(sym_rates)) if sym_rates else None
    am = [r for r in rows if r.get("session") == "AM"]
    pm = [r for r in rows if r.get("session") == "PM"]

    def _cov(xs):
        n = len(xs)
        k = sum(1 for r in xs if row_primary(r, mark, tol))
        return {"n": n, "target_valid": k, "coverage_rate": k / n if n else None}

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
        "am": _cov(am),
        "pm": _cov(pm),
        "TARGET_MISSINGNESS_SELECTION_BIAS": bias,
        "BIAS_EXPLAINABLE": explainable,
        "event_rate_smd": er,
    }


def evaluate_combo(rows: list[dict[str, Any]], mark: str, tol: float) -> dict[str, Any]:
    n = len(rows)
    prim = sum(1 for r in rows if row_primary(r, mark, tol))
    coh = cohort_stats(rows, mark, tol)
    miss = missingness(rows, mark, tol)
    itayose = 0
    auction = 0
    future = 0
    noncont_t0 = 0
    for r in rows:
        if not row_primary(r, mark, tol):
            continue
        if r.get("t1_phase") in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}:
            auction += 1
        if not r.get("tse_t0_cont"):
            noncont_t0 += 1
        st = str(r.get("last_state") or "")
        if st in ITAYOSE_STATES | SPECIAL_STATES:
            age = _f(r.get("m1_t0_age") if mark != "M2" else r.get("m2_t0_age"))
            lag = _f(r.get("last_lag"))
            if age is not None and lag is not None and abs(age - lag) < 1e-6:
                itayose += 1
    hard = bool(
        coh.get("TARGET_COHORT_COVERAGE_ADEQUATE")
        and itayose == 0
        and auction == 0
        and future == 0
        and noncont_t0 == 0
    )
    return {
        "mark": mark,
        "max_mark_age_sec": tol,
        "PRIMARY_ROWS": prim,
        "PRIMARY_COVERAGE_RATE": prim / n if n else None,
        "median_target_valid_n_per_cohort": coh.get("median_target_valid_n_per_cohort"),
        "p10_target_valid_n_per_cohort": coh.get("p10"),
        "median_frac_of_universe": coh.get("median_frac_of_universe"),
        "TARGET_COHORT_COVERAGE_ADEQUATE": coh.get("TARGET_COHORT_COVERAGE_ADEQUATE"),
        "score_decile_coverage_range": miss.get("score_decile_coverage_range"),
        "symbol_coverage_range": miss.get("symbol_coverage_range"),
        "smd_spread_bps": next((x["smd_valid_minus_null"] for x in miss["smd"] if x["feature"] == "spread_bps"), None),
        "smd_event_rate_60s": next((x["smd_valid_minus_null"] for x in miss["smd"] if x["feature"] == "event_rate_60s"), None),
        "smd_log_bid_qty": next((x["smd_valid_minus_null"] for x in miss["smd"] if x["feature"] == "log_bid_qty"), None),
        "smd_score": next((x["smd_valid_minus_null"] for x in miss["smd"] if x["feature"] == "score"), None),
        "smd_price_level": next((x["smd_valid_minus_null"] for x in miss["smd"] if x["feature"] == "price_level"), None),
        "ITAYOSE_BASE_ROW_N": itayose,
        "CLOSING_AUCTION_ENDPOINT_ROW_N": auction,
        "NONCONTINUOUS_T0_ROW_N": noncont_t0,
        "FUTURE_EVENT_USED": bool(future),
        "hard_pass": hard,
        "_coh": coh,
        "_miss": miss,
    }


def select_contract(combos: list[dict[str, Any]]) -> dict[str, Any]:
    passers = [c for c in combos if c.get("hard_pass") and c.get("mark") in MARK_CANDIDATES]
    if passers:
        passers.sort(key=lambda c: (float(c["max_mark_age_sec"]), TIE_BREAK_MARK_ORDER.index(c["mark"])))
        chosen = dict(passers[0])
        chosen["selection"] = "shortest_age_among_hard_pass"
        return chosen
    ranked = [c for c in combos if c.get("mark") in MARK_CANDIDATES]
    ranked.sort(
        key=lambda c: (
            -(float(c.get("median_target_valid_n_per_cohort") or 0.0)),
            -(float(c.get("p10_target_valid_n_per_cohort") or 0.0)),
            float(c.get("max_mark_age_sec") or 99.0),
        )
    )
    chosen = dict(ranked[0]) if ranked else {}
    chosen["selection"] = "none_hard_pass_best_cohort_not_ready"
    chosen["hard_pass"] = False
    return chosen


def m0_reference(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    tse = 0
    v2like = 0
    for r in rows:
        t0ok = _f(r.get("m0_t0_px")) is not None
        t1ok = _f(r.get("m0_t1_px")) is not None
        if t0ok and t1ok and r.get("tse_t0_cont") and r.get("tse_t1_cont"):
            tse += 1
        if t0ok and t1ok and r.get("tse_t0_cont") and r.get("anchor") not in {"15:00", "15:10", "15:15", "15:20"}:
            v2like += 1
    return {
        "mark": "M0",
        "max_mark_age_sec": 5.0,
        "note": "V2 last_executable_mid reference. qty>=100 + executable + 5s. Not selectable.",
        "PRIMARY_ROWS_TSE_SESSION": tse,
        "PRIMARY_COVERAGE_TSE": tse / n if n else None,
        "PRIMARY_ROWS_EXCL_POST_1500_ANCHORS": v2like,
    }


def locked_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cont = [r for r in rows if r.get("tse_t0_cont") and r.get("t0_has_event")]
    n = len(cont)
    eq = cross = pos = 0
    itayose_locked = 0
    cont_locked = 0
    by_state: Counter = Counter()
    for r in cont:
        bid, ask = _f(r.get("last_bid")), _f(r.get("last_ask"))
        st = str(r.get("last_state") or "")
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            continue
        if ask > bid + 1e-12:
            pos += 1
            continue
        if abs(ask - bid) <= 1e-12:
            eq += 1
        elif bid > ask + 1e-12:
            cross += 1
        by_state[st or "UNKNOWN"] += 1
        signs_itayose = (str(r.get("last_ask_sign") or "") in PREOPEN_ITAYOSE_SIGNS) or (
            str(r.get("last_bid_sign") or "") in PREOPEN_ITAYOSE_SIGNS
        )
        if st in ITAYOSE_STATES or signs_itayose:
            itayose_locked += 1
        elif st not in SPECIAL_STATES:
            cont_locked += 1
    return {
        "n_continuous_t0_with_event": n,
        "n_ask_gt_bid": pos,
        "n_bid_eq_ask": eq,
        "n_bid_gt_ask": cross,
        "n_locked_or_crossed": eq + cross,
        "n_locked_with_itayose_or_preopen_state": itayose_locked,
        "n_locked_in_non_itayose_state": cont_locked,
        "locked_by_state": dict(by_state),
        "rule": "M1 requires ask>bid. Locked/crossed is invalid two-sided quote, not auto-classified as pre-open. Pre-open uses AskSign/BidSign/state.",
    }


def first_fail(row: dict[str, Any], mark: str, tol: float) -> Optional[str]:
    if row_primary(row, mark, tol):
        return None
    if row.get("empty"):
        return "SYMBOL_DATA_MISSING"
    if not row.get("tse_t0_cont"):
        return "T0_OUTSIDE_CONTINUOUS"
    if not row.get("t0_has_event"):
        return "CAPTURE_GAP"
    p0, _, _ = mark_side(row, "t0", mark, tol)
    if p0 is None:
        lag = _f(row.get("last_lag"))
        if lag is not None and lag > 120:
            return "CAPTURE_GAP"
        st = str(row.get("last_state") or "")
        ask_s, bid_s = str(row.get("last_ask_sign") or ""), str(row.get("last_bid_sign") or "")
        if st in ITAYOSE_STATES or ask_s in PREOPEN_ITAYOSE_SIGNS or bid_s in PREOPEN_ITAYOSE_SIGNS:
            return "T0_ITAYOSE"
        if st in SPECIAL_STATES or ask_s in SPECIAL_QUOTE_SIGNS or bid_s in SPECIAL_QUOTE_SIGNS or row.get("last_special"):
            return "T0_SPECIAL"
        bid, ask = _f(row.get("last_bid")), _f(row.get("last_ask"))
        m1_age = _f(row.get("m1_t0_age"))
        if mark != "M2" and m1_age is not None and m1_age > float(tol) + 1e-12:
            return "T0_STALE"
        m2_age = _f(row.get("m2_t0_age"))
        if mark == "M2" and m2_age is not None and m2_age > float(tol) + 1e-12:
            return "T0_STALE"
        if mark == "M3" and (
            (m1_age is not None and m1_age > float(tol)) or (m2_age is not None and m2_age > float(tol))
        ):
            return "T0_STALE"
        if bid is not None and ask is not None and bid > 0 and ask > 0 and bid + 1e-12 >= ask:
            return "T0_LOCKED_CROSSED"
        return "T0_NO_VALID_MARK"
    if not row.get("tse_t1_cont"):
        if row.get("t1_phase") in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}:
            return "T600_CLOSING_AUCTION"
        return "T600_OUTSIDE_CONTINUOUS"
    p1, _, _ = mark_side(row, "t1", mark, tol)
    if p1 is None:
        m1_age = _f(row.get("m1_t1_age"))
        m2_age = _f(row.get("m2_t1_age"))
        if mark != "M2" and m1_age is not None and m1_age > float(tol) + 1e-12:
            return "T600_STALE"
        if mark == "M2" and m2_age is not None and m2_age > float(tol) + 1e-12:
            return "T600_STALE"
        if mark == "M3" and (
            (m1_age is not None and m1_age > float(tol)) or (m2_age is not None and m2_age > float(tol))
        ):
            return "T600_STALE"
        return "T600_NO_VALID_MARK"
    return "OTHER"


def waterfall(rows: list[dict[str, Any]], mark: str, tol: float) -> list[dict[str, Any]]:
    n = len(rows)
    stages = [("ROWS_TOTAL", rows)]
    s1 = [r for r in rows if r.get("tse_t0_cont")]
    stages.append(("ROWS_IN_CONTINUOUS_T0", s1))
    s2 = [r for r in s1 if mark_side(r, "t0", mark, tol)[0] is not None]
    stages.append(("ROWS_PRICE_MARK_T0_VALID", s2))
    s3 = [r for r in s2 if r.get("tse_t1_cont")]
    stages.append(("ROWS_T600_IN_CONTINUOUS", s3))
    s4 = [r for r in s3 if mark_side(r, "t1", mark, tol)[0] is not None]
    stages.append(("ROWS_PRICE_MARK_T600_VALID", s4))
    s5 = [r for r in s4 if row_primary(r, mark, tol)]
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


def first_fail_table(rows: list[dict[str, Any]], mark: str, tol: float) -> list[dict[str, Any]]:
    nulls = [r for r in rows if not row_primary(r, mark, tol)]
    n = len(nulls)
    c = Counter(first_fail(r, mark, tol) or "OTHER" for r in nulls)
    return [{"reason": k, "n": v, "share": v / n if n else None} for k, v in c.most_common()]


def by_anchor(rows: list[dict[str, Any]], mark: str, tol: float) -> list[dict[str, Any]]:
    cal = {r["anchor"]: r for r in plus600_calendar()}
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("anchor"))].append(r)
    out = []
    for h, m in UNIFORM10:
        an = f"{h:02d}:{m:02d}"
        xs = g.get(an) or []
        n = len(xs)
        valid = sum(1 for r in xs if row_primary(r, mark, tol))
        reasons = Counter(first_fail(r, mark, tol) or "NONE" for r in xs if not row_primary(r, mark, tol))
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
                "highlight": an in HIGHLIGHT_ANCHORS,
            }
        )
    return out


def by_day(rows: list[dict[str, Any]], mark: str, tol: float) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("date"))].append(r)
    out = []
    for day in sorted(g):
        xs = g[day]
        n = len(xs)
        valid = sum(1 for r in xs if row_primary(r, mark, tol))
        reasons = Counter(first_fail(r, mark, tol) or "NONE" for r in xs if not row_primary(r, mark, tol))
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


def by_symbol(rows: list[dict[str, Any]], mark: str, tol: float) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("symbol"))].append(r)
    out = []
    for sym in sorted(g):
        xs = g[sym]
        n = len(xs)
        valid = sum(1 for r in xs if row_primary(r, mark, tol))
        out.append({"symbol": sym, "rows_total": n, "target_valid": valid, "coverage_rate": valid / n if n else None})
    out.sort(key=lambda r: (r["coverage_rate"] is None, r["coverage_rate"] or 0.0, r["symbol"]))
    return out


def _rank(x: np.ndarray) -> np.ndarray:
    o = np.argsort(x, kind="mergesort")
    r = np.empty(x.size, dtype=float)
    r[o] = np.arange(x.size, dtype=float)
    return r


def label_stability(rows: list[dict[str, Any]], mark: str, tol: float) -> dict[str, Any]:
    tols = list(AGE_TOLERANCES_SEC)
    i = tols.index(float(tol)) if float(tol) in tols else 0
    neighbors = []
    if i + 1 < len(tols):
        neighbors.append(float(tols[i + 1]))
    if i - 1 >= 0:
        neighbors.append(float(tols[i - 1]))
    out = {"selected_mark": mark, "selected_age": tol, "pairs": []}
    a_rets = []
    keys = []
    for r in rows:
        ret = row_return(r, mark, tol)
        if ret is None:
            continue
        a_rets.append(ret)
        keys.append((r.get("date"), r.get("symbol"), r.get("anchor")))
    idx = {k: j for j, k in enumerate(keys)}
    for nb in neighbors:
        diffs = []
        aa = []
        bb = []
        for r in rows:
            k = (r.get("date"), r.get("symbol"), r.get("anchor"))
            if k not in idx:
                continue
            rb = row_return(r, mark, nb)
            if rb is None:
                continue
            ra = a_rets[idx[k]]
            diffs.append(abs(ra - rb))
            aa.append(ra)
            bb.append(rb)
        if not diffs:
            out["pairs"].append({"neighbor_age": nb, "n": 0})
            continue
        arr = np.asarray(diffs, dtype=float)
        rho = None
        if len(aa) >= 10:
            rho = float(np.corrcoef(_rank(np.asarray(aa)), _rank(np.asarray(bb)))[0, 1])
        out["pairs"].append(
            {
                "neighbor_age": nb,
                "n": int(arr.size),
                "mean_abs_diff": float(np.mean(arr)),
                "median_abs_diff": float(np.median(arr)),
                "p90_abs_diff": float(np.quantile(arr, 0.90)),
                "spearman": rho,
            }
        )
    return out


def verdict_pack(*, chosen: dict[str, Any], miss: dict[str, Any], coh: dict[str, Any], n: int, prim: int) -> dict[str, Any]:
    session_ok = True
    itayose = int(chosen.get("ITAYOSE_BASE_ROW_N") or 0)
    auction = int(chosen.get("CLOSING_AUCTION_ENDPOINT_ROW_N") or 0)
    future = bool(chosen.get("FUTURE_EVENT_USED"))
    noncont = int(chosen.get("NONCONTINUOUS_T0_ROW_N") or 0)
    cohort_ok = bool(coh.get("TARGET_COHORT_COVERAGE_ADEQUATE"))
    bias = bool(miss.get("TARGET_MISSINGNESS_SELECTION_BIAS"))
    explainable = bool(miss.get("BIAS_EXPLAINABLE"))
    bias_gate = (not bias) or explainable
    ready = bool(
        session_ok
        and itayose == 0
        and noncont == 0
        and auction == 0
        and not future
        and cohort_ok
        and bias_gate
        and chosen.get("selection") == "shortest_age_among_hard_pass"
    )
    defects = []
    if not cohort_ok:
        defects.append("COVERAGE")
    if bias and not explainable:
        defects.append("MISSINGNESS")
    if itayose or auction or future or noncont or not session_ok:
        defects.append("MARK_SEMANTICS")
    if ready:
        verdict = "TARGET_PRICE_CONTRACT_V3_READY"
    elif len(defects) > 1:
        verdict = "TARGET_PRICE_CONTRACT_V3_MULTIPLE_DEFECTS"
    elif defects == ["COVERAGE"]:
        verdict = "TARGET_PRICE_CONTRACT_V3_COVERAGE_INADEQUATE"
    elif defects == ["MISSINGNESS"]:
        verdict = "TARGET_PRICE_CONTRACT_V3_MISSINGNESS_BIASED"
    else:
        verdict = "TARGET_PRICE_CONTRACT_V3_MARK_SEMANTICS_INVALID"
    if ready:
        nxt = (
            "TARGET V3 READY on integrity gates. Do not auto-start C this run. "
            "Await explicit C REBUILD instruction. Runtime CLOCK/EXIT stay on Dual Lane 15:00."
        )
    else:
        nxt = (
            "Do not start C REBUILD. Target V3 is not ready. Do not expand age >60s. "
            "Do not use PnL to pick a mark. B remains closed."
        )
    return {
        "SESSION_SOT_VALID": session_ok,
        "PM_CONTINUOUS_END": "15:25",
        "SELECTED_MARK_CONTRACT": chosen.get("mark"),
        "SELECTED_MAX_MARK_AGE_SEC": chosen.get("max_mark_age_sec"),
        "ROWS_TOTAL": n,
        "PRIMARY_ROWS": prim,
        "PRIMARY_COVERAGE_RATE": prim / n if n else None,
        "MEDIAN_TARGET_VALID_N_PER_COHORT": coh.get("median_target_valid_n_per_cohort"),
        "P10_TARGET_VALID_N_PER_COHORT": coh.get("p10"),
        "TARGET_MISSINGNESS_SELECTION_BIAS": bias,
        "BIAS_EXPLAINABLE": explainable,
        "SCORE_DECILE_COVERAGE_RANGE": miss.get("score_decile_coverage_range"),
        "SYMBOL_COVERAGE_RANGE": miss.get("symbol_coverage_range"),
        "ITAYOSE_BASE_ROW_N": itayose,
        "CLOSING_AUCTION_ENDPOINT_ROW_N": auction,
        "NONCONTINUOUS_T0_ROW_N": noncont,
        "FUTURE_EVENT_USED": False,
        "TARGET_COHORT_COVERAGE_ADEQUATE": cohort_ok,
        "TARGET_V3_READY_FOR_C_REBUILD": ready,
        "VERDICT": verdict,
        "defects": defects,
        "RECOMMENDED_NEXT_STEP": nxt,
        "selection_rule": chosen.get("selection"),
        "QTY_SITES": list(QTY_SITES),
    }
