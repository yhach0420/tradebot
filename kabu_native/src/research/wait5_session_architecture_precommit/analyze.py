"""AM/PM capacity classification, support, AM dominance, CASE A-C. No model."""
from __future__ import annotations

from typing import Any, Optional

from research.canonical_entry_performance_rebase.analyze import rank_group, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_execution_feasibility.analyze import labeled_rows
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _close, _median, _rate
from research.wait5_execution_aware_rebase.analyze import postfill_geometry
from research.wait5_session_architecture_precommit import (
    AM_NEXT_TASK,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    CURRENT_SCORE_ROLE,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    PM_NEXT_TASK,
    RATE_MIN,
)


def _ge(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return float(a) + 1e-12 >= float(b)


def freeze_parity(*, sess: dict[str, Any], cur: dict[str, Any]) -> dict[str, Any]:
    am = sess.get("AM") or {}
    pm = sess.get("PM") or {}
    bys = cur.get("by_session") or {}
    obs = {
        "AM_COHORT_N": am.get("COHORT_N"),
        "AM_ANY": am.get("FILLABLE_ANY_COHORT_RATE"),
        "AM_MULTI": am.get("MULTI_FILLABLE_COHORT_RATE"),
        "AM_THREEPLUS": am.get("THREEPLUS_RATE"),
        "AM_FILL5_N": am.get("Y_FILL5_POS_N"),
        "PM_COHORT_N": pm.get("COHORT_N"),
        "PM_ANY": pm.get("FILLABLE_ANY_COHORT_RATE"),
        "PM_MULTI": pm.get("MULTI_FILLABLE_COHORT_RATE"),
        "PM_THREEPLUS": pm.get("THREEPLUS_RATE"),
        "PM_FILL5_N": pm.get("Y_FILL5_POS_N"),
        "CURRENT_FILL5_ENRICHMENT": cur.get("CURRENT_FILL5_ENRICHMENT"),
        "AM_CURRENT_FILL5_ENRICHMENT": (bys.get("AM") or {}).get("ENRICHMENT"),
        "PM_CURRENT_FILL5_ENRICHMENT": (bys.get("PM") or {}).get("ENRICHMENT"),
    }
    checks = {
        "AM_COHORT_N": int(obs["AM_COHORT_N"] or -1) == int(PARITY_EXPECTED["AM_COHORT_N"]),
        "AM_ANY": _close(obs["AM_ANY"], PARITY_EXPECTED["AM_ANY"], PARITY_ABS_TOL),
        "AM_MULTI": _close(obs["AM_MULTI"], PARITY_EXPECTED["AM_MULTI"], PARITY_ABS_TOL),
        "AM_THREEPLUS": _close(obs["AM_THREEPLUS"], PARITY_EXPECTED["AM_THREEPLUS"], PARITY_ABS_TOL),
        "AM_FILL5_N": int(obs["AM_FILL5_N"] or -1) == int(PARITY_EXPECTED["AM_FILL5_N"]),
        "PM_COHORT_N": int(obs["PM_COHORT_N"] or -1) == int(PARITY_EXPECTED["PM_COHORT_N"]),
        "PM_ANY": _close(obs["PM_ANY"], PARITY_EXPECTED["PM_ANY"], PARITY_ABS_TOL),
        "PM_MULTI": _close(obs["PM_MULTI"], PARITY_EXPECTED["PM_MULTI"], PARITY_ABS_TOL),
        "PM_THREEPLUS": _close(obs["PM_THREEPLUS"], PARITY_EXPECTED["PM_THREEPLUS"], PARITY_ABS_TOL),
        "PM_FILL5_N": int(obs["PM_FILL5_N"] or -1) == int(PARITY_EXPECTED["PM_FILL5_N"]),
        "CURRENT_FILL5_ENRICHMENT": _close(
            obs["CURRENT_FILL5_ENRICHMENT"], PARITY_EXPECTED["CURRENT_FILL5_ENRICHMENT"], PARITY_ABS_TOL
        ),
        "AM_CURRENT_FILL5_ENRICHMENT": _close(
            obs["AM_CURRENT_FILL5_ENRICHMENT"],
            PARITY_EXPECTED["AM_CURRENT_FILL5_ENRICHMENT"],
            PARITY_ABS_TOL,
        ),
        "PM_CURRENT_FILL5_ENRICHMENT": _close(
            obs["PM_CURRENT_FILL5_ENRICHMENT"],
            PARITY_EXPECTED["PM_CURRENT_FILL5_ENRICHMENT"],
            PARITY_ABS_TOL,
        ),
    }
    return {"BASE_PARITY": all(checks.values()), "checks": checks, "expected": dict(PARITY_EXPECTED), "observed": obs}


def capacity_flags(pack: dict[str, Any]) -> dict[str, bool]:
    return {
        "FILLABILITY_CAPABLE": _ge(pack.get("FILLABLE_ANY_COHORT_RATE"), RATE_MIN),
        "RANKING_CAPABLE": _ge(pack.get("MULTI_FILLABLE_COHORT_RATE"), RATE_MIN),
        "FULL_TOP3_CAPABLE": _ge(pack.get("THREEPLUS_RATE"), RATE_MIN),
    }


def sess_rows(rows: list[dict[str, Any]], sess: str) -> list[dict[str, Any]]:
    return [r for r in labeled_rows(rows) if session_of(r) == sess]


def y_support(rows: list[dict[str, Any]], *, filled_only: bool = False) -> dict[str, Any]:
    lab = labeled_rows(rows)
    xs = [r for r in lab if (not filled_only) or int(r.get("Y_FILL5") or 0) == 1]
    pos = [r for r in xs if int(r.get("Y_FILL5") or 0) == 1] if not filled_only else xs
    daily = []
    by_day = {d: 0 for d in ELIGIBLE_DAYS}
    for r in pos:
        d = str(r.get("date") or "")
        if d in by_day:
            by_day[d] += 1
    for d in ELIGIBLE_DAYS:
        daily.append({"date": d, "POS_N": by_day[d]})
    total = len(pos)
    train = [total - by_day[d] for d in ELIGIBLE_DAYS]
    return {
        "POS_N": total,
        "NEG_N": (len(lab) - total) if not filled_only else None,
        "LABELED_N": len(lab),
        "daily": daily,
        "MIN_TRAIN_POS_N": min(train) if train else None,
        "MEDIAN_TRAIN_POS_N": _median([float(v) for v in train]),
        "MAX_TRAIN_POS_N": max(train) if train else None,
        "DAY_MIN_POS_N": min(by_day.values()) if by_day else None,
        "DAY_MEDIAN_POS_N": _median([float(v) for v in by_day.values()]),
        "DAY_MAX_POS_N": max(by_day.values()) if by_day else None,
    }


def pm_top1_fill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(sess_rows(rows, "PM"))
    n = 0
    f = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        ranked = rank_group(grp, "current_score")
        if not ranked:
            continue
        n += 1
        if int(ranked[0].get("Y_FILL5") or 0) == 1:
            f += 1
    return {
        "PM_CURRENT_TOP1_N": n,
        "PM_CURRENT_TOP1_FILL_N": f,
        "PM_CURRENT_TOP1_FILL_RATE": _rate(f, n),
    }


def decide(*, parity_ok: bool, am_flags: dict[str, bool], pm_flags: dict[str, bool], am_dom_ok: bool) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": "C",
            "VERDICT": "WAIT5_SESSION_ARCHITECTURE_INTEGRITY_FAILED",
            "AM_ARCHITECTURE": None,
            "PM_ARCHITECTURE": None,
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Frozen AM/PM W5 capacity or CURRENT fill enrichment did not reproduce.",
            "note": "CASE C. STOP. BASE_PARITY failed.",
        }
    am_rank = bool(am_flags.get("RANKING_CAPABLE"))
    pm_fill = bool(pm_flags.get("FILLABILITY_CAPABLE"))
    pm_rank = bool(pm_flags.get("RANKING_CAPABLE"))
    if am_rank and (not am_dom_ok):
        return {
            "CASE": "B",
            "VERDICT": "WAIT5_SESSION_SPLIT_QUALITY_RANKING_UNSUPPORTED",
            "AM_ARCHITECTURE": "FILLABILITY_ONLY",
            "PM_ARCHITECTURE": "FILLABILITY_FIRST_SINGLE_ADMISSION",
            "NEXT_RESEARCH": "WAIT5_SESSION_TARGET_LEARNABILITY",
            "PRIMARY_FINDING": (
                "AM has ranking capacity but fillable U/D dominance is insufficient, so AM Stage2 "
                "quality ranking is not precommitted. PM remains fillability-first single admission."
            ),
            "note": "CASE B. STOP. No models trained.",
        }
    if am_rank and pm_fill and (not pm_rank) and am_dom_ok:
        return {
            "CASE": "A",
            "VERDICT": "WAIT5_SESSION_SPLIT_ARCHITECTURE_PRECOMMITTED",
            "AM_ARCHITECTURE": "TWO_STAGE_EXECUTION_QUALITY",
            "PM_ARCHITECTURE": "FILLABILITY_FIRST_SINGLE_ADMISSION",
            "NEXT_RESEARCH": "WAIT5_SESSION_TARGET_LEARNABILITY",
            "PRIMARY_FINDING": (
                "AM is ranking-capable under W5, including Top3 capacity, so the AM research task is "
                "two-stage fillability then unscalarized U/D quality. PM is fillability-capable but not "
                "ranking-capable, so PM is fillability-first single admission only. No common AM/PM model "
                "or target is allowed. Runtime WAIT remains 1.0."
            ),
            "note": "CASE A. STOP. Session learnability not started this run.",
        }
    return {
        "CASE": "C",
        "VERDICT": "WAIT5_SESSION_ARCHITECTURE_INTEGRITY_FAILED",
        "AM_ARCHITECTURE": None,
        "PM_ARCHITECTURE": None,
        "NEXT_RESEARCH": "NONE",
        "PRIMARY_FINDING": "Frozen AM/PM capacity classification did not match the precommitted CASE A/B geometry.",
        "note": "CASE C. STOP.",
    }


def pack_constants() -> dict[str, Any]:
    return {
        "CURRENT_SCORE_ROLE": CURRENT_SCORE_ROLE,
        "AM_NEXT_TASK": AM_NEXT_TASK,
        "PM_NEXT_TASK": PM_NEXT_TASK,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
    }
