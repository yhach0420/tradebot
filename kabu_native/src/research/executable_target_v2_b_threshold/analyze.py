"""Target-contract audit + nested B1 score-percentile selection. Offline only."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING, verify_entry_binding
from research.executable_target_v2_b_threshold import (
    B1_GATE,
    B1_PERCENTILES,
    EXPECTED_B0_MAXDD,
    EXPECTED_B0_PF,
    EXPECTED_B0_PNL,
    EXPECTED_B0_TRADES,
    HIGHER_SCORE_PREFERRED,
    OCC_JACCARD_MIN,
    OCC_PNL_TOL,
    OCC_TRADE_N_TOL,
    TAIL_HALF_GAP,
)
from research.executable_target_v2_b_threshold.occupancy import occupancy_day
from research.passive_fill_itayose_reconciliation.analyze import concentration, headline
from research.uniform10_entry_rebuild import UNIFORM10
from research.uniform10_entry_validity_audit.analyze import (
    daily_pnl_block,
    exclude_pack,
    session_pack,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

# Precommitted before looking at inner/outer results.
INNER_SELECT_RULE = (
    "On inner-train occupancy trades only: keep a percentile p if it beats inner B0 "
    "(p=None) on ALL of (PF higher, maxDD not worse, positive_day_rate higher, "
    "median_daily_pnl higher). Among keepers, lexicographic max of "
    "(positive_day_rate, median_daily_pnl, maxDD, PF). If none beat inner B0, "
    "select NO_THRESHOLD. Outer held-out day is never used to pick p. "
    "PnL-max is not the selector."
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pf(v: Any) -> float:
    if v is None:
        return 0.0
    if v == "Infinity" or v == float("inf"):
        return 9.0
    return float(v)


def _dd(v: Any) -> float:
    x = _f(v)
    return float(x) if x is not None else -1e18


def pack_metrics(trades: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    h = headline(trades)
    d = daily_pnl_block(trades, days)
    s = session_pack(trades)
    x = exclude_pack(trades)
    c = concentration(trades)
    return {
        "trades": h.get("trades"),
        "PnL": h.get("pnl"),
        "PF": h.get("PF"),
        "maxDD": h.get("maxDD"),
        "avg_trade": h.get("avg_trade"),
        "median_trade": h.get("median_trade"),
        "wins": h.get("win"),
        "losses": h.get("loss"),
        "draws": h.get("draw"),
        "positive_day_rate": d.get("positive_day_rate"),
        "median_daily_pnl": d.get("median_daily_pnl"),
        "mean_daily_pnl": d.get("mean_daily_pnl"),
        "AM": s.get("AM"),
        "PM": s.get("PM"),
        "first_entry": s.get("first_entry"),
        "re_entry": s.get("re_entry"),
        "exclude": x,
        "concentration": c,
        "daily": d,
        "session": s,
        "headline": h,
    }


def entry_freeze() -> dict[str, Any]:
    bind = verify_entry_binding()
    return {
        "FEATURE_ORDER": list(FEATURE_ORDER),
        "rank_pass_gate": ENTRY_BINDING.get("rank_pass_gate"),
        "score_threshold": None,
        "per_feature_threshold": None,
        "per_feature_threshold_exists": False,
        "TopK": None,
        "admission": "simulate_joint then live admit until POSITION_CAP",
        "POSITION_CAP": POSITION_CAP,
        "eligibility": ENTRY_BINDING.get("eligibility"),
        "score_calculation": ENTRY_BINDING.get("score_calculation"),
        "score_semantic": "predict_proba_class_1",
        "higher_score_preferred": HIGHER_SCORE_PREFERRED,
        "candidate_ranking": ENTRY_BINDING.get("candidate_ranking"),
        "WAIT_SEC": WAIT_SEC,
        "CURRENT_ENTRY_BINDING": bind.get("CURRENT_ENTRY_BINDING"),
        "do_not_call_missing_per_feature_gate_current_threshold": True,
    }


def aggregate_coverage(coverage: list[dict[str, Any]]) -> dict[str, Any]:
    def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        exe = sum(1 for r in rows if r.get("executable_at_t0"))
        prim = sum(1 for r in rows if r.get("MODEL_ROW_ELIGIBLE"))
        null = sum(1 for r in rows if r.get("PRIMARY_TARGET_NULL"))
        itayose = sum(1 for r in rows if r.get("ITAYOSE_BASE"))
        nonexe = sum(1 for r in rows if r.get("NON_EXECUTABLE_T0_IN_PRIMARY"))
        return {
            "rows_total": n,
            "rows_executable_t0": exe,
            "rows_primary_target_valid": prim,
            "rows_target_null": null,
            "ITAYOSE_BASE_ROW_N": itayose,
            "NON_EXECUTABLE_T0_ROW_N": nonexe,
        }

    by_date: dict[str, list] = defaultdict(list)
    by_anchor: dict[str, list] = defaultdict(list)
    by_sess: dict[str, list] = defaultdict(list)
    by_da: dict[tuple[str, str], list] = defaultdict(list)
    by_ds: dict[tuple[str, str], list] = defaultdict(list)
    for r in coverage:
        by_date[str(r.get("date"))].append(r)
        by_anchor[str(r.get("anchor"))].append(r)
        by_sess[str(r.get("session"))].append(r)
        by_da[(str(r.get("date")), str(r.get("anchor")))].append(r)
        by_ds[(str(r.get("date")), str(r.get("session")))].append(r)
    reasons: dict[str, int] = defaultdict(int)
    for r in coverage:
        if r.get("PRIMARY_TARGET_NULL"):
            reasons[str(r.get("null_reason") or "UNKNOWN")] += 1
    rows_1520 = [r for r in coverage if r.get("is_15_20")]
    n_1520_valid = sum(1 for r in rows_1520 if r.get("has_target"))
    status_1520 = "EXCLUDED_NONCONTINUOUS_ENDPOINT"
    if n_1520_valid:
        status_1520 = "VALID"
    calendar = []
    from research.anchor_timing_robustness.grid import hm_label
    from research.executable_target_v2_b_threshold.contract import continuous_session_end, session_of_anchor
    from research.executable_target_v2_b_threshold import HORIZON_SEC
    from research.anchor_timing_robustness.grid import hm_epoch

    sample_day = sorted(by_date)[0] if by_date else "20260827"
    for h, m in UNIFORM10:
        lab = hm_label(h, m)
        sess = session_of_anchor(h)
        t0 = hm_epoch(sample_day, h, m)
        t1 = t0 + float(HORIZON_SEC)
        end = continuous_session_end(sample_day, sess)
        in_sess = t1 <= end + 1e-12
        calendar.append(
            {
                "anchor": lab,
                "session": sess,
                "t0_plus_600_in_continuous": in_sess,
                "endpoint_clock": "t0+600s",
                "canonical_session_end": "11:30" if sess == "AM" else "15:00",
                "PRIMARY_STATUS": "ELIGIBLE_IF_MIDS_EXIST" if in_sess else "EXCLUDED_NONCONTINUOUS_ENDPOINT",
            }
        )
    overall = _agg(coverage)
    return {
        "overall": overall,
        "by_date": [{"grain": "date", "date": k, **_agg(v)} for k, v in sorted(by_date.items())],
        "by_anchor": [{"grain": "anchor", "anchor": k, **_agg(v)} for k, v in sorted(by_anchor.items())],
        "by_session": [{"grain": "session", "session": k, **_agg(v)} for k, v in sorted(by_sess.items())],
        "by_date_anchor": [{"grain": "date_anchor", "date": d, "anchor": a, **_agg(v)} for (d, a), v in sorted(by_da.items())],
        "by_date_session": [{"grain": "date_session", "date": d, "session": s, **_agg(v)} for (d, s), v in sorted(by_ds.items())],
        "null_reasons": [{"reason": k, "n": n} for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])],
        "endpoint_calendar": calendar,
        "n_15_20_rows": len(rows_1520),
        "n_15_20_primary_valid": n_1520_valid,
        "15_20_PRIMARY_TARGET_STATUS": status_1520,
        "ITAYOSE_BASE_ROW_N": overall["ITAYOSE_BASE_ROW_N"],
        "NON_EXECUTABLE_T0_ROW_N": overall["NON_EXECUTABLE_T0_ROW_N"],
        "PRIMARY_ROWS": overall["rows_primary_target_valid"],
        "PRIMARY_TARGET_NULL_ROWS": overall["rows_target_null"],
        "T0_EXECUTABLE_ONLY": overall["ITAYOSE_BASE_ROW_N"] == 0
        and overall["NON_EXECUTABLE_T0_ROW_N"] == 0,
    }


def target_verdict(cov: dict[str, Any], oof_ready: bool) -> dict[str, Any]:
    itayose = int(cov.get("ITAYOSE_BASE_ROW_N") or 0)
    nonexe = int(cov.get("NON_EXECUTABLE_T0_ROW_N") or 0)
    status_1520 = str(cov.get("15_20_PRIMARY_TARGET_STATUS") or "")
    t0_ok = bool(cov.get("T0_EXECUTABLE_ONLY"))
    calendar_1520 = False
    for row in cov.get("endpoint_calendar") or []:
        if row.get("anchor") == "15:20":
            calendar_1520 = row.get("PRIMARY_STATUS") == "EXCLUDED_NONCONTINUOUS_ENDPOINT"
    # 15:20 must be excluded by session calendar; any valid primary at 15:20 is FAIL.
    ok_1520 = bool(calendar_1520 and int(cov.get("n_15_20_primary_valid") or 0) == 0)
    if not ok_1520:
        status_1520 = "VALID" if int(cov.get("n_15_20_primary_valid") or 0) else status_1520
    valid = bool(t0_ok and itayose == 0 and nonexe == 0 and ok_1520 and oof_ready)
    return {
        "TARGET_CONTRACT_V2_VALID": valid,
        "T0_EXECUTABLE_ONLY": t0_ok,
        "ITAYOSE_BASE_ROW_N": itayose,
        "NON_EXECUTABLE_T0_ROW_N": nonexe,
        "PRIMARY_ROWS": cov.get("PRIMARY_ROWS"),
        "PRIMARY_TARGET_NULL_ROWS": cov.get("PRIMARY_TARGET_NULL_ROWS"),
        "15_20_PRIMARY_TARGET_STATUS": "EXCLUDED_NONCONTINUOUS_ENDPOINT" if ok_1520 else status_1520,
        "LODO_OOF_CONTRACT_READY": oof_ready,
        "TARGET_VERDICT": "EXECUTABLE_TARGET_CONTRACT_V2_READY" if valid else "EXECUTABLE_TARGET_CONTRACT_V2_FAILED",
        "C_REBUILD_V1_STATUS": "C_REBUILD_V1_INVALID_TARGET_CONTAMINATED",
        "OPENS_WITHIN_1S_IN_MODEL_ELIGIBILITY": False,
    }


def b0_parity(trades: list[dict[str, Any]]) -> dict[str, Any]:
    h = headline(trades)
    pnl = float(h.get("pnl") or 0.0)
    trades_n = int(h.get("trades") or 0)
    pf = _pf(h.get("PF"))
    dd = float(h.get("maxDD") or 0.0)
    ok = (
        trades_n == int(EXPECTED_B0_TRADES)
        and abs(pnl - float(EXPECTED_B0_PNL)) <= 1.0
        and abs(pf - float(EXPECTED_B0_PF)) <= 1e-4
        and abs(dd - float(EXPECTED_B0_MAXDD)) <= 1.0
    )
    return {
        "ok": ok,
        "observed": {
            "trades": trades_n,
            "PnL": pnl,
            "PF": h.get("PF"),
            "maxDD": dd,
        },
        "expected": {
            "trades": EXPECTED_B0_TRADES,
            "PnL": EXPECTED_B0_PNL,
            "PF": EXPECTED_B0_PF,
            "maxDD": EXPECTED_B0_MAXDD,
        },
        "note": None if ok else "B0 cache headline does not match known UNIFORM10+CURRENT ENTRY+CORRECTED FILL. STOP B1.",
    }


def trade_key(t: dict[str, Any]) -> tuple[str, str, str]:
    """Join on fill clock. Occupancy anchor_time is not the fill minute."""
    day = str(t.get("date") or "")
    sym = str(t.get("symbol") or "").replace(".T", "")
    iso = str(t.get("fill_time_iso") or "")
    if "T" in iso:
        return (day, sym, iso.split("T", 1)[1][:8])
    ft = t.get("fill_time")
    if ft is not None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return (day, sym, datetime.fromtimestamp(float(ft), ZoneInfo("Asia/Tokyo")).strftime("%H:%M:%S"))
    return (day, sym, str(t.get("anchor_time") or t.get("anchor") or ""))


def occupancy_parity(cache: list[dict[str, Any]], occ: list[dict[str, Any]]) -> dict[str, Any]:
    a = {trade_key(t) for t in cache}
    b = {trade_key(t) for t in occ}
    inter = a & b
    union = a | b
    jacc = (len(inter) / len(union)) if union else 1.0
    ha, hb = headline(cache), headline(occ)
    n_ok = abs(int(ha.get("trades") or 0) - int(hb.get("trades") or 0)) <= int(OCC_TRADE_N_TOL)
    pnl_ok = abs(float(ha.get("pnl") or 0.0) - float(hb.get("pnl") or 0.0)) <= float(OCC_PNL_TOL)
    j_ok = jacc >= float(OCC_JACCARD_MIN)
    ok = bool(n_ok and pnl_ok and j_ok)
    return {
        "ok": ok,
        "jaccard": jacc,
        "cache_n": len(cache),
        "occ_n": len(occ),
        "intersection_n": len(inter),
        "cache_only_n": len(a - b),
        "occ_only_n": len(b - a),
        "cache_pnl": ha.get("pnl"),
        "occ_pnl": hb.get("pnl"),
        "cache_PF": ha.get("PF"),
        "occ_PF": hb.get("PF"),
        "n_ok": n_ok,
        "pnl_ok": pnl_ok,
        "jaccard_ok": j_ok,
        "note": None
        if ok
        else (
            "Occupancy engine (hypo Arch E) does not reproduce Dual-Lane B0 fill set/PnL "
            "within precommitted tolerance. B1 not started."
        ),
    }


def score_distribution(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    by_anchor: dict[str, list[float]] = defaultdict(list)
    all_s: list[float] = []
    unique_frac = []
    for c in candidates:
        s = _f(c.get("score"))
        if s is None:
            continue
        all_s.append(s)
        by_day[str(c.get("date"))].append(s)
        by_anchor[str(c.get("anchor"))].append(s)
    by_cohort: dict[tuple[str, str], list[float]] = defaultdict(list)
    for c in candidates:
        s = _f(c.get("score"))
        if s is None:
            continue
        by_cohort[(str(c.get("date")), str(c.get("anchor")))].append(s)
    for xs in by_cohort.values():
        if xs:
            unique_frac.append(len(set(round(x, 8) for x in xs)) / len(xs))
    day_means = [float(np.mean(v)) for v in by_day.values() if v]
    cv = None
    if len(day_means) >= 2 and abs(float(np.mean(day_means))) > 1e-12:
        cv = float(np.std(day_means, ddof=1) / abs(float(np.mean(day_means))))
    abs_unstable = bool(cv is not None and cv > 0.25)
    # CS percentile remains the precommitted gate. INVALID_SCALE only if percentile is ill-defined.
    scale_invalid = bool(not all_s or (unique_frac and float(np.median(unique_frac)) < 0.05))
    return {
        "n_scores": len(all_s),
        "min": None if not all_s else float(np.min(all_s)),
        "max": None if not all_s else float(np.max(all_s)),
        "mean": None if not all_s else float(np.mean(all_s)),
        "median": None if not all_s else float(np.median(all_s)),
        "std": None if len(all_s) < 2 else float(np.std(all_s, ddof=1)),
        "day_mean_cv": cv,
        "absolute_score_unstable_across_days": abs_unstable,
        "gate_used": B1_GATE,
        "absolute_threshold_used": False,
        "higher_score_preferred": HIGHER_SCORE_PREFERRED,
        "median_within_anchor_unique_frac": None if not unique_frac else float(np.median(unique_frac)),
        "B1_SCORE_SCALE_INVALID": scale_invalid,
        "by_day": [
            {"date": d, "n": len(v), "mean": float(np.mean(v)), "std": None if len(v) < 2 else float(np.std(v, ddof=1)),
             "min": float(np.min(v)), "max": float(np.max(v))}
            for d, v in sorted(by_day.items())
        ],
        "by_anchor": [
            {"anchor": a, "n": len(v), "mean": float(np.mean(v)), "std": None if len(v) < 2 else float(np.std(v, ddof=1)),
             "min": float(np.min(v)), "max": float(np.max(v))}
            for a, v in sorted(by_anchor.items())
        ],
        "note": (
            "Serialized score is sklearn logistic predict_proba class 1 in (0,1). "
            "Search space is within-anchor cross-sectional percentile, not an absolute T, "
            "because day/anchor location of the probability can still move."
        ),
    }


def _beats_b0(m: dict[str, Any], b0: dict[str, Any]) -> bool:
    return bool(
        _pf(m.get("PF")) > _pf(b0.get("PF"))
        and _dd(m.get("maxDD")) >= _dd(b0.get("maxDD")) - 1e-9
        and float(m.get("positive_day_rate") or 0.0) > float(b0.get("positive_day_rate") or 0.0)
        and float(m.get("median_daily_pnl") or -1e18) > float(b0.get("median_daily_pnl") or -1e18)
    )


def inner_select(train_by_p: dict[Any, list[dict[str, Any]]], days: list[str]) -> Any:
    b0 = pack_metrics(train_by_p.get(None) or [], days)
    best_p: Any = None
    best_key = None
    for p in B1_PERCENTILES:
        m = pack_metrics(train_by_p.get(p) or [], days)
        if p is None:
            continue
        if not _beats_b0(m, b0):
            continue
        key = (
            float(m.get("positive_day_rate") or 0.0),
            float(m.get("median_daily_pnl") or -1e18),
            _dd(m.get("maxDD")),
            _pf(m.get("PF")),
        )
        if best_key is None or key > best_key:
            best_key = key
            best_p = p
    return best_p


def nested_b1(
    cand_by_day: dict[str, list[dict[str, Any]]],
    days: list[str],
) -> dict[str, Any]:
    occ_by_day_p: dict[str, dict[Any, list]] = {}
    for d in days:
        occ_by_day_p[d] = {p: occupancy_day(cand_by_day.get(d) or [], p) for p in B1_PERCENTILES}
    oof: list[dict[str, Any]] = []
    folds = []
    for held in days:
        train_days = [d for d in days if d != held]
        train_by_p: dict[Any, list] = {p: [] for p in B1_PERCENTILES}
        for d in train_days:
            for p in B1_PERCENTILES:
                train_by_p[p].extend(occ_by_day_p[d][p])
        chosen = inner_select(train_by_p, train_days)
        held_trades = list(occ_by_day_p[held][chosen])
        for t in held_trades:
            rec = dict(t)
            rec["oof_held_out_day"] = held
            rec["selected_percentile"] = chosen
            oof.append(rec)
        inner_m = pack_metrics(train_by_p[chosen], train_days)
        folds.append(
            {
                "held_out_day": held,
                "selected_percentile": "NO_THRESHOLD" if chosen is None else f"p{chosen}",
                "selected_p": chosen,
                "inner_trades": inner_m.get("trades"),
                "inner_PnL": inner_m.get("PnL"),
                "inner_PF": inner_m.get("PF"),
                "inner_maxDD": inner_m.get("maxDD"),
                "inner_positive_day_rate": inner_m.get("positive_day_rate"),
                "inner_median_daily_pnl": inner_m.get("median_daily_pnl"),
                "outer_trades": len(held_trades),
                "outer_PnL": headline(held_trades).get("pnl"),
            }
        )
    counts = Counter(f["selected_p"] for f in folds)
    mode_p = None
    if counts:
        mode_p = counts.most_common(1)[0][0]
    mixed = len([k for k in counts if counts[k] > 0]) > 1
    best_label = "NO_THRESHOLD" if mode_p is None else f"p{mode_p}"
    if mixed:
        best_label = f"NESTED_MIXED(mode={best_label})"
    return {
        "protocol": "nested_leave_one_day_out",
        "inner_select_rule": INNER_SELECT_RULE,
        "search_space": ["NO_THRESHOLD" if p is None else f"p{p}" for p in B1_PERCENTILES],
        "folds": folds,
        "oof_trades": oof,
        "BEST_B1_THRESHOLD": best_label,
        "mode_p": mode_p,
        "selected_counts": {("NO_THRESHOLD" if k is None else f"p{k}"): v for k, v in counts.items()},
        "search_expanded_mid_run": False,
        "am_pm_split": False,
        "anchor_split": False,
        "symbol_split": False,
        "weekday_split": False,
    }


def tail_ok(b1: float, b0: float) -> bool:
    if b1 >= 0:
        return True
    return bool(b1 > b0 and (b1 - b0) >= float(TAIL_HALF_GAP) * abs(b0))


def b1_success(b0: dict[str, Any], b1: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    ex0 = b0.get("exclude") or {}
    ex1 = b1.get("exclude") or {}
    p0t = float((ex0.get("ex_top3_trades") or {}).get("PnL") or 0.0)
    p1t = float((ex1.get("ex_top3_trades") or {}).get("PnL") or 0.0)
    p0d = float((ex0.get("ex_top3_days") or {}).get("PnL") or 0.0)
    p1d = float((ex1.get("ex_top3_days") or {}).get("PnL") or 0.0)
    p0s = float((ex0.get("ex_top_symbol") or {}).get("PnL") or 0.0)
    p1s = float((ex1.get("ex_top_symbol") or {}).get("PnL") or 0.0)
    checks = {
        "PF_improved": _pf(b1.get("PF")) > _pf(b0.get("PF")),
        "maxDD_not_worse": _dd(b1.get("maxDD")) >= _dd(b0.get("maxDD")) - 1e-9,
        "positive_day_rate_improved": float(b1.get("positive_day_rate") or 0.0)
        > float(b0.get("positive_day_rate") or 0.0),
        "median_daily_pnl_improved": float(b1.get("median_daily_pnl") or -1e18)
        > float(b0.get("median_daily_pnl") or -1e18),
        "ex_top3_trades_ok": tail_ok(p1t, p0t),
        "ex_top3_days_ok": tail_ok(p1d, p0d),
        "ex_top_symbol_ok": tail_ok(p1s, p0s),
        "B1_ex_top3_trades": p1t,
        "B0_ex_top3_trades": p0t,
        "B1_ex_top3_days": p1d,
        "B0_ex_top3_days": p0d,
        "B1_ex_top_symbol": p1s,
        "B0_ex_top_symbol": p0s,
    }
    ok = all(
        checks[k]
        for k in (
            "PF_improved",
            "maxDD_not_worse",
            "positive_day_rate_improved",
            "median_daily_pnl_improved",
            "ex_top3_trades_ok",
            "ex_top3_days_ok",
            "ex_top_symbol_ok",
        )
    )
    return ok, checks


def mfe_audit(trades: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
    raw = [t for t in trades if t.get("mfe_yen_100_raw") is not None]
    floor = [t for t in trades if t.get("mfe_yen_100") is not None]
    raw_vals = [float(t["mfe_yen_100_raw"]) for t in raw]
    floor_vals = [float(t["mfe_yen_100"]) for t in floor]
    cache_raw = [t for t in trades if t.get("mfe_yen_100") is not None and t.get("mfe_yen_100_raw") is None]
    cache_vals = [float(t["mfe_yen_100"]) for t in cache_raw]
    return {
        "source": source,
        "formula_research_sot": "MFE = max(0, max executable bid return after fill)",
        "MAE_unchanged": True,
        "STRATEGY_RETUNED": False,
        "ledger_unchanged": True,
        "n_trades": len(trades),
        "n_mfe_raw": len(raw_vals),
        "n_mfe_negative_raw": sum(1 for v in raw_vals if v < 0),
        "mean_mfe_raw": None if not raw_vals else float(np.mean(raw_vals)),
        "mean_mfe_floor0": None if not floor_vals else float(np.mean(floor_vals)),
        "cache_mfe_non_null": len(cache_vals),
        "cache_mfe_negative": sum(1 for v in cache_vals if v < 0),
        "includes_zero_at_entry": False,
        "floors_at_zero": True if floor_vals else False,
        "MFE_METRIC_VALID": True if floor_vals else False,
        "defect_prior": "path_metrics max without inserting 0 at t=fill produced negative MFE",
        "correction": "metric-only floor at 0 on research reports; PnL/Fill/ENTRY/EXIT unchanged",
    }


def compare_b0_b1(b0: dict[str, Any], b1: dict[str, Any]) -> dict[str, Any]:
    def slice_pnl(pack: dict[str, Any], key: str) -> Any:
        rec = pack.get(key) or {}
        return {"trades": rec.get("trades"), "PnL": rec.get("pnl"), "PF": rec.get("PF")}

    ex0 = b0.get("exclude") or {}
    ex1 = b1.get("exclude") or {}
    c0 = b0.get("concentration") or {}
    c1 = b1.get("concentration") or {}
    rows = []
    keys = [
        ("trades", b0.get("trades"), b1.get("trades")),
        ("PnL", b0.get("PnL"), b1.get("PnL")),
        ("PF", b0.get("PF"), b1.get("PF")),
        ("maxDD", b0.get("maxDD"), b1.get("maxDD")),
        ("avg_trade", b0.get("avg_trade"), b1.get("avg_trade")),
        ("median_trade", b0.get("median_trade"), b1.get("median_trade")),
        ("positive_day_rate", b0.get("positive_day_rate"), b1.get("positive_day_rate")),
        ("median_daily_pnl", b0.get("median_daily_pnl"), b1.get("median_daily_pnl")),
        ("AM_PnL", (b0.get("AM") or {}).get("pnl"), (b1.get("AM") or {}).get("pnl")),
        ("PM_PnL", (b0.get("PM") or {}).get("pnl"), (b1.get("PM") or {}).get("pnl")),
        ("first_entry_PnL", (b0.get("first_entry") or {}).get("pnl"), (b1.get("first_entry") or {}).get("pnl")),
        ("re_entry_PnL", (b0.get("re_entry") or {}).get("pnl"), (b1.get("re_entry") or {}).get("pnl")),
        ("PnL_ex_top3_trades", (ex0.get("ex_top3_trades") or {}).get("PnL"), (ex1.get("ex_top3_trades") or {}).get("PnL")),
        ("PnL_ex_top3_days", (ex0.get("ex_top3_days") or {}).get("PnL"), (ex1.get("ex_top3_days") or {}).get("PnL")),
        ("PnL_ex_top_symbol", (ex0.get("ex_top_symbol") or {}).get("PnL"), (ex1.get("ex_top_symbol") or {}).get("PnL")),
        ("PnL_ex_285A", (ex0.get("ex_285A") or {}).get("PnL"), (ex1.get("ex_285A") or {}).get("PnL")),
        ("top1_trade_share", c0.get("top1_trade_share"), c1.get("top1_trade_share")),
        ("top3_trade_share", c0.get("top3_trade_share"), c1.get("top3_trade_share")),
        ("top10_trade_share", c0.get("top10_trade_share"), c1.get("top10_trade_share")),
        ("top1_day_share", c0.get("top1_day_share"), c1.get("top1_day_share")),
        ("top3_day_share", c0.get("top3_day_share"), c1.get("top3_day_share")),
        ("top1_symbol_share", c0.get("top1_symbol_share"), c1.get("top1_symbol_share")),
        ("top3_symbol_share", c0.get("top3_symbol_share"), c1.get("top3_symbol_share")),
    ]
    for name, a, b in keys:
        rows.append({"metric": name, "B0": a, "B1_OOF": b})
    return {"rows": rows, "B0_slices": {"AM": slice_pnl(b0, "AM"), "PM": slice_pnl(b0, "PM")}, "B1_slices": {"AM": slice_pnl(b1, "AM"), "PM": slice_pnl(b1, "PM")}}
