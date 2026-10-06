"""Replay frozen 27-spec OOF scores. Same specs, same LODO. Not a new model."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import SCORE_KEY
from research.am_entry_profit_improvement.models import contamination_n, fit_spec, score_spec
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS


def _filter_days(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date") or "") in want]


def _day_rows(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date") or "") == str(day)]


def process_oof_scores(payload: dict[str, Any]) -> dict[str, Any]:
    """Fit frozen spec on 17 days, score held-out day. Repeat 18 days. Return OOF predictions only."""
    warnings.filterwarnings("ignore")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    spec = dict(payload.get("spec") or {})
    days = [str(d) for d in (payload.get("days") or list(ELIGIBLE_DAYS))]
    spec_id = str(spec.get("spec_id") or "")
    leak = {
        "HELDOUT_FIT_LEAK_N": 0,
        "PM_ROWS_USED_N": 0,
        "TARGET_CONTAMINATION_N": contamination_n(list(spec.get("features") or [])),
        "FUTURE_FEATURE_USE_N": 0,
        "NEW_MODEL_N": 0,
        "INNER_SELECTION_N": 0,
    }
    cache_path = Path(str(payload.get("cache_path") or ""))
    if cache_path.is_file():
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if saved.get("ok") and saved.get("spec_id") == spec_id and saved.get("scores"):
            print(f"  score-cache {spec_id} n={len(saved.get('scores') or {})}", flush=True)
            return saved
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    for r in rows:
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
    if leak["PM_ROWS_USED_N"]:
        return {"ok": False, "spec_id": spec_id, "blocker": "PM_ROWS", "integrity": leak}

    scores: dict[str, float | None] = {}
    print(f"  spec-scores {spec_id} days={len(days)}", flush=True)
    for outer in days:
        train_days = [d for d in days if d != outer]
        if outer in train_days or len(train_days) != 17:
            leak["HELDOUT_FIT_LEAK_N"] += 1
            return {"ok": False, "spec_id": spec_id, "blocker": "TRAIN_DAY_CONTRACT", "integrity": leak}
        train = _filter_days(rows, train_days)
        if any(str(r.get("date") or "") == outer for r in train):
            leak["HELDOUT_FIT_LEAK_N"] += 1
            return {"ok": False, "spec_id": spec_id, "blocker": "OUTER_IN_TRAIN", "integrity": leak}
        fit = fit_spec(train, spec)
        scored = score_spec(_day_rows(rows, outer), fit)
        for r in scored:
            scores[row_key(r)] = r.get(SCORE_KEY)
    body = {
        "ok": True,
        "spec_id": spec_id,
        "architecture_id": spec.get("architecture_id"),
        "representation_id": spec.get("representation_id"),
        "fold_n": len(days),
        "score_n": len(scores),
        "scores": scores,
        "integrity": leak,
    }
    print(f"  spec-scores {spec_id} n={len(scores)}", flush=True)
    return body
