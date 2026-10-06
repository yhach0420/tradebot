"""Oracle Top3 geometry, P_FILL rank buckets, CONTROL/FILL_ONLY swap. No RF. No strategy."""
from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any, Optional

import numpy as np

from research.am_wait5_fill_upside_geometry import FINAL_SELECTION_N, RANK_BUCKETS, SLOTS, SPEARMAN_MIN_N
from research.am_wait5_two_stage_development.oof import _cohort_block, eval_arm, select_control, select_fill_only
from research.canonical_entry_performance_rebase.analyze import _f, rank_group
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import _rank
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _mean, _median, _rate


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


def _exec_val(r: dict[str, Any], key: str) -> float:
    if not _filled(r):
        return 0.0
    v = _f(r.get(key))
    return float(v) if v is not None else 0.0


def _cond_val(r: dict[str, Any], key: str) -> Optional[float]:
    if not _filled(r):
        return None
    return _f(r.get(key))


def _rank_bucket(rank: int) -> str:
    if rank <= 3:
        return "R1_3"
    if rank <= 5:
        return "R4_5"
    if rank <= 10:
        return "R6_10"
    return "R11_PLUS"


def _blank_bucket() -> dict[str, Any]:
    return {
        "n": 0,
        "fill_n": 0,
        "cond_u": [],
        "cond_d": [],
        "exec_u": [],
        "exec_d": [],
    }


def _finish_bucket(b: dict[str, Any]) -> dict[str, Any]:
    cu = list(b["cond_u"])
    cd = list(b["cond_d"])
    return {
        "CANDIDATE_N": int(b["n"]),
        "FILL_N": int(b["fill_n"]),
        "FILL_RATE": _rate(int(b["fill_n"]), int(b["n"])),
        "COND_U_MEAN": _mean(cu),
        "COND_U_MEDIAN": _median(cu),
        "COND_D_MEAN": _mean(cd),
        "COND_D_MEDIAN": _median(cd),
        "EXEC_U_MEAN": _mean(b["exec_u"]),
        "EXEC_D_MEAN": _mean(b["exec_d"]),
    }


def _push_bucket(acc: dict[str, dict[str, Any]], rank: int, r: dict[str, Any]) -> None:
    b = acc[_rank_bucket(rank)]
    b["n"] += 1
    filled = _filled(r)
    if filled:
        b["fill_n"] += 1
    b["exec_u"].append(_exec_val(r, "U_FILL"))
    b["exec_d"].append(_exec_val(r, "D_FILL"))
    u = _cond_val(r, "U_FILL")
    d = _cond_val(r, "D_FILL")
    if u is not None:
        b["cond_u"].append(float(u))
    if d is not None:
        b["cond_d"].append(float(d))


def _set_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    fill_n = sum(1 for r in rows if _filled(r))
    cu = [float(v) for r in rows if (v := _cond_val(r, "U_FILL")) is not None]
    cd = [float(v) for r in rows if (v := _cond_val(r, "D_FILL")) is not None]
    eu = [_exec_val(r, "U_FILL") for r in rows]
    ed = [_exec_val(r, "D_FILL") for r in rows]
    return {
        "N": n,
        "FILL_N": fill_n,
        "FILL_RATE": _rate(fill_n, n),
        "COND_U": _mean(cu),
        "COND_D": _mean(cd),
        "EXEC_U": _mean(eu),
        "EXEC_D": _mean(ed),
    }


def _arrays(grp: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(grp)
    fl = np.zeros(n, dtype=np.int8)
    eu = np.zeros(n, dtype=np.float64)
    ed = np.zeros(n, dtype=np.float64)
    for i, r in enumerate(grp):
        if not _filled(r):
            continue
        fl[i] = 1
        u = _f(r.get("U_FILL"))
        d = _f(r.get("D_FILL"))
        eu[i] = float(u) if u is not None else 0.0
        ed[i] = float(d) if d is not None else 0.0
    return fl, eu, ed


def _oracle_cohort(
    grp: list[dict[str, Any]],
    fo_fill: int,
    fo_eu: float,
    fo_ed: float,
) -> dict[str, Any]:
    n = len(grp)
    expected = int(comb(n, int(FINAL_SELECTION_N))) if n >= int(FINAL_SELECTION_N) else 0
    if n < int(FINAL_SELECTION_N):
        return {
            "enumerated": 0,
            "expected": expected,
            "error": 1,
            "fill_nonworse_u": False,
            "same_fill_u": False,
            "fill_nonworse_ud": False,
            "same_fill_ud": False,
            "dominated": False,
            "best_eu": None,
            "delta_eu": None,
        }
    fl, eu, ed = _arrays(grp)
    slots = float(SLOTS)
    cnt = 0
    best_eu = float(fo_eu)
    hit_fn_u = hit_same_u = hit_fn_ud = hit_same_ud = False
    dominated = False
    fo_fill_i = int(fo_fill)
    fo_eu_f = float(fo_eu)
    fo_ed_f = float(fo_ed)
    for i, j, k in combinations(range(n), int(FINAL_SELECTION_N)):
        cnt += 1
        nf = int(fl[i]) + int(fl[j]) + int(fl[k])
        su = (float(eu[i]) + float(eu[j]) + float(eu[k])) / slots
        sd = (float(ed[i]) + float(ed[j]) + float(ed[k])) / slots
        ge_fill = nf >= fo_fill_i
        ge_u = su + 1e-15 >= fo_eu_f
        gt_u = su > fo_eu_f + 1e-15
        if ge_fill:
            if su > best_eu:
                best_eu = su
            if gt_u:
                hit_fn_u = True
                if sd + 1e-15 >= fo_ed_f:
                    hit_fn_ud = True
            if ge_u and (nf > fo_fill_i or gt_u):
                dominated = True
        if nf == fo_fill_i and gt_u:
            hit_same_u = True
            if sd + 1e-15 >= fo_ed_f:
                hit_same_ud = True
    err = 0 if cnt == expected else 1
    return {
        "enumerated": cnt,
        "expected": expected,
        "error": err,
        "fill_nonworse_u": hit_fn_u,
        "same_fill_u": hit_same_u,
        "fill_nonworse_ud": hit_fn_ud,
        "same_fill_ud": hit_same_ud,
        "dominated": dominated,
        "best_eu": best_eu,
        "delta_eu": float(best_eu) - fo_eu_f,
    }


def evaluate_scored(scored: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    control = eval_arm(scored, select_control, use_days)
    fill_only = eval_arm(scored, select_fill_only, use_days)
    c_by = control.get("selected_by") or {}
    fo_by = fill_only.get("selected_by") or {}
    by = group_cohorts(scored)
    buckets = {k: _blank_bucket() for k in RANK_BUCKETS}
    overall_u_p: list[float] = []
    overall_u_y: list[float] = []
    overall_d_p: list[float] = []
    overall_d_y: list[float] = []
    daily_sp_u: dict[str, tuple[list[float], list[float]]] = {d: ([], []) for d in use_days}
    daily_sp_d: dict[str, tuple[list[float], list[float]]] = {d: ([], []) for d in use_days}
    cohort_sp_u: list[float] = []
    cohort_sp_d: list[float] = []
    ctrl_only_rows: list[dict[str, Any]] = []
    fo_only_rows: list[dict[str, Any]] = []
    swap_daily = {
        d: {
            "out": [],
            "inn": [],
            "delta_fr": [],
            "delta_cu": [],
            "delta_cd": [],
            "delta_eu": [],
            "delta_ed": [],
        }
        for d in use_days
    }
    n_coh = 0
    enum_err = 0
    fn_u = same_u = fn_ud = same_ud = 0
    dominated_n = 0
    deltas: list[float] = []
    daily_oracle = {
        d: {
            "coh": 0,
            "fn_u": 0,
            "same_u": 0,
            "fn_ud": 0,
            "same_ud": 0,
            "dom": 0,
            "delta": [],
        }
        for d in use_days
    }

    for (date, _sess, _an), grp in by.items():
        day = str(date)
        if day not in daily_oracle:
            continue
        if len(grp) < int(MIN_COHORT_N):
            continue
        key = (str(date), str(_sess), str(_an))
        ctrl = c_by.get(key) or select_control(grp)
        fo = fo_by.get(key) or select_fill_only(grp)
        if len(fo) != int(FINAL_SELECTION_N):
            continue
        n_coh += 1
        ranked = rank_group(grp, "fill_score")
        for i, r in enumerate(ranked, start=1):
            _push_bucket(buckets, i, r)
            if not _filled(r):
                continue
            p = _f(r.get("fill_score"))
            u = _f(r.get("U_FILL"))
            d = _f(r.get("D_FILL"))
            if p is not None and u is not None:
                overall_u_p.append(float(p))
                overall_u_y.append(float(u))
                daily_sp_u[day][0].append(float(p))
                daily_sp_u[day][1].append(float(u))
            if p is not None and d is not None:
                overall_d_p.append(float(p))
                overall_d_y.append(float(d))
                daily_sp_d[day][0].append(float(p))
                daily_sp_d[day][1].append(float(d))
        c_u: list[float] = []
        c_uy: list[float] = []
        c_d: list[float] = []
        c_dy: list[float] = []
        for r in ranked:
            if not _filled(r):
                continue
            p = _f(r.get("fill_score"))
            u = _f(r.get("U_FILL"))
            d = _f(r.get("D_FILL"))
            if p is not None and u is not None:
                c_u.append(float(p))
                c_uy.append(float(u))
            if p is not None and d is not None:
                c_d.append(float(p))
                c_dy.append(float(d))
        spu = spearman_small(c_u, c_uy)
        spd = spearman_small(c_d, c_dy)
        if spu is not None:
            cohort_sp_u.append(spu)
        if spd is not None:
            cohort_sp_d.append(spd)

        cset = {_sym(r) for r in ctrl}
        fset = {_sym(r) for r in fo}
        cmap = {_sym(r): r for r in ctrl}
        fmap = {_sym(r): r for r in fo}
        out_rows = [cmap[s] for s in sorted(cset - fset) if s in cmap]
        in_rows = [fmap[s] for s in sorted(fset - cset) if s in fmap]
        ctrl_only_rows.extend(out_rows)
        fo_only_rows.extend(in_rows)
        sb = swap_daily.get(day)
        if sb is not None:
            sb["out"].extend(out_rows)
            sb["inn"].extend(in_rows)

        fo_blk = _cohort_block(fo)
        oc = _oracle_cohort(grp, int(fo_blk["n_fill"]), float(fo_blk["exec_u"]), float(fo_blk["exec_d"]))
        enum_err += int(oc["error"])
        db = daily_oracle[day]
        db["coh"] += 1
        if oc["fill_nonworse_u"]:
            fn_u += 1
            db["fn_u"] += 1
        if oc["same_fill_u"]:
            same_u += 1
            db["same_u"] += 1
        if oc["fill_nonworse_ud"]:
            fn_ud += 1
            db["fn_ud"] += 1
        if oc["same_fill_ud"]:
            same_ud += 1
            db["same_ud"] += 1
        if oc["dominated"]:
            dominated_n += 1
            db["dom"] += 1
        if oc["delta_eu"] is not None:
            deltas.append(float(oc["delta_eu"]))
            db["delta"].append(float(oc["delta_eu"]))

    daily_u_rows = []
    daily_d_rows = []
    for d in use_days:
        su = spearman_small(daily_sp_u[d][0], daily_sp_u[d][1])
        sd = spearman_small(daily_sp_d[d][0], daily_sp_d[d][1])
        daily_u_rows.append({"date": d, "SPEARMAN": su})
        daily_d_rows.append({"date": d, "SPEARMAN": sd})

    swap_day_rows = []
    for d in use_days:
        out_s = _set_stats(swap_daily[d]["out"])
        in_s = _set_stats(swap_daily[d]["inn"])
        rec = {
            "date": d,
            "CONTROL_ONLY_FILL_RATE": out_s.get("FILL_RATE"),
            "FILL_ONLY_ONLY_FILL_RATE": in_s.get("FILL_RATE"),
            "CONTROL_ONLY_COND_U": out_s.get("COND_U"),
            "FILL_ONLY_ONLY_COND_U": in_s.get("COND_U"),
            "CONTROL_ONLY_COND_D": out_s.get("COND_D"),
            "FILL_ONLY_ONLY_COND_D": in_s.get("COND_D"),
            "CONTROL_ONLY_EXEC_U": out_s.get("EXEC_U"),
            "FILL_ONLY_ONLY_EXEC_U": in_s.get("EXEC_U"),
            "CONTROL_ONLY_EXEC_D": out_s.get("EXEC_D"),
            "FILL_ONLY_ONLY_EXEC_D": in_s.get("EXEC_D"),
        }
        rec["DELTA_FILL_RATE"] = (
            None
            if out_s.get("FILL_RATE") is None or in_s.get("FILL_RATE") is None
            else float(in_s["FILL_RATE"]) - float(out_s["FILL_RATE"])
        )
        rec["DELTA_COND_U"] = (
            None
            if out_s.get("COND_U") is None or in_s.get("COND_U") is None
            else float(in_s["COND_U"]) - float(out_s["COND_U"])
        )
        rec["DELTA_COND_D"] = (
            None
            if out_s.get("COND_D") is None or in_s.get("COND_D") is None
            else float(in_s["COND_D"]) - float(out_s["COND_D"])
        )
        rec["DELTA_EXEC_U"] = (
            None
            if out_s.get("EXEC_U") is None or in_s.get("EXEC_U") is None
            else float(in_s["EXEC_U"]) - float(out_s["EXEC_U"])
        )
        rec["DELTA_EXEC_D"] = (
            None
            if out_s.get("EXEC_D") is None or in_s.get("EXEC_D") is None
            else float(in_s["EXEC_D"]) - float(out_s["EXEC_D"])
        )
        swap_day_rows.append(rec)

    oracle_day_rows = []
    for d in use_days:
        b = daily_oracle[d]
        if int(b["coh"]) <= 0:
            continue
        oracle_day_rows.append(
            {
                "date": d,
                "COHORT_N": int(b["coh"]),
                "FILL_NONWORSE_U_IMPROVE_RATE": _rate(int(b["fn_u"]), int(b["coh"])),
                "SAME_FILL_U_IMPROVE_RATE": _rate(int(b["same_u"]), int(b["coh"])),
                "FILL_NONWORSE_U_D_NONWORSE_RATE": _rate(int(b["fn_ud"]), int(b["coh"])),
                "SAME_FILL_U_D_NONWORSE_RATE": _rate(int(b["same_ud"]), int(b["coh"])),
                "FILL_ONLY_DOMINATED_RATE": _rate(int(b["dom"]), int(b["coh"])),
                "ORACLE_EXEC_U_DELTA": _mean(b["delta"]),
            }
        )

    u_daily_vals = [float(v) for r in daily_u_rows if (v := r.get("SPEARMAN")) is not None]
    d_daily_vals = [float(v) for r in daily_d_rows if (v := r.get("SPEARMAN")) is not None]
    out_c = dict(control)
    out_fo = dict(fill_only)
    out_c.pop("selected_by", None)
    out_fo.pop("selected_by", None)
    net_fill = int(fill_only.get("WOULD_FILL_N") or 0) - int(control.get("WOULD_FILL_N") or 0)
    delta_cu = None
    delta_cd = None
    if _f(fill_only.get("COND_U_MEAN")) is not None and _f(control.get("COND_U_MEAN")) is not None:
        delta_cu = float(fill_only["COND_U_MEAN"]) - float(control["COND_U_MEAN"])
    if _f(fill_only.get("COND_D_MEAN")) is not None and _f(control.get("COND_D_MEAN")) is not None:
        delta_cd = float(fill_only["COND_D_MEAN"]) - float(control["COND_D_MEAN"])
    return {
        "CONTROL": out_c,
        "FILL_ONLY": out_fo,
        "NET_ADDITIONAL_FILL_N": net_fill,
        "CONTROL_COND_U": control.get("COND_U_MEAN"),
        "FILL_ONLY_COND_U": fill_only.get("COND_U_MEAN"),
        "DELTA_COND_U": delta_cu,
        "CONTROL_COND_D": control.get("COND_D_MEAN"),
        "FILL_ONLY_COND_D": fill_only.get("COND_D_MEAN"),
        "DELTA_COND_D": delta_cd,
        "spearman": {
            "P_FILL_U_OVERALL": spearman_small(overall_u_p, overall_u_y),
            "P_FILL_D_OVERALL": spearman_small(overall_d_p, overall_d_y),
            "P_FILL_U_DAILY_MEDIAN": _median(u_daily_vals),
            "P_FILL_D_DAILY_MEDIAN": _median(d_daily_vals),
            "P_FILL_U_COHORT_MEDIAN": _median(cohort_sp_u),
            "P_FILL_D_COHORT_MEDIAN": _median(cohort_sp_d),
            "U_POS_DAYS": sum(1 for v in u_daily_vals if v > 0),
            "U_NEG_DAYS": sum(1 for v in u_daily_vals if v < 0),
            "U_ZERO_DAYS": sum(1 for v in u_daily_vals if v == 0),
            "D_POS_DAYS": sum(1 for v in d_daily_vals if v > 0),
            "D_NEG_DAYS": sum(1 for v in d_daily_vals if v < 0),
            "D_ZERO_DAYS": sum(1 for v in d_daily_vals if v == 0),
            "daily_u": daily_u_rows,
            "daily_d": daily_d_rows,
        },
        "buckets": {k: _finish_bucket(buckets[k]) for k in RANK_BUCKETS},
        "swap": {
            "CONTROL_ONLY": _set_stats(ctrl_only_rows),
            "FILL_ONLY_ONLY": _set_stats(fo_only_rows),
            "daily": swap_day_rows,
        },
        "oracle": {
            "COHORT_N": n_coh,
            "FILL_NONWORSE_U_IMPROVE_COHORT_N": fn_u,
            "FILL_NONWORSE_U_IMPROVE_RATE": _rate(fn_u, n_coh),
            "SAME_FILL_U_IMPROVE_COHORT_N": same_u,
            "SAME_FILL_U_IMPROVE_RATE": _rate(same_u, n_coh),
            "FILL_NONWORSE_U_D_NONWORSE_COHORT_N": fn_ud,
            "FILL_NONWORSE_U_D_NONWORSE_RATE": _rate(fn_ud, n_coh),
            "SAME_FILL_U_D_NONWORSE_COHORT_N": same_ud,
            "SAME_FILL_U_D_NONWORSE_RATE": _rate(same_ud, n_coh),
            "FILL_ONLY_DOMINATED_IN_FILL_U_COHORT_N": dominated_n,
            "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": _rate(dominated_n, n_coh),
            "ORACLE_EXEC_U_DELTA_MEDIAN": _median(deltas),
            "ORACLE_EXEC_U_DELTA_MEAN": _mean(deltas),
            "ORACLE_SUBSET_ENUMERATION_ERROR_N": enum_err,
            "daily": oracle_day_rows,
        },
    }
