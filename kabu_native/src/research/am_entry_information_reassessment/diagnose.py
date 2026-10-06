"""Off-frontier FUD_GOOD vs false-dominator diagnostic. No selection. No fitting."""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from math import comb
from typing import Any, Optional

import numpy as np

from research.am_constrained_execution_architecture_reassessment.geometry import (
    _arrays,
    _pareto_keep,
    _pct_rank,
    _sel_idx,
    join_integrity,
)
from research.am_direct_exec_u_development.oof import select_direct
from research.am_entry_information_reassessment import CANONICAL_FEATURES, FINAL_SELECTION_N, SLOTS, SPEARMAN_MIN_N
from research.am_wait5_fill_upside_geometry.geometry import spearman_small
from research.am_wait5_two_stage_development.oof import _cohort_block, eval_arm, select_fill_only
from research.canonical_entry_performance_rebase.analyze import _f, rank_group, row_key
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import F2_UNION, MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import spearman
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _mean, _median, _rate


def attach_raw_features(scored: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> int:
    by = {row_key(r): r for r in raw_rows}
    miss = 0
    for s in scored:
        raw = by.get(row_key(s))
        if raw is None:
            miss += 1
            continue
        for f in F2_UNION:
            s[f] = raw.get(f)
    return miss


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "")


def _cohort_ranks(grp: list[dict[str, Any]], key: str) -> np.ndarray:
    n = len(grp)
    ranks = np.full(n, np.nan, dtype=float)
    by = {_sym(r): i for i, r in enumerate(grp)}
    for k, r in enumerate(rank_group(grp, key), start=1):
        i = by.get(_sym(r))
        if i is not None:
            ranks[i] = float(k)
    return ranks


def _feat_col(grp: list[dict[str, Any]], name: str) -> np.ndarray:
    out = np.full(len(grp), np.nan, dtype=float)
    for i, r in enumerate(grp):
        v = _f(r.get(name))
        if v is not None:
            out[i] = float(v)
    return out


def _sign_days(xs: list[float]) -> tuple[int, int, int]:
    pos = sum(1 for v in xs if v > 0)
    neg = sum(1 for v in xs if v < 0)
    zero = sum(1 for v in xs if v == 0)
    return pos, neg, zero


def _mask_median(vals: np.ndarray, mask: np.ndarray) -> Optional[float]:
    sl = vals[mask]
    sl = sl[np.isfinite(sl)]
    if sl.size <= 0:
        return None
    return float(np.median(sl))


def _cat(chunks: list[np.ndarray]) -> Optional[np.ndarray]:
    xs = [c for c in chunks if c is not None and int(c.size) > 0]
    if not xs:
        return None
    return np.concatenate(xs)


def _arr_median(xs: Optional[np.ndarray]) -> Optional[float]:
    if xs is None or int(xs.size) <= 0:
        return None
    return float(np.median(xs))


def _arr_mean(xs: Optional[np.ndarray]) -> Optional[float]:
    if xs is None or int(xs.size) <= 0:
        return None
    return float(np.mean(xs))


def _cohort_diag(grp: list[dict[str, Any]], fo: list[dict[str, Any]], du: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(grp)
    k = int(FINAL_SELECTION_N)
    expected = int(comb(n, k)) if n >= k else 0
    blank = {
        "error": 1 if expected else 0,
        "any_fu": False,
        "any_fud": False,
        "frontier_fu": False,
        "frontier_fud": False,
        "fo_on": False,
        "du_on": False,
        "fud_fill_pct": None,
        "fud_execu_pct": None,
        "off_fud_n": 0,
        "off_fud": False,
        "fd_n": 0,
        "fd_fill_n": 0,
        "fd_u_n": 0,
        "fd_d_n": 0,
        "fd_mult_n": 0,
        "dpx": None,
        "dpy": None,
        "daf": None,
        "dau": None,
        "dad": None,
        "go": None,
        "do": None,
        "hh": None,
        "in_fud": None,
        "rk_pf": None,
        "rk_pe": None,
        "fl": None,
        "eu": None,
        "ed": None,
        "pf": None,
        "pe": None,
    }
    if n < k:
        return blank
    pf, pe, fl, eu, ed = _arrays(grp)
    fo_blk = _cohort_block(fo)
    fo_fill = int(fo_blk["n_fill"])
    fo_eu = float(fo_blk["exec_u"])
    fo_ed = float(fo_blk["exec_d"])
    slots = float(SLOTS)
    triples = np.asarray(list(combinations(range(n), k)), dtype=np.int32)
    m = int(triples.shape[0])
    err = 0 if m == expected else 1
    ii = triples[:, 0]
    jj = triples[:, 1]
    kk = triples[:, 2]
    act_fill = fl[ii].astype(np.int16) + fl[jj].astype(np.int16) + fl[kk].astype(np.int16)
    act_u = (eu[ii] + eu[jj] + eu[kk]) / slots
    act_d = (ed[ii] + ed[jj] + ed[kk]) / slots
    pred_x = pf[ii] + pf[jj] + pf[kk]
    pred_y = pe[ii] + pe[jj] + pe[kk]
    fu = (act_fill >= fo_fill) & (act_u > fo_eu + 1e-15)
    fud = fu & (act_d + 1e-15 >= fo_ed)
    finite = np.isfinite(pred_x) & np.isfinite(pred_y)
    keep = np.zeros(m, dtype=bool)
    if int(finite.sum()) > 0:
        keep[np.flatnonzero(finite)[_pareto_keep(pred_x[finite], pred_y[finite])]] = True
    fo_idx = _sel_idx(grp, fo)
    du_idx = _sel_idx(grp, du)
    triple_t = {tuple(int(x) for x in triples[t]): t for t in range(m)}
    fo_on = bool(fo_idx is not None and fo_idx in triple_t and keep[triple_t[fo_idx]])
    du_on = bool(du_idx is not None and du_idx in triple_t and keep[triple_t[du_idx]])
    xs = pred_x[finite]
    ys = pred_y[finite]
    fud_fill_pct = fud_execu_pct = None
    if bool(fud.any()):
        fud_i = np.flatnonzero(fud)
        best = int(fud_i[int(np.argmax(act_u[fud_i]))])
        if pred_x[best] == pred_x[best]:
            fud_fill_pct = _pct_rank(float(pred_x[best]), xs)
            fud_execu_pct = _pct_rank(float(pred_y[best]), ys)
    off = fud & ~keep
    off_n = int(off.sum())
    is_fd = np.zeros(m, dtype=bool)
    pair_dpx: list[np.ndarray] = []
    pair_dpy: list[np.ndarray] = []
    pair_daf: list[np.ndarray] = []
    pair_dau: list[np.ndarray] = []
    pair_dad: list[np.ndarray] = []
    go = np.zeros(n, dtype=bool)
    do = np.zeros(n, dtype=bool)
    for g in np.flatnonzero(off & finite):
        gx = float(pred_x[g])
        gy = float(pred_y[g])
        fd = finite & ~fud & (pred_x >= gx) & (pred_y >= gy) & ((pred_x > gx) | (pred_y > gy))
        if not bool(fd.any()):
            continue
        is_fd |= fd
        pair_dpx.append(pred_x[fd] - gx)
        pair_dpy.append(pred_y[fd] - gy)
        pair_daf.append(act_fill[fd].astype(float) - float(act_fill[g]))
        pair_dau.append(act_u[fd] - float(act_u[g]))
        pair_dad.append(act_d[fd] - float(act_d[g]))
        g_mem = triples[g]
        d_mem = triples[fd]
        for a in g_mem:
            if np.any(~np.any(d_mem == int(a), axis=1)):
                go[int(a)] = True
        only = d_mem[~np.isin(d_mem, g_mem)]
        if only.size:
            do[only.astype(np.int32)] = True
    fd_idx = np.flatnonzero(is_fd)
    fill_flag = act_fill[fd_idx] < fo_fill
    u_flag = ~(act_u[fd_idx] > fo_eu + 1e-15)
    d_flag = ~(act_d[fd_idx] + 1e-15 >= fo_ed)
    n_fail = fill_flag.astype(np.int8) + u_flag.astype(np.int8) + d_flag.astype(np.int8)
    multiple = n_fail >= 2
    in_fud = np.zeros(n, dtype=bool)
    if bool(fud.any()):
        in_fud[triples[fud].ravel()] = True
    med_p = float(np.nanmedian(pf)) if np.isfinite(pf).any() else None
    med_e = float(np.nanmedian(pe)) if np.isfinite(pe).any() else None
    hh = np.zeros(n, dtype=bool)
    if med_p is not None and med_e is not None:
        hh = np.isfinite(pf) & np.isfinite(pe) & (pf >= med_p) & (pe >= med_e)
    return {
        "error": err,
        "any_fu": bool(fu.any()),
        "any_fud": bool(fud.any()),
        "frontier_fu": bool(np.any(keep & fu)),
        "frontier_fud": bool(np.any(keep & fud)),
        "fo_on": fo_on,
        "du_on": du_on,
        "fud_fill_pct": fud_fill_pct,
        "fud_execu_pct": fud_execu_pct,
        "off_fud_n": off_n,
        "off_fud": bool(off_n > 0),
        "fd_n": int(fd_idx.size),
        "fd_fill_n": int(np.sum(fill_flag & ~multiple)),
        "fd_u_n": int(np.sum((~fill_flag) & u_flag & (~d_flag))),
        "fd_d_n": int(np.sum((~fill_flag) & (~u_flag) & d_flag)),
        "fd_mult_n": int(np.sum(multiple)),
        "dpx": np.concatenate(pair_dpx) if pair_dpx else None,
        "dpy": np.concatenate(pair_dpy) if pair_dpy else None,
        "daf": np.concatenate(pair_daf) if pair_daf else None,
        "dau": np.concatenate(pair_dau) if pair_dau else None,
        "dad": np.concatenate(pair_dad) if pair_dad else None,
        "go": go,
        "do": do,
        "hh": hh,
        "in_fud": in_fud,
        "rk_pf": _cohort_ranks(grp, "fill_score"),
        "rk_pe": _cohort_ranks(grp, "pred_EXEC_U"),
        "fl": fl,
        "eu": eu,
        "ed": ed,
        "pf": pf,
        "pe": pe,
    }


def evaluate_information(scored: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    from research.am_entry_information_reassessment import DAY_CONSISTENT_MIN_N

    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    join = join_integrity(scored)
    fill_only = eval_arm(scored, select_fill_only, use_days)
    direct = eval_arm(scored, select_direct, use_days)
    fo_by = fill_only.get("selected_by") or {}
    du_by = direct.get("selected_by") or {}
    by = group_cohorts(scored)
    feats = list(CANONICAL_FEATURES)
    n_coh = enum_err = 0
    any_fu = any_fud = fr_fu = fr_fud = fo_on = du_on = 0
    off_coh = off_subset = 0
    fd_n = fd_coh = 0
    fd_fill = fd_u = fd_d = fd_mult = 0
    fd_per: list[float] = []
    fud_fp: list[float] = []
    fud_ep: list[float] = []
    dpx_all: list[np.ndarray] = []
    dpy_all: list[np.ndarray] = []
    daf_all: list[np.ndarray] = []
    dau_all: list[np.ndarray] = []
    dad_all: list[np.ndarray] = []
    go_n = do_n = go_fill = do_fill = 0
    go_pf: list[float] = []
    do_pf: list[float] = []
    go_pe: list[float] = []
    do_pe: list[float] = []
    go_cu: list[float] = []
    do_cu: list[float] = []
    go_cd: list[float] = []
    do_cd: list[float] = []
    go_rk_pe: list[float] = []
    do_rk_pe: list[float] = []
    go_rk_pf: list[float] = []
    do_rk_pf: list[float] = []
    hh_n = hh_fill = hh_in_fud = hh_fp = 0
    hh_eu: list[float] = []
    hh_ed: list[float] = []
    feat_cohort: dict[str, list[tuple[str, float]]] = {f: [] for f in feats}
    feat_hh_cohort: dict[str, list[tuple[str, float]]] = {f: [] for f in feats}
    overall_p: list[float] = []
    overall_e: list[float] = []
    res_fill_x: dict[str, list[float]] = {f: [] for f in feats}
    res_fill_y: dict[str, list[float]] = {f: [] for f in feats}
    res_u_x: dict[str, list[float]] = {f: [] for f in feats}
    res_u_y: dict[str, list[float]] = {f: [] for f in feats}
    res_fill_day: dict[str, dict[str, tuple[list[float], list[float]]]] = {
        f: {d: ([], []) for d in use_days} for f in feats
    }
    res_u_day: dict[str, dict[str, tuple[list[float], list[float]]]] = {
        f: {d: ([], []) for d in use_days} for f in feats
    }

    for (date, sess, an), grp in by.items():
        day = str(date)
        if day not in use_days:
            continue
        if len(grp) < int(MIN_COHORT_N):
            continue
        key = (str(date), str(sess), str(an))
        fo = fo_by.get(key) or select_fill_only(grp)
        du = du_by.get(key) or select_direct(grp)
        if len(fo) != int(FINAL_SELECTION_N):
            continue
        n_coh += 1
        g = _cohort_diag(grp, fo, du)
        enum_err += int(g["error"])
        if g["any_fu"]:
            any_fu += 1
        if g["any_fud"]:
            any_fud += 1
        if g["frontier_fu"]:
            fr_fu += 1
        if g["frontier_fud"]:
            fr_fud += 1
        if g["fo_on"]:
            fo_on += 1
        if g["du_on"]:
            du_on += 1
        if g["fud_fill_pct"] is not None:
            fud_fp.append(float(g["fud_fill_pct"]))
            fud_ep.append(float(g["fud_execu_pct"]))
        off_subset += int(g["off_fud_n"])
        if g["off_fud"]:
            off_coh += 1
        fd_n += int(g["fd_n"])
        if int(g["fd_n"]) > 0:
            fd_coh += 1
            fd_per.append(float(g["fd_n"]))
        elif g["off_fud"]:
            fd_per.append(0.0)
        fd_fill += int(g["fd_fill_n"])
        fd_u += int(g["fd_u_n"])
        fd_d += int(g["fd_d_n"])
        fd_mult += int(g["fd_mult_n"])
        if g["dpx"] is not None:
            dpx_all.append(g["dpx"])
            dpy_all.append(g["dpy"])
            daf_all.append(g["daf"])
            dau_all.append(g["dau"])
            dad_all.append(g["dad"])
        go = g["go"]
        do = g["do"]
        hh = g["hh"]
        in_fud = g["in_fud"]
        fl = g["fl"]
        eu = g["eu"]
        ed = g["ed"]
        pf = g["pf"]
        pe = g["pe"]
        rk_pf = g["rk_pf"]
        rk_pe = g["rk_pe"]
        if go is not None and do is not None and fl is not None:
            go_n += int(go.sum())
            do_n += int(do.sum())
            go_fill += int((go & (fl == 1)).sum())
            do_fill += int((do & (fl == 1)).sum())
            for i in np.flatnonzero(go):
                if np.isfinite(pf[i]):
                    go_pf.append(float(pf[i]))
                if np.isfinite(pe[i]):
                    go_pe.append(float(pe[i]))
                if int(fl[i]) == 1:
                    go_cu.append(float(eu[i]))
                    go_cd.append(float(ed[i]))
                if np.isfinite(rk_pe[i]):
                    go_rk_pe.append(float(rk_pe[i]))
                if np.isfinite(rk_pf[i]):
                    go_rk_pf.append(float(rk_pf[i]))
            for i in np.flatnonzero(do):
                if np.isfinite(pf[i]):
                    do_pf.append(float(pf[i]))
                if np.isfinite(pe[i]):
                    do_pe.append(float(pe[i]))
                if int(fl[i]) == 1:
                    do_cu.append(float(eu[i]))
                    do_cd.append(float(ed[i]))
                if np.isfinite(rk_pe[i]):
                    do_rk_pe.append(float(rk_pe[i]))
                if np.isfinite(rk_pf[i]):
                    do_rk_pf.append(float(rk_pf[i]))
            if int(go.sum()) > 0 and int(do.sum()) > 0:
                for f in feats:
                    col = _feat_col(grp, f)
                    a = _mask_median(col, go)
                    b = _mask_median(col, do)
                    if a is not None and b is not None:
                        feat_cohort[f].append((day, float(a) - float(b)))
                    if hh is not None and int((go & hh).sum()) > 0 and int((do & hh).sum()) > 0:
                        ah = _mask_median(col, go & hh)
                        bh = _mask_median(col, do & hh)
                        if ah is not None and bh is not None:
                            feat_hh_cohort[f].append((day, float(ah) - float(bh)))
        if hh is not None and fl is not None:
            nh = int(hh.sum())
            hh_n += nh
            hh_fill += int((hh & (fl == 1)).sum())
            if in_fud is not None:
                hh_in_fud += int((hh & in_fud).sum())
                hh_fp += int((hh & ~in_fud).sum())
            for i in np.flatnonzero(hh):
                hh_eu.append(float(eu[i]))
                hh_ed.append(float(ed[i]))
        for i, r in enumerate(grp):
            p = _f(r.get("fill_score"))
            e = _f(r.get("pred_EXEC_U"))
            if p is not None and e is not None:
                overall_p.append(float(p))
                overall_e.append(float(e))
            yi = 1.0 if int(r.get("Y_FILL5") or 0) == 1 else 0.0
            au = float(eu[i]) if eu is not None else 0.0
            fill_res = None if p is None else yi - float(p)
            u_res = None if e is None else au - float(e)
            for f in feats:
                fv = _f(r.get(f))
                if fv is None:
                    continue
                if fill_res is not None:
                    res_fill_x[f].append(float(fv))
                    res_fill_y[f].append(float(fill_res))
                    res_fill_day[f][day][0].append(float(fv))
                    res_fill_day[f][day][1].append(float(fill_res))
                if u_res is not None:
                    res_u_x[f].append(float(fv))
                    res_u_y[f].append(float(u_res))
                    res_u_day[f][day][0].append(float(fv))
                    res_u_day[f][day][1].append(float(u_res))

    dpx = _cat(dpx_all)
    dpy = _cat(dpy_all)
    daf = _cat(daf_all)
    dau = _cat(dau_all)
    dad = _cat(dad_all)
    fd_tot = fd_fill + fd_u + fd_d + fd_mult
    feature_rows = []
    residual_rows = []
    min_cons = int(DAY_CONSISTENT_MIN_N)
    for f in feats:
        diffs = [v for _d, v in feat_cohort[f]]
        by_day: dict[str, list[float]] = defaultdict(list)
        for d, v in feat_cohort[f]:
            by_day[d].append(v)
        day_med_f = []
        for d in use_days:
            if not by_day.get(d):
                continue
            med = _median(by_day[d])
            if med is not None:
                day_med_f.append(float(med))
        pos, neg, zero = _sign_days(day_med_f)
        hh_diffs = [v for _d, v in feat_hh_cohort[f]]
        hh_day: dict[str, list[float]] = defaultdict(list)
        for d, v in feat_hh_cohort[f]:
            hh_day[d].append(v)
        hh_day_f = []
        for d in use_days:
            if not hh_day.get(d):
                continue
            med = _median(hh_day[d])
            if med is not None:
                hh_day_f.append(float(med))
        hh_pos, hh_neg, hh_zero = _sign_days(hh_day_f)
        feature_rows.append(
            {
                "feature": f,
                "MEDIAN_PAIRED_DIFF": _median(diffs),
                "MEAN_PAIRED_DIFF": _mean(diffs),
                "POS_DAYS": pos,
                "NEG_DAYS": neg,
                "ZERO_DAYS": zero,
                "DAY_CONSISTENT": bool((pos >= min_cons and pos > neg) or (neg >= min_cons and neg > pos)),
                "HH_MEDIAN_PAIRED_DIFF": _median(hh_diffs),
                "HH_POS_DAYS": hh_pos,
                "HH_NEG_DAYS": hh_neg,
                "HH_ZERO_DAYS": hh_zero,
                "COHORT_DIFF_N": len(diffs),
            }
        )
        fill_sp = spearman_small(res_fill_x[f], res_fill_y[f], min_n=int(SPEARMAN_MIN_N))
        u_sp = spearman_small(res_u_x[f], res_u_y[f], min_n=int(SPEARMAN_MIN_N))
        fill_day_sp = []
        u_day_sp = []
        for d in use_days:
            a, b = res_fill_day[f][d]
            sp = spearman_small(a, b, min_n=int(SPEARMAN_MIN_N))
            if sp is not None:
                fill_day_sp.append(float(sp))
            a2, b2 = res_u_day[f][d]
            sp2 = spearman_small(a2, b2, min_n=int(SPEARMAN_MIN_N))
            if sp2 is not None:
                u_day_sp.append(float(sp2))
        fpos, fneg, fzero = _sign_days(fill_day_sp)
        upos, uneg, uzero = _sign_days(u_day_sp)
        residual_rows.append(
            {
                "feature": f,
                "FILL_RESIDUAL_SPEARMAN": fill_sp,
                "EXEC_U_RESIDUAL_SPEARMAN": u_sp,
                "FILL_POS_DAYS": fpos,
                "FILL_NEG_DAYS": fneg,
                "FILL_ZERO_DAYS": fzero,
                "FILL_DAY_CONSISTENT": bool((fpos >= min_cons and fpos > fneg) or (fneg >= min_cons and fneg > fpos)),
                "EXEC_U_POS_DAYS": upos,
                "EXEC_U_NEG_DAYS": uneg,
                "EXEC_U_ZERO_DAYS": uzero,
                "EXEC_U_DAY_CONSISTENT": bool((upos >= min_cons and upos > uneg) or (uneg >= min_cons and uneg > upos)),
            }
        )

    return {
        "join": join,
        "COHORT_N": n_coh,
        "SUBSET_ENUMERATION_ERROR_N": enum_err,
        "ANY_FU_GOOD_RATE": _rate(any_fu, n_coh),
        "ANY_FUD_GOOD_RATE": _rate(any_fud, n_coh),
        "FRONTIER_FU_GOOD_RATE": _rate(fr_fu, n_coh),
        "FRONTIER_FUD_GOOD_RATE": _rate(fr_fud, n_coh),
        "FILL_ONLY_ON_FRONTIER_RATE": _rate(fo_on, n_coh),
        "DIRECT_ON_FRONTIER_RATE": _rate(du_on, n_coh),
        "FUD_GOOD_FILL_PERCENTILE_MEDIAN": _median(fud_fp),
        "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": _median(fud_ep),
        "PRED_FILL_EXECU_SPEARMAN_OVERALL": spearman(overall_p, overall_e),
        "OFF_FRONTIER_FUD_GOOD_COHORT_N": off_coh,
        "OFF_FRONTIER_FUD_GOOD_SUBSET_N": off_subset,
        "FALSE_DOMINATOR_N": fd_n,
        "FALSE_DOMINATOR_COHORT_N": fd_coh,
        "FALSE_DOMINATOR_PER_COHORT_MEDIAN": _median(fd_per),
        "FD_FILL_FAIL_RATE": _rate(fd_fill, fd_tot),
        "FD_U_FAIL_RATE": _rate(fd_u, fd_tot),
        "FD_D_FAIL_RATE": _rate(fd_d, fd_tot),
        "FD_MULTIPLE_FAIL_RATE": _rate(fd_mult, fd_tot),
        "DELTA_PRED_FILL_MEDIAN": _arr_median(dpx),
        "DELTA_PRED_FILL_MEAN": _arr_mean(dpx),
        "DELTA_PRED_EXEC_U_MEDIAN": _arr_median(dpy),
        "DELTA_PRED_EXEC_U_MEAN": _arr_mean(dpy),
        "DELTA_ACT_FILL_MEDIAN": _arr_median(daf),
        "DELTA_ACT_FILL_MEAN": _arr_mean(daf),
        "DELTA_ACT_EXEC_U_MEDIAN": _arr_median(dau),
        "DELTA_ACT_EXEC_U_MEAN": _arr_mean(dau),
        "DELTA_ACT_EXEC_D_MEDIAN": _arr_median(dad),
        "DELTA_ACT_EXEC_D_MEAN": _arr_mean(dad),
        "PAIR_N": int(dpx.size) if dpx is not None else 0,
        "GOOD_ONLY_N": go_n,
        "DOMINATOR_ONLY_N": do_n,
        "GOOD_ONLY_FILL_RATE": _rate(go_fill, go_n),
        "DOMINATOR_ONLY_FILL_RATE": _rate(do_fill, do_n),
        "GOOD_ONLY_COND_U": _mean(go_cu),
        "DOMINATOR_ONLY_COND_U": _mean(do_cu),
        "GOOD_ONLY_COND_D": _mean(go_cd),
        "DOMINATOR_ONLY_COND_D": _mean(do_cd),
        "GOOD_ONLY_P_FILL_MEDIAN": _median(go_pf),
        "DOMINATOR_ONLY_P_FILL_MEDIAN": _median(do_pf),
        "GOOD_ONLY_PRED_EXEC_U_MEDIAN": _median(go_pe),
        "DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN": _median(do_pe),
        "GOOD_ONLY_PRED_EXECU_RANK_MEDIAN": _median(go_rk_pe),
        "DOMINATOR_ONLY_PRED_EXECU_RANK_MEDIAN": _median(do_rk_pe),
        "GOOD_ONLY_PFILL_RANK_MEDIAN": _median(go_rk_pf),
        "DOMINATOR_ONLY_PFILL_RANK_MEDIAN": _median(do_rk_pf),
        "TOP_PRED_FALSE_POSITIVE_RATE": _rate(hh_fp, hh_n),
        "HIGH_HIGH_N": hh_n,
        "HIGH_HIGH_FILL_RATE": _rate(hh_fill, hh_n),
        "HIGH_HIGH_EXEC_U": _mean(hh_eu),
        "HIGH_HIGH_EXEC_D": _mean(hh_ed),
        "HIGH_HIGH_FUD_CONTRIBUTION_RATE": _rate(hh_in_fud, hh_n),
        "FILL_ONLY_FILL_RATE": fill_only.get("SELECTED_FILL_RATE"),
        "DIRECT_FILL_RATE": direct.get("SELECTED_FILL_RATE"),
        "features": feature_rows,
        "residuals": residual_rows,
    }
