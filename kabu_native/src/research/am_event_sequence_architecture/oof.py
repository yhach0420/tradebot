"""18-day LODO RF. Sequence posteriors attached per outer fold. No in-sample seq meta."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

from research.am_entry_information_expansion import ARM_X14_RF, FAMILY_RF, INFO_X14, JOINT_SCORE_KEY
from research.am_entry_information_expansion.oof import expanded_features
from research.am_event_sequence_architecture import SEQ_POSTERIOR3
from research.am_event_sequence_architecture.models import contamination_n, fit_rf, score_rf
from research.am_event_sequence_architecture.precommit import extra_features_for
from research.am_event_sequence_architecture.sequence_oof import attach_seq_fold
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.direct_joint_objective.oof import representation_grid
from research.entry_objective_redesign_c3 import FEATURE_SETS


def _filter_days(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date") or "") in want]


def _day_rows(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date") or "") == str(day)]


def arm_spec_grid(arm_id: str) -> list[dict[str, Any]]:
    extra = list(extra_features_for(arm_id))
    out = []
    for rep in representation_grid():
        cand = expanded_features(
            list(rep.get("features") or FEATURE_SETS.get(rep.get("feature_set")) or []),
            INFO_X14,
        )
        rec = dict(rep)
        rec["architecture_id"] = arm_id
        rec["family"] = FAMILY_RF
        rec["information"] = INFO_X14
        rec["frozen_model"] = ARM_X14_RF
        rec["candidate_features"] = list(cand)
        rec["extra_features"] = list(extra)
        rec["features"] = list(dict.fromkeys((*cand, *extra)))
        rec["n_features"] = len(rec["features"])
        rec["n_candidate_features"] = len(cand)
        rec["n_extra_features"] = len(extra)
        rec["spec_id"] = f"{arm_id}|{rep.get('representation_id')}"
        rec["uses_seq"] = any(f in SEQ_POSTERIOR3 for f in extra)
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
    uses_seq = bool(spec.get("uses_seq"))
    leak = {
        "HELDOUT_FIT_LEAK_N": 0,
        "PM_ROWS_USED_N": 0,
        "TARGET_CONTAMINATION_N": contamination_n(list(spec.get("features") or [])),
        "SEQ_META_IN_SAMPLE_TRAIN_N": 0,
        "SEQ_INNER_HELDOUT_FIT_LEAK_N": 0,
        "SEQ_OUTER_HELDOUT_FIT_LEAK_N": 0,
        "SEQ_SCALER_HELDOUT_USE_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "MODEL_SEARCH_N": 0,
    }
    cache_path = Path(str(payload.get("cache_path") or ""))
    if cache_path.is_file():
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if saved.get("ok") and saved.get("spec_id") == spec_id and saved.get("scores"):
            sample = next(iter((saved.get("scores") or {}).values()), None)
            if isinstance(sample, dict) and "P_WIN" in (sample or {}):
                print(f"  score-cache {spec_id} n={len(saved.get('scores') or {})}", flush=True)
                return saved
    reuse = Path(str(payload.get("reuse_cache_path") or ""))
    if reuse.is_file() and not uses_seq:
        saved = json.loads(reuse.read_text(encoding="utf-8"))
        sample = next(iter((saved.get("scores") or {}).values()), None)
        if saved.get("ok") and saved.get("scores") and isinstance(sample, dict) and "P_WIN" in (sample or {}):
            body = dict(saved)
            body["spec_id"] = spec_id
            body["architecture_id"] = spec.get("architecture_id")
            body["representation_id"] = spec.get("representation_id")
            body["reused_from"] = str(reuse)
            print(f"  reuse-cache {spec_id} n={len(body.get('scores') or {})}", flush=True)
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(body, default=str), encoding="utf-8")
            return body
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    for r in rows:
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
    if leak["PM_ROWS_USED_N"]:
        return {"ok": False, "spec_id": spec_id, "blocker": "PM_ROWS", "integrity": leak}
    stack = {}
    if uses_seq:
        stack = json.loads(Path(payload["stack_path"]).read_text(encoding="utf-8"))

    scores: dict[str, Any] = {}
    print(f"  spec-scores {spec_id} days={len(days)} extra={len(spec.get('extra_features') or [])} seq={uses_seq}", flush=True)
    for outer in days:
        train_days = [d for d in days if d != outer]
        if outer in train_days or len(train_days) != 17:
            leak["HELDOUT_FIT_LEAK_N"] += 1
            return {"ok": False, "spec_id": spec_id, "blocker": "TRAIN_DAY_CONTRACT", "integrity": leak}
        train = _filter_days(rows, train_days)
        test = _day_rows(rows, outer)
        if any(str(r.get("date") or "") == outer for r in train):
            leak["HELDOUT_FIT_LEAK_N"] += 1
            return {"ok": False, "spec_id": spec_id, "blocker": "OUTER_IN_TRAIN", "integrity": leak}
        if uses_seq:
            train, tr_leak = attach_seq_fold(train, stack, outer, role="train")
            test, te_leak = attach_seq_fold(test, stack, outer, role="test")
            leak["SEQ_META_IN_SAMPLE_TRAIN_N"] += int(tr_leak.get("SEQ_META_IN_SAMPLE_TRAIN_N") or 0)
            leak["SEQ_META_IN_SAMPLE_TRAIN_N"] += int(te_leak.get("SEQ_META_IN_SAMPLE_TRAIN_N") or 0)
            if int(tr_leak.get("SEQ_LOOKUP_MISS_N") or 0) or int(te_leak.get("SEQ_LOOKUP_MISS_N") or 0):
                return {
                    "ok": False,
                    "spec_id": spec_id,
                    "blocker": "SEQ_LOOKUP_MISS",
                    "integrity": leak,
                    "miss_train": tr_leak.get("SEQ_LOOKUP_MISS_N"),
                    "miss_test": te_leak.get("SEQ_LOOKUP_MISS_N"),
                }
        fit = fit_rf(train, spec)
        scored = score_rf(test, fit)
        for r in scored:
            scores[row_key(r)] = {
                "JOINT_SCORE": r.get(JOINT_SCORE_KEY),
                "P_WIN": r.get("P_WIN"),
                "P_LOSS": r.get("P_LOSS"),
                "P_NEUTRAL": r.get("P_NEUTRAL"),
            }
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
        "store_proba": True,
        "scores": scores,
        "integrity": leak,
    }
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(body, default=str), encoding="utf-8")
    return body
