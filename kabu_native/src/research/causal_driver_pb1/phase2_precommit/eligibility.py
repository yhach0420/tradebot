"""Input-only day eligibility and date-count folds. No stock outcomes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST
from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase1 import SOURCE_ID, UTC_FILE_FIRST, UTC_FILE_LAST
from research.causal_driver_pb1.phase1.bars import load_and_pair
from research.causal_driver_pb1.phase2_precommit import (
    DAY_ELIGIBILITY_EXPECTED_N,
    DAY_ELIGIBILITY_MIN_COVERAGE,
    DAY_ELIGIBILITY_WINDOW,
)


def split_count_ordered(dates: list[str], n_parts: int) -> list[list[str]]:
    ordered = sorted(dates)
    n = len(ordered)
    if n_parts == 2:
        mid = n // 2
        return [ordered[:mid], ordered[mid:]]
    if n_parts == 3:
        a = n // 3
        b = 2 * (n // 3)
        return [ordered[:a], ordered[a:b], ordered[b:]]
    raise ValueError("n_parts_unsupported")


def _hhmm(dt) -> str:
    return dt.strftime("%H:%M")


def fx_morning_coverage(bars) -> dict[str, Any]:
    lo, hi = DAY_ELIGIBILITY_WINDOW
    by_date: dict[str, int] = defaultdict(int)
    for bar in bars:
        if bar.quality_status != QualityStatus.VALID:
            continue
        hhmm = _hhmm(bar.bar_start)
        if hhmm < lo or hhmm > hi:
            continue
        by_date[bar.jst_date] += 1
    rows = []
    eligible = []
    excluded = []
    for day in sorted(by_date):
        if day < DEV_FIRST or day > C1_LAST:
            continue
        actual = int(by_date[day])
        ratio = actual / float(DAY_ELIGIBILITY_EXPECTED_N)
        rec = {
            "date": day,
            "expected_n": DAY_ELIGIBILITY_EXPECTED_N,
            "actual_n": actual,
            "coverage_ratio": ratio,
            "eligible": ratio >= DAY_ELIGIBILITY_MIN_COVERAGE,
        }
        rows.append(rec)
        if rec["eligible"]:
            eligible.append(day)
        else:
            excluded.append(rec)
    # Days with zero FX bars in the window do not appear in by_date; they are ineligible if they are TSE days,
    # but this function is FX-input-only and does not name-exclude holidays.
    return {
        "window": f"{lo}-{hi} JST",
        "expected_n": DAY_ELIGIBILITY_EXPECTED_N,
        "min_coverage": DAY_ELIGIBILITY_MIN_COVERAGE,
        "eligible_dates": eligible,
        "excluded_rows": excluded,
        "rows": rows,
        "named_date_exclusions": [],
        "christmas_special_cased": False,
    }


def development_and_c1_folds(eligible_dates: list[str]) -> dict[str, Any]:
    dev = [d for d in eligible_dates if DEV_FIRST <= d <= DEV_LAST]
    c1 = [d for d in eligible_dates if C1_FIRST <= d <= C1_LAST]
    dev_early, dev_late = split_count_ordered(dev, 2)
    c1_early, c1_middle, c1_late = split_count_ordered(c1, 3)
    payload = {
        "development_dates": dev,
        "c1_dates": c1,
        "DEV_EARLY": dev_early,
        "DEV_LATE": dev_late,
        "C1_EARLY": c1_early,
        "C1_MIDDLE": c1_middle,
        "C1_LATE": c1_late,
        "dev_early_last": dev_early[-1] if dev_early else None,
        "dev_late_first": dev_late[0] if dev_late else None,
        "c1_early_last": c1_early[-1] if c1_early else None,
        "c1_middle_first": c1_middle[0] if c1_middle else None,
        "c1_middle_last": c1_middle[-1] if c1_middle else None,
        "c1_late_first": c1_late[0] if c1_late else None,
        "split_rule": "timestamp_sorted_date_count_equal_split_remainder_to_last_fold",
        "outcome_used": False,
    }
    payload["development_fold_boundaries"] = {
        "DEV_EARLY": {"first": dev_early[0] if dev_early else None, "last": dev_early[-1] if dev_early else None, "n": len(dev_early)},
        "DEV_LATE": {"first": dev_late[0] if dev_late else None, "last": dev_late[-1] if dev_late else None, "n": len(dev_late)},
    }
    payload["c1_fold_boundaries"] = {
        "C1_EARLY": {"first": c1_early[0] if c1_early else None, "last": c1_early[-1] if c1_early else None, "n": len(c1_early)},
        "C1_MIDDLE": {"first": c1_middle[0] if c1_middle else None, "last": c1_middle[-1] if c1_middle else None, "n": len(c1_middle)},
        "C1_LATE": {"first": c1_late[0] if c1_late else None, "last": c1_late[-1] if c1_late else None, "n": len(c1_late)},
    }
    payload["fold_boundary_sha256"] = sha256_obj(
        {
            "dev": payload["development_fold_boundaries"],
            "c1": payload["c1_fold_boundaries"],
            "dev_dates": dev,
            "c1_dates": c1,
        }
    )
    return payload


def compute_fx_eligibility() -> dict[str, Any]:
    packed = load_and_pair(utc_first=UTC_FILE_FIRST, utc_last=UTC_FILE_LAST, source_id=SOURCE_ID)
    cov = fx_morning_coverage(packed.get("bars") or [])
    folds = development_and_c1_folds(cov["eligible_dates"])
    return {
        "pass": len(folds["development_dates"]) >= 40 and len(folds["c1_dates"]) >= 20,
        "conflicting_duplicate_n": int(packed.get("conflicting_duplicate_n") or 0),
        "coverage": cov,
        "folds": folds,
        "paired_bar_n": int(packed.get("paired_bar_n") or 0),
    }
