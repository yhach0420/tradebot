"""Nested OOF on Canonical rows. Same 27-spec tiebreak. Rank then join TARGET."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from research.canonical_entry_performance_rebase.analyze import ranking_no_backfill
from research.entry_objective_redesign_c3.oof import (
    SCORE_KEY,
    TARGET,
    executable_rows,
    fit_on_universe,
    fit_ridge_prepared,
    ranking_pop,
    score_prepared,
    score_rows,
    spec_grid,
    spec_label,
    _tie_key,
    normalize_rows,
)


def eval_spec_cv(rows: list[dict[str, Any]], spec: dict[str, Any], days: list[str]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    top3s: list[float] = []
    top1s: list[float] = []
    top5s: list[float] = []
    sps: list[float] = []
    for hold in days:
        tr = [r for r in prepared if str(r.get("date")) != hold]
        te = [r for r in prepared if str(r.get("date")) == hold]
        if not ranking_pop(tr) or not te:
            continue
        fit = fit_ridge_prepared(ranking_pop(tr), spec)
        scored = score_prepared(te, fit)
        m = ranking_no_backfill(scored, SCORE_KEY)
        if m.get("TOP3_UPLIFT") is not None:
            top3s.append(float(m["TOP3_UPLIFT"]))
        if m.get("TOP1_UPLIFT") is not None:
            top1s.append(float(m["TOP1_UPLIFT"]))
        if m.get("TOP5_UPLIFT") is not None:
            top5s.append(float(m["TOP5_UPLIFT"]))
        if m.get("MEAN_DAILY_SPEARMAN") is not None:
            sps.append(float(m["MEAN_DAILY_SPEARMAN"]))
    return {
        **spec,
        "spec_id": spec_label(spec),
        "mean_daily_top3_uplift": float(np.mean(top3s)) if top3s else None,
        "mean_daily_top1_uplift": float(np.mean(top1s)) if top1s else None,
        "mean_daily_top5_uplift": float(np.mean(top5s)) if top5s else None,
        "mean_daily_spearman": float(np.mean(sps)) if sps else None,
        "inner_n_days": len(top3s),
    }


def inner_select(train: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({str(r.get("date")) for r in ranking_pop(train)})
    grid = spec_grid()
    ranked = []
    for i, spec in enumerate(grid, start=1):
        rec = eval_spec_cv(train, spec, days)
        ranked.append(rec)
        print(
            f"    inner {i}/{len(grid)} {rec['spec_id']} "
            f"top3={rec.get('mean_daily_top3_uplift')} n={rec.get('inner_n_days')}",
            flush=True,
        )
    ranked.sort(key=_tie_key)
    return {"inner_leaderboard": ranked, "selected": ranked[0] if ranked else {}}


def _slim_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_set": spec.get("feature_set"),
        "features": spec.get("features"),
        "n_features": spec.get("n_features"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "spec_id": spec.get("spec_id"),
        "mean_daily_top3_uplift": spec.get("mean_daily_top3_uplift"),
        "mean_daily_top1_uplift": spec.get("mean_daily_top1_uplift"),
        "mean_daily_top5_uplift": spec.get("mean_daily_top5_uplift"),
        "mean_daily_spearman": spec.get("mean_daily_spearman"),
        "inner_n_days": spec.get("inner_n_days"),
    }


def slim_fit(fit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "feature_set",
        "features",
        "normalization",
        "alpha",
        "kind",
        "scaler_mean",
        "scaler_scale",
        "coef",
        "intercept",
        "train_n",
    )
    return {k: fit.get(k) for k in keys}


def slim_scored(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = (
        "date",
        "symbol",
        "anchor",
        "session",
        "current_score",
        SCORE_KEY,
        TARGET,
        "executable_at_t0",
        "modeling_bucket",
    )
    return [{k: r.get(k) for k in keep} for r in rows]


def process_outer_fold(payload: dict[str, Any]) -> dict[str, Any]:
    """ProcessPool worker. Outer held-out day never enters spec selection or fit."""
    hold = str(payload["hold"])
    rows_path = Path(payload["rows_path"])
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    train = [r for r in rows if str(r.get("date")) != hold]
    test = [r for r in rows if str(r.get("date")) == hold]
    print(f"  outer holdout {hold} train_days={len({str(r.get('date')) for r in train})}", flush=True)
    inner = inner_select(train)
    spec = inner.get("selected") or {}
    fit = fit_on_universe(train, spec)
    scored = score_rows(executable_rows(test), fit)
    hold_m = ranking_no_backfill(scored, SCORE_KEY)
    cur_m = ranking_no_backfill([r for r in scored if r.get("current_score") is not None], "current_score")
    fold = {
        "holdout_day": hold,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "n_features": spec.get("n_features"),
        "spec_id": spec.get("spec_id"),
        "inner_top3": spec.get("mean_daily_top3_uplift"),
        "holdout_c3_top3": hold_m.get("TOP3_UPLIFT"),
        "holdout_cur_top3": cur_m.get("TOP3_UPLIFT"),
        "holdout_c3_top1": hold_m.get("TOP1_UPLIFT"),
        "holdout_cur_top1": cur_m.get("TOP1_UPLIFT"),
        "holdout_c3_top5": hold_m.get("TOP5_UPLIFT"),
        "holdout_cur_top5": cur_m.get("TOP5_UPLIFT"),
        "holdout_c3_spearman": hold_m.get("MEAN_DAILY_SPEARMAN"),
        "holdout_cur_spearman": cur_m.get("MEAN_DAILY_SPEARMAN"),
        "inner_leaderboard_head": [
            {"spec_id": x.get("spec_id"), "mean_daily_top3_uplift": x.get("mean_daily_top3_uplift")}
            for x in (inner.get("inner_leaderboard") or [])[:5]
        ],
        "fit_kind": fit.get("kind"),
        "fit_train_n": fit.get("train_n"),
    }
    return {
        "ok": True,
        "hold": hold,
        "spec": _slim_spec(spec),
        "fit": slim_fit(fit),
        "fold": fold,
        "scored": slim_scored(scored),
    }


def stitch_oof(fold_bodies: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[tuple[str, str, str], float] = {}
    oof_rows: list[dict[str, Any]] = []
    specs = []
    folds = []
    fits: dict[str, dict[str, Any]] = {}
    for b in sorted(fold_bodies, key=lambda x: str(x.get("hold") or "")):
        spec = b.get("spec") or {}
        specs.append(spec)
        folds.append(b.get("fold") or {})
        fits[str(b.get("hold"))] = b.get("fit") or {}
        for r in b.get("scored") or []:
            rec = dict(r)
            oof_rows.append(rec)
            sc = rec.get(SCORE_KEY)
            if sc is None:
                continue
            by_key[(str(rec.get("date")), str(rec.get("anchor")), str(rec.get("symbol")))] = float(sc)
    attached = []
    for r in rows:
        rec = dict(r)
        rec[SCORE_KEY] = by_key.get((str(r.get("date")), str(r.get("anchor")), str(r.get("symbol"))))
        attached.append(rec)
    keys = [spec_label(s) if s.get("spec_id") is None else str(s.get("spec_id")) for s in specs]
    counts = Counter(keys)
    most = counts.most_common(1)[0] if counts else ("", 0)
    return {
        "folds": folds,
        "selected_specs": specs,
        "oof_rows": oof_rows,
        "attached": attached,
        "fits": fits,
        "SPEC_COUNTS": dict(counts),
        "MOST_COMMON_SPEC": most[0],
        "MOST_COMMON_SPEC_SHARE": (most[1] / len(keys)) if keys else None,
    }
