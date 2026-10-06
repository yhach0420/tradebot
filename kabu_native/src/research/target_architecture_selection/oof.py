"""27 frozen specs. Train T1/T2/T3. Rank by score, then join MFE/DOWNSIDE. No nested selector."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, ranking_no_backfill
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
from research.target_architecture_selection import (
    COMPONENT_KEYS,
    ELIGIBLE_DAYS,
    TARGET_KEYS,
    TRAIN_IDS,
)


def matched_base(rows: list[dict[str, Any]], feats: list[str]) -> list[dict[str, Any]]:
    """Same candidate set for every component join. Rank does not depend on which y is joined."""
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
        if _f(r.get("T1")) is None or _f(r.get("T2")) is None or _f(r.get("T3")) is None:
            continue
        out.append(r)
    return out


def component_top3(matched: list[dict[str, Any]], ykey: str) -> dict[str, Any]:
    labeled = []
    for r in matched:
        rec = dict(r)
        rec[TARGET] = rec.get(ykey)
        labeled.append(rec)
    rank_cur = ranking_no_backfill(labeled, "current_score")
    rank_new = ranking_no_backfill(labeled, SCORE_KEY)
    paired = paired_daily(rank_cur, rank_new)
    days = [
        {"date": r.get("date"), "DELTA_TOP3_UPLIFT": r.get("DELTA_TOP3_UPLIFT")}
        for r in (paired.get("days") or [])
    ]
    t3 = delta_series_stats(
        [float(v) for r in days if (v := _f(r.get("DELTA_TOP3_UPLIFT"))) is not None]
    )
    return {
        "MATCHED_RANKING_ROWS": len(matched),
        "MODEL_TOP3": rank_new.get("TOP3_UPLIFT"),
        "CURRENT_TOP3": rank_cur.get("TOP3_UPLIFT"),
        "TOP3_DELTA": paired.get("TOP3_DELTA_MEAN"),
        "TOP3_DELTA_MEAN": t3.get("mean"),
        "TOP3_DELTA_MEDIAN": t3.get("median"),
        "days": days,
    }


def _label_train(rows: list[dict[str, Any]], ykey: str) -> list[dict[str, Any]]:
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
    print(f"  component-spec start {sid}", flush=True)
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    by_train: dict[str, Any] = {}
    for tid in TRAIN_IDS:
        ykey = TARGET_KEYS[tid]
        labeled = _label_train(prepared, ykey)
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
        base = matched_base(scored, feats)
        comps = {}
        for cname, ckey in COMPONENT_KEYS.items():
            comps[cname] = component_top3(base, ckey)
        n_mfe = comps["MFE"].get("MATCHED_RANKING_ROWS")
        n_dn = comps["DOWNSIDE"].get("MATCHED_RANKING_ROWS")
        n_path = comps["PATH"].get("MATCHED_RANKING_ROWS")
        if n_mfe != n_dn or n_mfe != n_path:
            return {
                "ok": False,
                "spec_id": sid,
                "blocker": f"component_matched_mismatch mfe={n_mfe} down={n_dn} path={n_path}",
            }
        by_train[tid] = {"outer_folds": fold_n, "MATCHED_RANKING_ROWS": n_mfe, "components": comps}
        print(
            f"  {sid} train={tid} folds={fold_n} matched={n_mfe} "
            f"mfe_d={comps['MFE'].get('TOP3_DELTA')} down_d={comps['DOWNSIDE'].get('TOP3_DELTA')}",
            flush=True,
        )
    return {
        "ok": True,
        "spec_id": sid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "n_features": spec.get("n_features"),
        "by_train": by_train,
    }
