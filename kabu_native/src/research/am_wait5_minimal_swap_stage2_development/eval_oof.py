"""Minimal-swap evaluation on frozen OOF rows. No RF. No old TWO_STAGE arm."""
from __future__ import annotations

from typing import Any, Optional

from research.am_wait5_minimal_swap_stage2_development import (
    FINAL_SELECTION_N,
    LOCKED_FILL_SLOTS,
    MAX_MEMBERSHIP_SWAP_PER_COHORT,
    STAGE1_SHORTLIST_N,
)
from research.am_wait5_stage2_fill_edge_precommit.analyze import quality_winner
from research.am_wait5_two_stage_development.oof import _cohort_block, select_control, select_fill_only
from research.am_wait5_two_stage_objective_precommit.analyze import joint_quality_tuple, percentile_ranks
from research.canonical_entry_performance_rebase.analyze import _f, rank_group
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _mean, _rate


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "")


def _filled(r: dict[str, Any]) -> bool:
    return int(r.get("Y_FILL5") or 0) == 1


def _prep(grp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in grp:
        rec = dict(r)
        rec["P_FILL5"] = rec.get("fill_score")
        out.append(rec)
    return out


def select_minimal_swap(grp: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lock fill ranks 1-2. Quality winner among ranks 3-5. Percentile ranks on Top5 preds."""
    prepared = _prep(grp)
    short = [dict(r) for r in rank_group(prepared, "P_FILL5")[: int(STAGE1_SHORTLIST_N)]]
    for i, r in enumerate(short, start=1):
        r["P_FILL_RANK"] = i
        r["LOCKED_FILL_SLOT"] = i <= int(LOCKED_FILL_SLOTS)
        r["QUALITY_POOL"] = i > int(LOCKED_FILL_SLOTS)
    usable = [
        r
        for r in short
        if _f(r.get("pred_U")) is not None and _f(r.get("pred_D")) is not None
    ]
    if usable:
        u_ranks = percentile_ranks([(float(r["pred_U"]), _sym(r)) for r in usable])
        d_ranks = percentile_ranks([(float(r["pred_D"]), _sym(r)) for r in usable])
        by = {}
        for r, ur, dr in zip(usable, u_ranks, d_ranks):
            joint, mean_ud = joint_quality_tuple(ur, dr)
            by[_sym(r)] = {
                "U_RANK": ur,
                "D_RANK": dr,
                "JOINT_QUALITY_SCORE": joint,
                "TIE_MEAN_UD_RANK": mean_ud,
            }
        for r in short:
            extra = by.get(_sym(r))
            if extra:
                r.update(extra)
    locked = short[: int(LOCKED_FILL_SLOTS)]
    pool = [
        r
        for r in short[int(LOCKED_FILL_SLOTS) :]
        if _f(r.get("U_RANK")) is not None and _f(r.get("D_RANK")) is not None
    ]
    win = quality_winner(pool)
    if win is None and len(short) > int(LOCKED_FILL_SLOTS):
        win = short[int(LOCKED_FILL_SLOTS)]
    selected = [dict(r) for r in locked]
    for r in short[int(LOCKED_FILL_SLOTS) :]:
        r["QUALITY_WINNER"] = bool(win is not None and _sym(r) == _sym(win))
        r["SELECTED"] = bool(r.get("QUALITY_WINNER"))
    if win is not None:
        rec = dict(win)
        rec["QUALITY_WINNER"] = True
        rec["SELECTED"] = True
        selected.append(rec)
    for r in locked:
        r["SELECTED"] = True
        r["QUALITY_WINNER"] = False
    return selected[: int(FINAL_SELECTION_N)], short


def _daily_blank(days: list[str]) -> dict[str, dict[str, Any]]:
    return {
        d: {"coh": 0, "sel": 0, "fill": 0, "any": 0, "eu": [], "ed": []} for d in days
    }


def _push_arm(daily: dict[str, dict[str, Any]], date: str, blk: dict[str, Any]) -> None:
    b = daily.get(str(date))
    if b is None:
        return
    b["coh"] += 1
    b["sel"] += int(blk["n_sel"])
    b["fill"] += int(blk["n_fill"])
    b["any"] += int(blk["any_fill"])
    b["eu"].append(float(blk["exec_u"]))
    b["ed"].append(float(blk["exec_d"]))


def _finish_arm(
    *,
    n_coh: int,
    n_sel: int,
    n_fill: int,
    n_any: int,
    fills_per: list[int],
    exec_u: list[float],
    exec_d: list[float],
    cond_u: list[float],
    cond_d: list[float],
    daily: dict[str, dict[str, Any]],
    days: list[str],
) -> dict[str, Any]:
    import numpy as np

    from research.passive_wait_policy_reassessment.analyze import _median

    day_rows = []
    for d in days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        day_rows.append(
            {
                "date": d,
                "SELECTED_FILL_RATE": _rate(int(b["fill"]), int(b["sel"])),
                "ANY_SELECTED_FILL_COHORT_RATE": _rate(int(b["any"]), int(b["coh"])),
                "EXEC_U": float(np.mean(b["eu"])) if b["eu"] else None,
                "EXEC_D": float(np.mean(b["ed"])) if b["ed"] else None,
                "COHORT_N": int(b["coh"]),
                "SELECTED_N": int(b["sel"]),
                "WOULD_FILL_N": int(b["fill"]),
            }
        )
    return {
        "SELECTED_N": n_sel,
        "WOULD_FILL_N": n_fill,
        "COHORT_N": n_coh,
        "SELECTED_FILL_RATE": _rate(n_fill, n_sel),
        "ANY_SELECTED_FILL_COHORT_RATE": _rate(n_any, n_coh),
        "MEAN_FILLS_PER_COHORT": float(np.mean(fills_per)) if fills_per else None,
        "EXEC_U": float(np.mean(exec_u)) if exec_u else None,
        "EXEC_D": float(np.mean(exec_d)) if exec_d else None,
        "COND_U_MEAN": float(np.mean(cond_u)) if cond_u else None,
        "COND_U_MEDIAN": _median(cond_u),
        "COND_D_MEAN": float(np.mean(cond_d)) if cond_d else None,
        "COND_D_MEDIAN": _median(cond_d),
        "COND_N": len(cond_u),
        "daily": day_rows,
    }


def evaluate_scored(scored: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    by = group_cohorts(scored)
    acc = {
        "CONTROL": {"n_coh": 0, "n_sel": 0, "n_fill": 0, "n_any": 0, "fills": [], "eu": [], "ed": [], "cu": [], "cd": [], "daily": _daily_blank(use_days)},
        "FILL_ONLY": {"n_coh": 0, "n_sel": 0, "n_fill": 0, "n_any": 0, "fills": [], "eu": [], "ed": [], "cu": [], "cd": [], "daily": _daily_blank(use_days)},
        "MINIMAL_SWAP": {"n_coh": 0, "n_sel": 0, "n_fill": 0, "n_any": 0, "fills": [], "eu": [], "ed": [], "cu": [], "cd": [], "daily": _daily_blank(use_days)},
    }
    eq = ne = 0
    swapped_in = swapped_out = 0
    r3_ret = r4_sel = r5_sel = 0
    max_swap_viol = 0
    swap_out_rows: list[dict[str, Any]] = []
    swap_in_rows: list[dict[str, Any]] = []

    def _add(name: str, date: str, selected: list[dict[str, Any]]) -> None:
        blk = _cohort_block(selected)
        a = acc[name]
        a["n_coh"] += 1
        a["n_sel"] += int(blk["n_sel"])
        a["n_fill"] += int(blk["n_fill"])
        a["n_any"] += int(blk["any_fill"])
        a["fills"].append(int(blk["n_fill"]))
        a["eu"].append(float(blk["exec_u"]))
        a["ed"].append(float(blk["exec_d"]))
        a["cu"].extend(blk["cond_u"])
        a["cd"].extend(blk["cond_d"])
        _push_arm(a["daily"], date, blk)

    for (date, _sess, _an), grp in by.items():
        if str(date) not in acc["CONTROL"]["daily"]:
            continue
        if len(grp) < int(MIN_COHORT_N):
            continue
        ctrl = select_control(grp)
        fo = select_fill_only(grp)
        ms, short = select_minimal_swap(grp)
        if len(fo) != int(FINAL_SELECTION_N) or len(ms) != int(FINAL_SELECTION_N):
            continue
        _add("CONTROL", str(date), ctrl)
        _add("FILL_ONLY", str(date), fo)
        _add("MINIMAL_SWAP", str(date), ms)

        fo_set = {_sym(r) for r in fo}
        ms_set = {_sym(r) for r in ms}
        out_syms = fo_set - ms_set
        in_syms = ms_set - fo_set
        if fo_set == ms_set:
            eq += 1
        else:
            ne += 1
        if len(out_syms) > int(MAX_MEMBERSHIP_SWAP_PER_COHORT) or len(in_syms) > int(MAX_MEMBERSHIP_SWAP_PER_COHORT):
            max_swap_viol += 1
        swapped_out += len(out_syms)
        swapped_in += len(in_syms)

        ranks = {_sym(r): int(r.get("P_FILL_RANK") or 0) for r in short}
        third = ms[2] if len(ms) > 2 else None
        third_rank = ranks.get(_sym(third), 0) if third is not None else 0
        if third_rank == 3:
            r3_ret += 1
        elif third_rank == 4:
            r4_sel += 1
        elif third_rank == 5:
            r5_sel += 1

        if out_syms and in_syms:
            fo_by = {_sym(r): r for r in fo}
            ms_by = {_sym(r): r for r in ms}
            for s in sorted(out_syms):
                if s in fo_by:
                    swap_out_rows.append(fo_by[s])
            for s in sorted(in_syms):
                if s in ms_by:
                    swap_in_rows.append(ms_by[s])

    def _arm(name: str) -> dict[str, Any]:
        a = acc[name]
        return _finish_arm(
            n_coh=int(a["n_coh"]),
            n_sel=int(a["n_sel"]),
            n_fill=int(a["n_fill"]),
            n_any=int(a["n_any"]),
            fills_per=list(a["fills"]),
            exec_u=list(a["eu"]),
            exec_d=list(a["ed"]),
            cond_u=list(a["cu"]),
            cond_d=list(a["cd"]),
            daily=a["daily"],
            days=use_days,
        )

    def _swap_fill(rows: list[dict[str, Any]]) -> tuple[int, int, Optional[float]]:
        n = len(rows)
        nf = sum(1 for r in rows if _filled(r))
        return n, nf, _rate(nf, n)

    def _swap_cond(rows: list[dict[str, Any]], key: str) -> Optional[float]:
        xs = []
        for r in rows:
            if not _filled(r):
                continue
            v = _f(r.get(key))
            if v is None:
                continue
            xs.append(float(v))
        return _mean(xs)

    out_n, out_fill, out_fr = _swap_fill(swap_out_rows)
    in_n, in_fill, in_fr = _swap_fill(swap_in_rows)
    out_cu = _swap_cond(swap_out_rows, "U_FILL")
    in_cu = _swap_cond(swap_in_rows, "U_FILL")
    out_cd = _swap_cond(swap_out_rows, "D_FILL")
    in_cd = _swap_cond(swap_in_rows, "D_FILL")
    tot = eq + ne
    return {
        "CONTROL": _arm("CONTROL"),
        "FILL_ONLY": _arm("FILL_ONLY"),
        "MINIMAL_SWAP": _arm("MINIMAL_SWAP"),
        "distinctness": {
            "MINIMAL_SWAP_EQ_FILL_ONLY_COHORT_N": eq,
            "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_N": ne,
            "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE": _rate(ne, tot),
            "SWAPPED_OUT_N": swapped_out,
            "SWAPPED_IN_N": swapped_in,
            "R3_RETAINED_N": r3_ret,
            "R4_SELECTED_N": r4_sel,
            "R5_SELECTED_N": r5_sel,
            "MAX_SWAP_VIOLATION_N": max_swap_viol,
        },
        "swap_only": {
            "SWAP_OUT_N": out_n,
            "SWAP_IN_N": in_n,
            "SWAP_OUT_FILL_N": out_fill,
            "SWAP_IN_FILL_N": in_fill,
            "SWAP_OUT_FILL_RATE": out_fr,
            "SWAP_IN_FILL_RATE": in_fr,
            "SWAP_FILL_DELTA": float(in_fill - out_fill),
            "SWAP_OUT_COND_U": out_cu,
            "SWAP_IN_COND_U": in_cu,
            "DELTA_SWAP_COND_U": None if out_cu is None or in_cu is None else float(in_cu) - float(out_cu),
            "SWAP_OUT_COND_D": out_cd,
            "SWAP_IN_COND_D": in_cd,
            "DELTA_SWAP_COND_D": None if out_cd is None or in_cd is None else float(in_cd) - float(out_cd),
        },
    }
