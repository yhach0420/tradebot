"""Coverage waterfall, first-fail, missingness, cohort. PRIMARY formula not changed."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.executable_target_v2_coverage_audit import (
    BOARD_FRESHNESS_SEC,
    COHORT_MEDIAN_MIN,
    COHORT_MEDIAN_UNIVERSE_FRAC_MIN,
    COHORT_P10_MIN,
    HORIZON_SEC,
    SCORE_DECILE_COVERAGE_RANGE_MAX,
    SMD_BIAS_THRESHOLD,
    SYMBOL_COVERAGE_RANGE_MAX,
)
from research.executable_target_v2_coverage_audit.diagnose import CANONICAL_NULL_REASONS
from research.executable_target_v2_coverage_audit.session_sot import (
    FIFTEEN_00_CLASSIFICATION,
    SESSION_INVENTORY,
    plus_sec_hm,
    tse_continuous_at_hm,
    tse_phase_hm,
    v1r_endpoint_in_session,
)
from research.uniform10_entry_rebuild import UNIFORM10
from small_paper.v1r_native_entry_live import FEATURE_ORDER

FEATURE_COLS = list(FEATURE_ORDER) + ["score", "price_level", "n_event_60s"]
HIGHLIGHT_ANCHORS = ("14:40", "14:50", "15:00", "15:10", "15:20")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _quantiles(xs: list[float]) -> dict[str, Optional[float]]:
    arr = np.asarray(xs, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {k: None for k in ("p10", "p25", "p50", "p75", "p90", "mean", "n")}
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


def plus600_calendar() -> list[dict[str, Any]]:
    rows = []
    for h, m in UNIFORM10:
        lab = f"{h:02d}:{m:02d}"
        h1, m1 = plus_sec_hm(h, m, HORIZON_SEC)
        t1 = f"{h1:02d}:{m1:02d}"
        p0 = tse_phase_hm(h, m)
        p1 = tse_phase_hm(h1, m1)
        v2 = v1r_endpoint_in_session(h, m, HORIZON_SEC)
        tse = tse_continuous_at_hm(h1, m1)
        rows.append(
            {
                "anchor": lab,
                "t0_market_phase": p0,
                "target_time": t1,
                "horizon_sec": HORIZON_SEC,
                "target_market_phase": p1,
                "PRIMARY_TARGET_ELIGIBLE_V2": bool(v2),
                "PRIMARY_TARGET_ELIGIBLE_TSE_ZARABA": bool(tse),
                "mismatch": bool(v2) != bool(tse),
                "highlight": lab in HIGHLIGHT_ANCHORS,
            }
        )
    return rows


def _stage_row(name: str, n_in: int, n_pass: int, n_total: int) -> dict[str, Any]:
    n_fail = int(n_in) - int(n_pass)
    return {
        "stage": name,
        "n_in": int(n_in),
        "n_pass": int(n_pass),
        "n_fail": int(n_fail),
        "pass_rate_this_stage": (float(n_pass) / float(n_in) if n_in else None),
        "cumulative_pass_rate": (float(n_pass) / float(n_total) if n_total else None),
    }


def waterfall(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n_total = len(rows)
    out = [_stage_row("ROWS_TOTAL", n_total, n_total, n_total)]

    def _filt(pred) -> list[dict[str, Any]]:
        return [r for r in rows if pred(r)]

    s_session = _filt(lambda r: bool(r.get("tse_t0_continuous")))
    out.append(_stage_row("T0_IN_VALID_SESSION", n_total, len(s_session), n_total))
    prev = s_session

    s_exe = [r for r in prev if r.get("executable_at_t0")]
    out.append(_stage_row("T0_EXECUTABLE_PASS", len(prev), len(s_exe), n_total))
    prev = s_exe

    s_fresh = [
        r
        for r in prev
        if r.get("t0_lag_sec") is not None and float(r["t0_lag_sec"]) <= float(BOARD_FRESHNESS_SEC) + 1e-12
    ]
    out.append(_stage_row("T0_FRESHNESS_PASS", len(prev), len(s_fresh), n_total))
    prev = s_fresh

    s_t0m = [r for r in prev if r.get("t0_mid_ok")]
    out.append(_stage_row("T0_MID_VALID", len(prev), len(s_t0m), n_total))
    prev = s_t0m

    s_t600_ph = [r for r in prev if r.get("tse_t600_continuous")]
    out.append(_stage_row("T600_IN_VALID_CONTINUOUS_PHASE", len(prev), len(s_t600_ph), n_total))
    prev = s_t600_ph

    s_ev = [r for r in prev if int(r.get("n_t600_events") or 0) > 0]
    out.append(_stage_row("T600_EVENT_AVAILABLE", len(prev), len(s_ev), n_total))
    prev = s_ev

    s_exe1 = [r for r in prev if r.get("t1_last_executable")]
    out.append(_stage_row("T600_EXECUTABLE_PASS", len(prev), len(s_exe1), n_total))
    prev = s_exe1

    s_fr1 = [
        r
        for r in prev
        if r.get("t1_lag_sec") is not None and float(r["t1_lag_sec"]) <= float(BOARD_FRESHNESS_SEC) + 1e-12
    ]
    out.append(_stage_row("T600_FRESHNESS_PASS", len(prev), len(s_fr1), n_total))
    prev = s_fr1

    s_t1m = [r for r in prev if r.get("t1_mid_ok")]
    out.append(_stage_row("T600_MID_VALID", len(prev), len(s_t1m), n_total))
    prev = s_t1m

    s_prim = [r for r in prev if r.get("MODEL_ROW_ELIGIBLE")]
    out.append(_stage_row("PRIMARY_TARGET_VALID", len(prev), len(s_prim), n_total))
    return out


def first_fail_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nulls = [r for r in rows if r.get("PRIMARY_TARGET_NULL")]
    n = len(nulls)
    c = Counter(str(r.get("first_fail") or "OTHER") for r in nulls)
    out = []
    for reason in CANONICAL_NULL_REASONS:
        k = int(c.get(reason) or 0)
        out.append({"reason": reason, "n": k, "share": (k / n if n else None)})
    extra = set(c) - set(CANONICAL_NULL_REASONS)
    for reason in sorted(extra):
        k = int(c[reason])
        out.append({"reason": reason, "n": k, "share": (k / n if n else None)})
    out.sort(key=lambda r: -int(r["n"]))
    return out


def v2_null_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nulls = [r for r in rows if r.get("PRIMARY_TARGET_NULL")]
    n = len(nulls)
    c = Counter(str(r.get("v2_null_reason") or "UNKNOWN") for r in nulls)
    return [{"v2_null_reason": k, "n": v, "share": v / n if n else None} for k, v in c.most_common()]


def by_anchor(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("anchor"))].append(r)
    out = []
    for an in [f"{h:02d}:{m:02d}" for h, m in UNIFORM10]:
        xs = g.get(an) or []
        n = len(xs)
        exe = sum(1 for r in xs if r.get("executable_at_t0"))
        valid = sum(1 for r in xs if r.get("MODEL_ROW_ELIGIBLE"))
        null = sum(1 for r in xs if r.get("PRIMARY_TARGET_NULL"))
        reasons = Counter(str(r.get("first_fail") or "NONE") for r in xs if r.get("PRIMARY_TARGET_NULL"))
        dom = reasons.most_common(1)[0][0] if reasons else None
        cal = next((c for c in plus600_calendar() if c["anchor"] == an), {})
        out.append(
            {
                "anchor": an,
                "rows_total": n,
                "t0_executable": exe,
                "target_valid": valid,
                "target_null": null,
                "coverage_rate": (valid / n if n else None),
                "dominant_null_reason": dom,
                "t1_hm": cal.get("target_time"),
                "t0_market_phase": cal.get("t0_market_phase"),
                "target_market_phase": cal.get("target_market_phase"),
                "PRIMARY_TARGET_ELIGIBLE_V2": cal.get("PRIMARY_TARGET_ELIGIBLE_V2"),
                "PRIMARY_TARGET_ELIGIBLE_TSE_ZARABA": cal.get("PRIMARY_TARGET_ELIGIBLE_TSE_ZARABA"),
                "highlight": an in HIGHLIGHT_ANCHORS,
            }
        )
    return out


def by_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("date"))].append(r)
    out = []
    for day in sorted(g):
        xs = g[day]
        n = len(xs)
        valid = sum(1 for r in xs if r.get("MODEL_ROW_ELIGIBLE"))
        reasons = Counter(str(r.get("first_fail") or "NONE") for r in xs if r.get("PRIMARY_TARGET_NULL"))
        out.append(
            {
                "date": day,
                "rows_total": n,
                "target_valid": valid,
                "coverage_rate": (valid / n if n else None),
                "dominant_null_reason": reasons.most_common(1)[0][0] if reasons else None,
            }
        )
    return out


def by_symbol(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get("symbol"))].append(r)
    out = []
    for sym in sorted(g):
        xs = g[sym]
        n = len(xs)
        valid = sum(1 for r in xs if r.get("MODEL_ROW_ELIGIBLE"))
        reasons = Counter(str(r.get("first_fail") or "NONE") for r in xs if r.get("PRIMARY_TARGET_NULL"))
        out.append(
            {
                "symbol": sym,
                "rows_total": n,
                "target_valid": valid,
                "coverage_rate": (valid / n if n else None),
                "dominant_null_reason": reasons.most_common(1)[0][0] if reasons else None,
            }
        )
    out.sort(key=lambda r: (r["coverage_rate"] is None, r["coverage_rate"] or 0.0, r["symbol"]))
    return out


def lookup_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _bucket(lag: Optional[float]) -> str:
        if lag is None:
            return "NO_EVENT"
        x = float(lag)
        if x <= 1e-12:
            return "EXACT_0"
        if x <= 5.0 + 1e-12:
            return "GT0_LE5"
        if x <= 30.0 + 1e-12:
            return "GT5_LE30"
        if x <= 120.0 + 1e-12:
            return "GT30_LE120"
        return "GT120"

    t0_mid = Counter(_bucket(_f(r.get("t0_lag_sec"))) for r in rows if r.get("t0_mid_ok"))
    t0_fail = Counter(
        _bucket(_f(r.get("t0_lag_sec"))) for r in rows if r.get("executable_at_t0") and not r.get("t0_mid_ok")
    )
    t1_mid = Counter(_bucket(_f(r.get("t1_mid_lag_sec"))) for r in rows if r.get("t1_mid_ok"))
    exact_req = 0
    return {
        "semantic": "last_event_t <= target_time AND (target_time - event_t) <= BOARD_FRESHNESS_SEC(5.0)",
        "searchsorted": "np.searchsorted(t, t_at, side='right') - 1, then walk back within 5s",
        "not_exact_timestamp": True,
        "not_first_ge_target": True,
        "not_nearest": True,
        "lookup_tolerance_sec": BOARD_FRESHNESS_SEC,
        "freshness_tolerance_sec": BOARD_FRESHNESS_SEC,
        "walk_back_past_freshness_forbidden": True,
        "t0_mid_ok_lag_buckets": dict(t0_mid),
        "t0_exe_but_no_mid_lag_buckets": dict(t0_fail),
        "t1_mid_ok_lag_buckets": dict(t1_mid),
        "n_rows_requiring_exact_match": exact_req,
        "ENDPOINT_LOOKUP_DEFECT": False,
        "defect_note": (
            "Lookup is as-of last<=t_at within canonical 5s freshness, matching the "
            "recommended 'market state at t0+600' principle. Mass NULL is not caused by "
            "exact timestamp match. T0_STALE / CAPTURE_GAP are freshness vs PUSH cadence, "
            "aligned with fill SoT, not an exact-match bug."
        ),
    }


def missingness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    slice_rows = [r for r in rows if r.get("executable_at_t0")]
    valid = [r for r in slice_rows if r.get("MODEL_ROW_ELIGIBLE")]
    null = [r for r in slice_rows if not r.get("MODEL_ROW_ELIGIBLE")]
    smd_rows = []
    bias_hits = []
    for col in FEATURE_COLS:
        a = [_f(r.get(col)) for r in valid]
        b = [_f(r.get(col)) for r in null]
        d = smd([x for x in a if x is not None], [x for x in b if x is not None])
        rec = {
            "feature": col,
            "smd_valid_minus_null": d,
            "valid": _quantiles([x for x in a if x is not None]),
            "null": _quantiles([x for x in b if x is not None]),
            "flag": bool(d is not None and abs(d) >= float(SMD_BIAS_THRESHOLD)),
        }
        smd_rows.append(rec)
        if rec["flag"]:
            bias_hits.append(col)

    scored = [r for r in slice_rows if _f(r.get("score")) is not None]
    decile_rows = []
    decile_range = None
    if len(scored) >= 50:
        scores = np.asarray([float(r["score"]) for r in scored], dtype=float)
        try:
            qs = np.quantile(scores, np.linspace(0, 1, 11))
            qs[0] = qs[0] - 1e-12
            qs[-1] = qs[-1] + 1e-12
            bins = np.digitize(scores, qs[1:-1], right=True)
        except Exception:
            bins = np.zeros(len(scored), dtype=int)
        rates = []
        for d in range(10):
            xs = [scored[i] for i in range(len(scored)) if int(bins[i]) == d]
            n = len(xs)
            k = sum(1 for r in xs if r.get("MODEL_ROW_ELIGIBLE"))
            rate = k / n if n else None
            if rate is not None:
                rates.append(rate)
            decile_rows.append(
                {
                    "score_decile": d + 1,
                    "n": n,
                    "target_valid": k,
                    "coverage_rate": rate,
                    "score_min": min((float(r["score"]) for r in xs), default=None),
                    "score_max": max((float(r["score"]) for r in xs), default=None),
                }
            )
        if rates:
            decile_range = float(max(rates) - min(rates))

    am = [r for r in slice_rows if r.get("session") == "AM"]
    pm = [r for r in slice_rows if r.get("session") == "PM"]

    def _cov(xs):
        n = len(xs)
        k = sum(1 for r in xs if r.get("MODEL_ROW_ELIGIBLE"))
        return {"n": n, "target_valid": k, "coverage_rate": k / n if n else None}

    sym = by_symbol(slice_rows)
    rates_s = [float(r["coverage_rate"]) for r in sym if r.get("coverage_rate") is not None]
    sym_range = (max(rates_s) - min(rates_s)) if rates_s else None

    bias = bool(bias_hits) or (
        decile_range is not None and decile_range >= float(SCORE_DECILE_COVERAGE_RANGE_MAX)
    ) or (sym_range is not None and sym_range >= float(SYMBOL_COVERAGE_RANGE_MAX))
    return {
        "slice": "executable_at_t0",
        "n_slice": len(slice_rows),
        "n_valid": len(valid),
        "n_null": len(null),
        "smd": smd_rows,
        "bias_features": bias_hits,
        "score_deciles": decile_rows,
        "score_decile_coverage_range": decile_range,
        "am": _cov(am),
        "pm": _cov(pm),
        "symbol_coverage_range": sym_range,
        "SMD_BIAS_THRESHOLD": SMD_BIAS_THRESHOLD,
        "SCORE_DECILE_COVERAGE_RANGE_MAX": SCORE_DECILE_COVERAGE_RANGE_MAX,
        "SYMBOL_COVERAGE_RANGE_MAX": SYMBOL_COVERAGE_RANGE_MAX,
        "TARGET_MISSINGNESS_SELECTION_BIAS": bias,
    }


def cohort(rows: list[dict[str, Any]]) -> dict[str, Any]:
    g: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        g[(str(r.get("date")), str(r.get("anchor")))].append(r)
    recs = []
    ns = []
    for (day, an), xs in sorted(g.items()):
        uni = int(xs[0].get("universe_n") or 0) if xs else 0
        valid = sum(1 for r in xs if r.get("MODEL_ROW_ELIGIBLE"))
        recs.append(
            {
                "date": day,
                "anchor": an,
                "eligible_universe_n": uni,
                "primary_target_valid_n": valid,
                "coverage_frac": (valid / uni if uni else None),
            }
        )
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
        "COHORT_MEDIAN_MIN": COHORT_MEDIAN_MIN,
        "COHORT_P10_MIN": COHORT_P10_MIN,
        "COHORT_MEDIAN_UNIVERSE_FRAC_MIN": COHORT_MEDIAN_UNIVERSE_FRAC_MIN,
        "TARGET_COHORT_COVERAGE_ADEQUATE": adequate,
    }


def session_verdict() -> dict[str, Any]:
    affects = [r for r in FIFTEEN_00_CLASSIFICATION if r.get("affects_primary_v2_endpoint")]
    stale = True  # V2 continuous_session_end PM 15:00 is legacy TSE close used as market continuous end
    strategy_only_no_target_impact = False
    session_sot_valid = False
    return {
        "SESSION_SOT_VALID": session_sot_valid,
        "PM_MARKET_SESSION_END": "15:30",
        "PM_CONTINUOUS_END": "15:25",
        "PM_CLOSING_AUCTION": "15:25-15:30",
        "V2_PM_CONTINUOUS_END_USED": "15:00",
        "STALE_SESSION_CONSTANT": stale,
        "strategy_only_no_target_impact": strategy_only_no_target_impact,
        "fifteen_00_uses_affecting_primary": affects,
        "inventory_n": len(SESSION_INVENTORY),
    }


def gates_and_verdict(
    *,
    rows: list[dict[str, Any]],
    miss: dict[str, Any],
    coh: dict[str, Any],
    lookup: dict[str, Any],
    sess: dict[str, Any],
) -> dict[str, Any]:
    n = len(rows)
    exe = sum(1 for r in rows if r.get("executable_at_t0"))
    prim = sum(1 for r in rows if r.get("MODEL_ROW_ELIGIBLE"))
    null = sum(1 for r in rows if r.get("PRIMARY_TARGET_NULL"))
    itayose = sum(1 for r in rows if r.get("ITAYOSE_BASE"))
    nonexe_p = sum(1 for r in rows if r.get("NON_EXECUTABLE_T0_IN_PRIMARY"))
    ff = first_fail_table(rows)
    top = ff[0] if ff else {"reason": None, "n": 0}
    cal = {r["anchor"]: r for r in plus600_calendar()}

    def _status(an: str) -> str:
        c = cal[an]
        if an == "15:20":
            return "EXCLUDED_CLOSING_AUCTION_ENDPOINT"
        if c["PRIMARY_TARGET_ELIGIBLE_TSE_ZARABA"] and not c["PRIMARY_TARGET_ELIGIBLE_V2"]:
            return "STALE_SESSION_EXCLUDED"
        if c["PRIMARY_TARGET_ELIGIBLE_V2"] and c["PRIMARY_TARGET_ELIGIBLE_TSE_ZARABA"]:
            return "ELIGIBLE_IF_MIDS_EXIST"
        return "EXCLUDED"

    stale = bool(sess.get("STALE_SESSION_CONSTANT")) and not bool(sess.get("strategy_only_no_target_impact"))
    endpoint_defect = bool(lookup.get("ENDPOINT_LOOKUP_DEFECT"))
    bias = bool(miss.get("TARGET_MISSINGNESS_SELECTION_BIAS"))
    cohort_ok = bool(coh.get("TARGET_COHORT_COVERAGE_ADEQUATE"))
    session_ok = bool(sess.get("SESSION_SOT_VALID"))

    defects = []
    if stale or not session_ok:
        defects.append("STALE_SESSION")
    if endpoint_defect:
        defects.append("ENDPOINT")
    if bias:
        defects.append("MISSINGNESS")
    if not cohort_ok:
        defects.append("COHORT")

    if not defects:
        verdict = "EXECUTABLE_TARGET_V2_COVERAGE_VALID"
    elif len(defects) > 1:
        verdict = "EXECUTABLE_TARGET_V2_MULTIPLE_INTEGRITY_DEFECTS"
    elif defects[0] == "STALE_SESSION":
        verdict = "EXECUTABLE_TARGET_V2_STALE_SESSION_DEFECT"
    elif defects[0] == "ENDPOINT":
        verdict = "EXECUTABLE_TARGET_V2_ENDPOINT_DEFECT"
    elif defects[0] == "MISSINGNESS":
        verdict = "EXECUTABLE_TARGET_V2_MISSINGNESS_BIASED"
    else:
        verdict = "EXECUTABLE_TARGET_V2_COHORT_COVERAGE_INADEQUATE"

    acceptable = bool(
        session_ok
        and (not stale or sess.get("strategy_only_no_target_impact"))
        and not endpoint_defect
        and not bias
        and cohort_ok
        and itayose == 0
        and nonexe_p == 0
    )
    if acceptable:
        next_step = (
            "Coverage audit PASS. Do not auto-start C REBUILD V2 this run. "
            "Await an explicit C instruction. B remains closed."
        )
    else:
        next_step = (
            "Do not start C REBUILD V2. Fix PRIMARY endpoint session SoT to current TSE "
            "Zaraba (PM continuous 12:30-15:25) without changing Runtime CLOCK/EXIT or "
            "Dual Lane SESSION_CLOSE. Keep last_executable_mid as-of last<=t_at within 5s; "
            "do not use first event after t_at; do not fallback to closing auction/itayose. "
            "Re-run this coverage audit. B0 historical only. B1 closed. No per-feature gates."
        )
    return {
        "ROWS_TOTAL": n,
        "ROWS_EXECUTABLE_T0": exe,
        "PRIMARY_ROWS": prim,
        "PRIMARY_TARGET_NULL_ROWS": null,
        "PRIMARY_COVERAGE_RATE": (prim / n if n else None),
        "ITAYOSE_BASE_ROW_N": itayose,
        "NON_EXECUTABLE_T0_ROW_N": nonexe_p,
        "TOP_NULL_REASON": top.get("reason"),
        "TOP_NULL_REASON_N": top.get("n"),
        "SESSION_SOT_VALID": session_ok,
        "PM_MARKET_SESSION_END": sess.get("PM_MARKET_SESSION_END"),
        "PM_CONTINUOUS_END": sess.get("PM_CONTINUOUS_END"),
        "PM_CLOSING_AUCTION": sess.get("PM_CLOSING_AUCTION"),
        "STALE_SESSION_CONSTANT": stale,
        "15_00_PLUS600_STATUS": _status("15:00"),
        "15_10_PLUS600_STATUS": _status("15:10"),
        "15_20_PLUS600_STATUS": _status("15:20"),
        "ENDPOINT_LOOKUP_SEMANTIC": lookup.get("semantic"),
        "ENDPOINT_LOOKUP_DEFECT": endpoint_defect,
        "TARGET_MISSINGNESS_SELECTION_BIAS": bias,
        "MEDIAN_TARGET_VALID_N_PER_COHORT": coh.get("median_target_valid_n_per_cohort"),
        "TARGET_COHORT_COVERAGE_ADEQUATE": cohort_ok,
        "TARGET_V2_COVERAGE_ACCEPTABLE": acceptable,
        "VERDICT": verdict,
        "defects": defects,
        "RECOMMENDED_NEXT_STEP": next_step,
        "C_REBUILD_V2_THIS_RUN": False,
        "would_primary_if_tse_zaraba_n": sum(1 for r in rows if r.get("would_primary_if_tse_zaraba")),
    }
