"""Input-only eligible days on 09:00–11:30 dependency window. No future returns."""
from __future__ import annotations

from math import ceil
from typing import Any

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST, FV_FIRST
from research.causal_driver_pb1.cross_sectional_precommit import GLOBAL_MIN_VALID_LEADERS, LEADER_N
from research.causal_driver_pb1.cross_sectional_precommit.clock import AM_END_MIN, AM_START_MIN, N_AM, leader_fresh_at_t
from research.causal_driver_pb1.cross_sectional_precommit.leaders import load_leader_am_presence
from research.causal_driver_pb1.cross_sectional_precommit_v1_1 import DEPENDENCY_WINDOW, LEADER_DAY_COVERAGE_MIN
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.eligibility import development_and_c1_folds
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations

DEPENDENCY_MINS = tuple(range(AM_START_MIN, AM_END_MIN + 1))
N_DEP = len(DEPENDENCY_MINS)
N_DEP_MIN_OK = int(ceil(LEADER_DAY_COVERAGE_MIN * N_DEP))


def day_leader_dep_fresh_n(present_row) -> int:
    n = 0
    for t in DEPENDENCY_MINS:
        if leader_fresh_at_t(present_row, int(t)):
            n += 1
    return n


def compute_dependency_day_eligibility(*, leader_symbols: list[str], tse_days: list[str]) -> dict[str, Any]:
    if any(d >= FV_FIRST for d in tse_days):
        raise RuntimeError("tse_days_opened_fv")
    dates = [d for d in tse_days if DEV_FIRST <= d <= C1_LAST]
    n_d = len(dates)
    n_ok = []
    ok_mask = []
    for i, sym in enumerate(leader_symbols, start=1):
        packed = load_leader_am_presence(symbol=sym, date_lo=DEV_FIRST, date_hi=C1_LAST, dates=dates)
        present = packed["present"]
        counts = [day_leader_dep_fresh_n(present[j]) for j in range(n_d)]
        n_ok.append(counts)
        ok_mask.append([(c / float(N_DEP)) >= LEADER_DAY_COVERAGE_MIN for c in counts])
        print(f"LEADER_DEP_DAY {i}/{len(leader_symbols)} {sym}", flush=True)
    all8 = []
    global7 = []
    rows = []
    for j, day in enumerate(dates):
        n_pass = sum(1 for m in ok_mask if m[j])
        rec = {
            "date": day,
            "leaders_passing_n": n_pass,
            "all8": n_pass == LEADER_N,
            "global_7of8": n_pass >= GLOBAL_MIN_VALID_LEADERS,
            "fresh_n_by_leader": [int(n_ok[k][j]) for k in range(len(leader_symbols))],
            "dependency_minute_n": N_DEP,
            "dependency_window": f"{DEPENDENCY_WINDOW[0]}-{DEPENDENCY_WINDOW[1]}",
            "min_fresh_n": N_DEP_MIN_OK,
            "bar_start_0900_may_be_unavailable": True,
        }
        rows.append(rec)
        if rec["all8"]:
            all8.append(day)
        if rec["global_7of8"]:
            global7.append(day)
    eligible = list(all8)
    folds = development_and_c1_folds(eligible)
    shuffle = generate_shuffle_permutations(eligible)
    if any(d >= FV_FIRST for d in eligible):
        raise RuntimeError("eligible_opened_fv")
    assert N_AM == N_DEP
    return {
        "pass": len(folds.get("development_dates") or []) >= 40 and len(folds.get("c1_dates") or []) >= 20,
        "eligible_dates": eligible,
        "eligible_day_sha256": sha256_obj(eligible),
        "eligible_global_7of8_dates": global7,
        "family_rule": "all_8_leaders_dependency_window_09:00-11:30_coverage_ge_95pct_age_le_60s",
        "global_clock_rule": "at_least_7_of_8_valid_leaders_at_T",
        "dependency_window": f"{DEPENDENCY_WINDOW[0]}-{DEPENDENCY_WINDOW[1]}",
        "dependency_minute_n": N_DEP,
        "bar_start_0900_unavailable_does_not_fail_95pct_alone": True,
        "usdjpy_fx_exclusions_inherited": False,
        "folds": folds,
        "shuffle": {k: v for k, v in shuffle.items() if k != "maps"},
        "rows": rows,
        "leader_symbols": list(leader_symbols),
        "tse_day_n": len(dates),
        "eligible_n": len(eligible),
        "eligible_dev_n": len(folds.get("development_dates") or []),
        "eligible_c1_n": len(folds.get("c1_dates") or []),
        "future_return_used": False,
        "c1_future_return_outcomes": False,
    }
