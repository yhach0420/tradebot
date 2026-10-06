"""C1 confirmation, C7, offset, shuffle, identity specificity, concentration. Frozen candidates only."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery import (
    C7_ABS_RATIO,
    EXPECTED_PERMUTATION_SHA256,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_N,
    LEADER_AGE_SEC,
    MKT_MIN_FRAC,
    MKT_MIN_N,
    OFFSET_ABS_RATIO,
    OFFSETS,
    PEER_MIN_FRAC,
    PEER_MIN_N,
    STRICT_TARGET_AGE_SEC,
    TARGET_AGE_SEC,
)
from research.causal_driver_pb1.cross_sectional_discovery.features import (
    basket_future_and_lag,
    leader_ret_offset,
    listed_mask,
    precompute_drivers,
    scope_members,
)
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS, _w_ols_fe, date_minute_ids, fit_sample, point_b_fx, q5_minus_q1
from research.causal_driver_pb1.phase2_discovery.placebos import c1_gates
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations


def _date_mask(dates: list[str], wanted: set[str]) -> np.ndarray:
    m = np.array([d in wanted for d in dates], dtype=np.bool_)
    return np.repeat(m, N_CLOCK)


def _subset_point(y, fx, ctr, minute_ids, mask) -> float | None:
    if int(mask.sum()) < 40:
        return None
    return point_b_fx(y[mask], fx[mask], [c[mask] for c in ctr], minute_ids[mask])


def _obs_pack(y, fx, ctr):
    mask = np.isfinite(y) & np.isfinite(fx)
    for c in ctr:
        mask = mask & np.isfinite(c)
    return mask


def confirm_candidates(
    *,
    px: np.ndarray,
    age: np.ndarray,
    symbols: list[str],
    dates: list[str],
    listing_start: dict[str, Any],
    frozen_leaders: list[dict[str, Any]],
    scopes: list[dict[str, Any]],
    sector_of: dict[str, str],
    folds: dict[str, Any],
    candidates: list[dict[str, Any]],
    leader_fx_by_date: dict[tuple[str, int, str], dict[str, np.ndarray]],
    eligible_dates: list[str],
    px_dev: np.ndarray | None,
    age_dev: np.ndarray | None,
    dates_dev: list[str],
) -> list[dict[str, Any]]:
    n_d = len(dates)
    date_ids, minute_ids = date_minute_ids(n_d)
    listed = listed_mask(symbols=symbols, dates=dates, listing_start=listing_start)
    leader_set = {r["leader_symbol"] for r in frozen_leaders}
    drivers = precompute_drivers(px=px, age=age, symbols=symbols, frozen_leaders=frozen_leaders)
    mkt_mem = scope_members(symbols=symbols, sector_of=sector_of, leader_set=leader_set, sector_id=None)
    scope_map = {s["scope_id"]: s for s in scopes}
    months = [d[:6] for d in dates]
    month_u = sorted(set(months))
    month_id = np.repeat(np.array([month_u.index(m) for m in months], dtype=np.int32), N_CLOCK)
    fold_masks = {
        "C1_EARLY": _date_mask(dates, set(folds.get("C1_EARLY") or [])),
        "C1_MIDDLE": _date_mask(dates, set(folds.get("C1_MIDDLE") or [])),
        "C1_LATE": _date_mask(dates, set(folds.get("C1_LATE") or [])),
    }
    shuffle = generate_shuffle_permutations(eligible_dates, return_maps=True)
    if shuffle.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256:
        raise RuntimeError("shuffle_sha_mismatch_at_c1")
    maps = list(shuffle.get("maps") or [])
    unsh = set(shuffle.get("unshufflable") or [])
    date_str = np.repeat(np.array(dates, dtype=object), N_CLOCK)
    clock_idx = np.tile(np.arange(N_CLOCK, dtype=np.int32), n_d)
    pos = {s: i for i, s in enumerate(symbols)}
    out = []
    for cand in candidates:
        sc = scope_map[cand["scope_id"]]
        h = int(cand["horizon"])
        w = int(cand["lookback"])
        dev_b = float(cand["DEV_beta_primary"])
        if sc["global"]:
            mem = mkt_mem
            min_n, min_frac = GLOBAL_MIN_N, GLOBAL_MIN_FRAC
        else:
            mem = scope_members(symbols=symbols, sector_of=sector_of, leader_set=leader_set, sector_id=sc["sector_id"])
            min_n, min_frac = PEER_MIN_N, PEER_MIN_FRAC
        pack = basket_future_and_lag(
            px=px, age=age, members=mem, listed=listed, horizon=h, max_age=TARGET_AGE_SEC, min_n=min_n, min_frac=min_frac
        )
        mkt = basket_future_and_lag(
            px=px, age=age, members=mkt_mem, listed=listed, horizon=h, max_age=TARGET_AGE_SEC, min_n=MKT_MIN_N, min_frac=MKT_MIN_FRAC
        )
        y = pack["y"].reshape(-1)
        self_lag = pack["lag"].reshape(-1)
        mlag = mkt["lag"].reshape(-1)
        ctr = [self_lag] if sc["global"] else [self_lag, mlag]
        fx = drivers["global"][w].reshape(-1) if sc["global"] else drivers["by_leader"][sc["leader_symbol"]][w].reshape(-1)
        pooled = fit_sample(y, fx, ctr, date_ids, minute_ids, n_d)
        pooled.pop("beta", None)
        fold_recs = {}
        for name, mask in fold_masks.items():
            pb = _subset_point(y, fx, ctr, minute_ids, mask)
            fold_recs[name] = {"ok": pb is not None, "b_fx": pb}
        same_m = tot_m = 0
        for mi, _m in enumerate(month_u):
            pb = _subset_point(y, fx, ctr, minute_ids, month_id != mi)
            tot_m += 1
            if pooled.get("ok") and pb is not None and int(np.sign(pb)) == int(np.sign(dev_b)):
                same_m += 1
        lomo = (same_m / tot_m) if tot_m else None
        q51 = q5_minus_q1(y, fx, cand.get("Q_boundaries") or {}) if pooled.get("ok") else None
        rec = c1_gates(rec={**pooled, **cand}, fold_recs=fold_recs, q51=q51, lomo_frac=lomo, dev_b=dev_b)
        rec["failed_at"] = None
        for k in ("C1", "C2", "C3", "C4", "C5", "C6"):
            if not rec.get(k):
                rec["failed_at"] = k
                rec["c1_confirmed"] = False
                rec["C7"] = False
                out.append(rec)
                break
        else:
            rec = _apply_c7(rec, pack_strict=_strict_pack(px, age, mem, listed, mkt_mem, h, sc), drivers=drivers, sc=sc, w=w, date_ids=date_ids, minute_ids=minute_ids, n_d=n_d, dev_b=dev_b)
            if not rec.get("C7"):
                rec["failed_at"] = "C7"
                rec["c1_confirmed"] = False
                out.append(rec)
                continue
            rec["c1_confirmed"] = True
            rec.update(
                _placebos(
                    rec=rec,
                    y=y,
                    fx=fx,
                    ctr=ctr,
                    minute_ids=minute_ids,
                    date_ids=date_ids,
                    n_d=n_d,
                    sc=sc,
                    w=w,
                    h=h,
                    px=px,
                    age=age,
                    mem=mem,
                    listed=listed,
                    symbols=symbols,
                    dates=dates,
                    frozen_leaders=frozen_leaders,
                    drivers=drivers,
                    pos=pos,
                    date_str=date_str,
                    clock_idx=clock_idx,
                    maps=maps,
                    unsh=unsh,
                    leader_fx_by_date=leader_fx_by_date,
                    dev_b=dev_b,
                )
            )
            out.append(rec)
    return out


def _strict_pack(px, age, mem, listed, mkt_mem, h, sc):
    min_n, min_frac = (GLOBAL_MIN_N, GLOBAL_MIN_FRAC) if sc["global"] else (PEER_MIN_N, PEER_MIN_FRAC)
    pack = basket_future_and_lag(
        px=px, age=age, members=mem, listed=listed, horizon=int(h), max_age=STRICT_TARGET_AGE_SEC, min_n=min_n, min_frac=min_frac
    )
    mkt = basket_future_and_lag(
        px=px, age=age, members=mkt_mem, listed=listed, horizon=int(h), max_age=STRICT_TARGET_AGE_SEC, min_n=MKT_MIN_N, min_frac=MKT_MIN_FRAC
    )
    return pack, mkt


def _apply_c7(rec, pack_strict, drivers, sc, w, date_ids, minute_ids, n_d, dev_b):
    pack, mkt = pack_strict
    y = pack["y"].reshape(-1)
    self_lag = pack["lag"].reshape(-1)
    mlag = mkt["lag"].reshape(-1)
    ctr = [self_lag] if sc["global"] else [self_lag, mlag]
    fx = drivers["global"][int(w)].reshape(-1) if sc["global"] else drivers["by_leader"][sc["leader_symbol"]][int(w)].reshape(-1)
    fit = fit_sample(y, fx, ctr, date_ids, minute_ids, n_d)
    fit.pop("beta", None)
    b60 = fit.get("b_fx")
    bpri = rec.get("b_fx")
    rec["beta_C1_60"] = b60
    rec["ci_C1_60"] = [fit.get("ci_lo"), fit.get("ci_hi")] if fit.get("ok") else None
    same = bool(b60 is not None and np.sign(b60) == np.sign(dev_b) and np.sign(dev_b) != 0)
    ci_ok = bool(fit.get("ok") and fit.get("ci_excludes_0"))
    mag = bool(b60 is not None and bpri is not None and abs(float(b60)) >= C7_ABS_RATIO * abs(float(bpri)))
    rec["C7"] = bool(same and ci_ok and mag)
    rec["C7_sign"] = same
    rec["C7_ci_excludes_0"] = ci_ok
    rec["C7_abs_ratio_ok"] = mag
    return rec


def _placebos(
    *,
    rec,
    y,
    fx,
    ctr,
    minute_ids,
    date_ids,
    n_d,
    sc,
    w,
    h,
    px,
    age,
    mem,
    listed,
    symbols,
    dates,
    frozen_leaders,
    drivers,
    pos,
    date_str,
    clock_idx,
    maps,
    unsh,
    leader_fx_by_date,
    dev_b,
) -> dict[str, Any]:
    failed = None
    offset = _offset_map(px=px, age=age, sc=sc, w=w, y=y, ctr=ctr, minute_ids=minute_ids, pos=pos, frozen_leaders=frozen_leaders, drivers=drivers)
    rec["offset"] = offset
    if not offset.get("ok"):
        failed = "OFFSET"
    sh = None
    ident = None
    conc = None
    cf = None
    if failed is None:
        key_kind = "GLOBAL" if sc["global"] else sc["leader_symbol"]
        fx_map = leader_fx_by_date.get((key_kind, int(w), "primary")) or {}
        controls = np.column_stack(ctr) if ctr else np.zeros((y.shape[0], 0))
        mask = _obs_pack(y, fx, ctr)
        sh = shuffle_clock(
            y=y[mask],
            fx_by_date=fx_map,
            date_str=date_str[mask],
            clock_idx=clock_idx[mask],
            controls=controls[mask] if controls.size else controls,
            minute_ids=minute_ids[mask],
            maps=maps,
            unshufflable=unsh,
            real_abs=abs(float(rec["b_fx"])),
        )
        rec["shuffle"] = sh
        if not sh.get("ok"):
            failed = "SHUFFLE"
    if failed is None:
        ident = identity_gate(y=y, ctr=ctr, minute_ids=minute_ids, sc=sc, w=w, drivers=drivers, frozen_leaders=frozen_leaders, actual_beta=float(rec["b_fx"]))
        rec["identity"] = ident
        if not ident.get("ok"):
            failed = "IDENTITY"
    if failed is None:
        conc = concentration(
            px=px, age=age, mem=mem, listed=listed, h=h, y_primary=y, fx=fx, ctr=ctr, minute_ids=minute_ids, symbols=symbols, dates=dates, sc=sc, dev_sign=int(np.sign(dev_b))
        )
        rec["concentration"] = conc
        if not conc.get("ok"):
            failed = "CONCENTRATION"
    if failed is None:
        cf = common_factor(y=y, fx=fx, ctr=ctr, minute_ids=minute_ids, sc=sc, dev_sign=int(np.sign(dev_b)))
        rec["common_factor"] = cf
        if not cf.get("ok"):
            failed = "COMMON_FACTOR"
    rec["offset_pass"] = bool(offset.get("ok"))
    rec["shuffle_pass"] = bool(sh.get("ok")) if sh else False
    rec["identity_pass"] = bool(ident.get("ok")) if ident else False
    rec["concentration_pass"] = bool(conc.get("ok")) if conc else False
    rec["common_factor_pass"] = bool(cf.get("ok")) if cf else False
    rec["final_pass"] = failed is None
    rec["failed_at"] = failed
    return rec


def _offset_map(*, px, age, sc, w, y, ctr, minute_ids, pos, frozen_leaders, drivers):
    offset_b: dict[int, float | None] = {}
    for k in OFFSETS:
        if sc["global"]:
            parts = []
            for r in frozen_leaders:
                parts.append(leader_ret_offset(px=px, age=age, si=int(pos[r["leader_symbol"]]), w=int(w), k=int(k)))
            fxk = np.nanmean(np.stack(parts, axis=0), axis=0)
            valid = np.isfinite(np.stack(parts, axis=0))
            fxk = np.where(valid.sum(axis=0) >= 7, np.nansum(np.stack(parts, axis=0), axis=0) / np.maximum(valid.sum(axis=0), 1), np.nan)
        else:
            fxk = leader_ret_offset(px=px, age=age, si=int(pos[sc["leader_symbol"]]), w=int(w), k=int(k))
        offset_b[int(k)] = point_b_fx(y, fxk.reshape(-1), ctr, minute_ids)
    b0 = offset_b.get(0)
    l0 = b0 is not None and rec_sign_ok(b0, y, ctr)
    # offset 0 confirmed: finite and same sign as k=0 C1 primary, which is ctr model at k=0.
    c1_b = point_b_fx(y, (drivers["global"][int(w)] if sc["global"] else drivers["by_leader"][sc["leader_symbol"]][int(w)]).reshape(-1), ctr, minute_ids)
    ok0 = bool(b0 is not None and c1_b is not None and np.sign(b0) == np.sign(c1_b) and np.sign(c1_b) != 0)
    lag_ok = any(offset_b.get(o) is not None and np.sign(offset_b[o]) == np.sign(c1_b) for o in (-1, -3)) if c1_b is not None else False
    fut = [abs(offset_b[o]) for o in (1, 3, 5) if offset_b.get(o) is not None]
    causal = [abs(offset_b[o]) for o in (-5, -3, -1, 0) if offset_b.get(o) is not None]
    max_fut = max(fut) if fut else 0.0
    max_cau = max(causal) if causal else 0.0
    future_only = bool(max_fut > 0 and max_cau < 0.5 * max_fut)
    mag = bool(b0 is not None and (max_fut == 0 or abs(b0) >= OFFSET_ABS_RATIO * max_fut))
    ok = bool(ok0 and lag_ok and (not future_only) and mag)
    return {
        "ok": ok,
        "offset_b": {str(k): (None if v is None else float(v)) for k, v in offset_b.items()},
        "offset0_confirmed": ok0,
        "lag_m1_or_m3": lag_ok,
        "future_only_shape_absent": not future_only,
        "abs0_ge_half_max_future": mag,
        "fail_label": None if ok else "FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD",
    }


def rec_sign_ok(b0, y, ctr):
    return b0 is not None


def shuffle_clock(*, y, fx_by_date, date_str, clock_idx, controls, minute_ids, maps, unshufflable, real_abs):
    n_min = int(minute_ids.max()) + 1 if minute_ids.size else 1
    uniq, inv = np.unique(date_str.astype(str), return_inverse=True)
    ones = np.ones(y.shape[0], dtype=np.float64)
    stats = []
    for mp in maps:
        src = []
        ok_src = True
        for d in uniq:
            key = d if d in unshufflable else mp.get(str(d), str(d))
            vec = fx_by_date.get(str(key))
            if vec is None or int(np.asarray(vec).shape[0]) != N_CLOCK:
                ok_src = False
                break
            src.append(np.asarray(vec, dtype=np.float64))
        if not ok_src:
            continue
        grid = np.vstack(src)
        fx = grid[inv, clock_idx]
        if controls.size == 0:
            x = fx.reshape(-1, 1)
        else:
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
    return {"ok": ok, "n_valid_perm": int(arr.size), "p95_abs": p95, "real_abs": float(real_abs), "n": 1000, "seed": 20251127}


def identity_gate(*, y, ctr, minute_ids, sc, w, drivers, frozen_leaders, actual_beta):
    if sc["global"]:
        return {"ok": True, "label": "LEADER_IDENTITY_SPECIFICITY_GATE", "result": "NOT_APPLICABLE"}
    alts = []
    for r in frozen_leaders:
        if r["leader_symbol"] == sc["leader_symbol"]:
            continue
        b = point_b_fx(y, drivers["by_leader"][r["leader_symbol"]][int(w)].reshape(-1), ctr, minute_ids)
        alts.append({"leader_symbol": r["leader_symbol"], "beta": None if b is None else float(b)})
    abs_alts = [abs(a["beta"]) for a in alts if a["beta"] is not None]
    mx = max(abs_alts) if abs_alts else None
    ok = bool(mx is not None and abs(float(actual_beta)) > mx)
    return {
        "ok": ok,
        "label": "LEADER_IDENTITY_SPECIFICITY_GATE",
        "not_empirical_p95": True,
        "actual_abs": abs(float(actual_beta)),
        "max_alt_abs": mx,
        "alts": alts,
        "ties_fail": True,
    }


def concentration(*, px, age, mem, listed, h, y_primary, fx, ctr, minute_ids, symbols, dates, sc, dev_sign):
    n_s = px.shape[0]
    n_d = px.shape[1]
    from research.causal_driver_pb1.cross_sectional_discovery.features import CLOCK_AM, N_AM

    r_sym = np.full((n_s, n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        a1 = int(ai) + int(h)
        if a1 >= N_AM:
            continue
        p0 = px[:, :, int(ai)]
        p1 = px[:, :, a1]
        g0 = age[:, :, int(ai)]
        g1 = age[:, :, a1]
        ok = mem[:, None] & listed & np.isfinite(p0) & np.isfinite(p1) & (p0 > 0) & (p1 > 0) & (g0 <= TARGET_AGE_SEC) & (g1 <= TARGET_AGE_SEC)
        tmp = np.full(p0.shape, np.nan, dtype=np.float64)
        good = ok & (p0 > 0) & (p1 > 0)
        tmp[good] = LOG_BPS * np.log(p1[good] / p0[good])
        r_sym[:, :, ci] = tmp
    idx = np.where(mem)[0]
    if sc["global"]:
        contrib = np.array([np.nanmean(np.abs(r_sym[j])) if np.isfinite(r_sym[j]).any() else -1.0 for j in idx])
        order = idx[np.argsort(-contrib)]
        y1 = np.nanmean(r_sym[[j for j in idx if j != int(order[0])]], axis=0).reshape(-1)
        drop5 = set(int(x) for x in order[:5])
        y5 = np.nanmean(r_sym[[j for j in idx if j not in drop5]], axis=0).reshape(-1)
        b1 = point_b_fx(y1, fx, ctr, minute_ids)
        b5 = point_b_fx(y5, fx, ctr, minute_ids)
        ok1 = bool(b1 is not None and int(np.sign(b1)) == int(dev_sign))
        ok5 = bool(b5 is not None and int(np.sign(b5)) == int(dev_sign))
        return {"ok": bool(ok1 and ok5), "remove_top1_same_sign": ok1, "remove_top5_same_sign": ok5, "kind": "global"}
    same = 0
    n_const = int(idx.size)
    for j in idx:
        keep = [k for k in idx if k != j]
        y = np.nanmean(r_sym[keep], axis=0).reshape(-1)
        b = point_b_fx(y, fx, ctr, minute_ids)
        if b is not None and int(np.sign(b)) == int(dev_sign):
            same += 1
    loso = same / float(max(n_const, 1))
    mean_abs = np.array([np.nanmean(np.abs(r_sym[j])) if np.isfinite(r_sym[j]).any() else -1.0 for j in idx])
    top = int(idx[int(np.argmax(mean_abs))]) if n_const else None
    keep_top = [k for k in idx if k != top]
    y_top = np.nanmean(r_sym[keep_top], axis=0).reshape(-1) if keep_top else np.full(n_d * N_CLOCK, np.nan)
    b_top = point_b_fx(y_top, fx, ctr, minute_ids)
    top_ok = bool(b_top is not None and int(np.sign(b_top)) == int(dev_sign))
    return {"ok": bool(loso >= 0.80 and top_ok), "loso_same_sign_frac": float(loso), "remove_top_same_sign": top_ok, "kind": "same_sector"}


def common_factor(*, y, fx, ctr, minute_ids, sc, dev_sign):
    if sc["global"]:
        return {"ok": True, "result": "NOT_APPLICABLE"}
    b_with = point_b_fx(y, fx, ctr, minute_ids)
    b_wo = point_b_fx(y, fx, ctr[:1], minute_ids)
    if b_with is None or b_wo is None:
        return {"ok": False, "with_market": None if b_with is None else float(b_with), "without_market": None if b_wo is None else float(b_wo)}
    flip = int(np.sign(b_with)) != int(np.sign(b_wo))
    return {
        "ok": bool(not flip and int(np.sign(b_with)) == int(dev_sign)),
        "with_market": float(b_with),
        "without_market": float(b_wo),
        "sign_flipped": bool(flip),
    }


def merge_leader_fx_maps(*maps: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for m in maps:
        out.update(m)
    return out


def driver_date_map(ret: np.ndarray, dates: list[str]) -> dict[str, np.ndarray]:
    return {str(d): ret[i].copy() for i, d in enumerate(dates)}
