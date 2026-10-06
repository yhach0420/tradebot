"""Candidate freeze, C1 gates, offset/lead, shuffle, concentration. No retune."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit import DAY_SHUFFLE_N, DAY_SHUFFLE_SEED
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.phase2_discovery import EXPECTED_SHUFFLE_SHA256, OFFSETS_MIN
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, bar_index_for_available_t, grid_index
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS, _w_ols_fe, point_b_fx


def freeze_payload(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    payload = []
    for r in sorted(rows, key=lambda x: (str(x["target_scope"]), int(x["fx_lookback"]), int(x["response_horizon"]))):
        payload.append(
            {
                "target_scope": r["target_scope"],
                "fx_lookback": int(r["fx_lookback"]),
                "response_horizon": int(r["response_horizon"]),
                "direction": r["direction"],
                "DEV_b_fx": float(r["b_fx"]),
                "DEV_CI": [float(r["ci_lo"]), float(r["ci_hi"])],
                "DEV_q_value": float(r["q"]),
                "D1": bool(r["D1"]),
                "D2": bool(r["D2"]),
                "D3": bool(r["D3"]),
                "D4": bool(r["D4"]),
                "D5": bool(r["D5"]),
                "D6": bool(r["D6"]),
                "DEV_quintile_boundaries": r["quintile_boundaries"],
            }
        )
    return payload, sha256_obj(payload)


def c1_gates(*, rec: dict[str, Any], fold_recs: dict[str, dict[str, Any]], q51: float | None, lomo_frac: float | None, dev_b: float) -> dict[str, Any]:
    b = rec.get("b_fx")
    c1 = bool(rec.get("ok") and b is not None and np.sign(b) == np.sign(dev_b) and np.sign(dev_b) != 0)
    c2 = bool(rec.get("ok") and rec.get("ci_excludes_0"))
    signs = []
    c4_fail = False
    for name, fr in fold_recs.items():
        if not fr.get("ok"):
            signs.append(0)
            continue
        signs.append(int(np.sign(fr["b_fx"])))
        if np.sign(fr["b_fx"]) != 0 and np.sign(fr["b_fx"]) != np.sign(dev_b) and abs(fr["b_fx"]) > 1.5 * abs(dev_b):
            c4_fail = True
    same = sum(1 for s in signs if s == int(np.sign(dev_b)))
    c3 = same >= 2
    c4 = not c4_fail
    c5 = bool(q51 is not None and np.sign(q51) == np.sign(dev_b))
    c6 = bool(lomo_frac is not None and lomo_frac >= 0.75)
    out = dict(rec)
    out.update(
        {
            "C1": c1,
            "C2": c2,
            "C3": c3,
            "C4": c4,
            "C5": c5,
            "C6": c6,
            "c1_confirmed": bool(c1 and c2 and c3 and c4 and c5 and c6),
            "c1_fold_same_sign_n": int(same),
            "c1_q5_minus_q1": q51,
            "c1_lomo_frac": lomo_frac,
            "c1_fold_b": {k: v.get("b_fx") for k, v in fold_recs.items()},
        }
    )
    return out


def lead_gates(offset_b: dict[int, float | None], *, dev_b: float, c1_confirmed: bool) -> dict[str, Any]:
    b0 = offset_b.get(0)
    causal = [-10, -5, -3, -1, 0]
    future = [1, 3, 5, 10]
    l1 = bool(c1_confirmed and b0 is not None and np.sign(b0) == np.sign(dev_b))
    l2 = any(offset_b.get(o) is not None and np.sign(offset_b[o]) == np.sign(dev_b) for o in (-1, -3, -5))
    max_causal = max((abs(offset_b[o]) for o in causal if offset_b.get(o) is not None), default=0.0)
    max_future = max((abs(offset_b[o]) for o in future if offset_b.get(o) is not None), default=0.0)
    l3 = not (max_future > 0 and max_causal < 0.5 * max_future)
    fut_peak = max((abs(offset_b[o]) for o in (1, 3, 5) if offset_b.get(o) is not None), default=0.0)
    l4 = bool(b0 is not None and abs(b0) >= 0.50 * fut_peak) if fut_peak > 0 else bool(b0 is not None)
    return {
        "L1": l1,
        "L2": l2,
        "L3": l3,
        "L4": l4,
        "lead_pass": bool(l1 and l2 and l3 and l4),
        "offset_b_fx": {str(k): v for k, v in offset_b.items()},
        "fail_label": None if (l1 and l2 and l3 and l4) else "FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD",
    }


def verify_shuffle(eligible_dates: list[str]) -> dict[str, Any]:
    packed = generate_shuffle_permutations(eligible_dates, return_maps=True)
    if packed.get("permutation_sha256") != EXPECTED_SHUFFLE_SHA256:
        raise RuntimeError("shuffle_sha_mismatch_at_run")
    return packed


def shuffle_pass(
    *,
    y: np.ndarray,
    fx_by_date: dict[str, np.ndarray],
    date_str: np.ndarray,
    clock_idx: np.ndarray,
    controls: np.ndarray,
    minute_ids: np.ndarray,
    maps: list[dict[str, str]],
    unshufflable: set[str],
    real_abs: float,
) -> dict[str, Any]:
    n_min = int(minute_ids.max()) + 1 if minute_ids.size else 1
    gi = np.array([bar_index_for_available_t(CLOCK_MINS[int(c)]) for c in clock_idx], dtype=np.int32)
    uniq, inv = np.unique(date_str, return_inverse=True)
    ones = np.ones(y.shape[0], dtype=np.float64)
    stats = []
    for mp in maps:
        src = []
        ok_src = True
        for d in uniq:
            key = d if d in unshufflable else mp.get(str(d), str(d))
            vec = fx_by_date.get(str(key))
            if vec is None:
                ok_src = False
                break
            src.append(vec)
        if not ok_src:
            continue
        grid = np.vstack(src)
        fx = grid[inv, gi]
        x = np.column_stack([fx, controls] if controls.ndim == 2 else [fx, controls])
        mask = np.isfinite(y) & np.isfinite(x).all(axis=1)
        if int(mask.sum()) < 40:
            continue
        b = _w_ols_fe(y[mask], x[mask], minute_ids[mask], ones[mask], n_min)
        if b is not None:
            stats.append(abs(float(b[0])))
    arr = np.array(stats, dtype=np.float64) if stats else np.array([], dtype=np.float64)
    p95 = float(np.percentile(arr, 95)) if arr.size >= 20 else None
    ok = bool(p95 is not None and real_abs >= p95)
    return {
        "ok": ok,
        "n_valid_perm": int(arr.size),
        "p95_abs": p95,
        "real_abs": float(real_abs),
        "seed": DAY_SHUFFLE_SEED,
        "n": DAY_SHUFFLE_N,
    }


def _ctrl_list(controls: np.ndarray) -> list[np.ndarray]:
    if controls.ndim == 1:
        return [controls]
    return [controls[:, j] for j in range(controls.shape[1])]


def concentration_sector(*, close, valid, idx, horizon, fx, controls, minute_ids, dates_sel, clock_sel, dev_sign: int) -> dict[str, Any]:
    n_const = int(idx.size)
    bars0 = np.array([bar_index_for_available_t(CLOCK_MINS[int(c)]) for c in clock_sel], dtype=np.int32)
    bars1 = bars0 + int(horizon)
    n_obs = len(dates_sel)
    r_sym = np.full((n_const, n_obs), np.nan, dtype=np.float64)
    for j, si in enumerate(idx):
        p0 = close[int(si), dates_sel, bars0]
        p1 = close[int(si), dates_sel, bars1]
        ok = valid[int(si), dates_sel, bars0] & valid[int(si), dates_sel, bars1]
        good = ok & (p0 > 0) & (p1 > 0)
        r_sym[j, good] = LOG_BPS * np.log(p1[good] / p0[good])
    mean_abs = np.array([np.nanmean(np.abs(r_sym[j])) if np.isfinite(r_sym[j]).any() else -1.0 for j in range(n_const)])
    ctr = _ctrl_list(controls)
    same = 0
    for j in range(n_const):
        keep = [k for k in range(n_const) if k != j]
        y = np.nanmean(r_sym[keep], axis=0)
        b = point_b_fx(y, fx, ctr, minute_ids)
        if b is not None and int(np.sign(b)) == int(dev_sign):
            same += 1
    loso = same / float(max(n_const, 1))
    top = int(np.argmax(mean_abs))
    keep_top = [k for k in range(n_const) if k != top]
    y_top = np.nanmean(r_sym[keep_top], axis=0)
    b_top = point_b_fx(y_top, fx, ctr, minute_ids)
    top_ok = bool(b_top is not None and int(np.sign(b_top)) == int(dev_sign))
    return {
        "ok": bool(loso >= 0.80 and top_ok),
        "loso_same_sign_frac": float(loso),
        "remove_top_same_sign": top_ok,
        "top_symbol_index": int(idx[top]) if n_const else None,
    }


def concentration_mkt(*, close, valid, horizon, fx, controls, minute_ids, dates_sel, clock_sel, dev_sign: int) -> dict[str, Any]:
    n_s = close.shape[0]
    bars0 = np.array([bar_index_for_available_t(CLOCK_MINS[int(c)]) for c in clock_sel], dtype=np.int32)
    bars1 = bars0 + int(horizon)
    n_obs = len(dates_sel)
    r_sym = np.full((n_s, n_obs), np.nan, dtype=np.float64)
    for j in range(n_s):
        p0 = close[j, dates_sel, bars0]
        p1 = close[j, dates_sel, bars1]
        ok = valid[j, dates_sel, bars0] & valid[j, dates_sel, bars1]
        good = ok & (p0 > 0) & (p1 > 0)
        r_sym[j, good] = LOG_BPS * np.log(p1[good] / p0[good])
    contrib = np.array([np.nanmean(np.abs(r_sym[j])) if np.isfinite(r_sym[j]).any() else -1.0 for j in range(n_s)])
    order = np.argsort(-contrib)
    ctr = _ctrl_list(controls)

    def _b_drop(drop: set[int]) -> float | None:
        keep = [j for j in range(n_s) if j not in drop]
        y = np.nanmean(r_sym[keep], axis=0)
        return point_b_fx(y, fx, ctr, minute_ids)

    b1 = _b_drop({int(order[0])})
    b5 = _b_drop(set(int(x) for x in order[:5]))
    ok1 = bool(b1 is not None and int(np.sign(b1)) == int(dev_sign))
    ok5 = bool(b5 is not None and int(np.sign(b5)) == int(dev_sign))
    return {"ok": bool(ok1 and ok5), "remove_top1_same_sign": ok1, "remove_top5_same_sign": ok5}
