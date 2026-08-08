"""Attribution, audits, pass criteria, verdict."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import (
    HISTORICAL_DAYS,
    PASS_MATCHED_DELTA_MIN,
    PASS_MAX_NEG_DAYS,
    STRESS_DAYS_285A,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
    VERDICT_D,
)

MAX_SYMBOL_CONTRIB = 0.50


def _mean(evals: list[dict[str, Any]], H: int) -> float | None:
    rs = [float(e[f"return_{H}"]) for e in evals if e.get(f"return_{H}_valid")]
    return float(np.mean(rs)) if rs else None


def _day_means(evals: list[dict[str, Any]], H: int) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for e in evals:
        if e.get(f"return_{H}_valid"):
            by[e["date"]].append(float(e[f"return_{H}"]))
    return {d: float(np.mean(v)) for d, v in by.items() if len(v) >= 3}


def raw_delta(child: list[dict[str, Any]], parent: list[dict[str, Any]]) -> dict[str, Any]:
    c300, p300 = _mean(child, 300), _mean(parent, 300)
    c600, p600 = _mean(child, 600), _mean(parent, 600)
    return {
        "child_ret300": c300, "parent_ret300": p300,
        "raw_delta300": (c300 - p300) if c300 is not None and p300 is not None else None,
        "child_ret600": c600, "parent_ret600": p600,
        "raw_delta600": (c600 - p600) if c600 is not None and p600 is not None else None,
    }


def matched_delta(child: list[dict[str, Any]], parent: list[dict[str, Any]]) -> dict[str, Any]:
    """Match child to parent: same day, same symbol if possible, same session, same 5-min bucket."""
    def bucket(t: float) -> int:
        return int(float(t) // 300) * 300

    # index parent by (date, symbol, session, bucket) and fallback (date, session, bucket)
    by_sym: dict[tuple, list[float]] = defaultdict(list)
    by_sym600: dict[tuple, list[float]] = defaultdict(list)
    by_bucket: dict[tuple, list[float]] = defaultdict(list)
    by_bucket600: dict[tuple, list[float]] = defaultdict(list)
    for e in parent:
        b = bucket(e["signal_t"])
        ks = (e["date"], e["symbol"], e["session"], b)
        kb = (e["date"], e["session"], b)
        if e.get("return_300_valid"):
            by_sym[ks].append(float(e["return_300"]))
            by_bucket[kb].append(float(e["return_300"]))
        if e.get("return_600_valid"):
            by_sym600[ks].append(float(e["return_600"]))
            by_bucket600[kb].append(float(e["return_600"]))

    d300, d600 = [], []
    for e in child:
        b = bucket(e["signal_t"])
        ks = (e["date"], e["symbol"], e["session"], b)
        kb = (e["date"], e["session"], b)
        if e.get("return_300_valid"):
            pool = by_sym.get(ks) or by_bucket.get(kb)
            if pool:
                d300.append(float(e["return_300"]) - float(np.mean(pool)))
        if e.get("return_600_valid"):
            pool = by_sym600.get(ks) or by_bucket600.get(kb)
            if pool:
                d600.append(float(e["return_600"]) - float(np.mean(pool)))

    return {
        "matched_n_300": len(d300),
        "matched_delta300": float(np.mean(d300)) if d300 else None,
        "matched_n_600": len(d600),
        "matched_delta600": float(np.mean(d600)) if d600 else None,
    }


def day_level(
    parent: list[dict[str, Any]],
    old: list[dict[str, Any]],
    causal: list[dict[str, Any]],
) -> dict[str, Any]:
    p300, o300, c300 = _day_means(parent, 300), _day_means(old, 300), _day_means(causal, 300)
    p600, o600, c600 = _day_means(parent, 600), _day_means(old, 600), _day_means(causal, 600)
    days = []
    neg_cp300 = neg_cp600 = 0
    deltas300, deltas600 = [], []
    for d in HISTORICAL_DAYS:
        cp3 = (c300[d] - p300[d]) if d in c300 and d in p300 else None
        cp6 = (c600[d] - p600[d]) if d in c600 and d in p600 else None
        co3 = (c300[d] - o300[d]) if d in c300 and d in o300 else None
        co6 = (c600[d] - o600[d]) if d in c600 and d in o600 else None
        if cp3 is not None:
            deltas300.append(cp3)
            if cp3 < 0:
                neg_cp300 += 1
        if cp6 is not None:
            deltas600.append(cp6)
            if cp6 < 0:
                neg_cp600 += 1
        days.append({
            "date": d,
            "parent_ret300": p300.get(d), "old_ret300": o300.get(d), "causal_ret300": c300.get(d),
            "parent_ret600": p600.get(d), "old_ret600": o600.get(d), "causal_ret600": c600.get(d),
            "causal_parent_delta300": cp3, "causal_parent_delta600": cp6,
            "causal_old_delta300": co3, "causal_old_delta600": co6,
        })

    def conc(deltas: list[float]):
        if not deltas:
            return {}
        neg_mass = np.clip(-np.asarray(deltas), 0, None)
        tot = float(neg_mass.sum()) + 1e-12
        shares = neg_mass / tot
        return {
            "negative_days": int(np.sum(np.asarray(deltas) < 0)),
            "positive_days": int(np.sum(np.asarray(deltas) > 0)),
            "median_day_delta": float(np.median(deltas)),
            "worst_day_delta": float(np.min(deltas)),
            "best_day_delta": float(np.max(deltas)),
            "max_day_contribution": float(np.max(shares)),
        }

    return {
        "days": days,
        "negative_delta_days_300": neg_cp300,
        "negative_delta_days_600": neg_cp600,
        "concentration_300": conc(deltas300),
        "concentration_600": conc(deltas600),
    }


def loso_causal_parent(
    causal: list[dict[str, Any]],
    parent: list[dict[str, Any]],
) -> dict[str, Any]:
    def delta_omit(omit: str | None) -> float | None:
        c = [e for e in causal if omit is None or e["symbol"] != omit]
        p = [e for e in parent if omit is None or e["symbol"] != omit]
        cm, pm = _mean(c, 300), _mean(p, 300)
        if cm is None or pm is None:
            return None
        return cm - pm

    full = delta_omit(None)
    if full is None:
        return {"severe": False}
    syms = sorted({e["symbol"] for e in causal})
    rows = []
    for sym in syms:
        d = delta_omit(sym)
        if d is None:
            continue
        rows.append({"omitted_symbol": sym, "delta300": d, "contribution": full - d})
    # concentration on causal absolute negative
    rs = [(e["symbol"], float(e["return_300"])) for e in causal if e.get("return_300_valid")]
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
        "positive_loso": sum(1 for r in rows if (r["delta300"] or 0) < 0),
        "negative_loso": sum(1 for r in rows if (r["delta300"] or 0) >= 0),
        "max_symbol_contribution": max_share,
        "worst_omitted_symbol": worst["omitted_symbol"],
        "best_omitted_symbol": best["omitted_symbol"],
        "severe_symbol_concentration": max_share > MAX_SYMBOL_CONTRIB,
        "check_285A_stress": {d: "NOT_PRESENT" for d in STRESS_DAYS_285A},
    }


def feature_eligibility_audit(grid_meta: list[dict[str, Any]], grids: list[dict[str, Any]], control: list, parent: list) -> dict[str, Any]:
    """10S_GRID → FEATURE_OK coverage; ret shift via control vs need quality-ok clock — use meta + control."""
    total_grid = sum(m["grid_n"] for m in grid_meta)
    quality_ok = sum(m["quality_ok_n"] for m in grid_meta)
    feature_ok = sum(m["feature_ok_n"] for m in grid_meta)
    # time-of-day loss: feature_ok fraction by hour
    by_hour_q = defaultdict(int)
    by_hour_f = defaultdict(int)
    for r in grids:
        # grid_time may be iso; fall back epoch
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            JST = ZoneInfo("Asia/Tokyo")
            h = datetime.fromtimestamp(float(r["grid_epoch"]), tz=JST).hour
        except Exception:
            continue
        if r.get("quality_status") == "OK":
            by_hour_q[h] += 1
        if r.get("feature_status") == "OK":
            by_hour_f[h] += 1
    # ret: FEATURE_OK fixed clock (control) vs we don't have quality-only clock arm;
    # report coverage loss primarily; ret300 of control as feature-ok pool proxy
    return {
        "total_grid_rows": total_grid,
        "quality_ok_rows": quality_ok,
        "feature_ok_rows": feature_ok,
        "coverage_loss_quality_to_feature": (
            1.0 - feature_ok / quality_ok if quality_ok else None
        ),
        "feature_ok_ret300": _mean(control, 300),
        "feature_ok_ret600": _mean(control, 600),
        "parent_ret300": _mean(parent, 300),
        "parent_ret600": _mean(parent, 600),
        "delta300_feature_ok_minus_parent": (
            (_mean(control, 300) - _mean(parent, 300))
            if _mean(control, 300) is not None and _mean(parent, 300) is not None else None
        ),
        "delta600_feature_ok_minus_parent": (
            (_mean(control, 600) - _mean(parent, 600))
            if _mean(control, 600) is not None and _mean(parent, 600) is not None else None
        ),
        "by_hour_feature_ok_share": {
            str(h): (by_hour_f[h] / by_hour_q[h] if by_hour_q[h] else None)
            for h in sorted(set(by_hour_q) | set(by_hour_f))
        },
        "note": "FEATURE_OK vs 10S quality: no future label gate; ret shift uses FEATURE_OK_FIXED_GRID_CONTROL vs PARENT",
    }


def session_end_censoring(old: list, causal: list) -> dict[str, Any]:
    buckets = (">30min", "15-30min", "5-15min", "<5min")

    def hist(evals):
        counts = {b: 0 for b in buckets}
        for e in evals:
            m = e.get("minutes_to_session_close")
            if m is None:
                continue
            if m > 30:
                counts[">30min"] += 1
            elif m > 15:
                counts["15-30min"] += 1
            elif m > 5:
                counts["5-15min"] += 1
            else:
                counts["<5min"] += 1
        n = sum(counts.values()) or 1
        return {k: {"n": v, "share": v / n} for k, v in counts.items()}

    return {"old": hist(old), "causal": hist(causal)}


def anchor_spacing(old_rows: list[dict[str, Any]], causal_anchors: list[dict[str, Any]]) -> dict[str, Any]:
    def spacing(rows, key_epoch="grid_epoch"):
        by = defaultdict(list)
        for r in rows:
            by[(r["date"], r["symbol"], r.get("session"))].append(float(r[key_epoch]))
        gaps = []
        dens = []
        for k, eps in by.items():
            eps = sorted(eps)
            dens.append(len(eps))
            for i in range(1, len(eps)):
                gaps.append(eps[i] - eps[i - 1])
        if not gaps:
            return {"anchors_per_symbol_session_median": None}
        arr = np.asarray(gaps, dtype=float)
        return {
            "n_symbol_sessions": len(by),
            "anchors_per_symbol_session_median": float(np.median(dens)),
            "anchors_per_symbol_session_mean": float(np.mean(dens)),
            "spacing_p10": float(np.quantile(arr, 0.10)),
            "spacing_p50": float(np.quantile(arr, 0.50)),
            "spacing_p90": float(np.quantile(arr, 0.90)),
            "median_seconds_between_anchors": float(np.median(arr)),
        }

    # old population rows as anchors
    return {
        "old": spacing(old_rows, "grid_epoch"),
        "causal": spacing(causal_anchors, "grid_epoch"),
    }


def pass_criteria(
    *,
    prefix_ok: bool,
    uses_future: bool,
    matched: dict[str, Any],
    day: dict[str, Any],
) -> dict[str, Any]:
    m300 = matched.get("matched_delta300")
    m600 = matched.get("matched_delta600")
    neg300 = day.get("negative_delta_days_300", 99)
    neg600 = day.get("negative_delta_days_600", 99)
    ok = (
        (not uses_future)
        and prefix_ok
        and m300 is not None and m600 is not None
        and m300 >= PASS_MATCHED_DELTA_MIN
        and m600 >= PASS_MATCHED_DELTA_MIN
        and neg300 <= PASS_MAX_NEG_DAYS
        and neg600 <= PASS_MAX_NEG_DAYS
    )
    return {
        "pass": ok,
        "uses_future_information": uses_future,
        "prefix_invariance": prefix_ok,
        "matched_delta300": m300,
        "matched_delta600": m600,
        "matched_delta300_ok": m300 is not None and m300 >= PASS_MATCHED_DELTA_MIN,
        "matched_delta600_ok": m600 is not None and m600 >= PASS_MATCHED_DELTA_MIN,
        "neg_days_300": neg300,
        "neg_days_600": neg600,
        "neg_days_ok": neg300 <= PASS_MAX_NEG_DAYS and neg600 <= PASS_MAX_NEG_DAYS,
    }


def decide_verdict(
    *,
    criteria: dict[str, Any],
    feat_audit: dict[str, Any],
    prefix_status: str,
) -> dict[str, Any]:
    if prefix_status == "CAUSALITY_VIOLATION":
        return {
            "verdict": "E1_X33_CAUSALITY_VIOLATION",
            "next_phase": None,
            "freeze_manifest": False,
        }
    if prefix_status == "INSUFFICIENT_TESTS":
        return {
            "verdict": VERDICT_D,
            "next_phase": None,
            "freeze_manifest": False,
        }
    # Feature eligibility major bias: large coverage loss AND large ret deterioration
    cov_loss = feat_audit.get("coverage_loss_quality_to_feature")
    d300 = feat_audit.get("delta300_feature_ok_minus_parent")
    if cov_loss is not None and cov_loss > 0.5 and d300 is not None and d300 <= -5.0:
        return {
            "verdict": VERDICT_C,
            "next_phase": "X33_FEATURE_ELIGIBILITY_REPAIR",
            "freeze_manifest": False,
        }
    if criteria["pass"]:
        return {
            "verdict": VERDICT_A,
            "next_phase": "X34_ABSOLUTE_RISE_ENTRY_V3_NESTED_CV",
            "freeze_manifest": True,
        }
    return {
        "verdict": VERDICT_B,
        "next_phase": "X33B_CAUSAL_ANCHOR_ARCHITECTURE_DISCOVERY",
        "freeze_manifest": False,
    }
