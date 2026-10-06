"""Predicted fill/EXEC_U Pareto vs actual FU/FUD-good Top3 subsets. Diagnostic only."""
from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any, Optional

import numpy as np

from research.am_constrained_execution_architecture_reassessment import FINAL_SELECTION_N, SLOTS
from research.am_direct_exec_u_development.oof import select_direct
from research.am_wait5_fill_upside_geometry.geometry import spearman_small
from research.am_wait5_two_stage_development.oof import _cohort_block, eval_arm, select_fill_only
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import spearman
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _mean, _median, _pct, _rate


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "")


def join_integrity(rows: list[dict[str, Any]]) -> dict[str, int]:
    seen: dict[str, int] = {}
    for r in rows:
        k = row_key(r)
        seen[k] = int(seen.get(k) or 0) + 1
    dup = sum(1 for n in seen.values() if n > 1)
    return {
        "JOIN_MISS_N": 0,
        "DUPLICATE_KEY_N": dup,
        "ROW_N": len(rows),
        "UNIQUE_KEY_N": len(seen),
    }


def _arrays(grp: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(grp)
    pf = np.full(n, np.nan, dtype=float)
    pe = np.full(n, np.nan, dtype=float)
    fl = np.zeros(n, dtype=np.int8)
    eu = np.zeros(n, dtype=float)
    ed = np.zeros(n, dtype=float)
    for i, r in enumerate(grp):
        p = _f(r.get("fill_score"))
        e = _f(r.get("pred_EXEC_U"))
        if p is not None:
            pf[i] = float(p)
        if e is not None:
            pe[i] = float(e)
        if int(r.get("Y_FILL5") or 0) == 1:
            fl[i] = 1
            u = _f(r.get("U_FILL"))
            d = _f(r.get("D_FILL"))
            eu[i] = float(u) if u is not None else 0.0
            ed[i] = float(d) if d is not None else 0.0
    return pf, pe, fl, eu, ed


def _sel_idx(grp: list[dict[str, Any]], selected: list[dict[str, Any]]) -> Optional[tuple[int, ...]]:
    by = {_sym(r): i for i, r in enumerate(grp)}
    out = []
    for r in selected:
        i = by.get(_sym(r))
        if i is None:
            return None
        out.append(int(i))
    if len(out) != int(FINAL_SELECTION_N):
        return None
    return tuple(sorted(out))


def _pareto_keep(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    m = int(xs.size)
    keep = np.zeros(m, dtype=bool)
    if m <= 0:
        return keep
    order = np.lexsort((-ys, -xs))
    max_y = -np.inf
    last_x = None
    for idx in order:
        x = float(xs[idx])
        y = float(ys[idx])
        if y > max_y:
            keep[idx] = True
            max_y = y
            last_x = x
        elif y == max_y and last_x is not None and x == last_x:
            keep[idx] = True
    return keep


def _pct_rank(value: float, xs: np.ndarray) -> Optional[float]:
    if xs.size <= 0:
        return None
    return float(np.mean(xs <= float(value) + 1e-15))


def _cohort_geometry(
    grp: list[dict[str, Any]],
    fo: list[dict[str, Any]],
    du: list[dict[str, Any]],
) -> dict[str, Any]:
    n = len(grp)
    k = int(FINAL_SELECTION_N)
    expected = int(comb(n, k)) if n >= k else 0
    empty = {
        "enumerated": 0,
        "expected": expected,
        "error": 1 if expected else 0,
        "any_fu": False,
        "any_fud": False,
        "same_fill_u": False,
        "frontier_fu": False,
        "frontier_fud": False,
        "fo_on_frontier": False,
        "du_on_frontier": False,
        "frontier_size": 0,
        "n_subsets_pred": 0,
        "too_large": False,
        "fu_fill_pct": None,
        "fu_execu_pct": None,
        "fud_fill_pct": None,
        "fud_execu_pct": None,
    }
    if n < k:
        return empty
    pf, pe, fl, eu, ed = _arrays(grp)
    fo_blk = _cohort_block(fo)
    fo_fill = int(fo_blk["n_fill"])
    fo_eu = float(fo_blk["exec_u"])
    fo_ed = float(fo_blk["exec_d"])
    slots = float(SLOTS)
    m = expected
    err = 0
    pred_x = np.full(m, np.nan, dtype=float)
    pred_y = np.full(m, np.nan, dtype=float)
    fu = np.zeros(m, dtype=bool)
    fud = np.zeros(m, dtype=bool)
    same_u = False
    any_fu = False
    any_fud = False
    best_fu = -1
    best_fu_u = -np.inf
    best_fud = -1
    best_fud_u = -np.inf
    fo_idx = _sel_idx(grp, fo)
    du_idx = _sel_idx(grp, du)
    fo_t = du_t = None
    cnt = 0
    for t, (i, j, kk) in enumerate(combinations(range(n), k)):
        cnt = t + 1
        cur = (i, j, kk)
        if fo_idx is not None and cur == fo_idx:
            fo_t = t
        if du_idx is not None and cur == du_idx:
            du_t = t
        nf = int(fl[i]) + int(fl[j]) + int(fl[kk])
        su = (float(eu[i]) + float(eu[j]) + float(eu[kk])) / slots
        sd = (float(ed[i]) + float(ed[j]) + float(ed[kk])) / slots
        px = pf[i] + pf[j] + pf[kk]
        py = pe[i] + pe[j] + pe[kk]
        if px == px and py == py:
            pred_x[t] = px
            pred_y[t] = py
        ge_fill = nf >= fo_fill
        gt_u = su > fo_eu + 1e-15
        if ge_fill and gt_u:
            fu[t] = True
            any_fu = True
            if su > best_fu_u:
                best_fu_u = su
                best_fu = t
            if sd + 1e-15 >= fo_ed:
                fud[t] = True
                any_fud = True
                if su > best_fud_u:
                    best_fud_u = su
                    best_fud = t
        if nf == fo_fill and gt_u:
            same_u = True
    if cnt != expected:
        err = 1
        m = cnt
        pred_x = pred_x[:m]
        pred_y = pred_y[:m]
        fu = fu[:m]
        fud = fud[:m]
    ok = np.isfinite(pred_x) & np.isfinite(pred_y)
    n_pred = int(ok.sum())
    keep = np.zeros(m, dtype=bool)
    if n_pred > 0:
        keep_ok = _pareto_keep(pred_x[ok], pred_y[ok])
        keep[np.flatnonzero(ok)[keep_ok]] = True
    frontier_size = int(keep.sum())
    frontier_fu = bool(np.any(keep & fu))
    frontier_fud = bool(np.any(keep & fud))
    fo_on = bool(fo_t is not None and keep[int(fo_t)])
    du_on = bool(du_t is not None and keep[int(du_t)])
    xs = pred_x[ok]
    ys = pred_y[ok]
    fu_fill_pct = fu_execu_pct = fud_fill_pct = fud_execu_pct = None
    if best_fu >= 0 and pred_x[best_fu] == pred_x[best_fu]:
        fu_fill_pct = _pct_rank(float(pred_x[best_fu]), xs)
        fu_execu_pct = _pct_rank(float(pred_y[best_fu]), ys)
    if best_fud >= 0 and pred_x[best_fud] == pred_x[best_fud]:
        fud_fill_pct = _pct_rank(float(pred_x[best_fud]), xs)
        fud_execu_pct = _pct_rank(float(pred_y[best_fud]), ys)
    return {
        "enumerated": cnt,
        "expected": expected,
        "error": err,
        "any_fu": any_fu,
        "any_fud": any_fud,
        "same_fill_u": same_u,
        "frontier_fu": frontier_fu,
        "frontier_fud": frontier_fud,
        "fo_on_frontier": fo_on,
        "du_on_frontier": du_on,
        "frontier_size": frontier_size,
        "n_subsets_pred": n_pred,
        "too_large": bool(frontier_size >= n),
        "fu_fill_pct": fu_fill_pct,
        "fu_execu_pct": fu_execu_pct,
        "fud_fill_pct": fud_fill_pct,
        "fud_execu_pct": fud_execu_pct,
    }


def evaluate_scored(scored: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    join = join_integrity(scored)
    fill_only = eval_arm(scored, select_fill_only, use_days)
    direct = eval_arm(scored, select_direct, use_days)
    fo_by = fill_only.get("selected_by") or {}
    du_by = direct.get("selected_by") or {}
    by = group_cohorts(scored)

    n_coh = 0
    enum_err = 0
    any_fu = any_fud = same_u = 0
    fr_fu = fr_fud = 0
    fo_on = du_on = 0
    sizes: list[float] = []
    too_large = 0
    fu_fp: list[float] = []
    fu_ep: list[float] = []
    fud_fp: list[float] = []
    fud_ep: list[float] = []
    daily = {
        d: {"coh": 0, "any_fu": 0, "any_fud": 0, "fr_fu": 0, "fr_fud": 0} for d in use_days
    }
    overall_p: list[float] = []
    overall_e: list[float] = []
    daily_sp = {d: ([], []) for d in use_days}
    cohort_sp: list[float] = []

    for (date, sess, an), grp in by.items():
        day = str(date)
        if day not in daily:
            continue
        if len(grp) < int(MIN_COHORT_N):
            continue
        key = (str(date), str(sess), str(an))
        fo = fo_by.get(key) or select_fill_only(grp)
        du = du_by.get(key) or select_direct(grp)
        if len(fo) != int(FINAL_SELECTION_N):
            continue
        n_coh += 1
        g = _cohort_geometry(grp, fo, du)
        enum_err += int(g["error"])
        db = daily[day]
        db["coh"] += 1
        if g["any_fu"]:
            any_fu += 1
            db["any_fu"] += 1
        if g["any_fud"]:
            any_fud += 1
            db["any_fud"] += 1
        if g["same_fill_u"]:
            same_u += 1
        if g["frontier_fu"]:
            fr_fu += 1
            db["fr_fu"] += 1
        if g["frontier_fud"]:
            fr_fud += 1
            db["fr_fud"] += 1
        if g["fo_on_frontier"]:
            fo_on += 1
        if g["du_on_frontier"]:
            du_on += 1
        sizes.append(float(g["frontier_size"]))
        if g["too_large"]:
            too_large += 1
        if g["fu_fill_pct"] is not None:
            fu_fp.append(float(g["fu_fill_pct"]))
            fu_ep.append(float(g["fu_execu_pct"]))
        if g["fud_fill_pct"] is not None:
            fud_fp.append(float(g["fud_fill_pct"]))
            fud_ep.append(float(g["fud_execu_pct"]))

        cp: list[float] = []
        ce: list[float] = []
        for r in grp:
            p = _f(r.get("fill_score"))
            e = _f(r.get("pred_EXEC_U"))
            if p is None or e is None:
                continue
            overall_p.append(float(p))
            overall_e.append(float(e))
            daily_sp[day][0].append(float(p))
            daily_sp[day][1].append(float(e))
            cp.append(float(p))
            ce.append(float(e))
        spc = spearman_small(cp, ce)
        if spc is not None:
            cohort_sp.append(spc)

    any_fu_rate = _rate(any_fu, n_coh)
    any_fud_rate = _rate(any_fud, n_coh)
    fr_fu_rate = _rate(fr_fu, n_coh)
    fr_fud_rate = _rate(fr_fud, n_coh)
    fu_cap = None if not any_fu else float(fr_fu) / float(any_fu)
    fud_cap = None if not any_fud else float(fr_fud) / float(any_fud)
    day_rows = []
    daily_sp_rows = []
    daily_sp_xs: list[float] = []
    for d in use_days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        day_rows.append(
            {
                "date": d,
                "COHORT_N": int(b["coh"]),
                "ANY_FU_GOOD_RATE": _rate(int(b["any_fu"]), int(b["coh"])),
                "ANY_FUD_GOOD_RATE": _rate(int(b["any_fud"]), int(b["coh"])),
                "FRONTIER_FU_GOOD_RATE": _rate(int(b["fr_fu"]), int(b["coh"])),
                "FRONTIER_FUD_GOOD_RATE": _rate(int(b["fr_fud"]), int(b["coh"])),
            }
        )
        sp = spearman(daily_sp[d][0], daily_sp[d][1])
        daily_sp_rows.append({"date": d, "SPEARMAN": sp})
        if sp is not None:
            daily_sp_xs.append(float(sp))

    return {
        "join": join,
        "COHORT_N": n_coh,
        "SUBSET_ENUMERATION_ERROR_N": enum_err,
        "ANY_FU_GOOD_COHORT_N": any_fu,
        "ANY_FU_GOOD_RATE": any_fu_rate,
        "ANY_FUD_GOOD_COHORT_N": any_fud,
        "ANY_FUD_GOOD_RATE": any_fud_rate,
        "SAME_FILL_U_IMPROVE_RATE": _rate(same_u, n_coh),
        "FRONTIER_FU_GOOD_COHORT_N": fr_fu,
        "FRONTIER_FU_GOOD_RATE": fr_fu_rate,
        "FRONTIER_FUD_GOOD_COHORT_N": fr_fud,
        "FRONTIER_FUD_GOOD_RATE": fr_fud_rate,
        "FU_FRONTIER_CAPTURE_RATIO": fu_cap,
        "FUD_FRONTIER_CAPTURE_RATIO": fud_cap,
        "PRED_FRONTIER_SIZE_MEAN": _mean(sizes),
        "PRED_FRONTIER_SIZE_MEDIAN": _median(sizes),
        "PRED_FRONTIER_SIZE_P75": _pct(sizes, 0.75),
        "PRED_FRONTIER_SIZE_P90": _pct(sizes, 0.90),
        "FRONTIER_TOO_LARGE_RATE": _rate(too_large, n_coh),
        "FILL_ONLY_ON_FRONTIER_RATE": _rate(fo_on, n_coh),
        "DIRECT_ON_FRONTIER_RATE": _rate(du_on, n_coh),
        "FU_GOOD_FILL_PERCENTILE_MEDIAN": _median(fu_fp),
        "FU_GOOD_EXECU_PERCENTILE_MEDIAN": _median(fu_ep),
        "FUD_GOOD_FILL_PERCENTILE_MEDIAN": _median(fud_fp),
        "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": _median(fud_ep),
        "PRED_FILL_EXECU_SPEARMAN_OVERALL": spearman(overall_p, overall_e),
        "PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN": _median(daily_sp_xs),
        "PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN": _median(cohort_sp),
        "FILL_ONLY_FILL_RATE": fill_only.get("SELECTED_FILL_RATE"),
        "DIRECT_FILL_RATE": direct.get("SELECTED_FILL_RATE"),
        "FILL_ONLY_EXEC_U": fill_only.get("EXEC_U"),
        "DIRECT_EXEC_U": direct.get("EXEC_U"),
        "FILL_ONLY_EXEC_D": fill_only.get("EXEC_D"),
        "DIRECT_EXEC_D": direct.get("EXEC_D"),
        "DELTA_FILL": None
        if _f(direct.get("SELECTED_FILL_RATE")) is None or _f(fill_only.get("SELECTED_FILL_RATE")) is None
        else float(direct["SELECTED_FILL_RATE"]) - float(fill_only["SELECTED_FILL_RATE"]),
        "DELTA_EXEC_U": None
        if _f(direct.get("EXEC_U")) is None or _f(fill_only.get("EXEC_U")) is None
        else float(direct["EXEC_U"]) - float(fill_only["EXEC_U"]),
        "DELTA_EXEC_D": None
        if _f(direct.get("EXEC_D")) is None or _f(fill_only.get("EXEC_D")) is None
        else float(direct["EXEC_D"]) - float(fill_only["EXEC_D"]),
        "daily": day_rows,
        "spearman_daily": daily_sp_rows,
    }
