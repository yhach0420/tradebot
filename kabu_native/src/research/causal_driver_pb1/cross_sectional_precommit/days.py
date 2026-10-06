"""Input-only eligible days after leaders are frozen. No future returns. No USDJPY FX filter."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST
from research.causal_driver_pb1.cross_sectional_precommit import GLOBAL_MIN_VALID_LEADERS, LEADER_N
from research.causal_driver_pb1.cross_sectional_precommit.clock import N_CLOCK, N_DECISION_MIN_OK
from research.causal_driver_pb1.cross_sectional_precommit.leaders import day_leader_fresh_n, load_leader_am_presence
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.eligibility import development_and_c1_folds
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations


def compute_leader_day_eligibility(*, leader_symbols: list[str], tse_days: list[str]) -> dict[str, Any]:
    if any(d >= FV_FIRST for d in tse_days):
        raise RuntimeError("tse_days_opened_fv")
    dates = [d for d in tse_days if DEV_FIRST <= d <= C1_LAST]
    n_d = len(dates)
    n_ok = []
    ok_mask = []
    for i, sym in enumerate(leader_symbols, start=1):
        packed = load_leader_am_presence(symbol=sym, date_lo=DEV_FIRST, date_hi=C1_LAST, dates=dates)
        present = packed["present"]
        counts = [day_leader_fresh_n(present[j]) for j in range(n_d)]
        n_ok.append(counts)
        ok_mask.append([c >= N_DECISION_MIN_OK for c in counts])
        print(f"LEADER_DAY {i}/{len(leader_symbols)} {sym}", flush=True)
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
            "decision_minute_n": N_CLOCK,
            "min_fresh_n": N_DECISION_MIN_OK,
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
    return {
        "pass": len(folds.get("development_dates") or []) >= 40 and len(folds.get("c1_dates") or []) >= 20,
        "eligible_dates": eligible,
        "eligible_day_sha256": sha256_obj(eligible),
        "eligible_global_7of8_dates": global7,
        "family_rule": "all_8_leaders_day_coverage_ge_95pct_of_decision_minutes_age_le_60s",
        "global_clock_rule": "at_least_7_of_8_valid_leaders_at_T",
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
    }
