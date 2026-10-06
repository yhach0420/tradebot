"""Corrected PHASE2_ELIGIBLE_DAY = TSE cash day AND FX 08:30-11:30 coverage >= 95%."""
from __future__ import annotations

from typing import Any

from collections import defaultdict

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase1 import SOURCE_ID, UTC_FILE_FIRST, UTC_FILE_LAST
from research.causal_driver_pb1.phase1.bars import load_and_pair
from research.causal_driver_pb1.phase2_precommit.eligibility import development_and_c1_folds, fx_morning_coverage


def lomo_months(eligible_dates: list[str]) -> dict[str, Any]:
    by: dict[str, list[str]] = defaultdict(list)
    for day in sorted(eligible_dates):
        by[day[:6]].append(day)
    months = []
    for month in sorted(by):
        days = by[month]
        months.append({"month": month, "n": len(days), "first": days[0], "last": days[-1]})
    return {
        "population": "PHASE2_ELIGIBLE_DAY",
        "fx_only_dates_included": False,
        "month_n": len(months),
        "months": months,
    }


def compute_corrected_eligibility(*, tse_trading_days: list[str]) -> dict[str, Any]:
    tse = set(tse_trading_days)
    packed = load_and_pair(utc_first=UTC_FILE_FIRST, utc_last=UTC_FILE_LAST, source_id=SOURCE_ID)
    cov = fx_morning_coverage(packed.get("bars") or [])
    fx_ok = set(cov.get("eligible_dates") or [])
    fx_only = sorted(d for d in fx_ok if d not in tse)
    tse_fx_fail = []
    by_fx = {r["date"]: r for r in (cov.get("rows") or [])}
    for day in sorted(tse):
        rec = by_fx.get(day)
        if rec is None or not rec.get("eligible"):
            tse_fx_fail.append({"date": day, **(rec or {"actual_n": 0, "coverage_ratio": 0.0, "eligible": False})})
    eligible = sorted(d for d in fx_ok if d in tse)
    folds = development_and_c1_folds(eligible)
    non_tse_in_folds = [d for d in (folds.get("development_dates") or []) + (folds.get("c1_dates") or []) if d not in tse]
    lomo = lomo_months(eligible)
    return {
        "pass": len(folds.get("development_dates") or []) >= 40
        and len(folds.get("c1_dates") or []) >= 20
        and not non_tse_in_folds,
        "conflicting_duplicate_n": int(packed.get("conflicting_duplicate_n") or 0),
        "fx_coverage": cov,
        "eligible_dates": eligible,
        "eligible_day_sha256": sha256_obj(eligible),
        "fx_only_non_tse_dates": fx_only,
        "tse_but_fx_below_95": tse_fx_fail,
        "folds": folds,
        "lomo": lomo,
        "paired_bar_n": int(packed.get("paired_bar_n") or 0),
        "non_tse_in_folds": non_tse_in_folds,
        "named_date_exclusions": [],
    }
