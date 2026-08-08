"""Neutrality metrics: balanced aggregate, matched, day, coverage, LOSO/LODO."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x32_upstream_attribution import HORIZONS_SEC

from . import (
    HISTORICAL_DAYS,
    MAX_SYMBOL_CONTRIB,
    PASS_COVERAGE,
    PASS_MATCHED_MIN,
    PASS_MAX_NEG_DAYS,
    PASS_MEDIAN_ABS_DELTA,
    STRESS_DAYS_285A,
    VERDICT_FAIL,
    VERDICT_PASS,
)

JST = ZoneInfo("Asia/Tokyo")

TOD_BUCKETS = (
    ("09:00-09:30", (9, 0), (9, 30)),
    ("09:30-10:30", (9, 30), (10, 30)),
    ("10:30-AM_close", (10, 30), (11, 30)),
    ("PM_open-13:30", (12, 30), (13, 30)),
    ("13:30-14:30", (13, 30), (14, 30)),
    ("14:30-close", (14, 30), (15, 30)),
)


def _ss_key(e: dict[str, Any]) -> tuple:
    return (e["date"], e["symbol"], e["session"])


def symbol_session_means(evals: list[dict[str, Any]], H: int) -> dict[tuple, float]:
    by: dict[tuple, list[float]] = defaultdict(list)
    for e in evals:
        if e.get(f"return_{H}_valid"):
            by[_ss_key(e)].append(float(e[f"return_{H}"]))
    return {k: float(np.mean(v)) for k, v in by.items() if v}


def balanced_global(evals: list[dict[str, Any]], H: int) -> float | None:
    """Equal weight per symbol-session, then mean across symbol-sessions."""
    ss = symbol_session_means(evals, H)
    if not ss:
        return None
    return float(np.mean(list(ss.values())))


def episode_mean(evals: list[dict[str, Any]], H: int) -> float | None:
    rs = [float(e[f"return_{H}"]) for e in evals if e.get(f"return_{H}_valid")]
    return float(np.mean(rs)) if rs else None


def summarize_arm(evals: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "episodes": len(evals),
        "symbols": len({e["symbol"] for e in evals}),
        "symbol_days": len({(e["date"], e["symbol"]) for e in evals}),
        "symbol_sessions": len({_ss_key(e) for e in evals}),
    }
    for H in HORIZONS_SEC:
        out[f"ret{H}_episode"] = episode_mean(evals, H)
        out[f"ret{H}_balanced"] = balanced_global(evals, H)
    mfes = [float(e["mfe"]) for e in evals if e.get("mfe") is not None and np.isfinite(e["mfe"])]
    maes = [float(e["mae"]) for e in evals if e.get("mae") is not None and np.isfinite(e["mae"])]
    out["mfe"] = float(np.mean(mfes)) if mfes else None
    out["mae"] = float(np.mean(maes)) if maes else None
    return out


def matched_comparison(neutral: list[dict[str, Any]], parent: list[dict[str, Any]]) -> dict[str, Any]:
    def bucket(t: float) -> int:
        return int(float(t) // 300) * 300

    p_idx: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for e in parent:
        key = (e["date"], e["symbol"], e["session"], bucket(e["signal_t"]))
        for H in HORIZONS_SEC:
            if e.get(f"return_{H}_valid"):
                p_idx[key][H].append(float(e[f"return_{H}"]))

    deltas = {H: [] for H in HORIZONS_SEC}
    n = 0
    for e in neutral:
        key = (e["date"], e["symbol"], e["session"], bucket(e["signal_t"]))
        if key not in p_idx:
            continue
        n += 1
        for H in HORIZONS_SEC:
            if e.get(f"return_{H}_valid") and p_idx[key][H]:
                deltas[H].append(float(e[f"return_{H}"]) - float(np.mean(p_idx[key][H])))

    out = {"matched_n": n}
    for H in HORIZONS_SEC:
        out[f"delta{H}"] = float(np.mean(deltas[H])) if deltas[H] else None
    return out


def day_level(neutral: list[dict[str, Any]], parent: list[dict[str, Any]]) -> dict[str, Any]:
    def day_bal(evals, H):
        ss = symbol_session_means(evals, H)
        by_day: dict[str, list[float]] = defaultdict(list)
        for (d, _s, _sess), v in ss.items():
            by_day[d].append(v)
        return {d: float(np.mean(vs)) for d, vs in by_day.items() if vs}

    n300, p300 = day_bal(neutral, 300), day_bal(parent, 300)
    n600, p600 = day_bal(neutral, 600), day_bal(parent, 600)
    days = []
    abs300, abs600 = [], []
    neg300 = neg600 = 0
    for d in HISTORICAL_DAYS:
        d3 = (n300[d] - p300[d]) if d in n300 and d in p300 else None
        d6 = (n600[d] - p600[d]) if d in n600 and d in p600 else None
        if d3 is not None:
            abs300.append(abs(d3))
            if d3 < 0:
                neg300 += 1
        if d6 is not None:
            abs600.append(abs(d6))
            if d6 < 0:
                neg600 += 1
        days.append({
            "date": d,
            "neutral_ret300": n300.get(d), "parent_ret300": p300.get(d), "delta300": d3,
            "neutral_ret600": n600.get(d), "parent_ret600": p600.get(d), "delta600": d6,
        })
    return {
        "days": days,
        "negative_delta_days_300": neg300,
        "negative_delta_days_600": neg600,
        "median_abs_delta300": float(np.median(abs300)) if abs300 else None,
        "median_abs_delta600": float(np.median(abs600)) if abs600 else None,
    }


def coverage_audit(
    planned: list[dict[str, Any]],
    executed: list[dict[str, Any]],
    parent: list[dict[str, Any]],
) -> dict[str, Any]:
    parent_ss = {_ss_key(e) for e in parent}
    planned_ss = {_ss_key(a) for a in planned}
    exec_ss = {_ss_key(e) for e in executed}
    # coverage vs parent symbol-sessions
    covered = parent_ss & exec_ss
    share = len(covered) / len(parent_ss) if parent_ss else 0.0

    # anchors per symbol-session (executed)
    counts = defaultdict(int)
    by_ss_times: dict[tuple, list[float]] = defaultdict(list)
    for e in executed:
        k = _ss_key(e)
        counts[k] += 1
        by_ss_times[k].append(float(e["signal_t"]))
    dens = list(counts.values()) if counts else [0]
    gaps = []
    for times in by_ss_times.values():
        times = sorted(times)
        for i in range(1, len(times)):
            gaps.append(times[i] - times[i - 1])
    gap_arr = np.asarray(gaps, dtype=float) if gaps else np.asarray([0.0])
    return {
        "parent_symbol_sessions": len(parent_ss),
        "planned_symbol_sessions": len(planned_ss),
        "executed_symbol_sessions": len(exec_ss),
        "symbol_sessions_with_ge1_neutral": len(covered),
        "coverage_share": share,
        "coverage_ok": share >= PASS_COVERAGE,
        "anchors_per_symbol_session_median": float(np.median(dens)),
        "anchors_per_symbol_session_mean": float(np.mean(dens)),
        "spacing_p10": float(np.quantile(gap_arr, 0.10)) if gaps else None,
        "spacing_p50": float(np.quantile(gap_arr, 0.50)) if gaps else None,
        "spacing_p90": float(np.quantile(gap_arr, 0.90)) if gaps else None,
        "median_seconds_between_anchors": float(np.median(gap_arr)) if gaps else None,
    }


def tod_coverage(executed: list[dict[str, Any]]) -> dict[str, Any]:
    def bucket_name(epoch: float) -> str | None:
        dt = datetime.fromtimestamp(float(epoch), tz=JST)
        cur = dt.hour * 60 + dt.minute
        for name, (h0, m0), (h1, m1) in TOD_BUCKETS:
            if h0 * 60 + m0 <= cur < h1 * 60 + m1:
                return name
        return None

    counts = {name: 0 for name, *_ in TOD_BUCKETS}
    for e in executed:
        b = bucket_name(e["signal_t"])
        if b:
            counts[b] += 1
    total = sum(counts.values()) or 1
    return {
        "buckets": [
            {"bucket": k, "n": v, "share": v / total} for k, v in counts.items()
        ],
        "no_bucket_exclusion_by_performance": True,
    }


def loso(neutral: list[dict[str, Any]], parent: list[dict[str, Any]]) -> dict[str, Any]:
    def bal_delta(omit: str | None) -> float | None:
        n = [e for e in neutral if omit is None or e["symbol"] != omit]
        p = [e for e in parent if omit is None or e["symbol"] != omit]
        nb, pb = balanced_global(n, 300), balanced_global(p, 300)
        if nb is None or pb is None:
            return None
        return nb - pb

    full = bal_delta(None)
    if full is None:
        return {"severe_symbol_concentration": False}
    syms = sorted({e["symbol"] for e in neutral})
    rows = []
    for sym in syms:
        d = bal_delta(sym)
        if d is None:
            continue
        rows.append({"omitted_symbol": sym, "delta300": d, "contribution": abs(full - d)})
    # concentration on |delta| mass — use neutral episode negative mass
    rs = [(e["symbol"], float(e["return_300"])) for e in neutral if e.get("return_300_valid")]
    neg = np.array([max(0.0, -r) for _, r in rs])
    tot = float(neg.sum()) + 1e-12
    max_share = 0.0
    for sym in syms:
        idx = [i for i, (s, _) in enumerate(rs) if s == sym]
        max_share = max(max_share, float(neg[idx].sum()) / tot if idx else 0.0)
    if not rows:
        return {"full_delta300": full, "severe_symbol_concentration": False}
    worst = max(rows, key=lambda x: x["contribution"])
    best = min(rows, key=lambda x: x["contribution"])
    return {
        "full_delta300": full,
        "positive_loso": sum(1 for r in rows if abs(r["delta300"] or 0) <= PASS_MEDIAN_ABS_DELTA),
        "negative_loso": sum(1 for r in rows if abs(r["delta300"] or 0) > PASS_MEDIAN_ABS_DELTA),
        "max_symbol_contribution": max_share,
        "worst_omitted_symbol": worst["omitted_symbol"],
        "best_omitted_symbol": best["omitted_symbol"],
        "severe_symbol_concentration": max_share > MAX_SYMBOL_CONTRIB,
        "check_285A_stress": {d: "NOT_PRESENT" for d in STRESS_DAYS_285A},
    }


def lodo(day_audit: dict[str, Any]) -> dict[str, Any]:
    """Leave-one-day-out on daily deltas (no param fit)."""
    days = day_audit["days"]
    d300 = [d["delta300"] for d in days if d["delta300"] is not None]
    d600 = [d["delta600"] for d in days if d["delta600"] is not None]
    rows = []
    for omit in HISTORICAL_DAYS:
        xs = [d["delta300"] for d in days if d["date"] != omit and d["delta300"] is not None]
        ys = [d["delta600"] for d in days if d["date"] != omit and d["delta600"] is not None]
        rows.append({
            "omitted_day": omit,
            "mean_delta300": float(np.mean(xs)) if xs else None,
            "mean_delta600": float(np.mean(ys)) if ys else None,
            "median_abs_delta300": float(np.median(np.abs(xs))) if xs else None,
            "median_abs_delta600": float(np.median(np.abs(ys))) if ys else None,
        })
    valid = [r for r in rows if r["mean_delta300"] is not None]
    worst = max(valid, key=lambda r: abs(r["mean_delta300"])) if valid else None
    best = min(valid, key=lambda r: abs(r["mean_delta300"])) if valid else None
    return {
        "full_mean_delta300": float(np.mean(d300)) if d300 else None,
        "full_mean_delta600": float(np.mean(d600)) if d600 else None,
        "rows": rows,
        "worst_omitted_day": worst["omitted_day"] if worst else None,
        "best_omitted_day": best["omitted_day"] if best else None,
    }


def pass_and_verdict(
    *,
    prefix_ok: bool,
    uses_future: bool,
    matched: dict[str, Any],
    day: dict[str, Any],
    cov: dict[str, Any],
    loso_res: dict[str, Any],
) -> dict[str, Any]:
    m300 = matched.get("delta300")
    m600 = matched.get("delta600")
    checks = {
        "future_dependency_false": not uses_future,
        "prefix_invariance_pass": prefix_ok,
        "coverage_ge_90": bool(cov.get("coverage_ok")),
        "matched_delta300_ge_-2": m300 is not None and m300 >= PASS_MATCHED_MIN,
        "matched_delta600_ge_-2": m600 is not None and m600 >= PASS_MATCHED_MIN,
        "median_abs_delta300_le_2": (
            day.get("median_abs_delta300") is not None
            and day["median_abs_delta300"] <= PASS_MEDIAN_ABS_DELTA
        ),
        "median_abs_delta600_le_2": (
            day.get("median_abs_delta600") is not None
            and day["median_abs_delta600"] <= PASS_MEDIAN_ABS_DELTA
        ),
        "neg_days_300_le_8": day.get("negative_delta_days_300", 99) <= PASS_MAX_NEG_DAYS,
        "neg_days_600_le_8": day.get("negative_delta_days_600", 99) <= PASS_MAX_NEG_DAYS,
        "no_severe_symbol_concentration": not loso_res.get("severe_symbol_concentration"),
    }
    ok = all(checks.values())
    return {
        "pass": ok,
        "checks": checks,
        "verdict": VERDICT_PASS if ok else VERDICT_FAIL,
        "next_phase": (
            "E1_X34_ABSOLUTE_RISE_ENTRY_V3" if ok else None
        ),
        "freeze_manifest": ok,
    }
