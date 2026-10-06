"""Parity, 9-rep median, information CASE A-F. No fitting. No selection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.am_direct_exec_u_development.oof import process_am_direct
from research.am_entry_information_reassessment import (
    CANONICAL_FEATURES,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
)
from research.am_entry_information_reassessment.diagnose import attach_raw_features, evaluate_information
from research.am_wait5_two_stage_development.analyze import _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close, _median
from research.wait5_session_target_learnability import POS_REP_MIN


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def process_information(payload: dict[str, Any]) -> dict[str, Any]:
    scored_path = Path(str(payload.get("scored_cache_path") or ""))
    scored: list[dict[str, Any]] = []
    body: dict[str, Any] = {}
    if scored_path.is_file():
        saved = json.loads(scored_path.read_text(encoding="utf-8"))
        if saved.get("ok") and saved.get("rows"):
            scored = list(saved.get("rows") or [])
            body = {
                "ok": True,
                "representation_id": saved.get("representation_id") or payload.get("representation_id"),
                "feature_set": saved.get("feature_set"),
                "normalization": saved.get("normalization"),
                "outer_folds": saved.get("outer_folds"),
                "FILL_ONLY": saved.get("FILL_ONLY"),
                "DIRECT_EXEC_U": saved.get("DIRECT_EXEC_U"),
                "integrity": saved.get("integrity") or {},
            }
            print(f"  scored-cache {body.get('representation_id')} n={len(scored)}", flush=True)
    if not scored:
        body = process_am_direct({**payload, "keep_scored": True})
        if not body.get("ok"):
            return body
        scored = list(body.pop("scored") or [])
        raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
        feat_miss = attach_raw_features(scored, list(raw.get("rows") or []))
        leak = dict(body.get("integrity") or {})
        leak["JOIN_RAW_FEATURE_MISS_N"] = int(feat_miss)
        body["integrity"] = leak
        if scored_path.name:
            scored_path.parent.mkdir(parents=True, exist_ok=True)
            slim = {
                "ok": True,
                "representation_id": body.get("representation_id"),
                "feature_set": body.get("feature_set"),
                "normalization": body.get("normalization"),
                "outer_folds": body.get("outer_folds"),
                "FILL_ONLY": body.get("FILL_ONLY"),
                "DIRECT_EXEC_U": body.get("DIRECT_EXEC_U"),
                "integrity": body.get("integrity"),
                "rows": scored,
            }
            scored_path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")
    else:
        raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
        feat_miss = attach_raw_features(scored, list(raw.get("rows") or []))
        leak = dict(body.get("integrity") or {})
        leak["JOIN_RAW_FEATURE_MISS_N"] = int(leak.get("JOIN_RAW_FEATURE_MISS_N") or 0) + int(feat_miss)
        body["integrity"] = leak
    info = evaluate_information(scored, [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)])
    join = dict(info.pop("join") or {})
    join["JOIN_MISS_N"] = int((body.get("integrity") or {}).get("JOIN_FILL_SCORE_MISS_N") or 0)
    body.pop("selected", None)
    body["info"] = info
    body["join"] = join
    body["ok"] = True
    return body


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        info = b.get("info") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "outer_folds": b.get("outer_folds"),
            **{
                k: info.get(k)
                for k in (
                    "COHORT_N",
                    "ANY_FU_GOOD_RATE",
                    "ANY_FUD_GOOD_RATE",
                    "FRONTIER_FU_GOOD_RATE",
                    "FRONTIER_FUD_GOOD_RATE",
                    "FILL_ONLY_ON_FRONTIER_RATE",
                    "DIRECT_ON_FRONTIER_RATE",
                    "FUD_GOOD_FILL_PERCENTILE_MEDIAN",
                    "FUD_GOOD_EXECU_PERCENTILE_MEDIAN",
                    "PRED_FILL_EXECU_SPEARMAN_OVERALL",
                    "OFF_FRONTIER_FUD_GOOD_COHORT_N",
                    "OFF_FRONTIER_FUD_GOOD_SUBSET_N",
                    "FALSE_DOMINATOR_N",
                    "FALSE_DOMINATOR_COHORT_N",
                    "FALSE_DOMINATOR_PER_COHORT_MEDIAN",
                    "FD_FILL_FAIL_RATE",
                    "FD_U_FAIL_RATE",
                    "FD_D_FAIL_RATE",
                    "FD_MULTIPLE_FAIL_RATE",
                    "DELTA_PRED_FILL_MEDIAN",
                    "DELTA_PRED_FILL_MEAN",
                    "DELTA_PRED_EXEC_U_MEDIAN",
                    "DELTA_PRED_EXEC_U_MEAN",
                    "DELTA_ACT_FILL_MEDIAN",
                    "DELTA_ACT_FILL_MEAN",
                    "DELTA_ACT_EXEC_U_MEDIAN",
                    "DELTA_ACT_EXEC_U_MEAN",
                    "DELTA_ACT_EXEC_D_MEDIAN",
                    "DELTA_ACT_EXEC_D_MEAN",
                    "GOOD_ONLY_N",
                    "DOMINATOR_ONLY_N",
                    "GOOD_ONLY_FILL_RATE",
                    "DOMINATOR_ONLY_FILL_RATE",
                    "GOOD_ONLY_COND_U",
                    "DOMINATOR_ONLY_COND_U",
                    "GOOD_ONLY_COND_D",
                    "DOMINATOR_ONLY_COND_D",
                    "GOOD_ONLY_P_FILL_MEDIAN",
                    "DOMINATOR_ONLY_P_FILL_MEDIAN",
                    "GOOD_ONLY_PRED_EXEC_U_MEDIAN",
                    "DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN",
                    "GOOD_ONLY_PRED_EXECU_RANK_MEDIAN",
                    "DOMINATOR_ONLY_PRED_EXECU_RANK_MEDIAN",
                    "GOOD_ONLY_PFILL_RANK_MEDIAN",
                    "DOMINATOR_ONLY_PFILL_RANK_MEDIAN",
                    "TOP_PRED_FALSE_POSITIVE_RATE",
                    "HIGH_HIGH_FILL_RATE",
                    "HIGH_HIGH_EXEC_U",
                    "HIGH_HIGH_EXEC_D",
                    "HIGH_HIGH_FUD_CONTRIBUTION_RATE",
                    "SUBSET_ENUMERATION_ERROR_N",
                )
            },
            "JOIN_MISS_N": (b.get("join") or {}).get("JOIN_MISS_N"),
            "DUPLICATE_KEY_N": (b.get("join") or {}).get("DUPLICATE_KEY_N"),
        }
        out.append(rec)
    return out


def _feat_sign(row: dict[str, Any], med_key: str, cons_key: str) -> int:
    if not bool(row.get(cons_key)):
        return 0
    v = _f(row.get(med_key))
    if v is None or v == 0:
        return 0
    return 1 if float(v) > 0 else -1


def feature_consensus(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = {f: [] for f in CANONICAL_FEATURES}
    for b in bodies:
        for rec in (b.get("info") or {}).get("features") or []:
            name = str(rec.get("feature") or "")
            if name in by:
                by[name].append(rec)
    out = []
    for f in CANONICAL_FEATURES:
        rows = by[f]
        signs = [_feat_sign(r, "MEDIAN_PAIRED_DIFF", "DAY_CONSISTENT") for r in rows]
        pos = sum(1 for s in signs if s > 0)
        neg = sum(1 for s in signs if s < 0)
        supported = bool(pos >= int(POS_REP_MIN) or neg >= int(POS_REP_MIN))
        out.append(
            {
                "feature": f,
                "MEDIAN_PAIRED_DIFF": _median([float(v) for r in rows if (v := _f(r.get("MEDIAN_PAIRED_DIFF"))) is not None]),
                "POS_REP_N": pos,
                "NEG_REP_N": neg,
                "SUPPORTED": supported,
                "DIRECTION": "GOOD_ONLY_HIGHER" if pos > neg else ("DOMINATOR_ONLY_HIGHER" if neg > pos else "NONE"),
            }
        )
    return out


def residual_consensus(bodies: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
    sp_key = "FILL_RESIDUAL_SPEARMAN" if which == "FILL" else "EXEC_U_RESIDUAL_SPEARMAN"
    cons_key = "FILL_DAY_CONSISTENT" if which == "FILL" else "EXEC_U_DAY_CONSISTENT"
    by: dict[str, list[dict[str, Any]]] = {f: [] for f in CANONICAL_FEATURES}
    for b in bodies:
        for rec in (b.get("info") or {}).get("residuals") or []:
            name = str(rec.get("feature") or "")
            if name in by:
                by[name].append(rec)
    out = []
    for f in CANONICAL_FEATURES:
        rows = by[f]
        signs = [_feat_sign(r, sp_key, cons_key) for r in rows]
        pos = sum(1 for s in signs if s > 0)
        neg = sum(1 for s in signs if s < 0)
        supported = bool(pos >= int(POS_REP_MIN) or neg >= int(POS_REP_MIN))
        out.append(
            {
                "feature": f,
                "SPEARMAN_MEDIAN": _median([float(v) for r in rows if (v := _f(r.get(sp_key))) is not None]),
                "POS_REP_N": pos,
                "NEG_REP_N": neg,
                "SUPPORTED": supported,
                "DIRECTION": "POS" if pos > neg else ("NEG" if neg > pos else "NONE"),
            }
        )
    return out


def primary_failure(spec: list[dict[str, Any]]) -> str:
    rates = {
        "FILL_FAIL": _med_key(spec, "FD_FILL_FAIL_RATE"),
        "U_FAIL": _med_key(spec, "FD_U_FAIL_RATE"),
        "D_FAIL": _med_key(spec, "FD_D_FAIL_RATE"),
        "MULTIPLE_FAIL": _med_key(spec, "FD_MULTIPLE_FAIL_RATE"),
    }
    order = ("FILL_FAIL", "U_FAIL", "D_FAIL", "MULTIPLE_FAIL")
    best = None
    best_v = -1.0
    for k in order:
        v = rates[k]
        if v is None:
            continue
        if float(v) > best_v:
            best_v = float(v)
            best = k
    return str(best or "NONE")


def decide(
    *,
    parity_ok: bool,
    leak: dict[str, Any],
    feat_cons: list[dict[str, Any]],
    fill_cons: list[dict[str, Any]],
    exec_cons: list[dict[str, Any]],
) -> dict[str, Any]:
    must0 = (
        "MODEL_REFIT_N",
        "FEATURE_SEARCH_N",
        "NEW_FEATURE_N",
        "PM_ROWS_USED_N",
        "FUTURE_EVENT_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "SELECTION_REOPTIMIZATION_N",
        "WAIT_SEARCH_N",
        "POLICY_FROM_ACTUAL_N",
        "JOIN_MISS_N",
        "SUBSET_ENUMERATION_ERROR_N",
    )
    if (not parity_ok) or any(int(leak.get(k) or 0) != 0 for k in must0):
        return {
            "CASE": "F",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_ENTRY_INFORMATION_REASSESSMENT_INTEGRITY_FAILED",
            "PRIMARY_FINDING": "Parity, join, isolation, or enumeration integrity failed.",
            "note": "CASE F. STOP.",
            "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": False,
            "FILL_RESIDUAL_INFORMATION_SUPPORTED": False,
            "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": False,
        }
    feat_n = sum(1 for r in feat_cons if r.get("SUPPORTED"))
    fill_n = sum(1 for r in fill_cons if r.get("SUPPORTED"))
    exec_n = sum(1 for r in exec_cons if r.get("SUPPORTED"))
    feat_ok = feat_n >= 2
    fill_ok = fill_n >= 1
    exec_ok = exec_n >= 1
    aligned = 0
    feat_dir = {r["feature"]: r.get("DIRECTION") for r in feat_cons if r.get("SUPPORTED")}
    fill_dir = {r["feature"]: r.get("DIRECTION") for r in fill_cons if r.get("SUPPORTED")}
    exec_dir = {r["feature"]: r.get("DIRECTION") for r in exec_cons if r.get("SUPPORTED")}
    for f, d in feat_dir.items():
        if d == "NONE":
            continue
        # GOOD_ONLY_HIGHER ~ positive residual if models under-score those names
        if fill_dir.get(f) in {"POS", "NEG"} or exec_dir.get(f) in {"POS", "NEG"}:
            aligned += 1
    if feat_ok and (fill_ok or exec_ok) and aligned >= 1:
        return {
            "CASE": "A",
            "PRIMARY_MECHANISM": "EXISTING_CANONICAL_INFORMATION_NOT_CAPTURED_BY_CURRENT_MODELS",
            "NEXT_RESEARCH": "AM_MODEL_ARCHITECTURE_REASSESSMENT",
            "VERDICT": "AM_EXISTING_ENTRY_INFORMATION_UNDERUTILIZED",
            "PRIMARY_FINDING": (
                "GOOD_ONLY vs FALSE_DOMINATOR differs on multiple day-consistent canonical features, "
                "and residual association remains. No model is built this run."
            ),
            "note": "CASE A. STOP. No model architecture change this run.",
            "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": True,
            "FILL_RESIDUAL_INFORMATION_SUPPORTED": fill_ok,
            "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": exec_ok,
        }
    if (not feat_ok) and (not fill_ok) and (not exec_ok):
        return {
            "CASE": "B",
            "PRIMARY_MECHANISM": "CURRENT_CANONICAL_INFORMATION_INSUFFICIENT_FOR_LOCAL_DISCRIMINATION",
            "NEXT_RESEARCH": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
            "VERDICT": "AM_ENTRY_INFORMATION_INSUFFICIENT",
            "PRIMARY_FINDING": (
                "GOOD_ONLY vs FALSE_DOMINATOR shows little day-consistent canonical feature difference "
                "and residual association is weak."
            ),
            "note": "CASE B. STOP. No new feature this run.",
            "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": False,
            "FILL_RESIDUAL_INFORMATION_SUPPORTED": False,
            "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": False,
        }
    if fill_ok and (not exec_ok) and (not feat_ok):
        return {
            "CASE": "C",
            "PRIMARY_MECHANISM": "PASSIVE_ACCESSIBILITY_MODEL_INFORMATION_GAP",
            "NEXT_RESEARCH": "AM_FILLABILITY_MODEL_ARCHITECTURE_REASSESSMENT",
            "VERDICT": "AM_FILLABILITY_INFORMATION_UNDERUTILIZED",
            "PRIMARY_FINDING": "Canonical-feature association remains mainly in the FILL residual.",
            "note": "CASE C. STOP. No model this run.",
            "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": False,
            "FILL_RESIDUAL_INFORMATION_SUPPORTED": True,
            "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": False,
        }
    if exec_ok and (not fill_ok) and (not feat_ok):
        return {
            "CASE": "D",
            "PRIMARY_MECHANISM": "UPSIDE_MODEL_INFORMATION_GAP",
            "NEXT_RESEARCH": "AM_EXEC_U_MODEL_ARCHITECTURE_REASSESSMENT",
            "VERDICT": "AM_EXEC_U_INFORMATION_UNDERUTILIZED",
            "PRIMARY_FINDING": "Canonical-feature association remains mainly in the EXEC_U residual.",
            "note": "CASE D. STOP. No model this run.",
            "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": False,
            "FILL_RESIDUAL_INFORMATION_SUPPORTED": False,
            "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": True,
        }
    return {
        "CASE": "E",
        "PRIMARY_MECHANISM": "ENTRY_INFORMATION_GAP_MIXED",
        "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_HOLD",
        "VERDICT": "AM_ENTRY_INFORMATION_GAP_MIXED",
        "PRIMARY_FINDING": "Canonical-feature vs residual evidence is mixed under the frozen 9-rep convention.",
        "note": "CASE E. STOP. No model. No new feature.",
        "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": feat_ok,
        "FILL_RESIDUAL_INFORMATION_SUPPORTED": fill_ok,
        "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": exec_ok,
        "feat_n": feat_n,
        "fill_n": fill_n,
        "exec_n": exec_n,
        "aligned": aligned,
    }
