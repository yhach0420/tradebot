"""Input-only family-day eligibility on 09:00–11:30. No future returns."""
from __future__ import annotations

from math import ceil
from typing import Any

import numpy as np

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST, FV_FIRST
from research.causal_driver_pb1.cross_sectional_precommit.clock import N_AM
from research.causal_driver_pb1.cross_sectional_precommit.leaders import load_leader_am_presence
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.eligibility import development_and_c1_folds
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.sector_breadth_precommit import (
    DAY_COVERAGE_MIN,
    DEPENDENCY_WINDOW,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_VALID,
)

N_DEP = N_AM
N_DEP_MIN_OK = int(ceil(DAY_COVERAGE_MIN * N_AM))


def _fresh_from_present(present: np.ndarray) -> np.ndarray:
    """Last-completed age<=60s on AM grid timestamps. 09:00 is typically empty (BAR_START)."""
    n_s, n_d, n_t = present.shape
    fresh = np.zeros((n_s, n_d, n_t), dtype=np.bool_)
    if n_t > 1:
        fresh[:, :, 1] = present[:, :, 0]
    if n_t > 2:
        fresh[:, :, 2:] = present[:, :, 1:-1] | present[:, :, 0:-2]
    return fresh


def compute_family_days(*, symbols: list[str], tse_days: list[str]) -> dict[str, Any]:
    if any(d >= FV_FIRST for d in tse_days):
        raise RuntimeError("tse_days_opened_fv")
    dates = [d for d in tse_days if DEV_FIRST <= d <= C1_LAST]
    n_d = len(dates)
    n_s = len(symbols)
    present = np.zeros((n_s, n_d, N_AM), dtype=np.bool_)
    listing: list[str | None] = [None] * n_s
    for i, sym in enumerate(symbols):
        packed = load_leader_am_presence(symbol=sym, date_lo=DEV_FIRST, date_hi=C1_LAST, dates=dates)
        present[i] = packed["present"]
        hits = np.where(present[i].any(axis=1))[0]
        listing[i] = dates[int(hits[0])] if hits.size else None
        if (i + 1) % 21 == 0:
            print(f"UNIVERSE_DEP_DAY {i + 1}/{n_s}", flush=True)
    print(f"UNIVERSE_DEP_DAY {n_s}/{n_s}", flush=True)
    starts = np.array([s or "99999999" for s in listing])
    darr = np.array(dates)
    listed = darr[None, :] >= starts[:, None]
    fresh = _fresh_from_present(present)
    valid = listed[:, :, None] & fresh
    n_valid = valid.sum(axis=0)
    n_listed = listed.sum(axis=0).astype(np.float64)
    minute_ok = (n_valid >= GLOBAL_MIN_VALID) & (n_valid >= (GLOBAL_MIN_FRAC * n_listed)[:, None])
    cov = minute_ok.mean(axis=1)
    n_ok_min = minute_ok.sum(axis=1)
    eligible = []
    rows = []
    for j, day in enumerate(dates):
        rec = {
            "date": day,
            "listed_n": int(n_listed[j]),
            "valid_minute_n": int(n_ok_min[j]),
            "dependency_minute_n": N_AM,
            "coverage": float(cov[j]),
            "eligible": bool(cov[j] >= DAY_COVERAGE_MIN),
            "mean_valid_n": float(n_valid[j].mean()),
        }
        rows.append(rec)
        if rec["eligible"]:
            eligible.append(day)
    folds = development_and_c1_folds(eligible)
    shuffle = generate_shuffle_permutations(eligible)
    if any(d >= FV_FIRST for d in eligible):
        raise RuntimeError("eligible_opened_fv")
    listing_map = {sym: listing[i] for i, sym in enumerate(symbols)}
    listing_rows = [{"symbol": s, "listing_start": listing_map[s], "listed_at_dev_first": listing_map[s] is not None and listing_map[s] <= DEV_FIRST} for s in symbols]
    elig_cov = [r["coverage"] for r in rows if r["eligible"]]
    all_cov = [r["coverage"] for r in rows]
    coverage_summary = {
        "tse_day_n": n_d,
        "eligible_n": len(eligible),
        "ineligible_n": n_d - len(eligible),
        "dependency_minute_n": N_AM,
        "n_dep_min_ok": int(ceil(DAY_COVERAGE_MIN * N_AM)),
        "min_coverage": DAY_COVERAGE_MIN,
        "global_min_valid": GLOBAL_MIN_VALID,
        "global_min_frac": GLOBAL_MIN_FRAC,
        "mean_coverage_all": float(sum(all_cov) / len(all_cov)) if all_cov else None,
        "mean_coverage_eligible": float(sum(elig_cov) / len(elig_cov)) if elig_cov else None,
        "bar_start_0900_typically_unavailable": True,
        "bar_start_0900_unavailable_does_not_fail_95pct_alone": True,
        "input_only": True,
        "response_outcomes_used": False,
    }
    return {
        "pass": len(folds.get("development_dates") or []) >= 40 and len(folds.get("c1_dates") or []) >= 20,
        "eligible_dates": eligible,
        "eligible_day_sha256": sha256_obj(eligible),
        "family_rule": "tse_cash_and_global105_dep_window_0900_1130_ge_95pct_with_n_ge_90_and_frac_ge_80pct_age_le_60s",
        "dependency_window": f"{DEPENDENCY_WINDOW[0]}-{DEPENDENCY_WINDOW[1]}",
        "dependency_minute_n": N_AM,
        "min_coverage": DAY_COVERAGE_MIN,
        "global_min_valid": GLOBAL_MIN_VALID,
        "global_min_frac": GLOBAL_MIN_FRAC,
        "bar_start_0900_may_be_unavailable": True,
        "usdjpy_fx_exclusions_inherited": False,
        "leader_laggard_days_inherited": False,
        "folds": folds,
        "shuffle": {k: v for k, v in shuffle.items() if k != "maps"},
        "rows": rows,
        "listing_start": listing_map,
        "listing_rows": listing_rows,
        "listing_start_sha256": sha256_obj(listing_rows),
        "coverage_summary": coverage_summary,
        "tse_day_n": n_d,
        "eligible_n": len(eligible),
        "eligible_dev_n": len(folds.get("development_dates") or []),
        "eligible_c1_n": len(folds.get("c1_dates") or []),
        "n_dep_min_ok": int(ceil(DAY_COVERAGE_MIN * N_AM)),
        "future_return_used": False,
        "c1_future_return_outcomes": False,
    }
