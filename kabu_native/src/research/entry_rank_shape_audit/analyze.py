"""Parity, stability, Top1 robustness, and CASE A-D decision. Diagnostic only."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.entry_rank_shape_audit import EXPECTED_NESTED, PARITY_ATOL, SPEC_STABLE_SHARE_MIN
from research.entry_rank_shape_audit.oof import pearson


def _close(a: Any, b: Any, atol: float = PARITY_ATOL) -> bool:
    if isinstance(b, int) and not isinstance(b, bool):
        try:
            return int(a) == int(b)
        except (TypeError, ValueError):
            return False
    try:
        x = float(a)
        y = float(b)
    except (TypeError, ValueError):
        return False
    if x != x or y != y:
        return False
    return abs(x - y) <= atol


def nested_observed(rank_cur: dict[str, Any], rank_c3: dict[str, Any], paired: dict[str, Any]) -> dict[str, Any]:
    return {
        "CURRENT_TOP3": rank_cur.get("TOP3_UPLIFT"),
        "C3_TOP3": rank_c3.get("TOP3_UPLIFT"),
        "TOP3_DELTA": paired.get("TOP3_DELTA_MEAN"),
        "C3_TOP1": rank_c3.get("TOP1_UPLIFT"),
        "C3_TOP5": rank_c3.get("TOP5_UPLIFT"),
        "SPEARMAN": rank_c3.get("MEAN_DAILY_SPEARMAN"),
        "TOP3_DELTA_POSITIVE_DAYS": paired.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": paired.get("TOP3_DELTA_NEGATIVE_DAYS"),
    }


def nested_parity(obs: dict[str, Any]) -> dict[str, Any]:
    mismatches = []
    for k, ev in EXPECTED_NESTED.items():
        ov = obs.get(k)
        ok = _close(ov, ev)
        if not ok:
            mismatches.append({"key": k, "observed": ov, "expected": ev})
    return {
        "ok": len(mismatches) == 0,
        "observed": obs,
        "expected": dict(EXPECTED_NESTED),
        "mismatches": mismatches,
    }


def spec_stability(folds: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [str(f.get("spec_id") or "") for f in folds]
    counts = Counter(keys)
    most = counts.most_common(1)[0] if counts else ("", 0)
    share = (most[1] / len(keys)) if keys else None
    fs = Counter(str(f.get("feature_set") or "") for f in folds)
    norms = Counter(str(f.get("normalization") or "") for f in folds)
    alphas = Counter(str(f.get("alpha")) for f in folds)
    return {
        "feature_set_counts": dict(fs),
        "normalization_counts": dict(norms),
        "alpha_counts": dict(alphas),
        "SPEC_COUNTS": dict(counts),
        "MOST_COMMON_SPEC": most[0],
        "MOST_COMMON_SPEC_SHARE": share,
        "SPEC_SELECTION_STABLE": bool(share is not None and float(share) >= float(SPEC_STABLE_SHARE_MIN)),
        "n_folds": len(keys),
        "stable_share_min": SPEC_STABLE_SHARE_MIN,
    }


def inner_outer_corr(folds: list[dict[str, Any]]) -> dict[str, Any]:
    inner = []
    outer_t3 = []
    outer_d = []
    for f in folds:
        a = f.get("inner_top3")
        b = f.get("holdout_c3_top3")
        c = f.get("holdout_cur_top3")
        try:
            ia = float(a)
            ob = float(b)
            oc = float(c)
        except (TypeError, ValueError):
            continue
        if ia != ia or ob != ob or oc != oc:
            continue
        inner.append(ia)
        outer_t3.append(ob)
        outer_d.append(ob - oc)
    return {
        "n_folds": len(inner),
        "INNER_OUTER_TOP3_CORRELATION": pearson(inner, outer_t3),
        "INNER_OUTER_DELTA_CORRELATION": pearson(inner, outer_d),
        "note": (
            "Pearson across 18 outer folds: inner CV Top3 of the nested-selected spec "
            "vs holdout Top3 / holdout Top3-delta vs CURRENT. Not a new selector."
        ),
    }


def top1_robust(stats: dict[str, Any]) -> bool:
    mean = stats.get("mean")
    pos = int(stats.get("positive_days") or 0)
    neg = int(stats.get("negative_days") or 0)
    ex1 = stats.get("ex_best_day")
    ex3 = stats.get("ex_top3_days")
    if mean is None or ex1 is None or ex3 is None:
        return False
    return bool(float(mean) > 0 and pos >= neg and float(ex1) > 0 and float(ex3) >= 0)


def fixed_matrix_summary(spec_rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(spec_rows)
    pass_ak = [r for r in spec_rows if r.get("OOF_RANKING_GATE_PASS")]
    t3_pos = []
    t3_gt = []
    deltas = []
    for r in spec_rows:
        t3 = r.get("TOP3_UPLIFT")
        cur = r.get("CURRENT_TOP3_UPLIFT")
        d = r.get("TOP3_DELTA")
        if t3 is not None and float(t3) > 0:
            t3_pos.append(r)
        if t3 is not None and cur is not None and float(t3) > float(cur):
            t3_gt.append(r)
        if d is not None:
            deltas.append((float(d), r))
    deltas_sorted = sorted(deltas, key=lambda z: -z[0])
    best = deltas_sorted[0][1] if deltas_sorted else {}
    delta_vals = [z[0] for z in deltas]
    median = None
    if delta_vals:
        import numpy as np

        median = float(np.median(delta_vals))
    return {
        "FIXED_SPECS_N": n,
        "FIXED_SPECS_PASSING_ALL_AK_N": len(pass_ak),
        "FIXED_SPECS_TOP3_POSITIVE_N": len(t3_pos),
        "FIXED_SPECS_TOP3_GT_CURRENT_N": len(t3_gt),
        "BEST_FIXED_DIAGNOSTIC_SPEC": best.get("spec_id"),
        "BEST_FIXED_DIAGNOSTIC_TOP3_DELTA": best.get("TOP3_DELTA"),
        "MEDIAN_FIXED_TOP3_DELTA": median,
        "passing_ak_spec_ids": [r.get("spec_id") for r in pass_ak],
        "best_note": "Diagnostic only. Not adopted as strategy.",
    }


def decide(
    *,
    parity_ok: bool,
    nested_gate_pass: bool,
    passing_ak_n: int,
    top1_robust_flag: bool,
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
            "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
            "NEXT_RESEARCH": "NONE",
            "note": "Nested Canonical C3 OOF parity mismatch. STOP. No fixed-spec adoption.",
        }
    if nested_gate_pass:
        return {
            "CASE": None,
            "VERDICT": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
            "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
            "NEXT_RESEARCH": "NONE",
            "note": "Nested OOF A-K unexpectedly passed. Audit premise C3_CANONICAL_EDGE_NOT_REPRODUCED did not hold.",
        }
    ak = int(passing_ak_n)
    t1 = bool(top1_robust_flag)
    if ak >= 1 and t1:
        return {
            "CASE": "D",
            "VERDICT": "ENTRY_RANKING_FAILURE_MULTIFACTOR",
            "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANKING_FAILURE_MULTIFACTOR",
            "NEXT_RESEARCH": "ENTRY_OBJECTIVE_MODEL_ARCHITECTURE_REDESIGN",
            "note": (
                "At least one fixed spec passes frozen A-K while nested OOF fails, and Top1 delta is robust. "
                "Both spec-selection instability and Top1-only structure are present. "
                "No spec adopted. No Top1 strategy this run."
            ),
        }
    if ak >= 1:
        return {
            "CASE": "B",
            "VERDICT": "SPEC_SELECTION_INSTABILITY",
            "PRIMARY_FAILURE_MECHANISM": "SPEC_SELECTION_INSTABILITY",
            "NEXT_RESEARCH": "MODEL_SELECTION_ARCHITECTURE_RECONSIDERATION",
            "note": (
                "One or more fixed specs pass frozen A-K, but nested spec selection fails. "
                "Diagnostic only. BEST fixed spec is not adopted."
            ),
        }
    if t1:
        return {
            "CASE": "C",
            "VERDICT": "TOP1_ONLY_STRUCTURE_OBSERVED",
            "PRIMARY_FAILURE_MECHANISM": "TOP1_ONLY_STRUCTURE_OBSERVED",
            "NEXT_RESEARCH": "TOP1_OBJECTIVE_PRECOMMIT_DESIGN",
            "note": (
                "No fixed spec passes A-K. Nested Top1 delta is robust. "
                "Top1 strategy is not created this run. Next run may precommit a Top1 objective as new development."
            ),
        }
    return {
        "CASE": "A",
        "VERDICT": "NO_STABLE_RANKING_STRUCTURE_FOUND",
        "PRIMARY_FAILURE_MECHANISM": "NO_STABLE_RANKING_STRUCTURE_FOUND",
        "NEXT_RESEARCH": "ENTRY_OBJECTIVE_MODEL_ARCHITECTURE_REDESIGN",
        "note": (
            "No fixed spec passes frozen A-K and Top1 delta is not robust. "
            "Ridge + TARGET V4 shows no stable ranking structure on this 27-spec grid."
        ),
    }
