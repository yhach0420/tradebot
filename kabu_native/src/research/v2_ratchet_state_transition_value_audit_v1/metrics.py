"""State-transition summaries. Thresholds below were fixed before the capture scan."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

PRIMARY = (0, 1, 2, 3, 4)
# A step inside this band is noise, not an improvement.
P_STEP = 0.02
P_NET = 0.08
P_FLAT = 0.08
YEN_STEP = 50.0
YEN_NET = 100.0
FRAC_STEP = 0.02
FRAC_NET = 0.03
RR_STEP = 0.05
RR_NET = 0.15
MIN_STATE_N = 100
MIN_PERIOD_N = 30


def _q(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    return float(np.percentile(np.array(values, dtype=np.float64), pct))


def _mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return float(np.mean(np.array(values, dtype=np.float64)))


def _quant(values: list[float]) -> dict[str, Optional[float]]:
    return {name: _q(values, q) for name, q in (("median", 50), ("p10", 10), ("p25", 25), ("p75", 75), ("p90", 90), ("p95", 95))}


def _nums(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(r[key]) for r in rows if r.get(key) is not None]


def summarize(rows: list[dict[str, Any]], state: int) -> dict[str, Any]:
    chosen = [r for r in rows if int(r["state"]) == int(state)]
    nxt = [r for r in chosen if r["outcome"] == "NEXT_RATCHET"]
    exited = [r for r in chosen if r["outcome"] == "EXIT"]
    session = [r for r in chosen if r["outcome"] == "SESSION_END"]
    leave = exited + session
    n = len(chosen)
    cont = _nums(chosen, "continue_yen")
    cont_bps = _nums(chosen, "continue_bps")
    pos = sum(1 for v in cont if v > 0)
    neg = sum(1 for v in cont if v < 0)
    mfe = _nums(chosen, "mfe_bps")
    mae = _nums(chosen, "mae_bps")
    med_mfe = _q(mfe, 50)
    med_mae = _q(mae, 50)
    ratio = None if med_mfe is None or med_mae is None or abs(med_mae) < 1e-9 else med_mfe / abs(med_mae)

    def _side(group: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(group),
            "mean_continue_yen": _mean(_nums(group, "continue_yen")),
            "median_continue_yen": _q(_nums(group, "continue_yen"), 50),
            "median_dt_sec": _q(_nums(group, "dt_sec"), 50),
            "median_mfe_bps": _q(_nums(group, "mfe_bps"), 50),
            "median_mae_bps": _q(_nums(group, "mae_bps"), 50),
        }

    return {
        "state": int(state),
        "label": "STATE_4+" if int(state) == 4 else f"STATE_{int(state)}",
        "at_risk_n": n,
        "next_ratchet_n": len(nxt),
        "exit_n": len(leave),
        "thesis_exit_n": len(exited),
        "session_end_n": len(session),
        "p_next": None if n == 0 else len(nxt) / n,
        "p_exit": None if n == 0 else len(leave) / n,
        "time_to_next": _quant(_nums(nxt, "dt_sec")),
        "time_to_exit": _quant(_nums(leave, "dt_sec")),
        "mean_continue_yen": _mean(cont),
        "median_continue_yen": _q(cont, 50),
        "mean_continue_bps": _mean(cont_bps),
        "median_continue_bps": _q(cont_bps, 50),
        "positive_fraction": None if not cont else pos / len(cont),
        "negative_fraction": None if not cont else neg / len(cont),
        "continue_yen_quantiles": _quant(cont),
        "median_mfe_bps": med_mfe,
        "median_mae_bps": med_mae,
        "reward_risk": ratio,
        "median_px_minus_support": _q(_nums(chosen, "px_minus_support"), 50),
        "median_bid_minus_support": _q(_nums(chosen, "bid_minus_support"), 50),
        "median_spread": _q(_nums(chosen, "spread"), 50),
        "median_classified": _q(_nums(chosen, "classified"), 50),
        "median_ask10": _q(_nums(chosen, "ask10"), 50),
        "median_bid10": _q(_nums(chosen, "bid10"), 50),
        "median_activity": _q(_nums(chosen, "activity"), 50),
        "median_time_since_prev_sec": _q(_nums(chosen, "time_since_prev_sec"), 50),
        "median_price_advance": _q(_nums(chosen, "price_advance"), 50),
        "next_ratchet": _side(nxt),
        "exit_before_next": _side(leave),
    }


def _series(blocks: list[dict[str, Any]], key: str) -> list[Optional[float]]:
    return [None if b.get(key) is None else float(b[key]) for b in blocks]


def _rises(values: list[Optional[float]], step: float, net: float) -> bool:
    if any(v is None for v in values) or len(values) < 2:
        return False
    nums = [float(v) for v in values if v is not None]
    ups = sum(1 for a, b in zip(nums, nums[1:]) if b - a > step)
    downs = sum(1 for a, b in zip(nums, nums[1:]) if a - b > step)
    return bool(nums[-1] - nums[0] >= net and ups >= 2 and downs <= 1)


def _flat(values: list[Optional[float]], width: float) -> bool:
    nums = [float(v) for v in values if v is not None]
    if len(nums) < 2:
        return False
    return max(nums) - min(nums) <= width


def _sgn(value: Optional[float], tol: float) -> int:
    if value is None:
        return 0
    if value > tol:
        return 1
    if value < -tol:
        return -1
    return 0


def _edge(blocks: dict[int, dict[str, Any]]) -> tuple[Optional[float], Optional[float], int]:
    anchor = 4 if blocks[4]["at_risk_n"] >= MIN_PERIOD_N else 3
    if blocks[0]["p_next"] is None or blocks[anchor]["p_next"] is None:
        return None, None, anchor
    p_delta = float(blocks[anchor]["p_next"]) - float(blocks[0]["p_next"])
    c0 = blocks[0]["median_continue_yen"]
    c1 = blocks[anchor]["median_continue_yen"]
    c_delta = None if c0 is None or c1 is None else float(c1) - float(c0)
    return p_delta, c_delta, anchor


def build(rows: list[dict[str, Any]], dates: list[str], original: list[str], extension: list[str]) -> dict[str, Any]:
    blocks = {k: summarize(rows, k) for k in PRIMARY}
    usable = [blocks[k] for k in PRIMARY if blocks[k]["at_risk_n"] >= MIN_STATE_N]
    p_values = _series(usable, "p_next")
    med_values = _series(usable, "median_continue_yen")
    mean_values = _series(usable, "mean_continue_yen")
    transition_rises = _rises(p_values, P_STEP, P_NET)
    median_rises = _rises(med_values, YEN_STEP, YEN_NET)
    mean_rises = _rises(mean_values, YEN_STEP, YEN_NET)
    continuation_rises = bool(median_rises and mean_rises)
    rr_values = _series(usable, "reward_risk")
    rr_improves = _rises(rr_values, RR_STEP, RR_NET)
    frac_values = _series(usable, "positive_fraction")
    frac_rises = _rises(frac_values, FRAC_STEP, FRAC_NET)
    favorable = all(
        b["median_continue_yen"] is not None and b["median_continue_yen"] > 0 and (b["positive_fraction"] or 0) >= 0.55
        for b in usable
    )
    rr_favorable = all(b["reward_risk"] is not None and b["reward_risk"] >= 1.0 for b in usable)
    economics_ok = bool((continuation_rises or favorable) and (rr_improves or rr_favorable) and (frac_rises or favorable))
    p_flat = _flat(p_values, P_FLAT)
    c_flat = _flat(med_values, max(150.0, 0.25 * abs(float(med_values[0] or 0.0))))
    frac_flat = _flat(frac_values, 0.08)
    stationary = bool(p_flat and c_flat and frac_flat and not transition_rises and not continuation_rises)
    folds = [set(dates[i * len(dates) // 3 : (i + 1) * len(dates) // 3]) for i in range(3)]
    periods = {
        "ORIGINAL18": set(original),
        "EXTENSION17": set(extension),
        "FOLD1": folds[0],
        "FOLD2": folds[1],
        "FOLD3": folds[2],
    }
    full_p, full_c, _ = _edge(blocks)
    period_reports = {}
    consistent = {}
    for name, chosen_dates in periods.items():
        subset = [r for r in rows if r["date"] in chosen_dates]
        local = {k: summarize(subset, k) for k in PRIMARY}
        p_delta, c_delta, anchor = _edge(local)
        enough = local[0]["at_risk_n"] >= MIN_PERIOD_N and local[anchor]["at_risk_n"] >= MIN_PERIOD_N
        same = enough and _sgn(p_delta, P_STEP) == _sgn(full_p, P_STEP) and _sgn(c_delta, YEN_STEP) == _sgn(full_c, YEN_STEP)
        consistent[name] = bool(same)
        period_reports[name] = {
            "anchor_state": anchor,
            "p_delta": p_delta,
            "continue_delta": c_delta,
            "consistent": bool(same),
            "states": {local[k]["label"]: {key: local[k][key] for key in ("at_risk_n", "p_next", "mean_continue_yen", "median_continue_yen", "positive_fraction")} for k in PRIMARY},
        }
    all_consistent = all(consistent.values())
    if transition_rises and economics_ok and all_consistent:
        verdict, nxt = "V2_RATCHET_CONFIRMATION_LADDER_SUPPORTED_V1", "PRECOMMIT_STAGEWISE_RISK_ALLOCATION_TEST_V1"
    elif not all_consistent and (transition_rises or continuation_rises or rr_improves):
        verdict, nxt = "V2_RATCHET_STATE_STRUCTURE_NOT_STABLE_V1", "KEEP_V2_UNCHANGED"
    elif transition_rises and not economics_ok:
        verdict, nxt = "V2_RATCHET_TRANSITION_PROBABILITY_ONLY_V1", "NO_STAGEWISE_RISK_CHANGE_JUSTIFIED"
    elif (continuation_rises or rr_improves) and not transition_rises:
        verdict, nxt = "V2_RATCHET_CONTINUATION_VALUE_IMPROVES_WITHOUT_HAZARD_SHIFT_V1", "NO_STAGEWISE_RISK_CHANGE_JUSTIFIED"
    elif stationary:
        verdict, nxt = "V2_RATCHET_APPROXIMATELY_STATIONARY_HAZARD_V1", "DO_NOT_TREAT_RATCHET_AS_CONFIRMATION"
    else:
        verdict, nxt = "V2_RATCHET_STATE_STRUCTURE_NOT_STABLE_V1", "KEEP_V2_UNCHANGED"
    further = []
    if rows:
        top = max(int(r["state"]) for r in rows)
        for k in range(5, top + 1):
            block = summarize(rows, k)
            if block["at_risk_n"] >= 50:
                further.append(block)
    times_next = [blocks[k]["time_to_next"]["median"] for k in PRIMARY]
    faster = None
    if all(v is not None for v in times_next):
        faster = bool(float(times_next[-1]) < float(times_next[0]))
    return {
        "survivorship_warning": "STATE_1 and later are selected populations. A higher P_NEXT is a conditional-state property, not proof that the ratchet event caused the improvement.",
        "states": {blocks[k]["label"]: blocks[k] for k in PRIMARY},
        "further_states": further,
        "transition_probability_rises": transition_rises,
        "continuation_value_rises": continuation_rises,
        "state_local_reward_risk_improves": rr_improves,
        "economics_ok": economics_ok,
        "stationary_repeated_hazard": stationary,
        "successful_transitions_become_faster": faster,
        "periods": period_reports,
        "period_consistent": consistent,
        "confirmation_ladder_supported": verdict.endswith("LADDER_SUPPORTED_V1"),
        "stagewise_risk_allocation_justified": verdict.endswith("LADDER_SUPPORTED_V1"),
        "verdict": verdict,
        "next": nxt,
    }
