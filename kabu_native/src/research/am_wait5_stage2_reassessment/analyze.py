"""Swap / geometry / oracle diagnostics on frozen selections. No arm search."""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any, Optional

import numpy as np

from research.am_wait5_stage2_reassessment import (
    FINAL_SELECTION_N,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    POS_REP_MIN,
    SAME_FILL_FREQUENT_MIN,
    SAME_FILL_RARE_MAX,
    SPEARMAN_MIN_N,
    STAGE1_SHORTLIST_N,
)
from research.am_wait5_two_stage_development.analyze import _med_key
from research.am_wait5_two_stage_development.oof import _cohort_block, select_fill_only, select_two_stage
from research.canonical_entry_performance_rebase.analyze import _f, rank_group
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import _rank
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _close, _mean, _median, _rate


def spearman_small(xs: list[float], ys: list[float], min_n: int = SPEARMAN_MIN_N) -> Optional[float]:
    if len(xs) < int(min_n) or len(xs) != len(ys):
        return None
    aa = np.asarray(xs, dtype=float)
    bb = np.asarray(ys, dtype=float)
    if float(np.std(aa)) <= 1e-12 or float(np.std(bb)) <= 1e-12:
        return 0.0
    c = float(np.corrcoef(_rank(aa), _rank(bb))[0, 1])
    if c != c:
        return None
    return c


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "")


def _filled(r: dict[str, Any]) -> bool:
    return int(r.get("Y_FILL5") or 0) == 1


def _exec_pair(r: dict[str, Any]) -> tuple[float, float]:
    if not _filled(r):
        return 0.0, 0.0
    u = _f(r.get("U_FILL"))
    d = _f(r.get("D_FILL"))
    return (float(u) if u is not None else 0.0, float(d) if d is not None else 0.0)


def _by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_sym(r): r for r in rows}


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def _delta_stats(xs: list[float]) -> dict[str, Any]:
    st = (
        delta_series_stats(xs)
        if xs
        else {
            "mean": None,
            "median": None,
            "positive_days": 0,
            "negative_days": 0,
            "ex_best_day": None,
            "ex_top3_days": None,
            "n_days": 0,
        }
    )
    st["zero_days"] = sum(1 for v in xs if v == 0)
    return st


def _robust_up(rep_vals: list[float], day_st: dict[str, Any]) -> dict[str, Any]:
    med = _median(rep_vals)
    pos_rep = sum(1 for v in rep_vals if v > 0)
    pos_days = int(day_st.get("positive_days") or 0)
    neg_days = int(day_st.get("negative_days") or 0)
    gates = {
        "MEDIAN_GT_0": bool(med is not None and med > 0),
        "POS_REP_GE_6": bool(pos_rep >= int(POS_REP_MIN)),
        "POS_DAYS_GT_NEG": bool(pos_days > neg_days),
        "EX_BEST_GT_0": bool(day_st.get("ex_best_day") is not None and float(day_st["ex_best_day"]) > 0),
        "EX_TOP3_GE_0": bool(day_st.get("ex_top3_days") is not None and float(day_st["ex_top3_days"]) + 1e-12 >= 0.0),
    }
    return {
        "MEDIAN": med,
        "POSITIVE_REP_N": pos_rep,
        "POSITIVE_DAYS": pos_days,
        "NEGATIVE_DAYS": neg_days,
        "ZERO_DAYS": day_st.get("zero_days"),
        "EX_BEST_DAY": day_st.get("ex_best_day"),
        "EX_TOP3_DAYS": day_st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
    }


def _robust_down(rep_vals: list[float], day_st: dict[str, Any]) -> dict[str, Any]:
    med = _median(rep_vals)
    neg_rep = sum(1 for v in rep_vals if v < 0)
    pos_days = int(day_st.get("positive_days") or 0)
    neg_days = int(day_st.get("negative_days") or 0)
    gates = {
        "MEDIAN_LT_0": bool(med is not None and med < 0),
        "NEG_REP_GE_6": bool(neg_rep >= int(POS_REP_MIN)),
        "NEG_DAYS_GT_POS": bool(neg_days > pos_days),
        "EX_BEST_LT_0": bool(day_st.get("ex_best_day") is not None and float(day_st["ex_best_day"]) < 0),
        "EX_TOP3_LE_0": bool(day_st.get("ex_top3_days") is not None and float(day_st["ex_top3_days"]) - 1e-12 <= 0.0),
    }
    return {
        "MEDIAN": med,
        "NEGATIVE_REP_N": neg_rep,
        "POSITIVE_DAYS": pos_days,
        "NEGATIVE_DAYS": neg_days,
        "ZERO_DAYS": day_st.get("zero_days"),
        "EX_BEST_DAY": day_st.get("ex_best_day"),
        "EX_TOP3_DAYS": day_st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
    }


def _consensus_daily(rep_day_maps: list[dict[str, float]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for mp in rep_day_maps:
        for d, v in mp.items():
            by[str(d)].append(float(v))
    daily = []
    xs: list[float] = []
    for d in ELIGIBLE_DAYS:
        vals = by.get(d) or []
        if not vals:
            continue
        med = float(np.median(vals))
        xs.append(med)
        daily.append({"date": d, "CONSENSUS": med, "REP_N": len(vals)})
    return daily, _delta_stats(xs)


def analyze_representation(scored: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    by = group_cohorts(scored)
    out_n = in_n = 0
    out_fill = in_fill = 0
    out_cond_u: list[float] = []
    in_cond_u: list[float] = []
    out_cond_d: list[float] = []
    in_cond_d: list[float] = []
    out_exec_u: list[float] = []
    in_exec_u: list[float] = []
    out_exec_d: list[float] = []
    in_exec_d: list[float] = []
    pred_sp: list[float] = []
    actual_sp: list[float] = []
    oracle_ok = 0
    same_fill_ok = 0
    cohort_n = 0
    swap_cohort_n = 0
    fill_lost = fill_gained = fill_equal = 0
    pat_a = pat_b = pat_c = pat_d = 0
    daily = {
        d: {
            "out_n": 0,
            "in_n": 0,
            "out_fill": 0,
            "in_fill": 0,
            "out_cond_u": [],
            "in_cond_u": [],
            "out_cond_d": [],
            "in_cond_d": [],
            "swap_fill_delta": [],
            "pred_sp": [],
            "actual_sp": [],
            "lost": 0,
            "gained": 0,
            "equal": 0,
            "coh": 0,
            "swap_coh": 0,
            "oracle": 0,
            "same_fill": 0,
        }
        for d in use_days
    }

    for (date, _sess, _an), grp in by.items():
        if str(date) not in daily:
            continue
        if len(grp) < int(MIN_COHORT_N):
            continue
        short = rank_group(grp, "fill_score")[: int(STAGE1_SHORTLIST_N)]
        fo = select_fill_only(grp)
        ts = select_two_stage(grp)
        if len(fo) != int(FINAL_SELECTION_N) or len(ts) != int(FINAL_SELECTION_N):
            continue
        cohort_n += 1
        dayb = daily[str(date)]
        dayb["coh"] += 1

        fo_map = _by_symbol(fo)
        ts_map = _by_symbol(ts)
        fo_set = set(fo_map)
        ts_set = set(ts_map)
        swapped_out = [fo_map[s] for s in sorted(fo_set - ts_set)]
        swapped_in = [ts_map[s] for s in sorted(ts_set - fo_set)]

        pred_pairs = [
            (float(u), float(d))
            for r in short
            if (u := _f(r.get("pred_U"))) is not None and (d := _f(r.get("pred_D"))) is not None
        ]
        sp_pred = spearman_small([p[0] for p in pred_pairs], [p[1] for p in pred_pairs])
        if sp_pred is not None:
            pred_sp.append(sp_pred)
            dayb["pred_sp"].append(sp_pred)

        filled_short = [r for r in short if _filled(r)]
        act_pairs = [
            (float(u), float(d))
            for r in filled_short
            if (u := _f(r.get("U_FILL"))) is not None and (d := _f(r.get("D_FILL"))) is not None
        ]
        sp_act = spearman_small([p[0] for p in act_pairs], [p[1] for p in act_pairs])
        if sp_act is not None:
            actual_sp.append(sp_act)
            dayb["actual_sp"].append(sp_act)

        fo_blk = _cohort_block(fo)
        oracle_hit = False
        same_fill_hit = False
        if len(short) >= int(FINAL_SELECTION_N):
            fo_fill_n = int(fo_blk["n_fill"])
            fo_eu = float(fo_blk["exec_u"])
            fo_ed = float(fo_blk["exec_d"])
            for combo in combinations(range(len(short)), int(FINAL_SELECTION_N)):
                subset = [short[i] for i in combo]
                if {_sym(r) for r in subset} == fo_set:
                    continue
                blk = _cohort_block(subset)
                nf = int(blk["n_fill"])
                eu = float(blk["exec_u"])
                ed = float(blk["exec_d"])
                nonworse = (
                    nf >= fo_fill_n
                    and eu + 1e-15 >= fo_eu
                    and ed + 1e-15 >= fo_ed
                    and (eu > fo_eu + 1e-15 or ed > fo_ed + 1e-15)
                )
                if nonworse:
                    oracle_hit = True
                if nf == fo_fill_n and eu > fo_eu + 1e-15 and ed > fo_ed + 1e-15:
                    same_fill_hit = True
        if oracle_hit:
            oracle_ok += 1
            dayb["oracle"] += 1
        if same_fill_hit:
            same_fill_ok += 1
            dayb["same_fill"] += 1

        if fo_set == ts_set:
            continue
        swap_cohort_n += 1
        dayb["swap_coh"] += 1
        n_out = len(swapped_out)
        n_in = len(swapped_in)
        nf_out = sum(1 for r in swapped_out if _filled(r))
        nf_in = sum(1 for r in swapped_in if _filled(r))
        out_n += n_out
        in_n += n_in
        out_fill += nf_out
        in_fill += nf_in
        dayb["out_n"] += n_out
        dayb["in_n"] += n_in
        dayb["out_fill"] += nf_out
        dayb["in_fill"] += nf_in
        dayb["swap_fill_delta"].append(float(nf_in - nf_out))

        if nf_in < nf_out:
            fill_lost += 1
            dayb["lost"] += 1
        elif nf_in > nf_out:
            fill_gained += 1
            dayb["gained"] += 1
        else:
            fill_equal += 1
            dayb["equal"] += 1

        out_any = nf_out > 0
        in_any = nf_in > 0
        if out_any and in_any:
            pat_a += 1
        elif out_any and not in_any:
            pat_b += 1
        elif (not out_any) and in_any:
            pat_c += 1
        else:
            pat_d += 1

        for r in swapped_out:
            eu, ed = _exec_pair(r)
            out_exec_u.append(eu)
            out_exec_d.append(ed)
            if not _filled(r):
                continue
            u = _f(r.get("U_FILL"))
            d = _f(r.get("D_FILL"))
            if u is not None:
                out_cond_u.append(float(u))
                dayb["out_cond_u"].append(float(u))
            if d is not None:
                out_cond_d.append(float(d))
                dayb["out_cond_d"].append(float(d))
        for r in swapped_in:
            eu, ed = _exec_pair(r)
            in_exec_u.append(eu)
            in_exec_d.append(ed)
            if not _filled(r):
                continue
            u = _f(r.get("U_FILL"))
            d = _f(r.get("D_FILL"))
            if u is not None:
                in_cond_u.append(float(u))
                dayb["in_cond_u"].append(float(u))
            if d is not None:
                in_cond_d.append(float(d))
                dayb["in_cond_d"].append(float(d))

    day_rows = []
    for d in use_days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        ou = _mean(b["out_cond_u"])
        iu = _mean(b["in_cond_u"])
        od = _mean(b["out_cond_d"])
        idv = _mean(b["in_cond_d"])
        day_rows.append(
            {
                "date": d,
                "COHORT_N": int(b["coh"]),
                "SWAP_COHORT_N": int(b["swap_coh"]),
                "SWAPPED_OUT_N": int(b["out_n"]),
                "SWAPPED_IN_N": int(b["in_n"]),
                "SWAPPED_OUT_FILL_N": int(b["out_fill"]),
                "SWAPPED_IN_FILL_N": int(b["in_fill"]),
                "SWAP_FILL_DELTA": _mean(b["swap_fill_delta"]),
                "SWAP_OUT_COND_U": ou,
                "SWAP_IN_COND_U": iu,
                "DELTA_SWAP_COND_U": None if ou is None or iu is None else float(iu) - float(ou),
                "SWAP_OUT_COND_D": od,
                "SWAP_IN_COND_D": idv,
                "DELTA_SWAP_COND_D": None if od is None or idv is None else float(idv) - float(od),
                "PRED_U_D_SPEARMAN": _median(b["pred_sp"]),
                "ACTUAL_U_D_SPEARMAN": _median(b["actual_sp"]),
                "COHORT_SWAP_FILL_LOST_N": int(b["lost"]),
                "COHORT_SWAP_FILL_GAINED_N": int(b["gained"]),
                "COHORT_SWAP_FILL_EQUAL_N": int(b["equal"]),
                "ORACLE_JOINT_NONWORSE_N": int(b["oracle"]),
                "SAME_FILL_JOINT_IMPROVEMENT_N": int(b["same_fill"]),
            }
        )

    swap_out_cu = _mean(out_cond_u)
    swap_in_cu = _mean(in_cond_u)
    swap_out_cd = _mean(out_cond_d)
    swap_in_cd = _mean(in_cond_d)
    swap_exec_u_out = _mean(out_exec_u)
    swap_exec_u_in = _mean(in_exec_u)
    swap_exec_d_out = _mean(out_exec_d)
    swap_exec_d_in = _mean(in_exec_d)
    exec_d_gain = (
        None
        if swap_exec_d_in is None or swap_exec_d_out is None
        else float(swap_exec_d_in) - float(swap_exec_d_out)
    )
    filled_d_gain = None if swap_in_cd is None or swap_out_cd is None else float(swap_in_cd) - float(swap_out_cd)
    return {
        "COHORT_N": cohort_n,
        "SWAP_COHORT_N": swap_cohort_n,
        "SWAPPED_OUT_N": out_n,
        "SWAPPED_IN_N": in_n,
        "SWAPPED_OUT_FILL_N": out_fill,
        "SWAPPED_IN_FILL_N": in_fill,
        "SWAP_FILL_DELTA": float(in_fill - out_fill),
        "SWAPPED_OUT_FILL_RATE": _rate(out_fill, out_n),
        "SWAPPED_IN_FILL_RATE": _rate(in_fill, in_n),
        "SWAP_OUT_COND_U": swap_out_cu,
        "SWAP_IN_COND_U": swap_in_cu,
        "DELTA_SWAP_COND_U": None if swap_out_cu is None or swap_in_cu is None else float(swap_in_cu) - float(swap_out_cu),
        "SWAP_OUT_COND_D": swap_out_cd,
        "SWAP_IN_COND_D": swap_in_cd,
        "DELTA_SWAP_COND_D": filled_d_gain,
        "SWAP_EXEC_U_OUT": swap_exec_u_out,
        "SWAP_EXEC_U_IN": swap_exec_u_in,
        "SWAP_EXEC_D_OUT": swap_exec_d_out,
        "SWAP_EXEC_D_IN": swap_exec_d_in,
        "EXEC_D_GAIN_WITH_NONFILL": exec_d_gain,
        "FILLED_ONLY_D_GAIN": filled_d_gain,
        "COHORT_SWAP_FILL_LOST_N": fill_lost,
        "COHORT_SWAP_FILL_GAINED_N": fill_gained,
        "COHORT_SWAP_FILL_EQUAL_N": fill_equal,
        "PATTERN_OUT_FILL_IN_FILL_N": pat_a,
        "PATTERN_OUT_FILL_IN_NONFILL_N": pat_b,
        "PATTERN_OUT_NONFILL_IN_FILL_N": pat_c,
        "PATTERN_OUT_NONFILL_IN_NONFILL_N": pat_d,
        "PRED_U_D_SPEARMAN_MEAN": _mean(pred_sp),
        "PRED_U_D_SPEARMAN_MEDIAN": _median(pred_sp),
        "PRED_U_D_POS_COHORT_N": sum(1 for v in pred_sp if v > 0),
        "PRED_U_D_NEG_COHORT_N": sum(1 for v in pred_sp if v < 0),
        "PRED_U_D_EVAL_COHORT_N": len(pred_sp),
        "ACTUAL_U_D_SPEARMAN_MEAN": _mean(actual_sp),
        "ACTUAL_U_D_SPEARMAN_MEDIAN": _median(actual_sp),
        "ACTUAL_U_D_POS_COHORT_N": sum(1 for v in actual_sp if v > 0),
        "ACTUAL_U_D_NEG_COHORT_N": sum(1 for v in actual_sp if v < 0),
        "ACTUAL_U_D_EVAL_COHORT_N": len(actual_sp),
        "ORACLE_JOINT_NONWORSE_AVAILABLE_COHORT_N": oracle_ok,
        "ORACLE_JOINT_NONWORSE_AVAILABLE_RATE": _rate(oracle_ok, cohort_n),
        "SAME_FILL_JOINT_IMPROVEMENT_COHORT_N": same_fill_ok,
        "SAME_FILL_JOINT_IMPROVEMENT_RATE": _rate(same_fill_ok, cohort_n),
        "daily": day_rows,
    }


def _day_map(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for rec in rows or []:
        v = _f(rec.get(key))
        if v is None:
            continue
        out[str(rec.get("date"))] = float(v)
    return out


def aggregate(rep_rows: list[dict[str, Any]]) -> dict[str, Any]:
    def med(key: str) -> Optional[float]:
        return _med_key(rep_rows, key)

    u_maps = [_day_map(r.get("daily") or [], "DELTA_SWAP_COND_U") for r in rep_rows]
    d_maps = [_day_map(r.get("daily") or [], "DELTA_SWAP_COND_D") for r in rep_rows]
    f_maps = [_day_map(r.get("daily") or [], "SWAP_FILL_DELTA") for r in rep_rows]
    u_daily, u_st = _consensus_daily(u_maps)
    d_daily, d_st = _consensus_daily(d_maps)
    f_daily, f_st = _consensus_daily(f_maps)

    du_rep = [float(v) for r in rep_rows if (v := _f(r.get("DELTA_SWAP_COND_U"))) is not None]
    dd_rep = [float(v) for r in rep_rows if (v := _f(r.get("DELTA_SWAP_COND_D"))) is not None]
    filled_d = _robust_up(dd_rep, d_st)
    filled_u_down = _robust_down(du_rep, u_st)

    swap_in_fr = med("SWAPPED_IN_FILL_RATE")
    swap_out_fr = med("SWAPPED_OUT_FILL_RATE")
    in_lt_out = bool(
        swap_in_fr is not None and swap_out_fr is not None and float(swap_in_fr) < float(swap_out_fr)
    )
    mech_a = bool(in_lt_out and (not bool(filled_d.get("PASS"))))
    mech_b = bool(filled_d.get("PASS") and filled_u_down.get("PASS"))

    same_fill_rate = med("SAME_FILL_JOINT_IMPROVEMENT_RATE")
    oracle_rate = med("ORACLE_JOINT_NONWORSE_AVAILABLE_RATE")
    minrank_fails_joint = True
    mech_c = bool(
        same_fill_rate is not None
        and float(same_fill_rate) + 1e-12 >= float(SAME_FILL_FREQUENT_MIN)
        and minrank_fails_joint
    )
    mech_d = bool(same_fill_rate is not None and float(same_fill_rate) < float(SAME_FILL_RARE_MAX))

    flags = {
        "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE": mech_a,
        "GENUINE_U_D_SELECTION_TRADEOFF": mech_b,
        "MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET": mech_c,
        "STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY": mech_d,
    }
    order = (
        "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE",
        "GENUINE_U_D_SELECTION_TRADEOFF",
        "MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET",
        "STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY",
    )
    active = [k for k in order if flags[k]]
    return {
        "headline": {
            "SWAPPED_OUT_N": med("SWAPPED_OUT_N"),
            "SWAPPED_IN_N": med("SWAPPED_IN_N"),
            "SWAPPED_OUT_FILL_N": med("SWAPPED_OUT_FILL_N"),
            "SWAPPED_IN_FILL_N": med("SWAPPED_IN_FILL_N"),
            "SWAPPED_OUT_FILL_RATE": swap_out_fr,
            "SWAPPED_IN_FILL_RATE": swap_in_fr,
            "SWAP_FILL_DELTA": med("SWAP_FILL_DELTA"),
            "SWAP_OUT_COND_U": med("SWAP_OUT_COND_U"),
            "SWAP_IN_COND_U": med("SWAP_IN_COND_U"),
            "DELTA_SWAP_COND_U": med("DELTA_SWAP_COND_U"),
            "SWAP_OUT_COND_D": med("SWAP_OUT_COND_D"),
            "SWAP_IN_COND_D": med("SWAP_IN_COND_D"),
            "DELTA_SWAP_COND_D": med("DELTA_SWAP_COND_D"),
            "SWAP_EXEC_U_OUT": med("SWAP_EXEC_U_OUT"),
            "SWAP_EXEC_U_IN": med("SWAP_EXEC_U_IN"),
            "SWAP_EXEC_D_OUT": med("SWAP_EXEC_D_OUT"),
            "SWAP_EXEC_D_IN": med("SWAP_EXEC_D_IN"),
            "EXEC_D_GAIN_WITH_NONFILL": med("EXEC_D_GAIN_WITH_NONFILL"),
            "FILLED_ONLY_D_GAIN": med("FILLED_ONLY_D_GAIN"),
            "COHORT_SWAP_FILL_LOST_N": med("COHORT_SWAP_FILL_LOST_N"),
            "COHORT_SWAP_FILL_GAINED_N": med("COHORT_SWAP_FILL_GAINED_N"),
            "COHORT_SWAP_FILL_EQUAL_N": med("COHORT_SWAP_FILL_EQUAL_N"),
            "PRED_U_D_SPEARMAN_MEAN": med("PRED_U_D_SPEARMAN_MEAN"),
            "PRED_U_D_SPEARMAN_MEDIAN": med("PRED_U_D_SPEARMAN_MEDIAN"),
            "ACTUAL_U_D_SPEARMAN_MEAN": med("ACTUAL_U_D_SPEARMAN_MEAN"),
            "ACTUAL_U_D_SPEARMAN_MEDIAN": med("ACTUAL_U_D_SPEARMAN_MEDIAN"),
            "ORACLE_JOINT_NONWORSE_AVAILABLE_COHORT_N": med("ORACLE_JOINT_NONWORSE_AVAILABLE_COHORT_N"),
            "ORACLE_JOINT_NONWORSE_AVAILABLE_RATE": oracle_rate,
            "SAME_FILL_JOINT_IMPROVEMENT_COHORT_N": med("SAME_FILL_JOINT_IMPROVEMENT_COHORT_N"),
            "SAME_FILL_JOINT_IMPROVEMENT_RATE": same_fill_rate,
        },
        "daily": {
            "DELTA_SWAP_COND_U": u_daily,
            "DELTA_SWAP_COND_D": d_daily,
            "SWAP_FILL_DELTA": f_daily,
            "DELTA_SWAP_COND_U_STATS": u_st,
            "DELTA_SWAP_COND_D_STATS": d_st,
            "SWAP_FILL_DELTA_STATS": f_st,
        },
        "filled_only_d_robust": filled_d,
        "filled_only_u_worsens_robust": filled_u_down,
        "swap_in_fill_lt_out": in_lt_out,
        "same_fill_frequent": bool(
            same_fill_rate is not None and float(same_fill_rate) + 1e-12 >= float(SAME_FILL_FREQUENT_MIN)
        ),
        "same_fill_rare": bool(same_fill_rate is not None and float(same_fill_rate) < float(SAME_FILL_RARE_MAX)),
        "flags": flags,
        "active": active,
        "thresholds": {
            "SAME_FILL_FREQUENT_MIN": SAME_FILL_FREQUENT_MIN,
            "SAME_FILL_RARE_MAX": SAME_FILL_RARE_MAX,
            "POS_REP_MIN": POS_REP_MIN,
        },
    }


def decide(agg: dict[str, Any], *, leak: dict[str, int], parity_ok: bool) -> dict[str, Any]:
    if (not parity_ok) or any(int(leak.get(k) or 0) != 0 for k in leak):
        return {
            "VERDICT": "AM_STAGE2_REASSESSMENT_INTEGRITY_FAILED",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Parity or isolation integrity failed. No mechanism classification.",
            "note": "STOP. Integrity failed.",
        }
    flags = dict(agg.get("flags") or {})
    mapping = {
        "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE": (
            "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE",
            "AM_STAGE2_D_GAIN_IS_FILL_REDUCTION",
            "AM_STAGE2_FILL_PRESERVING_OBJECTIVE_PRECOMMIT",
            "EXEC_D gain vs FILL_ONLY is mainly lower fill exposure, not robust filled-candidate D.",
        ),
        "GENUINE_U_D_SELECTION_TRADEOFF": (
            "GENUINE_U_D_SELECTION_TRADEOFF",
            "AM_STAGE2_GENUINE_UD_TRADEOFF",
            "AM_STAGE2_CONSTRAINED_MULTI_OBJECTIVE_REASSESSMENT",
            "Filled-only D improves robustly while filled-only U worsens robustly.",
        ),
        "MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET": (
            "MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET",
            "AM_STAGE2_MINRANK_OBJECTIVE_MISMATCH",
            "AM_STAGE2_JOINT_OBJECTIVE_REDESIGN",
            "Same-fill U/D joint improvement is frequently available inside Top5, but min-rank misses it.",
        ),
        "STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY": (
            "STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY",
            "AM_STAGE2_SHORTLIST_GEOMETRY_LIMIT",
            "AM_STAGE1_STAGE2_INTERFACE_REASSESSMENT",
            "Same-fill U/D joint improvement is rarely available inside the Stage1 Top5.",
        ),
    }
    active = [k for k in mapping if flags.get(k)]
    if len(active) == 1:
        mech, verdict, nxt, finding = mapping[active[0]]
        return {
            "VERDICT": verdict,
            "PRIMARY_MECHANISM": mech,
            "NEXT_RESEARCH": nxt,
            "PRIMARY_FINDING": finding,
            "note": "Single mechanism. STOP. No new objective. No new model.",
        }
    if len(active) > 1:
        mechs = [mapping[k][0] for k in active]
        nxts = [mapping[k][2] for k in active]
        findings = [mapping[k][3] for k in active]
        return {
            "VERDICT": "AM_STAGE2_MECHANISM_MIXED",
            "PRIMARY_MECHANISM": " | ".join(mechs),
            "NEXT_RESEARCH": " | ".join(nxts),
            "PRIMARY_FINDING": " ".join(findings),
            "note": "Multiple mechanisms. Evidence listed. Not collapsed. STOP. No new objective.",
        }
    return {
        "VERDICT": "AM_STAGE2_MECHANISM_MIXED",
        "PRIMARY_MECHANISM": "NONE_OF_A_D_FULLY_MET",
        "NEXT_RESEARCH": "NONE",
        "PRIMARY_FINDING": (
            "No single precommitted mechanism class fully matched the frozen thresholds. "
            "See swap, filled-only, and oracle evidence."
        ),
        "note": "No A-D class fully met. STOP. No new objective. No new model.",
    }
