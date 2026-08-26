"""Separability diagnostics. No threshold / no counterfactual PnL / no EXIT."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_fixed_pnl_source_p3_3.metrics import dist
from research.mid_hold_state_separability_p4_0 import (
    CHECKPOINTS_SEC,
    GATE_CONF,
    GATE_NOT,
    GATE_SEP,
    GATE_WEAK,
    PREDECLARED_TOP3,
    REST11,
    STATE_VARS,
)


def _finite(xs: list[Any]) -> np.ndarray:
    out = []
    for x in xs:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v == v and v not in (float("inf"), float("-inf")):
            out.append(v)
    return np.asarray(out, dtype=float)


def _avg_rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    i = 0
    while i < a.size:
        j = i + 1
        while j < a.size and a[order[j]] == a[order[i]]:
            j += 1
        avg = 0.5 * (i + 1 + j)
        ranks[order[i:j]] = avg
        i = j
    return ranks


def auc_score(scores: list[Any], y: list[Any]) -> Optional[float]:
    pairs = []
    for s, t in zip(scores, y):
        try:
            fs = float(s)
            ft = int(t)
        except (TypeError, ValueError):
            continue
        if fs != fs or fs in (float("inf"), float("-inf")):
            continue
        if ft not in (0, 1):
            continue
        pairs.append((fs, ft))
    if len(pairs) < 8:
        return None
    arr = np.asarray(pairs, dtype=float)
    s = arr[:, 0]
    yy = arr[:, 1].astype(int)
    n1 = int(np.sum(yy == 1))
    n0 = int(np.sum(yy == 0))
    if n1 < 3 or n0 < 3:
        return None
    ranks = _avg_rank(s)
    sum_pos = float(np.sum(ranks[yy == 1]))
    auc = (sum_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)
    if auc != auc:
        return None
    return float(auc)


def eligible(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if int(r.get("horizon_sec") or 0) == int(horizon) and r.get("eligible") is True
    ]


def slice_days(rows: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date")) in set(days)]


def group_dist(rows: list[dict[str, Any]], var: str, pred) -> dict[str, Any]:
    sel = [r for r in rows if pred(r)]
    return {"n": len(sel), **dist([r.get(var) for r in sel])}


def separation_block(rows: list[dict[str, Any]], var: str) -> dict[str, Any]:
    win = group_dist(rows, var, lambda r: r.get("CANONICAL_FINAL_WIN"))
    loss = group_dist(rows, var, lambda r: r.get("CANONICAL_FINAL_LOSS"))
    fail = group_dist(
        rows,
        var,
        lambda r: r.get("CANONICAL_FINAL_LOSS") or r.get("EARLY_FAILURE_BEFORE_600"),
    )
    ext = group_dist(rows, var, lambda r: r.get("REACHED_600_EXTEND"))
    ex6 = group_dist(rows, var, lambda r: r.get("REACHED_600_EXIT"))
    mw, ml = win.get("median"), loss.get("median")
    direction = None
    if mw is not None and ml is not None:
        if float(mw) > float(ml):
            direction = "win_median_gt_loss"
        elif float(mw) < float(ml):
            direction = "win_median_lt_loss"
        else:
            direction = "equal"
    return {
        "WIN": win,
        "LOSS": loss,
        "FAILURE": fail,
        "EXTEND": ext,
        "EXIT600": ex6,
        "direction_win_vs_loss": direction,
    }


def _y_fail_win(r: dict[str, Any]) -> Optional[int]:
    if r.get("CANONICAL_FINAL_WIN"):
        return 0
    if r.get("CANONICAL_FINAL_LOSS") or r.get("EARLY_FAILURE_BEFORE_600"):
        return 1
    return None


def _y_exit_ext(r: dict[str, Any]) -> Optional[int]:
    if r.get("REACHED_600_EXTEND"):
        return 1
    if r.get("REACHED_600_EXIT"):
        return 0
    return None


def auc_block(rows: list[dict[str, Any]], var: str) -> dict[str, Any]:
    y1, s1 = [], []
    y2, s2 = [], []
    for r in rows:
        v = r.get(var)
        a = _y_fail_win(r)
        if a is not None:
            y1.append(a)
            s1.append(v)
        b = _y_exit_ext(r)
        if b is not None:
            y2.append(b)
            s2.append(v)
    a1 = auc_score(s1, y1)
    a2 = auc_score(s2, y2)
    return {
        "fail_vs_win": {
            "n": len(y1),
            "auc": a1,
            "auc_best": None if a1 is None else float(max(a1, 1.0 - a1)),
            "note": "target=1 failure/loss; raw AUC. auc_best = max(auc,1-auc) information magnitude only.",
        },
        "extend_vs_exit600": {
            "n": len(y2),
            "auc": a2,
            "auc_best": None if a2 is None else float(max(a2, 1.0 - a2)),
            "note": "target=1 EXTEND; raw AUC. Not a threshold.",
        },
    }


def quintiles(rows: list[dict[str, Any]], var: str) -> dict[str, Any]:
    vals = []
    for r in rows:
        try:
            v = float(r.get(var))
        except (TypeError, ValueError):
            continue
        if v != v or v in (float("inf"), float("-inf")):
            continue
        vals.append((v, r))
    if len(vals) < 10:
        return {"n": len(vals), "bins": [], "monotonic_loss": None, "monotonic_win": None, "monotonic_extend": None}
    xs = np.asarray([v for v, _ in vals], dtype=float)
    edges = np.unique(np.percentile(xs, [0, 20, 40, 60, 80, 100]))
    if edges.size < 3:
        return {"n": len(vals), "bins": [], "monotonic_loss": None, "monotonic_win": None, "monotonic_extend": None}
    bins = []
    loss_rates = []
    win_rates = []
    ext_rates = []
    for i in range(edges.size - 1):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i == edges.size - 2:
            sel = [r for v, r in vals if lo - 1e-15 <= v <= hi + 1e-15]
        else:
            sel = [r for v, r in vals if lo - 1e-15 <= v < hi]
        n = len(sel)
        n_loss = sum(1 for r in sel if r.get("CANONICAL_FINAL_LOSS") or r.get("EARLY_FAILURE_BEFORE_600"))
        n_win = sum(1 for r in sel if r.get("CANONICAL_FINAL_WIN"))
        n_ext = sum(1 for r in sel if r.get("REACHED_600_EXTEND"))
        lr = n_loss / n if n else None
        wr = n_win / n if n else None
        er = n_ext / n if n else None
        loss_rates.append(lr)
        win_rates.append(wr)
        ext_rates.append(er)
        bins.append(
            {
                "q": i + 1,
                "lo": lo,
                "hi": hi,
                "n": n,
                "loss_rate": lr,
                "win_rate": wr,
                "extend_rate": er,
            }
        )

    def _mono(rates: list) -> Optional[str]:
        xs = [x for x in rates if x is not None]
        if len(xs) < 3:
            return None
        up = all(xs[i] <= xs[i + 1] + 1e-12 for i in range(len(xs) - 1))
        down = all(xs[i] >= xs[i + 1] - 1e-12 for i in range(len(xs) - 1))
        if up and down:
            return "flat"
        if up:
            return "nondecreasing"
        if down:
            return "nonincreasing"
        # weak: first vs last
        if xs[-1] > xs[0] + 0.05:
            return "generally_up"
        if xs[-1] < xs[0] - 0.05:
            return "generally_down"
        return "mixed"

    return {
        "n": len(vals),
        "bins": bins,
        "monotonic_loss": _mono(loss_rates),
        "monotonic_win": _mono(win_rates),
        "monotonic_extend": _mono(ext_rates),
        "note": "Quintile edges are descriptive. Not an EXIT rule.",
    }


def preservation(rows: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    sel = [r for r in rows if r.get(flag)]
    n = len(sel)
    if n == 0:
        return {"n": 0}
    uw = sum(1 for r in sel if r.get("bid_return_from_fill") is not None and float(r["bid_return_from_fill"]) < 0)
    l60 = sum(1 for r in sel if r.get("bid_return_last_60s") is not None and float(r["bid_return_last_60s"]) < 0)
    imb = sum(1 for r in sel if r.get("imbalance") is not None and float(r["imbalance"]) < 0)
    gb = sum(1 for r in sel if r.get("bid_giveback_from_peak") is not None and float(r["bid_giveback_from_peak"]) < -1e-12)
    p5 = sum(1 for r in sel if r.get("imb_p5_persist") is True)
    return {
        "n": n,
        "underwater_bid_return_lt0": uw / n,
        "last60_lt0": l60 / n,
        "imbalance_lt0": imb / n,
        "giveback_below_peak": gb / n,
        "imb_p5_persist": p5 / n,
        "adverse_state_frequency": uw / n,
        "note": "adverse = bid_return_from_fill < 0 (sign, not a searched cutoff).",
    }


def recovery(rows: list[dict[str, Any]]) -> dict[str, Any]:
    adv = [
        r
        for r in rows
        if r.get("bid_return_from_fill") is not None and float(r["bid_return_from_fill"]) < 0
    ]
    n = len(adv)
    to_win = sum(1 for r in adv if r.get("CANONICAL_FINAL_WIN"))
    to_ext = sum(1 for r in adv if r.get("REACHED_600_EXTEND"))
    to_fail = sum(1 for r in adv if r.get("CANONICAL_FINAL_LOSS") or r.get("EARLY_FAILURE_BEFORE_600"))
    return {
        "adverse_n": n,
        "adverse_definition": "bid_return_from_fill < 0 at checkpoint (sign only)",
        "RECOVERED_TO_WIN": to_win,
        "RECOVERED_TO_EXTEND": to_ext,
        "FAILED": to_fail,
        "recovered_to_win_rate": (to_win / n) if n else None,
        "recovered_to_extend_rate": (to_ext / n) if n else None,
        "failed_rate": (to_fail / n) if n else None,
    }


def time_consistency(by_h: dict[int, dict[str, Any]], var: str) -> dict[str, Any]:
    dirs = []
    for h in CHECKPOINTS_SEC:
        d = ((by_h.get(h) or {}).get(var) or {}).get("direction_win_vs_loss")
        dirs.append({"horizon_sec": h, "direction": d})
    nonempty = [x["direction"] for x in dirs if x["direction"] in ("win_median_gt_loss", "win_median_lt_loss")]
    if not nonempty:
        return {"rows": dirs, "majority": None, "n_agree_majority": 0, "n_scored": 0}
    maj = max(set(nonempty), key=nonempty.count)
    n_ag = sum(1 for x in nonempty if x == maj)
    return {
        "rows": dirs,
        "majority": maj,
        "n_agree_majority": n_ag,
        "n_scored": len(nonempty),
        "consistent_multiple_checkpoints": n_ag >= 5 and len(nonempty) >= 6,
    }


def classify_gate(
    *,
    rest_consist: dict[str, dict[str, Any]],
    auc_rest: dict[int, dict[str, dict[str, Any]]],
    pres_top10: dict[int, dict[str, Any]],
    recov: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    br = rest_consist.get("bid_return_from_fill") or {}
    n_ag = int(br.get("n_agree_majority") or 0)
    n_sc = int(br.get("n_scored") or 0)
    maj = br.get("majority")
    aucs = []
    for h in CHECKPOINTS_SEC:
        a = (((auc_rest.get(h) or {}).get("bid_return_from_fill") or {}).get("fail_vs_win") or {}).get("auc_best")
        if a is not None:
            aucs.append(float(a))
    n_auc_ok = sum(1 for a in aucs if a >= 0.62)
    n_auc_weak = sum(1 for a in aucs if a >= 0.56)
    uw = [float(pres_top10[h]["adverse_state_frequency"]) for h in CHECKPOINTS_SEC if h in pres_top10 and pres_top10[h].get("n")]
    rec_win = [
        float(recov[h]["recovered_to_win_rate"])
        for h in CHECKPOINTS_SEC
        if h in recov and recov[h].get("recovered_to_win_rate") is not None and int(recov[h].get("adverse_n") or 0) >= 8
    ]
    mean_uw = float(np.mean(uw)) if uw else None
    mean_rec = float(np.mean(rec_win)) if rec_win else None
    confounded = (
        mean_uw is not None
        and mean_uw >= 0.25
        and mean_rec is not None
        and mean_rec >= 0.25
        and n_sc >= 5
    )
    rest_dir_ok = maj == "win_median_gt_loss" and n_ag >= 5
    if confounded:
        label = GATE_CONF
        why = (
            "REST11 may show return/path direction, but TOP10/underwater trades often recover to wins; "
            "a mid-hold cut would risk cutting recovering winners"
        )
    elif rest_dir_ok and n_auc_ok >= 4 and (mean_uw is None or mean_uw < 0.25):
        label = GATE_SEP
        why = "REST11 win vs loss median direction is consistent across checkpoints and AUC shows information without heavy TOP10 overlap"
    elif (rest_dir_ok or n_ag >= 4) and n_auc_weak >= 3:
        label = GATE_WEAK
        why = "some REST11 / multi-checkpoint direction and modest AUC, but not strong enough for a frozen gate"
    else:
        label = GATE_NOT
        why = "mid-hold states do not consistently separate future failures from winners on REST11"
    families = []
    if label in (GATE_SEP, GATE_WEAK):
        families.append("PRICE_PATH_DETERIORATION")
        imb_ag = int((rest_consist.get("imbalance") or {}).get("n_agree_majority") or 0)
        imb_maj = (rest_consist.get("imbalance") or {}).get("majority")
        if imb_ag >= 4 and imb_maj == "win_median_gt_loss":
            families.append("PRICE_PLUS_BOARD_DETERIORATION")
        families = families[:2]
    return {
        "MID_HOLD_GATEABILITY": label,
        "why": why,
        "REST11_bid_return_majority": maj,
        "REST11_bid_return_n_agree": n_ag,
        "REST11_bid_return_n_scored": n_sc,
        "n_checkpoints_auc_best_ge_062": n_auc_ok,
        "n_checkpoints_auc_best_ge_056": n_auc_weak,
        "top10_mean_underwater_rate": mean_uw,
        "adverse_mean_recovered_to_win_rate": mean_rec,
        "CANDIDATE_STATE_FAMILIES": families,
        "note": "No threshold selected. Families are candidates for P4-1 precommit only if SEPARABLE/WEAKLY.",
    }
