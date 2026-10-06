"""Nested OOF with precommitted Top1 primary and tie-break. No Top3/PnL selection."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f, ranking_no_backfill
from research.entry_objective_redesign_c3.oof import (
    TARGET,
    executable_rows,
    fit_on_universe,
    fit_ridge_prepared,
    normalize_rows,
    ranking_pop,
    score_prepared,
    score_rows,
    spec_grid,
    spec_label,
)
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.top1_objective_development import SCORE_KEY


def matched_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if _f(r.get("current_score")) is None:
            continue
        if _f(r.get(SCORE_KEY)) is None:
            continue
        out.append(r)
    return out


def _tie_key_top1(row: dict[str, Any]) -> tuple:
    t1 = row.get("mean_daily_top1_uplift")
    ex1 = row.get("ex_best_day_top1_delta")
    ex3 = row.get("ex_top3_days_top1_delta")
    pos = row.get("positive_day_count")
    nfeat = int(row.get("n_features") or 99)
    alpha = float(row.get("alpha") or 0.0)
    return (
        -(float(t1) if t1 is not None else -1e18),
        -(float(ex1) if ex1 is not None else -1e18),
        -(float(ex3) if ex3 is not None else -1e18),
        -(int(pos) if pos is not None else -10**9),
        nfeat,
        -alpha,
    )


def eval_spec_cv_top1(rows: list[dict[str, Any]], spec: dict[str, Any], days: list[str]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    uplifts: list[float] = []
    deltas: list[float] = []
    for hold in days:
        tr = [r for r in prepared if str(r.get("date")) != hold]
        te = [r for r in prepared if str(r.get("date")) == hold]
        if not ranking_pop(tr) or not te:
            continue
        fit = fit_ridge_prepared(ranking_pop(tr), spec)
        scored = score_prepared(te, fit, key=SCORE_KEY)
        matched = matched_rows(scored)
        if not matched:
            continue
        m = ranking_no_backfill(matched, SCORE_KEY)
        c = ranking_no_backfill(matched, "current_score")
        u = m.get("TOP1_UPLIFT")
        cu = c.get("TOP1_UPLIFT")
        if u is None:
            continue
        uplifts.append(float(u))
        if cu is not None:
            deltas.append(float(u) - float(cu))
    dstat = delta_series_stats(deltas)
    return {
        **spec,
        "spec_id": spec_label(spec),
        "mean_daily_top1_uplift": float(np.mean(uplifts)) if uplifts else None,
        "ex_best_day_top1_delta": dstat.get("ex_best_day"),
        "ex_top3_days_top1_delta": dstat.get("ex_top3_days"),
        "positive_day_count": int(dstat.get("positive_days") or 0),
        "inner_n_days": len(uplifts),
        "inner_n_delta_days": len(deltas),
    }


def inner_select(train: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({str(r.get("date")) for r in ranking_pop(train)})
    grid = spec_grid()
    ranked = []
    for i, spec in enumerate(grid, start=1):
        rec = eval_spec_cv_top1(train, spec, days)
        ranked.append(rec)
        print(
            f"    inner {i}/{len(grid)} {rec['spec_id']} "
            f"top1={rec.get('mean_daily_top1_uplift')} "
            f"ex1={rec.get('ex_best_day_top1_delta')} pos={rec.get('positive_day_count')} "
            f"n={rec.get('inner_n_days')}",
            flush=True,
        )
    ranked.sort(key=_tie_key_top1)
    return {"inner_leaderboard": ranked, "selected": ranked[0] if ranked else {}}


def slim_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_set": spec.get("feature_set"),
        "features": spec.get("features"),
        "n_features": spec.get("n_features"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "spec_id": spec.get("spec_id"),
        "mean_daily_top1_uplift": spec.get("mean_daily_top1_uplift"),
        "ex_best_day_top1_delta": spec.get("ex_best_day_top1_delta"),
        "ex_top3_days_top1_delta": spec.get("ex_top3_days_top1_delta"),
        "positive_day_count": spec.get("positive_day_count"),
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
        "m4_t1_px",
    )
    return [{k: r.get(k) for k in keep} for r in rows]


def slim_rank(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("daily_")}


def process_outer_fold(payload: dict[str, Any]) -> dict[str, Any]:
    """ProcessPool worker. Outer day isolated from spec/norm/alpha/fit."""
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
    scored = score_rows(executable_rows(test), fit, key=SCORE_KEY)
    matched = matched_rows(scored)
    hold_m = ranking_no_backfill(matched, SCORE_KEY)
    cur_m = ranking_no_backfill(matched, "current_score")
    fold = {
        "holdout_day": hold,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "n_features": spec.get("n_features"),
        "spec_id": spec.get("spec_id"),
        "inner_top1": spec.get("mean_daily_top1_uplift"),
        "inner_ex_best_day_top1_delta": spec.get("ex_best_day_top1_delta"),
        "inner_ex_top3_days_top1_delta": spec.get("ex_top3_days_top1_delta"),
        "inner_positive_day_count": spec.get("positive_day_count"),
        "holdout_top1": hold_m.get("TOP1_UPLIFT"),
        "holdout_cur_top1": cur_m.get("TOP1_UPLIFT"),
        "holdout_top1_delta": (
            float(hold_m["TOP1_UPLIFT"]) - float(cur_m["TOP1_UPLIFT"])
            if hold_m.get("TOP1_UPLIFT") is not None and cur_m.get("TOP1_UPLIFT") is not None
            else None
        ),
        "inner_leaderboard_head": [
            {
                "spec_id": x.get("spec_id"),
                "mean_daily_top1_uplift": x.get("mean_daily_top1_uplift"),
                "ex_best_day_top1_delta": x.get("ex_best_day_top1_delta"),
                "positive_day_count": x.get("positive_day_count"),
            }
            for x in (inner.get("inner_leaderboard") or [])[:5]
        ],
        "fit_kind": fit.get("kind"),
        "fit_train_n": fit.get("train_n"),
        "matched_n": len(matched),
    }
    return {
        "ok": True,
        "hold": hold,
        "spec": slim_spec(spec),
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


def spec_stability(folds: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [str(f.get("spec_id") or "") for f in folds]
    counts = Counter(keys)
    most = counts.most_common(1)[0] if counts else ("", 0)
    share = (most[1] / len(keys)) if keys else None
    return {
        "feature_set_counts": dict(Counter(str(f.get("feature_set") or "") for f in folds)),
        "normalization_counts": dict(Counter(str(f.get("normalization") or "") for f in folds)),
        "alpha_counts": dict(Counter(str(f.get("alpha")) for f in folds)),
        "SPEC_COUNTS": dict(counts),
        "MOST_COMMON_SPEC": most[0],
        "MOST_COMMON_SPEC_SHARE": share,
        "n_folds": len(keys),
    }
