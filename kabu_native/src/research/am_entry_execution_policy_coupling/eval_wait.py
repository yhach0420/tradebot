"""Frozen FILL_ONLY / DIRECT selection diagnostics on harvest waits. No re-rank. No refit."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_entry_execution_policy_coupling import SLOTS
from research.am_wait5_two_stage_development.oof import distinctness as _ts_distinct
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.entry_objective_redesign_c3.oof import spearman
from research.passive_wait_policy_reassessment import WAIT_IDS
from research.passive_wait_policy_reassessment.analyze import _mean, _median, _pct, _rate, wait_body

W5_CAUSE = {
    "NO_VALID_CONTINUOUS_BOARD": "NO_VALID_BOARD_WITHIN_5",
    "NO_ASK_CROSS_WITHIN_WAIT": "VALID_BOARD_BUT_NO_ASK_CROSS_WITHIN_5",
    "BOARD_BECAME_NONEXECUTABLE": "BECAME_NONEXECUTABLE",
}


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "")


def selected_by_from_slim(rows: list[dict[str, Any]]) -> dict[tuple, list[dict[str, Any]]]:
    by: dict[tuple, list[dict[str, Any]]] = {}
    for r in rows:
        k = (str(r.get("date") or ""), str(r.get("session") or "AM"), str(r.get("anchor") or ""))
        by.setdefault(k, []).append(r)
    return by


def attach_waits(rows: list[dict[str, Any]], harvest_by: dict[str, dict[str, Any]]) -> int:
    miss = 0
    for r in rows:
        h = harvest_by.get(row_key(r))
        if h is None:
            miss += 1
            r["waits"] = {}
            continue
        r["waits"] = dict(h.get("waits") or {})
        if h.get("limit") is not None:
            r["limit"] = h.get("limit")
        if h.get("t0") is not None:
            r["t0"] = h.get("t0")
    return miss


def y_vs_w5_mismatch(rows: list[dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        y = int(r.get("Y_FILL5") or 0) == 1
        w = bool(wait_body(r, "W5").get("WOULD_FILL"))
        if y != w:
            n += 1
    return n


def first_ask_cross_sec(r: dict[str, Any]) -> Optional[float]:
    w = wait_body(r, "W10")
    if not w.get("WOULD_FILL"):
        return None
    return _f(w.get("TIME_TO_FILL_SEC"))


def first_valid_board_sec(r: dict[str, Any]) -> Optional[float]:
    """Harvest-consistent: first fill-eligible valid board is the first ask-cross snapshot.
    NO_ASK_CROSS without fill has a valid board whose exact second is not stored."""
    return first_ask_cross_sec(r)


def had_valid_board(r: dict[str, Any], wid: str) -> bool:
    w = wait_body(r, wid)
    if w.get("WOULD_FILL"):
        return True
    return str(w.get("nonfill_class") or "") == "NO_ASK_CROSS_WITHIN_WAIT"


def w5_nonfill_cause(r: dict[str, Any]) -> str:
    w = wait_body(r, "W5")
    if w.get("WOULD_FILL"):
        return "FILL"
    klass = str(w.get("nonfill_class") or "OTHER")
    return W5_CAUSE.get(klass, "OTHER")


def w10_nonfill_bucket(r: dict[str, Any]) -> str:
    w = wait_body(r, "W10")
    if w.get("WOULD_FILL"):
        return "FILL"
    klass = str(w.get("nonfill_class") or "OTHER")
    if klass == "NO_VALID_CONTINUOUS_BOARD":
        return "NO_VALID_BOARD"
    if klass == "NO_ASK_CROSS_WITHIN_WAIT":
        return "NEVER_CROSS"
    if klass == "BOARD_BECAME_NONEXECUTABLE":
        return "BECAME_NONEXECUTABLE"
    return "OTHER"


def late_recoverable(r: dict[str, Any]) -> bool:
    if wait_body(r, "W5").get("WOULD_FILL"):
        return False
    w10 = wait_body(r, "W10")
    if not w10.get("WOULD_FILL"):
        return False
    t = _f(w10.get("TIME_TO_FILL_SEC"))
    if t is None:
        return True
    return float(t) > 5.0 - 1e-12 and float(t) <= 10.0 + 1e-12


def metrics_from_selected(
    selected_by: dict[tuple, list[dict[str, Any]]],
    days: list[str],
    wid: str,
) -> dict[str, Any]:
    n_coh = n_sel = n_fill = n_any = 0
    exec_u: list[float] = []
    exec_d: list[float] = []
    cond_u: list[float] = []
    cond_d: list[float] = []
    ttf: list[float] = []
    daily = {d: {"coh": 0, "sel": 0, "fill": 0, "any": 0, "eu": [], "ed": []} for d in days}
    slots = float(SLOTS)
    for (date, _sess, _an), top in selected_by.items():
        if not top:
            continue
        n_fill_c = 0
        eu = ed = 0.0
        any_f = 0
        for r in top:
            w = wait_body(r, wid)
            if not w.get("WOULD_FILL"):
                continue
            n_fill_c += 1
            any_f = 1
            u = _f(w.get("POSTFILL_MFE_600"))
            d = _f(w.get("POSTFILL_DOWNSIDE_AVOID_600"))
            eu += float(u) if u is not None else 0.0
            ed += float(d) if d is not None else 0.0
            if u is not None:
                cond_u.append(float(u))
            if d is not None:
                cond_d.append(float(d))
            tt = _f(w.get("TIME_TO_FILL_SEC"))
            if tt is not None:
                ttf.append(float(tt))
        n_coh += 1
        n_sel += len(top)
        n_fill += n_fill_c
        n_any += any_f
        exec_u.append(eu / slots)
        exec_d.append(ed / slots)
        b = daily.get(str(date))
        if b is not None:
            b["coh"] += 1
            b["sel"] += len(top)
            b["fill"] += n_fill_c
            b["any"] += any_f
            b["eu"].append(eu / slots)
            b["ed"].append(ed / slots)
    day_rows = []
    for d in days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        day_rows.append(
            {
                "date": d,
                "SELECTED_FILL_RATE": _rate(int(b["fill"]), int(b["sel"])),
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
        "EXEC_U": float(np.mean(exec_u)) if exec_u else None,
        "EXEC_D": float(np.mean(exec_d)) if exec_d else None,
        "COND_U_MEAN": float(np.mean(cond_u)) if cond_u else None,
        "COND_D_MEAN": float(np.mean(cond_d)) if cond_d else None,
        "COND_N": len(cond_u),
        "TIME_TO_FILL_MEDIAN": _median(ttf),
        "TIME_TO_FILL_P90": _pct(ttf, 0.90),
        "daily": day_rows,
    }


def _cause_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "FILL": 0,
        "NO_VALID_BOARD_WITHIN_5": 0,
        "VALID_BOARD_BUT_NO_ASK_CROSS_WITHIN_5": 0,
        "BECAME_NONEXECUTABLE": 0,
        "OTHER": 0,
        "NONFILL_N": 0,
    }
    for r in rows:
        c = w5_nonfill_cause(r)
        if c == "FILL":
            out["FILL"] += 1
            continue
        out["NONFILL_N"] += 1
        out[c] = int(out.get(c) or 0) + 1
    return out


def _pfill_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [float(v) for r in rows if (v := _f(r.get("fill_score"))) is not None]
    return {"N": len(rows), "P_FILL_N": len(xs), "P_FILL_MEAN": _mean(xs), "P_FILL_MEDIAN": _median(xs)}


def evaluate_rep(
    fo_slim: list[dict[str, Any]],
    du_slim: list[dict[str, Any]],
    days: list[str],
) -> dict[str, Any]:
    fo_by = selected_by_from_slim(fo_slim)
    du_by = selected_by_from_slim(du_slim)
    dist = _ts_distinct({"selected_by": fo_by}, {"selected_by": du_by})
    dist = {
        "DIRECT_EQ_FILL_ONLY_COHORT_N": dist.get("TWO_STAGE_EQ_FILL_ONLY_COHORT_N"),
        "DIRECT_NE_FILL_ONLY_COHORT_N": dist.get("TWO_STAGE_NE_FILL_ONLY_COHORT_N"),
        "DIRECT_NE_FILL_ONLY_COHORT_RATE": dist.get("TWO_STAGE_NE_FILL_ONLY_COHORT_RATE"),
        "SWAPPED_IN_N": dist.get("SWAPPED_IN_N"),
        "SWAPPED_OUT_N": dist.get("SWAPPED_OUT_N"),
    }

    common_rows: list[dict[str, Any]] = []
    fo_only: list[dict[str, Any]] = []
    du_only: list[dict[str, Any]] = []
    keys = sorted(set(fo_by) | set(du_by))
    for k in keys:
        fo = list(fo_by.get(k) or [])
        du = list(du_by.get(k) or [])
        fmap = {_sym(r): r for r in fo}
        dmap = {_sym(r): r for r in du}
        fs, ds = set(fmap), set(dmap)
        for s in fs & ds:
            common_rows.append(dmap[s])
        for s in fs - ds:
            fo_only.append(fmap[s])
        for s in ds - fs:
            du_only.append(dmap[s])

    waits = {}
    for wid in WAIT_IDS:
        fo_m = metrics_from_selected(fo_by, days, wid)
        du_m = metrics_from_selected(du_by, days, wid)
        waits[wid] = {
            "FILL_ONLY": fo_m,
            "DIRECT_EXEC_U": du_m,
            "DELTA_FILL": None
            if _f(du_m.get("SELECTED_FILL_RATE")) is None or _f(fo_m.get("SELECTED_FILL_RATE")) is None
            else float(du_m["SELECTED_FILL_RATE"]) - float(fo_m["SELECTED_FILL_RATE"]),
            "DELTA_EXEC_U": None
            if _f(du_m.get("EXEC_U")) is None or _f(fo_m.get("EXEC_U")) is None
            else float(du_m["EXEC_U"]) - float(fo_m["EXEC_U"]),
            "DELTA_EXEC_D": None
            if _f(du_m.get("EXEC_D")) is None or _f(fo_m.get("EXEC_D")) is None
            else float(du_m["EXEC_D"]) - float(fo_m["EXEC_D"]),
        }

    fo_all = fo_slim
    du_all = du_slim
    fo_cause = _cause_counts(fo_all)
    du_cause = _cause_counts(du_all)
    du_only_cause = _cause_counts(du_only)

    def _recover_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
        nf = [r for r in rows if not wait_body(r, "W5").get("WOULD_FILL")]
        rec = [r for r in nf if late_recoverable(r)]
        never = [r for r in nf if w10_nonfill_bucket(r) == "NEVER_CROSS"]
        noboard = [r for r in nf if w10_nonfill_bucket(r) == "NO_VALID_BOARD"]
        other = [r for r in nf if w10_nonfill_bucket(r) not in {"FILL", "NEVER_CROSS", "NO_VALID_BOARD"}]
        return {
            "W5_NONFILL_N": len(nf),
            "W10_RECOVER_N": len(rec),
            "RECOVER_RATE": _rate(len(rec), len(nf)),
            "NEVER_CROSS_10S_N": len(never),
            "NEVER_CROSS_10S_RATE": _rate(len(never), len(nf)),
            "NO_VALID_BOARD_10S_N": len(noboard),
            "NO_VALID_BOARD_10S_RATE": _rate(len(noboard), len(nf)),
            "OTHER_10S_N": len(other),
        }

    fo_rec = _recover_pack(fo_all)
    du_rec = _recover_pack(du_all)
    fo_only_rec = _recover_pack(fo_only)
    du_only_rec = _recover_pack(du_only)

    recovered = [r for r in du_all if late_recoverable(r)]
    rec_u = [_f(wait_body(r, "W10").get("POSTFILL_MFE_600")) for r in recovered]
    rec_d = [_f(wait_body(r, "W10").get("POSTFILL_DOWNSIDE_AVOID_600")) for r in recovered]
    rec_t = [_f(wait_body(r, "W10").get("TIME_TO_FILL_SEC")) for r in recovered]
    rec_u_f = [float(v) for v in rec_u if v is not None]
    rec_d_f = [float(v) for v in rec_d if v is not None]
    rec_t_f = [float(v) for v in rec_t if v is not None]

    du_only_nf = [r for r in du_only if not wait_body(r, "W5").get("WOULD_FILL")]
    fill_loss = {
        "FILL_LOSS_LATE_RECOVERABLE_N": sum(1 for r in du_only_nf if late_recoverable(r)),
        "FILL_LOSS_NEVER_CROSS_N": sum(1 for r in du_only_nf if w10_nonfill_bucket(r) == "NEVER_CROSS"),
        "FILL_LOSS_NO_VALID_BOARD_N": sum(1 for r in du_only_nf if w10_nonfill_bucket(r) == "NO_VALID_BOARD"),
        "FILL_LOSS_OTHER_N": sum(
            1 for r in du_only_nf if w10_nonfill_bucket(r) not in {"FILL", "NEVER_CROSS", "NO_VALID_BOARD"}
        ),
    }

    du_fills = [r for r in du_all if wait_body(r, "W5").get("WOULD_FILL")]
    ttf_d = []
    u_d = []
    d_d = []
    for r in du_fills:
        t = _f(wait_body(r, "W5").get("TIME_TO_FILL_SEC"))
        u = _f(wait_body(r, "W5").get("POSTFILL_MFE_600"))
        dv = _f(wait_body(r, "W5").get("POSTFILL_DOWNSIDE_AVOID_600"))
        if t is None:
            continue
        ttf_d.append(float(t))
        if u is not None:
            u_d.append(float(u))
        else:
            u_d.append(float("nan"))
        if dv is not None:
            d_d.append(float(dv))
        else:
            d_d.append(float("nan"))
    ttf_u_pairs = [(t, u) for t, u in zip(ttf_d, u_d) if u == u]
    ttf_d_pairs = [(t, d) for t, d in zip(ttf_d, d_d) if d == d]
    sp_u = spearman([a for a, _ in ttf_u_pairs], [b for _, b in ttf_u_pairs]) if ttf_u_pairs else None
    sp_d = spearman([a for a, _ in ttf_d_pairs], [b for _, b in ttf_d_pairs]) if ttf_d_pairs else None

    fo_only_fills = [r for r in fo_only if wait_body(r, "W5").get("WOULD_FILL")]
    du_only_fills = [r for r in du_only if wait_body(r, "W5").get("WOULD_FILL")]
    fo_only_cu = [_f(wait_body(r, "W5").get("POSTFILL_MFE_600")) for r in fo_only_fills]
    du_only_cu = [_f(wait_body(r, "W5").get("POSTFILL_MFE_600")) for r in du_only_fills]
    fo_only_cd = [_f(wait_body(r, "W5").get("POSTFILL_DOWNSIDE_AVOID_600")) for r in fo_only_fills]
    du_only_cd = [_f(wait_body(r, "W5").get("POSTFILL_DOWNSIDE_AVOID_600")) for r in du_only_fills]

    latency = {
        "FIRST_ASK_CROSS_N": sum(1 for r in du_all if first_ask_cross_sec(r) is not None),
        "FIRST_ASK_CROSS_MEDIAN": _median([float(v) for r in du_all if (v := first_ask_cross_sec(r)) is not None]),
        "HAD_VALID_BOARD_W5_N": sum(1 for r in du_all if had_valid_board(r, "W5")),
        "HAD_VALID_BOARD_W10_N": sum(1 for r in du_all if had_valid_board(r, "W10")),
        "FIRST_VALID_BOARD_SEC_MEDIAN": _median(
            [float(v) for r in du_all if (v := first_valid_board_sec(r)) is not None]
        ),
    }

    daily_rows = []
    fo5 = {r["date"]: r for r in (waits["W5"]["FILL_ONLY"].get("daily") or [])}
    du5 = {r["date"]: r for r in (waits["W5"]["DIRECT_EXEC_U"].get("daily") or [])}
    fo10 = {r["date"]: r for r in (waits["W10"]["FILL_ONLY"].get("daily") or [])}
    du10 = {r["date"]: r for r in (waits["W10"]["DIRECT_EXEC_U"].get("daily") or [])}
    for d in days:
        a5, b5 = fo5.get(d) or {}, du5.get(d) or {}
        a10, b10 = fo10.get(d) or {}, du10.get(d) or {}
        rec = {"date": d}
        for name, left, right, key in (
            ("FILL_DELTA_W5", b5, a5, "SELECTED_FILL_RATE"),
            ("FILL_DELTA_W10", b10, a10, "SELECTED_FILL_RATE"),
            ("EXEC_U_DELTA_W5", b5, a5, "EXEC_U"),
            ("EXEC_U_DELTA_W10", b10, a10, "EXEC_U"),
            ("EXEC_D_DELTA_W5", b5, a5, "EXEC_D"),
            ("EXEC_D_DELTA_W10", b10, a10, "EXEC_D"),
        ):
            lv, rv = _f(left.get(key)), _f(right.get(key))
            rec[name] = None if lv is None or rv is None else float(lv) - float(rv)
        d5, d10 = _f(b5.get("SELECTED_FILL_RATE")), _f(b10.get("SELECTED_FILL_RATE"))
        rec["DIRECT_FILL_RECOVERY_W5_W10"] = None if d5 is None or d10 is None else float(d10) - float(d5)
        daily_rows.append(rec)

    return {
        "COMMON_N": len(common_rows),
        "FILL_ONLY_ONLY_N": len(fo_only),
        "DIRECT_ONLY_N": len(du_only),
        "distinctness": dist,
        "pfill": {
            "FILL_ONLY_ONLY": _pfill_pack(fo_only),
            "DIRECT_ONLY": _pfill_pack(du_only),
            "DELTA_P_FILL_MEAN": None
            if _pfill_pack(fo_only).get("P_FILL_MEAN") is None or _pfill_pack(du_only).get("P_FILL_MEAN") is None
            else float(_pfill_pack(du_only)["P_FILL_MEAN"]) - float(_pfill_pack(fo_only)["P_FILL_MEAN"]),
            "DELTA_P_FILL_MEDIAN": None
            if _pfill_pack(fo_only).get("P_FILL_MEDIAN") is None or _pfill_pack(du_only).get("P_FILL_MEDIAN") is None
            else float(_pfill_pack(du_only)["P_FILL_MEDIAN"]) - float(_pfill_pack(fo_only)["P_FILL_MEDIAN"]),
        },
        "w5_cause": {
            "FILL_ONLY": fo_cause,
            "DIRECT_EXEC_U": du_cause,
            "DIRECT_ONLY": du_only_cause,
        },
        "recover": {
            "FILL_ONLY": fo_rec,
            "DIRECT_EXEC_U": du_rec,
            "FILL_ONLY_ONLY": fo_only_rec,
            "DIRECT_ONLY": du_only_rec,
        },
        "recovered": {
            "DIRECT_RECOVERED_N": len(recovered),
            "RECOVERED_COND_U": _mean(rec_u_f),
            "RECOVERED_COND_D": _mean(rec_d_f),
            "RECOVERED_TIME_TO_FILL_MEDIAN": _median(rec_t_f),
            "RECOVERED_TIME_TO_FILL_P90": _pct(rec_t_f, 0.90),
        },
        "fill_loss": fill_loss,
        "coupling": {
            "DIRECT_TTF_VS_U_SPEARMAN": sp_u,
            "DIRECT_TTF_VS_D_SPEARMAN": sp_d,
            "FILL_ONLY_ONLY_COND_U": _mean([float(v) for v in fo_only_cu if v is not None]),
            "DIRECT_ONLY_COND_U": _mean([float(v) for v in du_only_cu if v is not None]),
            "FILL_ONLY_ONLY_COND_D": _mean([float(v) for v in fo_only_cd if v is not None]),
            "DIRECT_ONLY_COND_D": _mean([float(v) for v in du_only_cd if v is not None]),
        },
        "latency": latency,
        "waits": waits,
        "daily": daily_rows,
        "Y_W5_MISMATCH_N": y_vs_w5_mismatch(fo_slim) + y_vs_w5_mismatch(du_slim),
        "WAIT_JOIN_MISS_CHECKED": True,
    }
