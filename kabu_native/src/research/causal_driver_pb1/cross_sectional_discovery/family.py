"""DEV 144-family OLS, BH FDR, D1–D7. No C1. No extra search."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery import (
    D7_ABS_RATIO,
    EXPECTED_FAMILY_N,
    FDR_Q,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_N,
    HORIZONS,
    LOOKBACKS,
    MKT_MIN_FRAC,
    MKT_MIN_N,
    PEER_MIN_FRAC,
    PEER_MIN_N,
    STRICT_TARGET_AGE_SEC,
    TARGET_AGE_SEC,
)
from research.causal_driver_pb1.cross_sectional_discovery.features import (
    basket_future_and_lag,
    precompute_drivers,
    scope_members,
)
from research.causal_driver_pb1.phase2_discovery.clock import N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import (
    apply_d_gates,
    bh_qvalues,
    date_minute_ids,
    fit_sample,
    point_b_fx,
    q5_minus_q1,
    quintile_bounds,
)


def _date_mask(dates: list[str], wanted: set[str]) -> np.ndarray:
    m = np.array([d in wanted for d in dates], dtype=np.bool_)
    return np.repeat(m, N_CLOCK)


def _subset_point(y, fx, ctr, minute_ids, mask) -> float | None:
    if int(mask.sum()) < 40:
        return None
    return point_b_fx(y[mask], fx[mask], [c[mask] for c in ctr], minute_ids[mask])


def _first_fail(rec: dict[str, Any]) -> str | None:
    for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
        if not rec.get(k):
            return k
    return None


def run_dev_family(
    *,
    px: np.ndarray,
    age: np.ndarray,
    symbols: list[str],
    dates: list[str],
    listed: np.ndarray,
    frozen_leaders: list[dict[str, Any]],
    scopes: list[dict[str, Any]],
    sector_of: dict[str, str],
    folds: dict[str, Any],
    target_age: float = TARGET_AGE_SEC,
) -> dict[str, Any]:
    n_d = len(dates)
    from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS

    date_ids, minute_ids = date_minute_ids(n_d)
    mod5 = np.tile(np.array([int(t) % 5 for t in CLOCK_MINS], dtype=np.int32), n_d)
    months = [d[:6] for d in dates]
    month_u = sorted(set(months))
    month_id = np.repeat(np.array([month_u.index(m) for m in months], dtype=np.int32), N_CLOCK)
    early_set = set(folds.get("DEV_EARLY") or [])
    late_set = set(folds.get("DEV_LATE") or [])
    m_early = _date_mask(dates, early_set)
    m_late = _date_mask(dates, late_set)
    leader_set = {r["leader_symbol"] for r in frozen_leaders}
    drivers = precompute_drivers(px=px, age=age, symbols=symbols, frozen_leaders=frozen_leaders)
    mkt_mem = scope_members(symbols=symbols, sector_of=sector_of, leader_set=leader_set, sector_id=None)
    glob_mem = mkt_mem
    print("DEV_BASKETS", flush=True)
    baskets: dict[tuple[int, str], dict[str, np.ndarray]] = {}
    mkt_lag: dict[int, np.ndarray] = {}
    for h in HORIZONS:
        mkt = basket_future_and_lag(
            px=px,
            age=age,
            members=mkt_mem,
            listed=listed,
            horizon=int(h),
            max_age=target_age,
            min_n=MKT_MIN_N,
            min_frac=MKT_MIN_FRAC,
        )
        mkt_lag[int(h)] = mkt["lag"]
        for sc in scopes:
            if sc["global"]:
                mem = glob_mem
                min_n, min_frac = GLOBAL_MIN_N, GLOBAL_MIN_FRAC
            else:
                mem = scope_members(symbols=symbols, sector_of=sector_of, leader_set=leader_set, sector_id=sc["sector_id"])
                min_n, min_frac = PEER_MIN_N, PEER_MIN_FRAC
            baskets[(int(h), sc["scope_id"])] = basket_future_and_lag(
                px=px,
                age=age,
                members=mem,
                listed=listed,
                horizon=int(h),
                max_age=target_age,
                min_n=min_n,
                min_frac=min_frac,
            )
    qbounds: dict[tuple[str, int], dict[str, Any]] = {}
    for sc in scopes:
        for w in LOOKBACKS:
            fx = drivers["global"][int(w)] if sc["global"] else drivers["by_leader"][sc["leader_symbol"]][int(w)]
            qbounds[(sc["scope_id"], int(w))] = quintile_bounds(fx.reshape(-1))
            qbounds[(sc["scope_id"], int(w))]["lookback"] = int(w)
            qbounds[(sc["scope_id"], int(w))]["scope_id"] = sc["scope_id"]
    family: list[dict[str, Any]] = []
    print("DEV_FAMILY_144", flush=True)
    k = 0
    for h in HORIZONS:
        mlag = mkt_lag[int(h)].reshape(-1)
        for sc in scopes:
            pack = baskets[(int(h), sc["scope_id"])]
            y = pack["y"].reshape(-1)
            self_lag = pack["lag"].reshape(-1)
            ctr = [self_lag] if sc["global"] else [self_lag, mlag]
            for w in LOOKBACKS:
                fx = (
                    drivers["global"][int(w)].reshape(-1)
                    if sc["global"]
                    else drivers["by_leader"][sc["leader_symbol"]][int(w)].reshape(-1)
                )
                pooled = fit_sample(y, fx, ctr, date_ids, minute_ids, n_d)
                pooled.pop("beta", None)
                eb = _subset_point(y, fx, ctr, minute_ids, m_early)
                lb = _subset_point(y, fx, ctr, minute_ids, m_late)
                early = {"ok": eb is not None, "b_fx": eb}
                late = {"ok": lb is not None, "b_fx": lb}
                sub_signs = []
                for r in range(5):
                    pb = _subset_point(y, fx, ctr, minute_ids, mod5 == r)
                    sub_signs.append(0 if pb is None else int(np.sign(pb)))
                same_m = tot_m = 0
                for mi, _month in enumerate(month_u):
                    pb = _subset_point(y, fx, ctr, minute_ids, month_id != mi)
                    tot_m += 1
                    if pooled.get("ok") and pb is not None and int(np.sign(pb)) == int(np.sign(pooled["b_fx"])):
                        same_m += 1
                lomo = (same_m / tot_m) if tot_m else None
                qb = qbounds[(sc["scope_id"], int(w))]
                q51 = q5_minus_q1(y, fx, qb) if pooled.get("ok") else None
                rec = {
                    "scope_id": sc["scope_id"],
                    "mechanism": sc["mechanism"],
                    "sector_id": sc.get("sector_id"),
                    "leader_symbol": sc.get("leader_symbol"),
                    "lookback": int(w),
                    "horizon": int(h),
                    "global": bool(sc["global"]),
                    **pooled,
                    "quintile_boundaries": qb,
                    "n_fut_mean": float(np.nanmean(pack["n_fut"])) if pack["n_fut"].size else None,
                }
                family.append(
                    apply_d_gates(rec=rec, early=early, late=late, q51=q51, sub_signs=sub_signs, lomo_frac=lomo, qval=1.0)
                )
                k += 1
                if k % 16 == 0:
                    print(f"DEV_TEST {k}/144", flush=True)
    if len(family) != EXPECTED_FAMILY_N:
        raise RuntimeError(f"family_n_{len(family)}")
    p = np.array([float(r["p_boot"]) if r.get("ok") and r.get("p_boot") is not None else 1.0 for r in family], dtype=np.float64)
    qv = bh_qvalues(p)
    out = []
    for rec, q in zip(family, qv):
        rec = dict(rec)
        rec["q"] = float(q)
        rec["D3"] = bool(float(q) <= FDR_Q)
        rec["candidate"] = bool(rec.get("D1") and rec.get("D2") and rec.get("D3") and rec.get("D4") and rec.get("D5") and rec.get("D6"))
        rec["D7"] = False
        rec["beta_60"] = None
        rec["direction"] = None if rec.get("b_fx") is None else ("UP" if rec["b_fx"] > 0 else "DOWN" if rec["b_fx"] < 0 else "ZERO")
        out.append(rec)
    d16 = [r for r in out if r.get("candidate")]
    print(f"DEV_D16_PASS {len(d16)}", flush=True)
    if d16:
        print("DEV_D7_STRICT60", flush=True)
        out = apply_d7(
            rows=out,
            px=px,
            age=age,
            symbols=symbols,
            dates=dates,
            listed=listed,
            frozen_leaders=frozen_leaders,
            scopes=scopes,
            sector_of=sector_of,
            date_ids=date_ids,
            minute_ids=minute_ids,
            n_d=n_d,
            drivers=drivers,
            mkt_mem=mkt_mem,
        )
    for rec in out:
        rec["failed_at"] = _first_fail(rec)
        rec["candidate"] = bool(all(rec.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7")))
    counts = Counter(r.get("failed_at") or "PASS" for r in out)
    return {
        "family": out,
        "n": len(out),
        "d16_n": int(sum(1 for r in out if all(r.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6")))),
        "candidate_n": int(sum(1 for r in out if r.get("candidate"))),
        "first_fail_counts": dict(counts),
        "drivers": drivers,
        "qbounds": {f"{k[0]}|{k[1]}": v for k, v in qbounds.items()},
    }


def apply_d7(
    *,
    rows: list[dict[str, Any]],
    px,
    age,
    symbols,
    dates,
    listed,
    frozen_leaders,
    scopes,
    sector_of,
    date_ids,
    minute_ids,
    n_d,
    drivers,
    mkt_mem,
) -> list[dict[str, Any]]:
    leader_set = {r["leader_symbol"] for r in frozen_leaders}
    scope_map = {s["scope_id"]: s for s in scopes}
    cache: dict[tuple[int, str], dict[str, np.ndarray]] = {}
    mkt_lag: dict[int, np.ndarray] = {}
    needed = {(int(r["horizon"]), r["scope_id"]) for r in rows if all(r.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6"))}
    hs = sorted({h for h, _ in needed})
    for h in hs:
        mkt = basket_future_and_lag(
            px=px,
            age=age,
            members=mkt_mem,
            listed=listed,
            horizon=int(h),
            max_age=STRICT_TARGET_AGE_SEC,
            min_n=MKT_MIN_N,
            min_frac=MKT_MIN_FRAC,
        )
        mkt_lag[int(h)] = mkt["lag"]
    for h, sid in needed:
        sc = scope_map[sid]
        if sc["global"]:
            mem = mkt_mem
            min_n, min_frac = GLOBAL_MIN_N, GLOBAL_MIN_FRAC
        else:
            mem = scope_members(symbols=symbols, sector_of=sector_of, leader_set=leader_set, sector_id=sc["sector_id"])
            min_n, min_frac = PEER_MIN_N, PEER_MIN_FRAC
        cache[(int(h), sid)] = basket_future_and_lag(
            px=px,
            age=age,
            members=mem,
            listed=listed,
            horizon=int(h),
            max_age=STRICT_TARGET_AGE_SEC,
            min_n=min_n,
            min_frac=min_frac,
        )
    out = []
    for rec in rows:
        rec = dict(rec)
        if not all(rec.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6")):
            rec["D7"] = False
            rec["D7_reason"] = "D1_D6_NOT_PASSED"
            out.append(rec)
            continue
        sc = scope_map[rec["scope_id"]]
        h = int(rec["horizon"])
        w = int(rec["lookback"])
        pack = cache[(h, rec["scope_id"])]
        y = pack["y"].reshape(-1)
        self_lag = pack["lag"].reshape(-1)
        mlag = mkt_lag[h].reshape(-1)
        ctr = [self_lag] if sc["global"] else [self_lag, mlag]
        fx = drivers["global"][w].reshape(-1) if sc["global"] else drivers["by_leader"][sc["leader_symbol"]][w].reshape(-1)
        fit = fit_sample(y, fx, ctr, date_ids, minute_ids, n_d)
        fit.pop("beta", None)
        b60 = fit.get("b_fx")
        bpri = rec.get("b_fx")
        rec["beta_60"] = b60
        rec["ci_60"] = [fit.get("ci_lo"), fit.get("ci_hi")] if fit.get("ok") else None
        rec["n_60"] = fit.get("n")
        same = bool(b60 is not None and bpri is not None and np.sign(b60) == np.sign(bpri) and np.sign(bpri) != 0)
        ci_ok = bool(fit.get("ok") and fit.get("ci_excludes_0"))
        mag = bool(b60 is not None and bpri is not None and abs(float(b60)) >= D7_ABS_RATIO * abs(float(bpri)))
        rec["D7"] = bool(same and ci_ok and mag)
        rec["D7_sign"] = same
        rec["D7_ci_excludes_0"] = ci_ok
        rec["D7_abs_ratio_ok"] = mag
        out.append(rec)
    return out
