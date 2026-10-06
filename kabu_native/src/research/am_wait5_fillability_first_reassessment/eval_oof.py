"""CONTROL vs FILL_ONLY selection / fill / conditional / exposure on frozen OOF. No RF."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_wait5_fillability_first_reassessment import SLOTS
from research.am_wait5_two_stage_development.oof import eval_arm, select_control, select_fill_only
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _mean, _median, _pct, _rate


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "")


def _filled(r: dict[str, Any]) -> bool:
    return int(r.get("Y_FILL5") or 0) == 1


def _exec_val(r: dict[str, Any], key: str) -> float:
    if not _filled(r):
        return 0.0
    v = _f(r.get(key))
    return float(v) if v is not None else 0.0


def _cond_vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    xs: list[float] = []
    for r in rows:
        if not _filled(r):
            continue
        v = _f(r.get(key))
        if v is None:
            continue
        xs.append(float(v))
    return xs


def _ttf_vals(rows: list[dict[str, Any]]) -> list[float]:
    xs: list[float] = []
    for r in rows:
        if not _filled(r):
            continue
        v = _f(r.get("TIME_TO_FILL_SEC"))
        if v is None:
            continue
        xs.append(float(v))
    return xs


def _ttf_pack(xs: list[float]) -> dict[str, Any]:
    return {
        "N": len(xs),
        "MEDIAN": _median(xs),
        "P75": _pct(xs, 0.75),
        "P90": _pct(xs, 0.90),
    }


def _by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_sym(r): r for r in rows}


def decompose_arms(
    control: dict[str, Any],
    fill_only: dict[str, Any],
    days: list[str],
) -> dict[str, Any]:
    fo_by = fill_only.get("selected_by") or {}
    c_by = control.get("selected_by") or {}
    keys = sorted(set(c_by) | set(fo_by))
    n_coh = 0
    common_n = ctrl_only_n = fo_only_n = 0
    changed = 0
    common_fill_n = ctrl_only_fill_n = fo_only_fill_n = 0
    ctrl_only_cu: list[float] = []
    fo_only_cu: list[float] = []
    ctrl_only_cd: list[float] = []
    fo_only_cd: list[float] = []
    e_in_u = e_out_u = e_in_d = e_out_d = 0.0
    ttf_ctrl: list[float] = []
    ttf_fo: list[float] = []
    ttf_fo_only: list[float] = []
    daily: dict[str, dict[str, Any]] = {
        d: {
            "coh": 0,
            "ctrl_sel": 0,
            "fo_sel": 0,
            "ctrl_fill": 0,
            "fo_fill": 0,
            "ctrl_cu": [],
            "fo_cu": [],
            "ctrl_cd": [],
            "fo_cd": [],
            "out_cu": [],
            "in_cu": [],
            "out_cd": [],
            "in_cd": [],
            "changed": 0,
        }
        for d in days
    }

    for k in keys:
        ctrl_rows = list(c_by.get(k) or [])
        fo_rows = list(fo_by.get(k) or [])
        if not ctrl_rows or not fo_rows:
            continue
        n_coh += 1
        date = str(k[0])
        cset = {_sym(r) for r in ctrl_rows}
        fset = {_sym(r) for r in fo_rows}
        cmap = _by_symbol(ctrl_rows)
        fmap = _by_symbol(fo_rows)
        common = cset & fset
        out_syms = cset - fset
        in_syms = fset - cset
        common_n += len(common)
        ctrl_only_n += len(out_syms)
        fo_only_n += len(in_syms)
        if cset != fset:
            changed += 1

        b = daily.get(date)
        if b is not None:
            b["coh"] += 1
            b["ctrl_sel"] += len(ctrl_rows)
            b["fo_sel"] += len(fo_rows)
            if cset != fset:
                b["changed"] += 1

        for r in ctrl_rows:
            ttf_ctrl.extend(_ttf_vals([r]))
            if b is not None and _filled(r):
                b["ctrl_fill"] += 1
                cu = _f(r.get("U_FILL"))
                cd = _f(r.get("D_FILL"))
                if cu is not None:
                    b["ctrl_cu"].append(float(cu))
                if cd is not None:
                    b["ctrl_cd"].append(float(cd))
        for r in fo_rows:
            ttf_fo.extend(_ttf_vals([r]))
            if b is not None and _filled(r):
                b["fo_fill"] += 1
                cu = _f(r.get("U_FILL"))
                cd = _f(r.get("D_FILL"))
                if cu is not None:
                    b["fo_cu"].append(float(cu))
                if cd is not None:
                    b["fo_cd"].append(float(cd))

        for s in common:
            r = cmap.get(s) or fmap.get(s)
            if r is not None and _filled(r):
                common_fill_n += 1
        for s in sorted(out_syms):
            r = cmap.get(s)
            if r is None:
                continue
            e_out_u += _exec_val(r, "U_FILL")
            e_out_d += _exec_val(r, "D_FILL")
            if _filled(r):
                ctrl_only_fill_n += 1
                ctrl_only_cu.extend(_cond_vals([r], "U_FILL"))
                ctrl_only_cd.extend(_cond_vals([r], "D_FILL"))
                if b is not None:
                    cu = _f(r.get("U_FILL"))
                    cd = _f(r.get("D_FILL"))
                    if cu is not None:
                        b["out_cu"].append(float(cu))
                    if cd is not None:
                        b["out_cd"].append(float(cd))
        for s in sorted(in_syms):
            r = fmap.get(s)
            if r is None:
                continue
            e_in_u += _exec_val(r, "U_FILL")
            e_in_d += _exec_val(r, "D_FILL")
            ttf_fo_only.extend(_ttf_vals([r]))
            if _filled(r):
                fo_only_fill_n += 1
                fo_only_cu.extend(_cond_vals([r], "U_FILL"))
                fo_only_cd.extend(_cond_vals([r], "D_FILL"))
                if b is not None:
                    cu = _f(r.get("U_FILL"))
                    cd = _f(r.get("D_FILL"))
                    if cu is not None:
                        b["in_cu"].append(float(cu))
                    if cd is not None:
                        b["in_cd"].append(float(cd))

    ctrl_fill_n = int(control.get("WOULD_FILL_N") or 0)
    fo_fill_n = int(fill_only.get("WOULD_FILL_N") or 0)
    net_fill = fo_fill_n - ctrl_fill_n
    denom = float(SLOTS) * float(n_coh) if n_coh else None
    obs_du = None
    obs_dd = None
    if _f(fill_only.get("EXEC_U")) is not None and _f(control.get("EXEC_U")) is not None:
        obs_du = float(fill_only["EXEC_U"]) - float(control["EXEC_U"])
    if _f(fill_only.get("EXEC_D")) is not None and _f(control.get("EXEC_D")) is not None:
        obs_dd = float(fill_only["EXEC_D"]) - float(control["EXEC_D"])

    sum_ctrl_u = float(control.get("EXEC_U") or 0.0) * float(SLOTS) * float(n_coh) if n_coh else 0.0
    sum_ctrl_d = float(control.get("EXEC_D") or 0.0) * float(SLOTS) * float(n_coh) if n_coh else 0.0
    u_ref = (sum_ctrl_u / float(ctrl_fill_n)) if ctrl_fill_n else 0.0
    d_ref = (sum_ctrl_d / float(ctrl_fill_n)) if ctrl_fill_n else 0.0
    n_in_f = fo_only_fill_n
    n_out_f = ctrl_only_fill_n
    u_in = (e_in_u / float(n_in_f)) if n_in_f else 0.0
    u_out = (e_out_u / float(n_out_f)) if n_out_f else 0.0
    d_in = (e_in_d / float(n_in_f)) if n_in_f else 0.0
    d_out = (e_out_d / float(n_out_f)) if n_out_f else 0.0
    dn = n_in_f - n_out_f
    extra_u = float(dn) * float(u_ref)
    extra_d = float(dn) * float(d_ref)
    qual_u = float(n_in_f) * (u_in - u_ref) - float(n_out_f) * (u_out - u_ref)
    qual_d = float(n_in_f) * (d_in - d_ref) - float(n_out_f) * (d_out - d_ref)

    def _scale(x: float) -> Optional[float]:
        if denom is None or denom == 0:
            return None
        return float(x) / float(denom)

    exec_u_extra = _scale(extra_u)
    exec_u_qual = _scale(qual_u)
    exec_d_extra = _scale(extra_d)
    exec_d_qual = _scale(qual_d)
    recon_u = None
    recon_d = None
    if obs_du is not None and exec_u_extra is not None and exec_u_qual is not None:
        recon_u = float(obs_du) - float(exec_u_extra) - float(exec_u_qual)
    if obs_dd is not None and exec_d_extra is not None and exec_d_qual is not None:
        recon_d = float(obs_dd) - float(exec_d_extra) - float(exec_d_qual)

    day_rows = []
    for d in days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        rec = {
            "date": d,
            "COHORT_N": int(b["coh"]),
            "CONTROL_FILL_RATE": _rate(int(b["ctrl_fill"]), int(b["ctrl_sel"])),
            "FILL_ONLY_FILL_RATE": _rate(int(b["fo_fill"]), int(b["fo_sel"])),
            "DELTA_FILL": None
            if b["ctrl_sel"] <= 0 or b["fo_sel"] <= 0
            else _rate(int(b["fo_fill"]), int(b["fo_sel"])) - _rate(int(b["ctrl_fill"]), int(b["ctrl_sel"])),
            "CONTROL_COND_U": _mean(b["ctrl_cu"]),
            "FILL_ONLY_COND_U": _mean(b["fo_cu"]),
            "DELTA_COND_U": None
            if not b["ctrl_cu"] or not b["fo_cu"]
            else float(np.mean(b["fo_cu"])) - float(np.mean(b["ctrl_cu"])),
            "CONTROL_COND_D": _mean(b["ctrl_cd"]),
            "FILL_ONLY_COND_D": _mean(b["fo_cd"]),
            "DELTA_COND_D": None
            if not b["ctrl_cd"] or not b["fo_cd"]
            else float(np.mean(b["fo_cd"])) - float(np.mean(b["ctrl_cd"])),
            "CONTROL_ONLY_COND_U": _mean(b["out_cu"]),
            "FILL_ONLY_ONLY_COND_U": _mean(b["in_cu"]),
            "DELTA_SWAP_COND_U": None
            if not b["out_cu"] or not b["in_cu"]
            else float(np.mean(b["in_cu"])) - float(np.mean(b["out_cu"])),
            "CONTROL_ONLY_COND_D": _mean(b["out_cd"]),
            "FILL_ONLY_ONLY_COND_D": _mean(b["in_cd"]),
            "DELTA_SWAP_COND_D": None
            if not b["out_cd"] or not b["in_cd"]
            else float(np.mean(b["in_cd"])) - float(np.mean(b["out_cd"])),
            "SELECTION_CHANGED_COHORT_N": int(b["changed"]),
        }
        day_rows.append(rec)

    cu_out = _mean(ctrl_only_cu)
    cu_in = _mean(fo_only_cu)
    cd_out = _mean(ctrl_only_cd)
    cd_in = _mean(fo_only_cd)
    return {
        "COHORT_N": n_coh,
        "COMMON_SELECTED_N": common_n,
        "CONTROL_ONLY_N": ctrl_only_n,
        "FILL_ONLY_ONLY_N": fo_only_n,
        "SELECTION_CHANGED_COHORT_N": changed,
        "SELECTION_CHANGED_COHORT_RATE": _rate(changed, n_coh),
        "COMMON_FILL_N": common_fill_n,
        "CONTROL_ONLY_FILL_N": ctrl_only_fill_n,
        "FILL_ONLY_ONLY_FILL_N": fo_only_fill_n,
        "CONTROL_ONLY_FILL_RATE": _rate(ctrl_only_fill_n, ctrl_only_n),
        "FILL_ONLY_ONLY_FILL_RATE": _rate(fo_only_fill_n, fo_only_n),
        "NET_ADDITIONAL_FILL_N": net_fill,
        "SWAP_NET_FILL_N": dn,
        "CONTROL_ONLY_COND_U": cu_out,
        "FILL_ONLY_ONLY_COND_U": cu_in,
        "DELTA_SWAP_COND_U": None if cu_out is None or cu_in is None else float(cu_in) - float(cu_out),
        "CONTROL_ONLY_COND_D": cd_out,
        "FILL_ONLY_ONLY_COND_D": cd_in,
        "DELTA_SWAP_COND_D": None if cd_out is None or cd_in is None else float(cd_in) - float(cd_out),
        "DELTA_EXEC_U": obs_du,
        "DELTA_EXEC_D": obs_dd,
        "EXEC_U_GAIN_FROM_EXTRA_FILL": exec_u_extra,
        "EXEC_U_GAIN_FROM_SELECTION_QUALITY": exec_u_qual,
        "EXEC_D_CHANGE_FROM_EXTRA_FILL": exec_d_extra,
        "EXEC_D_CHANGE_FROM_SELECTION_QUALITY": exec_d_qual,
        "RECONSTRUCTION_ERROR_U": recon_u,
        "RECONSTRUCTION_ERROR_D": recon_d,
        "TIME_TO_FILL_CONTROL": _ttf_pack(ttf_ctrl),
        "TIME_TO_FILL_FILL_ONLY": _ttf_pack(ttf_fo),
        "TIME_TO_FILL_FILL_ONLY_ONLY": _ttf_pack(ttf_fo_only),
        "daily": day_rows,
    }


def evaluate_scored(scored: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    control = eval_arm(scored, select_control, use_days)
    fill_only = eval_arm(scored, select_fill_only, use_days)
    decomp = decompose_arms(control, fill_only, use_days)
    ttf_miss = sum(
        1
        for r in scored
        if _filled(r) and _f(r.get("TIME_TO_FILL_SEC")) is None
    )
    out_c = dict(control)
    out_fo = dict(fill_only)
    out_c.pop("selected_by", None)
    out_fo.pop("selected_by", None)
    return {
        "CONTROL": out_c,
        "FILL_ONLY": out_fo,
        "decomp": decomp,
        "TTF_FILLED_MISS_N": ttf_miss,
    }


def attach_ttf(rows: list[dict[str, Any]], ttf_map: dict[str, Any]) -> int:
    miss = 0
    for r in rows:
        k = row_key(r)
        if k not in ttf_map:
            miss += 1
            r["TIME_TO_FILL_SEC"] = None
            continue
        r["TIME_TO_FILL_SEC"] = ttf_map.get(k)
    return miss
