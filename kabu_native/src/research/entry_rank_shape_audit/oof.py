"""Fixed-spec LODO and nested restitch. Rank then join TARGET. No inner selection."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import (
    _cohorts,
    _f,
    rank_group,
    ranking_no_backfill,
)
from research.c3_canonical_retrain.analyze import matched_rows
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.analyze import paired_daily, ranking_gate
from research.entry_objective_redesign_c3.oof import (
    SCORE_KEY,
    TARGET,
    executable_rows,
    fit_ridge_prepared,
    normalize_rows,
    ranking_pop,
    score_prepared,
    spec_label,
)
from research.entry_rank_shape_audit import ELIGIBLE_DAYS


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    a = np.asarray(xs, dtype=float)
    b = np.asarray(ys, dtype=float)
    if float(np.std(a)) <= 1e-18 or float(np.std(b)) <= 1e-18:
        return None
    c = np.corrcoef(a, b)[0, 1]
    if c != c:
        return None
    return float(c)


def delta_series_stats(deltas: list[float]) -> dict[str, Any]:
    pos = sum(1 for v in deltas if v > 0)
    neg = sum(1 for v in deltas if v < 0)
    ordered = sorted(deltas, reverse=True)
    rest_best = ordered[1:] if len(ordered) > 1 else []
    rest_top3 = ordered[3:] if len(ordered) > 3 else []
    return {
        "mean": float(np.mean(deltas)) if deltas else None,
        "median": float(np.median(deltas)) if deltas else None,
        "positive_days": pos,
        "negative_days": neg,
        "ex_best_day": float(np.mean(rest_best)) if rest_best else None,
        "ex_top3_days": float(np.mean(rest_top3)) if rest_top3 else None,
        "n_days": len(deltas),
    }


def top1_delta_stats(paired: dict[str, Any]) -> dict[str, Any]:
    deltas = []
    for r in paired.get("days") or []:
        v = _f(r.get("DELTA_TOP1_UPLIFT"))
        if v is None:
            continue
        deltas.append(float(v))
    return delta_series_stats(deltas)


def rank_position_targets(rows: list[dict[str, Any]], score_key: str, kmax: int = 5) -> dict[str, Any]:
    """Score-rank first, then join TARGET. Do not skip missing-target names."""
    by = _cohorts(rows)
    by_day: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    n_used = 0
    n_excl = 0
    miss = {k: 0 for k in range(1, kmax + 1)}
    for (day, _an), grp in sorted(by.items()):
        ranked = rank_group(grp, score_key)
        if len(ranked) < int(MIN_COHORT_N):
            n_excl += 1
            continue
        n_used += 1
        for k in range(1, kmax + 1):
            if k > len(ranked):
                miss[k] += 1
                continue
            yt = _f(ranked[k - 1].get(TARGET))
            if yt is None:
                miss[k] += 1
                continue
            by_day[str(day)][k].append(float(yt))

    out: dict[str, Any] = {
        "score_key": score_key,
        "n_cohorts_used": n_used,
        "n_cohorts_excluded_lt10": n_excl,
        "rank_target_missing": miss,
    }
    for k in range(1, kmax + 1):
        daily = []
        for _d, ranks in sorted(by_day.items()):
            vs = ranks.get(k) or []
            if vs:
                daily.append(float(np.mean(vs)))
        out[f"RANK{k}_TARGET"] = float(np.mean(daily)) if daily else None
        out[f"RANK{k}_N_DAYS"] = len(daily)
    r2 = out.get("RANK2_TARGET")
    r3 = out.get("RANK3_TARGET")
    r4 = out.get("RANK4_TARGET")
    r5 = out.get("RANK5_TARGET")
    out["RANK2_3_MEAN"] = (float(r2) + float(r3)) / 2.0 if r2 is not None and r3 is not None else None
    out["RANK4_5_MEAN"] = (float(r4) + float(r5)) / 2.0 if r4 is not None and r5 is not None else None
    return out


def slim_rank(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("daily_")}


def spec_metrics_from_scored(scored: list[dict[str, Any]]) -> dict[str, Any]:
    matched = matched_rows(scored)
    rank_cur = ranking_no_backfill(matched, "current_score")
    rank_c3 = ranking_no_backfill(matched, SCORE_KEY)
    paired = paired_daily(rank_cur, rank_c3)
    gate = ranking_gate(rank_cur, rank_c3, paired)
    return {
        "MATCHED_RANKING_ROWS": len(matched),
        "CURRENT": slim_rank(rank_cur),
        "C3": slim_rank(rank_c3),
        "paired": {k: v for k, v in paired.items() if k != "days"},
        "days": paired.get("days") or [],
        "gate": gate,
        "TOP1_UPLIFT": rank_c3.get("TOP1_UPLIFT"),
        "TOP3_UPLIFT": rank_c3.get("TOP3_UPLIFT"),
        "TOP5_UPLIFT": rank_c3.get("TOP5_UPLIFT"),
        "CURRENT_TOP3_UPLIFT": rank_cur.get("TOP3_UPLIFT"),
        "TOP3_DELTA": paired.get("TOP3_DELTA_MEAN"),
        "TOP3_DELTA_POSITIVE_DAYS": paired.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": paired.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "TOP3_DELTA_EX_BEST_DAY": paired.get("TOP3_DELTA_EX_BEST_DAY"),
        "TOP3_DELTA_EX_TOP3_DAYS": paired.get("TOP3_DELTA_EX_TOP3_DAYS"),
        "MEAN_DAILY_SPEARMAN": rank_c3.get("MEAN_DAILY_SPEARMAN"),
        "OOF_RANKING_GATE_PASS": bool(gate.get("OOF_RANKING_GATE_PASS")),
        "gate_fail": list(gate.get("fail") or []),
    }


def process_fixed_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """ProcessPool worker. One frozen spec, 18-day LODO, no inner selection."""
    spec = dict(payload.get("spec") or {})
    sid = spec_label(spec)
    rows_path = Path(payload["rows_path"])
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    print(f"  fixed-spec start {sid}", flush=True)
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    scored: list[dict[str, Any]] = []
    fold_n = 0
    for hold in days:
        tr = [r for r in prepared if str(r.get("date")) != hold]
        te = [r for r in prepared if str(r.get("date")) == hold]
        if not ranking_pop(tr) or not te:
            continue
        fit = fit_ridge_prepared(ranking_pop(tr), spec)
        scored.extend(score_prepared(te, fit))
        fold_n += 1
    metrics = spec_metrics_from_scored(scored)
    print(
        f"  fixed-spec done {sid} folds={fold_n} matched={metrics.get('MATCHED_RANKING_ROWS')} "
        f"top3={metrics.get('TOP3_UPLIFT')} delta={metrics.get('TOP3_DELTA')} "
        f"ak={metrics.get('OOF_RANKING_GATE_PASS')}",
        flush=True,
    )
    return {
        "ok": True,
        "spec_id": sid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "n_features": spec.get("n_features"),
        "outer_folds": fold_n,
        **metrics,
    }
