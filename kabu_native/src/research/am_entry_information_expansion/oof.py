"""18-day LODO OOF for one arm×representation. Held-out day never in fit."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

from research.am_entry_information_expansion import (
    ARM_BASE_LOGIT,
    ARM_BASE_RF,
    ARM_X14_LOGIT,
    ARM_X14_RF,
    FAMILY_LOGIT,
    FAMILY_RF,
    INFO_BASE,
    INFO_X14,
    JOINT_SCORE_KEY,
    X14_BUNDLE,
)
from research.am_entry_information_expansion.models import contamination_n, fit_family, score_family
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.direct_joint_objective.oof import representation_grid
from research.entry_objective_redesign_c3 import FEATURE_SETS


def _filter_days(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date") or "") in want]


def _day_rows(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date") or "") == str(day)]


def arm_meta(arm_id: str) -> dict[str, str]:
    if arm_id == ARM_BASE_LOGIT:
        return {"information": INFO_BASE, "family": FAMILY_LOGIT}
    if arm_id == ARM_BASE_RF:
        return {"information": INFO_BASE, "family": FAMILY_RF}
    if arm_id == ARM_X14_LOGIT:
        return {"information": INFO_X14, "family": FAMILY_LOGIT}
    if arm_id == ARM_X14_RF:
        return {"information": INFO_X14, "family": FAMILY_RF}
    return {"information": "", "family": ""}


def expanded_features(base_feats: list[str], information: str) -> list[str]:
    if information != INFO_X14:
        return list(base_feats)
    return list(dict.fromkeys((*base_feats, *X14_BUNDLE)))


def arm_spec_grid(arm_id: str) -> list[dict[str, Any]]:
    meta = arm_meta(arm_id)
    out = []
    for rep in representation_grid():
        feats = expanded_features(list(rep.get("features") or FEATURE_SETS.get(rep.get("feature_set")) or []), meta["information"])
        rec = dict(rep)
        rec["architecture_id"] = arm_id
        rec["family"] = meta["family"]
        rec["information"] = meta["information"]
        rec["features"] = feats
        rec["n_features"] = len(feats)
        rec["spec_id"] = f"{arm_id}|{rep.get('representation_id')}"
        out.append(rec)
    return out


def process_oof_scores(payload: dict[str, Any]) -> dict[str, Any]:
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
        "HYPERPARAMETER_SEARCH_N": 0,
    }
    cache_path = Path(str(payload.get("cache_path") or ""))
    store_proba = bool(payload.get("store_proba"))
    if cache_path.is_file():
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if saved.get("ok") and saved.get("spec_id") == spec_id and saved.get("scores"):
            sample = next(iter((saved.get("scores") or {}).values()), None)
            proba_ok = isinstance(sample, dict) and "P_WIN" in (sample or {})
            if (store_proba and proba_ok) or (not store_proba):
                print(f"  score-cache {spec_id} n={len(saved.get('scores') or {})}", flush=True)
                return saved
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    for r in rows:
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
    if leak["PM_ROWS_USED_N"]:
        return {"ok": False, "spec_id": spec_id, "blocker": "PM_ROWS", "integrity": leak}

    scores: dict[str, Any] = {}
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
        fit = fit_family(train, spec)
        scored = score_family(_day_rows(rows, outer), fit)
        for r in scored:
            if store_proba:
                scores[row_key(r)] = {
                    "JOINT_SCORE": r.get(JOINT_SCORE_KEY),
                    "P_WIN": r.get("P_WIN"),
                    "P_LOSS": r.get("P_LOSS"),
                    "P_NEUTRAL": r.get("P_NEUTRAL"),
                }
            else:
                scores[row_key(r)] = r.get(JOINT_SCORE_KEY)
        print(f"    hold={outer} train_n={fit.get('train_n')} kind={fit.get('kind')}", flush=True)
    body = {
        "ok": True,
        "spec_id": spec_id,
        "architecture_id": spec.get("architecture_id"),
        "representation_id": spec.get("representation_id"),
        "family": spec.get("family"),
        "information": spec.get("information"),
        "fold_n": len(days),
        "score_n": len(scores),
        "store_proba": store_proba,
        "scores": scores,
        "integrity": leak,
    }
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(body, default=str), encoding="utf-8")
    return body
