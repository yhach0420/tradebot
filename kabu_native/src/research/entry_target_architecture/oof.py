"""27 frozen specs × 5 targets. 18-day LODO. No inner spec selector."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, ranking_no_backfill, slim_rank
from research.entry_objective_redesign_c3.analyze import paired_daily
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
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.entry_target_architecture import ELIGIBLE_DAYS, TARGET_IDS, TARGET_KEYS


def _delta_from_days(days: list[dict[str, Any]], field: str) -> dict[str, Any]:
    xs = []
    for r in days or []:
        v = _f(r.get(field))
        if v is None:
            continue
        xs.append(float(v))
    return delta_series_stats(xs)


def matched_for_spec(rows: list[dict[str, Any]], feats: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if not r.get("common_cohort"):
            continue
        if _f(r.get("current_score")) is None:
            continue
        if _f(r.get(SCORE_KEY)) is None:
            continue
        if feats and not spec_features_ok(r, feats):
            continue
        if _f(r.get(TARGET)) is None:
            continue
        out.append(r)
    return out


def spec_metrics_from_scored(scored: list[dict[str, Any]], feats: list[str]) -> dict[str, Any]:
    matched = matched_for_spec(scored, feats)
    rank_cur = ranking_no_backfill(matched, "current_score")
    rank_new = ranking_no_backfill(matched, SCORE_KEY)
    paired = paired_daily(rank_cur, rank_new)
    days = paired.get("days") or []
    t3 = _delta_from_days(days, "DELTA_TOP3_UPLIFT")
    t1 = _delta_from_days(days, "DELTA_TOP1_UPLIFT")
    t5 = _delta_from_days(days, "DELTA_TOP5_UPLIFT")
    slim_days = [{"date": r.get("date"), "DELTA_TOP3_UPLIFT": r.get("DELTA_TOP3_UPLIFT")} for r in days]
    return {
        "MATCHED_RANKING_ROWS": len(matched),
        "CURRENT": slim_rank(rank_cur),
        "MODEL": slim_rank(rank_new),
        "paired": {k: v for k, v in paired.items() if k != "days"},
        "days": slim_days,
        "MEAN_DAILY_SPEARMAN": rank_new.get("MEAN_DAILY_SPEARMAN"),
        "TOP1_UPLIFT": rank_new.get("TOP1_UPLIFT"),
        "TOP3_UPLIFT": rank_new.get("TOP3_UPLIFT"),
        "TOP5_UPLIFT": rank_new.get("TOP5_UPLIFT"),
        "CURRENT_TOP1_UPLIFT": rank_cur.get("TOP1_UPLIFT"),
        "CURRENT_TOP3_UPLIFT": rank_cur.get("TOP3_UPLIFT"),
        "CURRENT_TOP5_UPLIFT": rank_cur.get("TOP5_UPLIFT"),
        "TOP1_DELTA": t1.get("mean"),
        "TOP3_DELTA": paired.get("TOP3_DELTA_MEAN"),
        "TOP5_DELTA": t5.get("mean"),
        "TOP1_DELTA_MEAN": t1.get("mean"),
        "TOP1_DELTA_MEDIAN": t1.get("median"),
        "TOP1_DELTA_POSITIVE_DAYS": t1.get("positive_days"),
        "TOP1_DELTA_NEGATIVE_DAYS": t1.get("negative_days"),
        "TOP1_DELTA_EX_BEST_DAY": t1.get("ex_best_day"),
        "TOP1_DELTA_EX_TOP3_DAYS": t1.get("ex_top3_days"),
        "TOP3_DELTA_MEAN": t3.get("mean"),
        "TOP3_DELTA_MEDIAN": t3.get("median"),
        "TOP3_DELTA_POSITIVE_DAYS": t3.get("positive_days"),
        "TOP3_DELTA_NEGATIVE_DAYS": t3.get("negative_days"),
        "TOP3_DELTA_EX_BEST_DAY": t3.get("ex_best_day"),
        "TOP3_DELTA_EX_TOP3_DAYS": t3.get("ex_top3_days"),
        "TOP5_DELTA_MEAN": t5.get("mean"),
        "TOP5_DELTA_MEDIAN": t5.get("median"),
        "TOP5_DELTA_POSITIVE_DAYS": t5.get("positive_days"),
        "TOP5_DELTA_NEGATIVE_DAYS": t5.get("negative_days"),
        "TOP5_DELTA_EX_BEST_DAY": t5.get("ex_best_day"),
        "TOP5_DELTA_EX_TOP3_DAYS": t5.get("ex_top3_days"),
    }


def _label_target(rows: list[dict[str, Any]], ykey: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec[TARGET] = rec.get(ykey)
        out.append(rec)
    return out


def process_fixed_spec(payload: dict[str, Any]) -> dict[str, Any]:
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
    by_target: dict[str, Any] = {}
    for tid in TARGET_IDS:
        ykey = TARGET_KEYS[tid]
        labeled = _label_target(prepared, ykey)
        scored: list[dict[str, Any]] = []
        fold_n = 0
        for hold in days:
            tr = [r for r in labeled if str(r.get("date")) != hold]
            te = [r for r in labeled if str(r.get("date")) == hold]
            if not ranking_pop(tr) or not te:
                continue
            fit = fit_ridge_prepared(ranking_pop(tr), spec)
            scored.extend(score_prepared(te, fit))
            fold_n += 1
        metrics = spec_metrics_from_scored(scored, feats)
        metrics["outer_folds"] = fold_n
        by_target[tid] = metrics
        print(
            f"  {sid} {tid} folds={fold_n} matched={metrics.get('MATCHED_RANKING_ROWS')} "
            f"top3={metrics.get('TOP3_UPLIFT')} delta={metrics.get('TOP3_DELTA')}",
            flush=True,
        )
    return {
        "ok": True,
        "spec_id": sid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "n_features": spec.get("n_features"),
        "by_target": by_target,
    }
