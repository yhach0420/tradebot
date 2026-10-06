"""C1 confirmation, C7, offset, shuffle, sector identity, concentration. Frozen candidates only."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery.confirm import driver_date_map, merge_leader_fx_maps, shuffle_clock
from research.causal_driver_pb1.cross_sectional_discovery.features import CLOCK_AM, N_AM, basket_future_and_lag, listed_mask
from research.causal_driver_pb1.phase2_discovery.clock import N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS, date_minute_ids, point_b_fx, q5_minus_q1
from research.causal_driver_pb1.phase2_discovery.placebos import c1_gates
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.sector_breadth_discovery import (
    C7_ABS_RATIO,
    DRIVER_AGE_SEC,
    EXPECTED_PERMUTATION_SHA256,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_N,
    MKT_EX_MIN_FRAC,
    MKT_EX_MIN_N,
    OFFSET_ABS_RATIO,
    OFFSETS,
    SECTOR_MIN_FRAC,
    SECTOR_MIN_N,
    STRICT_TARGET_AGE_SEC,
    TARGET_AGE_SEC,
)
from research.causal_driver_pb1.sector_breadth_discovery.features import (
    driver_from_ret,
    driver_loo,
    driver_offset,
    mkt_ex_members,
    precompute_drivers,
    scope_members,
)
from research.causal_driver_pb1.sector_breadth_discovery.infer import fit_frozen

_ = (driver_date_map, merge_leader_fx_maps)


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


def _min_n_frac(global_scope: bool) -> tuple[int, float]:
    if global_scope:
        return GLOBAL_MIN_N, GLOBAL_MIN_FRAC
    return SECTOR_MIN_N, SECTOR_MIN_FRAC


def _controls(*, sid: str, glob: bool, lag_self: np.ndarray, mkt_lag: dict[str, np.ndarray]) -> list[np.ndarray]:
    if glob:
        return [lag_self.reshape(-1)]
    return [lag_self.reshape(-1), mkt_lag[sid].reshape(-1)]


def confirm_candidates(
    *,
    px: np.ndarray,
    age: np.ndarray,
    symbols: list[str],
    dates: list[str],
    listing_start: dict[str, Any],
    scopes: list[dict[str, Any]],
    sector_of: dict[str, str],
    folds: dict[str, Any],
    candidates: list[dict[str, Any]],
    driver_fx_by_date: dict[tuple[str, str, int], dict[str, np.ndarray]],
    eligible_dates: list[str],
    boot_index: np.ndarray,
) -> list[dict[str, Any]]:
    n_d = len(dates)
    date_ids, minute_ids = date_minute_ids(n_d)
    listed = listed_mask(symbols=symbols, dates=dates, listing_start=listing_start)
    print("C1_DRIVERS", flush=True)
    drivers = precompute_drivers(px=px, age=age, listed=listed, scopes=scopes, symbols=symbols, sector_of=sector_of)
    mems = drivers["mems"]
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
    lag_cache: dict[str, np.ndarray] = {}
    mkt_cache: dict[str, np.ndarray] = {}
    y_cache: dict[tuple[int, str, float], np.ndarray] = {}

    def basket(sid: str, h: int, y_age: float) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        glob = sid == "GLOBAL_105"
        mem = mems[sid]
        min_n, min_frac = _min_n_frac(glob)
        if sid not in lag_cache:
            lag_pack = basket_future_and_lag(
                px=px, age=age, members=mem, listed=listed, horizon=1, max_age=DRIVER_AGE_SEC, min_n=min_n, min_frac=min_frac
            )
            lag_cache[sid] = lag_pack["lag"]
            if not glob:
                mex = mkt_ex_members(symbols=symbols, sector_of=sector_of, sector_id=sid.replace("SECTOR_", ""))
                mpack = basket_future_and_lag(
                    px=px,
                    age=age,
                    members=mex,
                    listed=listed,
                    horizon=1,
                    max_age=DRIVER_AGE_SEC,
                    min_n=MKT_EX_MIN_N,
                    min_frac=MKT_EX_MIN_FRAC,
                )
                mkt_cache[sid] = mpack["lag"]
        if (int(h), sid, y_age) not in y_cache:
            fut = basket_future_and_lag(
                px=px, age=age, members=mem, listed=listed, horizon=int(h), max_age=y_age, min_n=min_n, min_frac=min_frac
            )
            y_cache[(int(h), sid, y_age)] = fut["y"]
        mlag = None if glob else mkt_cache[sid]
        return y_cache[(int(h), sid, y_age)], lag_cache[sid], mlag

    out = []
    for cand in candidates:
        sid = str(cand["scope_id"])
        glob = sid == "GLOBAL_105"
        h = int(cand["horizon"])
        w = int(cand["lookback"])
        metric = str(cand["metric"])
        dev_b = float(cand["DEV_beta_primary"])
        y, self_lag, mlag = basket(sid, h, TARGET_AGE_SEC)
        yv = y.reshape(-1)
        ctr = [self_lag.reshape(-1)] if glob else [self_lag.reshape(-1), mlag.reshape(-1)]
        fx = drivers["by_scope"][sid][metric][w].reshape(-1)
        pooled = fit_frozen(yv, fx, ctr, date_ids, minute_ids, n_d, boot_index)
        pooled.pop("beta", None)
        fold_recs = {}
        for name, mask in fold_masks.items():
            pb = _subset_point(yv, fx, ctr, minute_ids, mask)
            fold_recs[name] = {"ok": pb is not None, "b_fx": pb}
        same_m = tot_m = 0
        for mi, _m in enumerate(month_u):
            pb = _subset_point(yv, fx, ctr, minute_ids, month_id != mi)
            tot_m += 1
            if pooled.get("ok") and pb is not None and int(np.sign(pb)) == int(np.sign(dev_b)):
                same_m += 1
        lomo = (same_m / tot_m) if tot_m else None
        q51 = q5_minus_q1(yv, fx, cand.get("Q_boundaries") or {}) if pooled.get("ok") else None
        rec = c1_gates(rec={**pooled, **cand}, fold_recs=fold_recs, q51=q51, lomo_frac=lomo, dev_b=dev_b)
        rec["failed_at"] = None
        early_fail = None
        for k in ("C1", "C2", "C3", "C4", "C5", "C6"):
            if not rec.get(k):
                early_fail = k
                break
        if early_fail:
            rec["failed_at"] = early_fail
            rec["c1_confirmed"] = False
            rec["C7"] = False
            rec["final_pass"] = False
            out.append(rec)
            continue
        rec = _apply_c7(
            rec,
            y60=basket(sid, h, STRICT_TARGET_AGE_SEC),
            fx=fx,
            glob=glob,
            date_ids=date_ids,
            minute_ids=minute_ids,
            n_d=n_d,
            boot_index=boot_index,
            dev_b=dev_b,
        )
        if not rec.get("C7"):
            rec["failed_at"] = "C7"
            rec["c1_confirmed"] = False
            rec["final_pass"] = False
            out.append(rec)
            continue
        rec["c1_confirmed"] = True
        rec.update(
            _placebos(
                rec=rec,
                y=yv,
                fx=fx,
                ctr=ctr,
                minute_ids=minute_ids,
                date_ids=date_ids,
                n_d=n_d,
                sid=sid,
                glob=glob,
                metric=metric,
                w=w,
                h=h,
                px=px,
                age=age,
                listed=listed,
                symbols=symbols,
                sector_of=sector_of,
                drivers=drivers,
                date_str=date_str,
                clock_idx=clock_idx,
                maps=maps,
                unsh=unsh,
                driver_fx_by_date=driver_fx_by_date,
                dev_b=dev_b,
                scopes=scopes,
            )
        )
        out.append(rec)
    return out


def _apply_c7(rec, y60, fx, glob, date_ids, minute_ids, n_d, boot_index, dev_b):
    y, self_lag, mlag = y60
    ctr = [self_lag.reshape(-1)] if glob else [self_lag.reshape(-1), mlag.reshape(-1)]
    fit = fit_frozen(y.reshape(-1), fx, ctr, date_ids, minute_ids, n_d, boot_index)
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
    sid,
    glob,
    metric,
    w,
    h,
    px,
    age,
    listed,
    symbols,
    sector_of,
    drivers,
    date_str,
    clock_idx,
    maps,
    unsh,
    driver_fx_by_date,
    dev_b,
    scopes,
) -> dict[str, Any]:
    failed = None
    mem = drivers["mems"][sid]
    offset = _offset_map(
        px=px, age=age, listed=listed, mem=mem, glob=glob, metric=metric, w=w, y=y, ctr=ctr, minute_ids=minute_ids, c1_b=rec.get("b_fx")
    )
    rec["offset"] = offset
    if not offset.get("ok"):
        failed = "OFFSET"
    sh = None
    ident = None
    dconc = None
    tconc = None
    cf = None
    if failed is None:
        fx_map = driver_fx_by_date.get((sid, metric, int(w))) or {}
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
        ident = identity_gate(
            y=y, ctr=ctr, minute_ids=minute_ids, glob=glob, metric=metric, w=w, sid=sid, drivers=drivers, scopes=scopes, actual_beta=float(rec["b_fx"])
        )
        rec["identity"] = ident
        rec["sector_identity"] = ident
        if not ident.get("ok"):
            failed = "IDENTITY"
    if failed is None:
        dconc = driver_concentration(
            ret=drivers["rets"][int(w)],
            mem=mem,
            listed=listed,
            glob=glob,
            metric=metric,
            y=y,
            ctr=ctr,
            minute_ids=minute_ids,
            fx_full=drivers["by_scope"][sid][metric][int(w)],
            dev_sign=int(np.sign(dev_b)),
            symbols=symbols,
        )
        rec["driver_concentration"] = dconc
        if not dconc.get("ok"):
            failed = "DRIVER_CONCENTRATION"
    if failed is None:
        tconc = target_concentration(
            px=px, age=age, mem=mem, listed=listed, glob=glob, h=h, y_primary=y, fx=fx, ctr=ctr, minute_ids=minute_ids, dev_sign=int(np.sign(dev_b))
        )
        rec["target_concentration"] = tconc
        rec["concentration"] = {"driver": dconc, "target": tconc}
        if not tconc.get("ok"):
            failed = "TARGET_CONCENTRATION"
    if failed is None:
        cf = common_factor(y=y, fx=fx, ctr=ctr, minute_ids=minute_ids, glob=glob, dev_sign=int(np.sign(dev_b)))
        rec["common_factor"] = cf
        if not cf.get("ok"):
            failed = "COMMON_FACTOR"
    rec["offset_pass"] = bool(offset.get("ok"))
    rec["shuffle_pass"] = bool(sh.get("ok")) if sh else False
    rec["identity_pass"] = bool(ident.get("ok")) if ident else False
    rec["sector_identity_pass"] = rec["identity_pass"]
    rec["driver_concentration_pass"] = bool(dconc.get("ok")) if dconc else False
    rec["target_concentration_pass"] = bool(tconc.get("ok")) if tconc else False
    rec["concentration_pass"] = bool(rec["driver_concentration_pass"] and rec["target_concentration_pass"])
    rec["common_factor_pass"] = bool(cf.get("ok")) if cf else False
    rec["final_pass"] = failed is None
    rec["failed_at"] = failed
    return rec


def _offset_map(*, px, age, listed, mem, glob, metric, w, y, ctr, minute_ids, c1_b):
    offset_b: dict[int, float | None] = {}
    for k in OFFSETS:
        fxk = driver_offset(px=px, age=age, listed=listed, mem=mem, w=int(w), k=int(k), global_scope=glob, metric=str(metric))
        offset_b[int(k)] = point_b_fx(y, fxk.reshape(-1), ctr, minute_ids)
    b0 = offset_b.get(0)
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


def identity_gate(*, y, ctr, minute_ids, glob, metric, w, sid, drivers, scopes, actual_beta):
    if glob:
        return {"ok": True, "label": "SECTOR_IDENTITY_SPECIFICITY_GATE", "result": "NOT_APPLICABLE"}
    alts = []
    for sc in scopes:
        other = str(sc["scope_id"])
        if other == sid or other == "GLOBAL_105":
            continue
        b = point_b_fx(y, drivers["by_scope"][other][metric][int(w)].reshape(-1), ctr, minute_ids)
        alts.append({"scope_id": other, "beta": None if b is None else float(b)})
    abs_alts = [abs(a["beta"]) for a in alts if a["beta"] is not None]
    mx = max(abs_alts) if abs_alts else None
    ok = bool(mx is not None and abs(float(actual_beta)) > mx)
    return {
        "ok": ok,
        "label": "SECTOR_IDENTITY_SPECIFICITY_GATE",
        "not_p_value": True,
        "actual_abs": abs(float(actual_beta)),
        "max_alt_abs": mx,
        "alt_n": len(alts),
        "alts": alts,
        "ties_fail": True,
    }


def driver_concentration(*, ret, mem, listed, glob, metric, y, ctr, minute_ids, fx_full, dev_sign, symbols):
    idx = np.where(mem)[0]
    full = np.asarray(fx_full, dtype=np.float64)
    infl = []
    same = 0
    n_const = int(idx.size)
    loo_b = []
    for j in idx:
        loo = driver_loo(ret=ret, mem=mem, listed=listed, drop_i=int(j), global_scope=glob, metric=str(metric))
        b = point_b_fx(y, loo.reshape(-1), ctr, minute_ids)
        loo_b.append(b)
        if b is not None and int(np.sign(b)) == int(dev_sign):
            same += 1
        delta = np.nanmean(np.abs(full - loo)) if np.isfinite(full).any() else -1.0
        infl.append(float(delta) if np.isfinite(delta) else -1.0)
    infl_arr = np.array(infl, dtype=np.float64)
    order = idx[np.argsort(-infl_arr)]
    if glob:
        drop1 = {int(order[0])} if n_const else set()
        drop5 = set(int(x) for x in order[: min(5, n_const)])
        mem1 = mem.copy()
        for j in drop1:
            mem1[j] = False
        mem5 = mem.copy()
        for j in drop5:
            mem5[j] = False
        d1 = driver_from_ret(ret=ret, mem=mem1, listed=listed, global_scope=True)[str(metric)]
        d5 = driver_from_ret(ret=ret, mem=mem5, listed=listed, global_scope=True)[str(metric)]
        b1 = point_b_fx(y, d1.reshape(-1), ctr, minute_ids)
        b5 = point_b_fx(y, d5.reshape(-1), ctr, minute_ids)
        ok1 = bool(b1 is not None and int(np.sign(b1)) == int(dev_sign))
        ok5 = bool(b5 is not None and int(np.sign(b5)) == int(dev_sign))
        return {
            "ok": bool(ok1 and ok5),
            "kind": "global",
            "drop_top1_same_sign": ok1,
            "drop_top5_same_sign": ok5,
            "top1_symbol": symbols[int(order[0])] if n_const else None,
        }
    frac = same / float(max(n_const, 1))
    top = int(order[0]) if n_const else None
    mem_top = mem.copy()
    if top is not None:
        mem_top[top] = False
    dtop = driver_from_ret(ret=ret, mem=mem_top, listed=listed, global_scope=False)[str(metric)]
    b_top = point_b_fx(y, dtop.reshape(-1), ctr, minute_ids)
    top_ok = bool(b_top is not None and int(np.sign(b_top)) == int(dev_sign))
    return {
        "ok": bool(frac >= 0.80 and top_ok),
        "kind": "sector",
        "loo_same_sign_frac": float(frac),
        "remove_most_influential_same_sign": top_ok,
        "most_influential_symbol": None if top is None else symbols[int(top)],
        "n_constituent": n_const,
    }


def target_concentration(*, px, age, mem, listed, glob, h, y_primary, fx, ctr, minute_ids, dev_sign):
    n_s = px.shape[0]
    n_d = px.shape[1]
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
        tmp[ok] = LOG_BPS * np.log(p1[ok] / p0[ok])
        r_sym[:, :, ci] = tmp
    idx = np.where(mem)[0]
    if glob:
        contrib = np.array([np.nanmean(np.abs(r_sym[j])) if np.isfinite(r_sym[j]).any() else -1.0 for j in idx])
        order = idx[np.argsort(-contrib)]
        y1 = np.nanmean(r_sym[[j for j in idx if j != int(order[0])]], axis=0).reshape(-1)
        drop5 = set(int(x) for x in order[:5])
        y5 = np.nanmean(r_sym[[j for j in idx if j not in drop5]], axis=0).reshape(-1)
        b1 = point_b_fx(y1, fx, ctr, minute_ids)
        b5 = point_b_fx(y5, fx, ctr, minute_ids)
        ok1 = bool(b1 is not None and int(np.sign(b1)) == int(dev_sign))
        ok5 = bool(b5 is not None and int(np.sign(b5)) == int(dev_sign))
        return {"ok": bool(ok1 and ok5), "kind": "global", "drop_top1_same_sign": ok1, "drop_top5_same_sign": ok5}
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
    return {"ok": bool(loso >= 0.80 and top_ok), "kind": "sector", "loo_same_sign_frac": float(loso), "remove_top_same_sign": top_ok}


def common_factor(*, y, fx, ctr, minute_ids, glob, dev_sign):
    if glob:
        return {"ok": True, "result": "NOT_APPLICABLE"}
    b_with = point_b_fx(y, fx, ctr, minute_ids)
    b_wo = point_b_fx(y, fx, ctr[:1], minute_ids)
    if b_with is None or b_wo is None:
        return {"ok": False, "with_market": None if b_with is None else float(b_with), "without_market": None if b_wo is None else float(b_wo)}
    same = int(np.sign(b_with)) == int(np.sign(b_wo)) and int(np.sign(b_with)) == int(dev_sign) and int(np.sign(b_with)) != 0
    return {
        "ok": bool(same),
        "with_market": float(b_with),
        "without_market": float(b_wo),
        "sign_unchanged": bool(int(np.sign(b_with)) == int(np.sign(b_wo))),
        "cannot_rescue_primary": True,
    }
