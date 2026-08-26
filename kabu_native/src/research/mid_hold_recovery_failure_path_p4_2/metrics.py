"""P4-2 descriptive path comparison. No p-value rule selection / no Gate."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_fixed_pnl_source_p3_3.metrics import dist
from research.mid_hold_recovery_failure_path_p4_2 import (
    CHECKPOINTS_SEC,
    FULL14,
    MECH_DRAWDOWN,
    MECH_MFE,
    MECH_MULTI,
    MECH_NOT,
    MECH_RECOVERY,
    MECH_SNAPSHOT,
    MECH_TOP3,
    PRIMARY_CHECKPOINTS,
    REST11,
    SNAPSHOT_VARS,
    TRAJECTORY_VARS,
)
from research.mid_hold_state_separability_p4_0.metrics import auc_score


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def cliffs_delta(a: list[Any], b: list[Any]) -> Optional[float]:
    xa = [_f(x) for x in a]
    xb = [_f(x) for x in b]
    xa = [x for x in xa if x is not None]
    xb = [x for x in xb if x is not None]
    if len(xa) < 3 or len(xb) < 3:
        return None
    gt = lt = 0
    for x in xa:
        for y in xb:
            if x > y:
                gt += 1
            elif x < y:
                lt += 1
    den = len(xa) * len(xb)
    if den <= 0:
        return None
    return float(gt - lt) / float(den)


def at_horizon(trades: list[dict[str, Any]], h: int, pred) -> list[dict[str, Any]]:
    out = []
    for t in trades:
        if not pred(t):
            continue
        rec = (t.get("by_horizon") or {}).get(int(h)) or {}
        if rec.get("eligible") is True:
            out.append(rec)
    return out


def rw(t: dict[str, Any]) -> bool:
    return bool(t.get("RECOVERING_WINNER"))


def pf(t: dict[str, Any]) -> bool:
    return bool(t.get("PERSISTENT_FAILURE"))


def slice_days(trades: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [t for t in trades if str(t.get("date")) in want]


def compare_var(trades: list[dict[str, Any]], h: int, var: str) -> dict[str, Any]:
    a = at_horizon(trades, h, rw)
    b = at_horizon(trades, h, pf)
    va = [r.get(var) for r in a]
    vb = [r.get(var) for r in b]
    da, db = dist(va), dist(vb)
    y = [0] * len([x for x in va if _f(x) is not None]) + [1] * len([x for x in vb if _f(x) is not None])
    s = [x for x in va if _f(x) is not None] + [x for x in vb if _f(x) is not None]
    auc = auc_score(s, y) if y else None
    auc_best = None if auc is None else float(max(auc, 1.0 - auc))
    mw, ml = da.get("median"), db.get("median")
    direction = None
    if mw is not None and ml is not None:
        if float(mw) > float(ml):
            direction = "rw_median_gt_pf"
        elif float(mw) < float(ml):
            direction = "rw_median_lt_pf"
        else:
            direction = "equal"
    return {
        "horizon_sec": h,
        "var": var,
        "RECOVERING_WINNER": da,
        "PERSISTENT_FAILURE": db,
        "direction": direction,
        "auc_fail_as_pos": auc,
        "auc_best": auc_best,
        "cliffs_delta_rw_minus_pf": cliffs_delta(va, vb),
        "note": "auc target 1=PERSISTENT_FAILURE. auc_best is information magnitude only. Not a cutoff.",
    }


def shape_block(trades: list[dict[str, Any]], pred, h_end: int) -> dict[str, Any]:
    n = bid_up = new_mfe = new_low = reb_up = 0
    for t in trades:
        if not pred(t):
            continue
        r0 = (t.get("by_horizon") or {}).get(120) or {}
        r1 = (t.get("by_horizon") or {}).get(int(h_end)) or {}
        if r0.get("eligible") is not True or r1.get("eligible") is not True:
            continue
        n += 1
        b0, b1 = _f(r0.get("bid_return_from_fill")), _f(r1.get("bid_return_from_fill"))
        if b0 is not None and b1 is not None and b1 > b0 + 1e-12:
            bid_up += 1
        m0, m1 = _f(r0.get("executable_mfe_to_t")), _f(r1.get("executable_mfe_to_t"))
        if m0 is not None and m1 is not None and m1 > m0 + 1e-12:
            new_mfe += 1
        a0, a1 = _f(r0.get("executable_mae_to_t")), _f(r1.get("executable_mae_to_t"))
        if a0 is not None and a1 is not None and a1 < a0 - 1e-12:
            new_low += 1
        q0, q1 = _f(r0.get("rebound_from_low_t")), _f(r1.get("rebound_from_low_t"))
        if q0 is not None and q1 is not None and q1 > q0 + 1e-12:
            reb_up += 1
    def _rt(k):
        return (k / n) if n else None
    return {
        "n": n,
        "bid_improved_n": bid_up,
        "bid_improved_rate": _rt(bid_up),
        "new_mfe_n": new_mfe,
        "new_mfe_rate": _rt(new_mfe),
        "new_low_n": new_low,
        "new_low_rate": _rt(new_low),
        "rebound_from_low_n": reb_up,
        "rebound_from_low_rate": _rt(reb_up),
        "interval": f"120→{h_end}",
        "note": "Structural comparisons only. Rates are not EXIT conditions.",
    }


def persistence_block(trades: list[dict[str, Any]], h: int) -> dict[str, Any]:
    def _counts(pred):
        recs = at_horizon(trades, h, pred)
        return dist([r.get("consecutive_underwater_count") for r in recs])

    def _still_uw(pred):
        recs = at_horizon(trades, h, pred)
        n = len(recs)
        k = sum(1 for r in recs if _f(r.get("bid_return_from_fill")) is not None and float(r["bid_return_from_fill"]) < 0)
        return {"n": n, "still_underwater_n": k, "still_underwater_rate": (k / n) if n else None}

    return {
        "horizon_sec": h,
        "RECOVERING_WINNER_consec": _counts(rw),
        "PERSISTENT_FAILURE_consec": _counts(pf),
        "RECOVERING_WINNER_still_uw": _still_uw(rw),
        "PERSISTENT_FAILURE_still_uw": _still_uw(pf),
        "note": "Persistence counts are descriptive. Not an N-in-a-row EXIT.",
    }


def interval_flags(trades: list[dict[str, Any]], h: int, pred) -> dict[str, Any]:
    recs = at_horizon(trades, h, pred)
    n = len(recs)
    nl = sum(1 for r in recs if r.get("new_low_created") is True)
    nm = sum(1 for r in recs if r.get("new_mfe_created") is True)
    return {
        "n": n,
        "new_low_n": nl,
        "new_low_rate": (nl / n) if n else None,
        "new_mfe_n": nm,
        "new_mfe_rate": (nm / n) if n else None,
    }


def earliest_divergence(comp: dict[int, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """First primary checkpoint where RW vs PF median direction is set and stays. Descriptive."""
    out = {}
    for var in ("bid_return_from_fill", "delta_bid_120_to_t", "rebound_from_low_t", "executable_mae_to_t", "delta_mae_prev_checkpoint", "executable_mfe_to_t", "delta_mfe_prev_checkpoint"):
        rows = []
        for h in PRIMARY_CHECKPOINTS:
            d = ((comp.get(h) or {}).get(var) or {}).get("direction")
            rows.append({"horizon_sec": h, "direction": d})
        first = None
        for row in rows:
            if row["direction"] in ("rw_median_gt_pf", "rw_median_lt_pf"):
                first = row["horizon_sec"]
                break
        out[var] = {"first_nonzero_median_direction": first, "path": rows, "note": "Not an optimal exit time."}
    return out


def day_stability(trades: list[dict[str, Any]], h: int, var: str) -> dict[str, Any]:
    same = opp = insuff = 0
    rows = []
    for day in FULL14:
        sl = slice_days(trades, [day])
        n_rw = len(at_horizon(sl, h, rw))
        n_pf = len(at_horizon(sl, h, pf))
        if n_rw < 1 or n_pf < 1:
            insuff += 1
            rows.append({"date": day, "n_rw": n_rw, "n_pf": n_pf, "direction": "insufficient"})
            continue
        a = dist([r.get(var) for r in at_horizon(sl, h, rw)])
        b = dist([r.get(var) for r in at_horizon(sl, h, pf)])
        mw, ml = a.get("median"), b.get("median")
        if mw is None or ml is None or float(mw) == float(ml):
            insuff += 1
            rows.append({"date": day, "n_rw": n_rw, "n_pf": n_pf, "direction": "equal_or_empty"})
            continue
        d = "rw_median_gt_pf" if float(mw) > float(ml) else "rw_median_lt_pf"
        all_d = compare_var(trades, h, var).get("direction")
        if d == all_d:
            same += 1
        else:
            opp += 1
        rows.append({"date": day, "n_rw": n_rw, "n_pf": n_pf, "direction": d, "median_rw": mw, "median_pf": ml})
    return {
        "horizon_sec": h,
        "var": var,
        "same_direction_days": same,
        "opposite_direction_days": opp,
        "insufficient_days": insuff,
        "rows": rows,
    }


def lodo_auc(trades: list[dict[str, Any]], h: int, var: str) -> dict[str, Any]:
    aucs = []
    for leave in FULL14:
        sl = [t for t in trades if str(t.get("date")) != str(leave)]
        blk = compare_var(sl, h, var)
        if blk.get("auc_best") is not None:
            aucs.append({"leave_date": leave, "auc_best": blk["auc_best"]})
    xs = [x["auc_best"] for x in aucs]
    return {
        "horizon_sec": h,
        "var": var,
        "n_days_scored": len(xs),
        "median": float(np.median(xs)) if xs else None,
        "min": float(np.min(xs)) if xs else None,
        "max": float(np.max(xs)) if xs else None,
        "note": "Descriptive LODO AUC magnitude. No model, no threshold.",
        "rows": aucs,
    }


def casebook_trade(t: dict[str, Any]) -> dict[str, Any]:
    path = []
    for h in CHECKPOINTS_SEC:
        r = (t.get("by_horizon") or {}).get(h) or {}
        path.append(
            {
                "horizon_sec": h,
                "eligible": r.get("eligible"),
                "bid_return": r.get("bid_return_from_fill"),
                "mfe": r.get("executable_mfe_to_t"),
                "mae": r.get("executable_mae_to_t"),
                "giveback": r.get("bid_giveback_from_peak"),
                "rebound": r.get("rebound_from_low_t"),
                "delta_bid_120": r.get("delta_bid_120_to_t"),
                "new_low": r.get("new_low_created"),
                "new_mfe": r.get("new_mfe_created"),
                "consec_uw": r.get("consecutive_underwater_count"),
            }
        )
    r120 = (t.get("by_horizon") or {}).get(120) or {}
    return {
        "trade_id": t.get("trade_id"),
        "date": t.get("date"),
        "symbol": t.get("symbol"),
        "FINAL_WIN": r120.get("FINAL_WIN"),
        "FINAL_LOSS": r120.get("FINAL_LOSS"),
        "FINAL_DRAW": r120.get("FINAL_DRAW"),
        "EXTEND_TO_750": r120.get("EXTEND_TO_750"),
        "EXIT_AT_600": r120.get("EXIT_AT_600"),
        "pnl_yen_100": r120.get("pnl_yen_100"),
        "TOP20": r120.get("TOP20_CANONICAL_WINNER"),
        "path": path,
    }


def classify_mechanism(
    *,
    all_comp: dict[int, dict[str, dict[str, Any]]],
    rest_comp: dict[int, dict[str, dict[str, Any]]],
    n_rw_rest: int,
    n_pf_rest: int,
    integrity: list[str],
) -> dict[str, Any]:
    if integrity:
        return {
            "MECHANISM_CLASSIFICATION": MECH_NOT if "FUTURE_LEAK" not in str(integrity) else "MATERIAL_DATA_ISSUE",
            "CANDIDATE_PATH_FAMILIES": [],
            "why": ";".join(integrity),
        }

    def _score(comp, var, hs, want_dir):
        n_ok = 0
        dirs = []
        for h in hs:
            blk = (comp.get(h) or {}).get(var) or {}
            d = blk.get("direction")
            a = blk.get("auc_best")
            dirs.append(d)
            if d == want_dir and a is not None and float(a) >= 0.62:
                n_ok += 1
            elif d == want_dir and a is None:
                n_ok += 0
        n_dir = sum(1 for d in dirs if d == want_dir)
        return n_ok, n_dir

    hs = (180, 240, 300, 360)
    rest_ok = n_rw_rest >= 3 and n_pf_rest >= 3
    families = []
    reasons = []

    rec_all_auc, rec_all_dir = _score(all_comp, "rebound_from_low_t", hs, "rw_median_gt_pf")
    rec_d_auc, rec_d_dir = _score(all_comp, "delta_bid_120_to_t", hs, "rw_median_gt_pf")
    mae_all_auc, mae_all_dir = _score(all_comp, "delta_mae_prev_checkpoint", hs, "rw_median_gt_pf")
    mfe_all_auc, mfe_all_dir = _score(all_comp, "delta_mfe_prev_checkpoint", hs, "rw_median_gt_pf")
    snap_all_auc, snap_all_dir = _score(all_comp, "bid_return_from_fill", hs, "rw_median_gt_pf")

    rec_r_auc, rec_r_dir = _score(rest_comp, "rebound_from_low_t", hs, "rw_median_gt_pf")
    rec_rd_auc, rec_rd_dir = _score(rest_comp, "delta_bid_120_to_t", hs, "rw_median_gt_pf")
    mae_r_auc, mae_r_dir = _score(rest_comp, "delta_mae_prev_checkpoint", hs, "rw_median_gt_pf")
    mfe_r_auc, mfe_r_dir = _score(rest_comp, "delta_mfe_prev_checkpoint", hs, "rw_median_gt_pf")
    snap_r_auc, snap_r_dir = _score(rest_comp, "bid_return_from_fill", hs, "rw_median_gt_pf")

    def _rest_family(all_auc, all_dir, rest_auc, rest_dir, name, min_dir=3):
        if all_dir >= min_dir and all_auc >= 2:
            if rest_ok and rest_dir >= min_dir and (rest_auc >= 1 or rest_dir >= 3):
                families.append(name)
                return True
            if rest_ok and rest_dir <= 1:
                reasons.append(f"{name}_TOP3_only")
                return False
            if not rest_ok and all_dir >= min_dir:
                reasons.append(f"{name}_REST11_n_too_small")
                return False
        return False

    got_rec = _rest_family(rec_all_auc + rec_d_auc, max(rec_all_dir, rec_d_dir), rec_r_auc + rec_rd_auc, max(rec_r_dir, rec_rd_dir), "RECOVERY_FROM_LOW")
    got_dd = _rest_family(mae_all_auc, mae_all_dir, mae_r_auc, mae_r_dir, "PERSISTENT_NEW_LOW")
    got_mfe = _rest_family(mfe_all_auc, mfe_all_dir, mfe_r_auc, mfe_r_dir, "MFE_STAGNATION")

    if rest_ok and snap_r_dir >= 3 and snap_all_dir >= 3 and not (got_rec or got_dd or got_mfe):
        if snap_r_auc >= 2 and rec_r_auc < 2 and mae_r_auc < 2 and mfe_r_auc < 2:
            label = MECH_SNAPSHOT
            why = "REST11 later snapshots still separate; trajectory increments do not add a second consistent family"
        else:
            label = MECH_NOT
            why = "no consistent REST11 trajectory family"
    elif len(families) >= 2:
        label = MECH_MULTI
        why = "multiple trajectory families consistent on ALL and REST11"
    elif families == ["RECOVERY_FROM_LOW"]:
        label = MECH_RECOVERY
        why = "rebound / delta_bid after 120 separates recovering winners on ALL and REST11"
    elif families == ["PERSISTENT_NEW_LOW"]:
        label = MECH_DRAWDOWN
        why = "MAE interval deterioration / new-low path separates on ALL and REST11"
    elif families == ["MFE_STAGNATION"]:
        label = MECH_MFE
        why = "MFE increment/stagnation separates on ALL and REST11"
    elif "TOP3_only" in ";".join(reasons) and snap_all_dir >= 3:
        label = MECH_TOP3
        why = ";".join(reasons) or "ALL pattern not REST11"
    elif snap_all_dir >= 3 and not rest_ok:
        label = MECH_SNAPSHOT
        why = "ALL snapshot separates; REST11 recovering-winner n too small for a trajectory family claim"
    else:
        label = MECH_NOT
        why = ";".join(reasons) or "recovering winners and persistent failures are not cleanly separable on trajectory after 120"

    return {
        "MECHANISM_CLASSIFICATION": label,
        "CANDIDATE_PATH_FAMILIES": families[:2],
        "why": why,
        "REST11_n_rw": n_rw_rest,
        "REST11_n_pf": n_pf_rest,
        "REST11_scored": rest_ok,
        "counts": {
            "rebound_ALL_auc_ge_062": rec_all_auc,
            "delta_bid_ALL_dir": rec_d_dir,
            "delta_mae_ALL_auc_ge_062": mae_all_auc,
            "delta_mfe_ALL_auc_ge_062": mfe_all_auc,
            "snapshot_bid_REST11_dir": snap_r_dir,
        },
        "note": "Classification is descriptive. Not a Gate. Max 2 families. No threshold.",
    }
