"""DEV 384-family with V1.2 MKT_EX gate. No p=1 fill for unevaluable tests."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery.features import basket_future_and_lag
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import apply_d_gates, date_minute_ids, point_b_fx, q5_minus_q1, quintile_bounds
from research.causal_driver_pb1.sector_breadth_corrected_discovery.mkt_ex import mkt_ex_past_5m
from research.causal_driver_pb1.sector_breadth_discovery import (
    D7_ABS_RATIO,
    EXPECTED_FAMILY_N,
    FDR_Q,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_N,
    HORIZONS,
    LOOKBACKS,
    METRICS,
    SECTOR_MIN_FRAC,
    SECTOR_MIN_N,
    STRICT_TARGET_AGE_SEC,
    TARGET_AGE_SEC,
)
from research.causal_driver_pb1.sector_breadth_discovery.features import DRIVER_AGE_SEC, mkt_ex_members, precompute_drivers
from research.causal_driver_pb1.sector_breadth_discovery.infer import fit_frozen
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import bh_qvalues_canonical


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


def _min_n_frac(global_scope: bool) -> tuple[int, float]:
    if global_scope:
        return GLOBAL_MIN_N, GLOBAL_MIN_FRAC
    return SECTOR_MIN_N, SECTOR_MIN_FRAC


def is_evaluable(rec: dict[str, Any]) -> bool:
    if not rec.get("ok"):
        return False
    n = rec.get("n")
    if n is None or int(n) <= 0:
        return False
    for k in ("b_fx", "ci_lo", "ci_hi", "p_boot"):
        v = rec.get(k)
        if v is None or not np.isfinite(float(v)):
            return False
    return True


def unevaluable_reason(rec: dict[str, Any], mkt_meta: dict[str, Any] | None) -> str:
    n = int(rec.get("n") or 0)
    if n <= 0:
        meta = mkt_meta or {}
        if rec.get("global"):
            return "DATA_MISSING" if int(np.isfinite(rec.get("n") or 0)) == 0 else "MODEL_BUILD_FAIL"
        if int(meta.get("finite_lag_n") or 0) == 0:
            return "CONTROL_GATE_FAIL"
        return "MODEL_BUILD_FAIL"
    if rec.get("reason") == "nonfinite_replicate":
        return "MODEL_BUILD_FAIL"
    if rec.get("reason") == "point_fail":
        return "MODEL_BUILD_FAIL"
    return "OTHER"


def run_dev_family(
    *,
    px: np.ndarray,
    age: np.ndarray,
    symbols: list[str],
    dates: list[str],
    listed: np.ndarray,
    scopes: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    sector_of: dict[str, str],
    folds: dict[str, Any],
    boot_index: np.ndarray,
) -> dict[str, Any]:
    n_d = len(dates)
    if len(tests) != EXPECTED_FAMILY_N:
        raise RuntimeError(f"family_n_{len(tests)}")
    date_ids, minute_ids = date_minute_ids(n_d)
    mod5 = np.tile(np.array([int(t) % 5 for t in CLOCK_MINS], dtype=np.int32), n_d)
    months = [d[:6] for d in dates]
    month_u = sorted(set(months))
    month_id = np.repeat(np.array([month_u.index(m) for m in months], dtype=np.int32), N_CLOCK)
    early_set = set(folds.get("DEV_EARLY") or [])
    late_set = set(folds.get("DEV_LATE") or [])
    m_early = _date_mask(dates, early_set)
    m_late = _date_mask(dates, late_set)
    print("DEV_DRIVERS", flush=True)
    drivers = precompute_drivers(px=px, age=age, listed=listed, scopes=scopes, symbols=symbols, sector_of=sector_of)
    mems = drivers["mems"]
    print("DEV_BASKETS", flush=True)
    y_cache: dict[tuple[int, str, float], np.ndarray] = {}
    lag_cache: dict[tuple[str, float], np.ndarray] = {}
    mkt_lag: dict[str, np.ndarray] = {}
    mkt_meta: dict[str, dict[str, Any]] = {}
    for sc in scopes:
        sid = sc["scope_id"]
        glob = sid == "GLOBAL_105"
        mem = mems[sid]
        min_n, min_frac = _min_n_frac(glob)
        lag_pack = basket_future_and_lag(
            px=px, age=age, members=mem, listed=listed, horizon=1, max_age=DRIVER_AGE_SEC, min_n=min_n, min_frac=min_frac
        )
        lag_cache[(sid, DRIVER_AGE_SEC)] = lag_pack["lag"]
        if not glob:
            mex = mkt_ex_members(symbols=symbols, sector_of=sector_of, sector_id=str(sc["sector_id"]))
            mpack = mkt_ex_past_5m(px=px, age=age, members=mex, listed=listed, max_age=DRIVER_AGE_SEC)
            mkt_lag[sid] = mpack["lag"]
            mkt_meta[sid] = {k: v for k, v in mpack.items() if k != "lag"}
        for h in HORIZONS:
            fut = basket_future_and_lag(
                px=px, age=age, members=mem, listed=listed, horizon=int(h), max_age=TARGET_AGE_SEC, min_n=min_n, min_frac=min_frac
            )
            y_cache[(int(h), sid, TARGET_AGE_SEC)] = fut["y"]
    qbounds: dict[tuple[str, str, int], dict[str, Any]] = {}
    for sc in scopes:
        for metric in METRICS:
            for w in LOOKBACKS:
                fx = drivers["by_scope"][sc["scope_id"]][metric][int(w)]
                qb = quintile_bounds(fx.reshape(-1))
                qb["metric"] = metric
                qb["scope_id"] = sc["scope_id"]
                qb["lookback"] = int(w)
                qbounds[(metric, sc["scope_id"], int(w))] = qb
    family: list[dict[str, Any]] = []
    print("DEV_FAMILY_384", flush=True)
    for k, t in enumerate(tests, start=1):
        metric = str(t["metric"])
        sid = str(t["scope_id"])
        w = int(t["lookback"])
        h = int(t["horizon"])
        glob = sid == "GLOBAL_105"
        y = y_cache[(h, sid, TARGET_AGE_SEC)].reshape(-1)
        self_lag = lag_cache[(sid, DRIVER_AGE_SEC)].reshape(-1)
        ctr = [self_lag] if glob else [self_lag, mkt_lag[sid].reshape(-1)]
        fx = drivers["by_scope"][sid][metric][w].reshape(-1)
        pooled = fit_frozen(y, fx, ctr, date_ids, minute_ids, n_d, boot_index)
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
        qb = qbounds[(metric, sid, w)]
        q51 = q5_minus_q1(y, fx, qb) if pooled.get("ok") else None
        rec = {
            "test_id": t.get("test_id"),
            "metric": metric,
            "scope_id": sid,
            "mechanism": "GLOBAL_BREADTH_DISPERSION" if glob else "SECTOR_BREADTH_DISPERSION",
            "sector_id": None if glob else sid.replace("SECTOR_", ""),
            "lookback": w,
            "horizon": h,
            "global": glob,
            "used_correction_branch": False if glob else bool((mkt_meta.get(sid) or {}).get("used_correction_branch")),
            "y_finite_n": int(np.isfinite(y).sum()),
            "fx_finite_n": int(np.isfinite(fx).sum()),
            "mkt_lag_finite_n": None if glob else int(np.isfinite(mkt_lag[sid]).sum()),
            **pooled,
            "quintile_boundaries": qb,
        }
        family.append(apply_d_gates(rec=rec, early=early, late=late, q51=q51, sub_signs=sub_signs, lomo_frac=lomo, qval=1.0))
        if k % 32 == 0:
            print(f"DEV_TEST {k}/384", flush=True)
    if len(family) != EXPECTED_FAMILY_N:
        raise RuntimeError(f"family_n_{len(family)}")
    bad = []
    for rec in family:
        if is_evaluable(rec):
            continue
        bad.append(
            {
                "test_id": rec.get("test_id"),
                "metric": rec.get("metric"),
                "scope_id": rec.get("scope_id"),
                "lookback": rec.get("lookback"),
                "horizon": rec.get("horizon"),
                "model_n": rec.get("n"),
                "ok": rec.get("ok"),
                "reason": rec.get("reason"),
                "unevaluable_class": unevaluable_reason(rec, mkt_meta.get(str(rec.get("scope_id") or ""))),
                "y_finite_n": rec.get("y_finite_n"),
                "fx_finite_n": rec.get("fx_finite_n"),
                "mkt_lag_finite_n": rec.get("mkt_lag_finite_n"),
            }
        )
    if bad:
        return {
            "family": family,
            "n": len(family),
            "all_evaluable": False,
            "unevaluable": bad,
            "unevaluable_n": len(bad),
            "drivers": drivers,
            "mkt_meta": {k: {kk: vv for kk, vv in v.items() if kk != "n_listed"} for k, v in mkt_meta.items()},
            "correction_scopes": sorted(k for k, v in mkt_meta.items() if v.get("used_correction_branch")),
        }
    p = np.array([float(r["p_boot"]) for r in family], dtype=np.float64)
    qv = bh_qvalues_canonical(p)
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
    d16 = [r for r in out if all(r.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6"))]
    print(f"DEV_D16_PASS {len(d16)}", flush=True)
    if d16:
        print("DEV_D7_STRICT60", flush=True)
        out = apply_d7(
            rows=out,
            px=px,
            age=age,
            listed=listed,
            symbols=symbols,
            sector_of=sector_of,
            date_ids=date_ids,
            minute_ids=minute_ids,
            n_d=n_d,
            drivers=drivers,
            boot_index=boot_index,
            mems=mems,
            mkt_lag=mkt_lag,
        )
    for rec in out:
        rec["failed_at"] = _first_fail(rec)
        rec["candidate"] = bool(all(rec.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7")))
    counts = Counter(r.get("failed_at") or "PASS" for r in out)
    return {
        "family": out,
        "n": len(out),
        "all_evaluable": True,
        "unevaluable": [],
        "unevaluable_n": 0,
        "d16_n": int(sum(1 for r in out if all(r.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6")))),
        "candidate_n": int(sum(1 for r in out if r.get("candidate"))),
        "first_fail_counts": dict(counts),
        "drivers": drivers,
        "p_boot": p.tolist(),
        "q_boot": [float(x) for x in qv],
        "bh_q_min": float(np.min(qv)),
        "mkt_meta": {k: {kk: vv for kk, vv in v.items() if not isinstance(vv, np.ndarray)} for k, v in mkt_meta.items()},
        "correction_scopes": sorted(k for k, v in mkt_meta.items() if v.get("used_correction_branch")),
    }


def apply_d7(
    *,
    rows: list[dict[str, Any]],
    px,
    age,
    listed,
    symbols,
    sector_of,
    date_ids,
    minute_ids,
    n_d,
    drivers,
    boot_index,
    mems,
    mkt_lag,
) -> list[dict[str, Any]]:
    needed = {(int(r["horizon"]), r["scope_id"]) for r in rows if all(r.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6"))}
    y60: dict[tuple[int, str], np.ndarray] = {}
    lag60: dict[str, np.ndarray] = {}
    sids = {sid for _, sid in needed}
    for sid in sids:
        glob = sid == "GLOBAL_105"
        mem = mems[sid]
        min_n, min_frac = _min_n_frac(glob)
        lag_pack = basket_future_and_lag(
            px=px, age=age, members=mem, listed=listed, horizon=1, max_age=DRIVER_AGE_SEC, min_n=min_n, min_frac=min_frac
        )
        lag60[sid] = lag_pack["lag"]
    for h, sid in needed:
        glob = sid == "GLOBAL_105"
        mem = mems[sid]
        min_n, min_frac = _min_n_frac(glob)
        fut = basket_future_and_lag(
            px=px, age=age, members=mem, listed=listed, horizon=int(h), max_age=STRICT_TARGET_AGE_SEC, min_n=min_n, min_frac=min_frac
        )
        y60[(int(h), sid)] = fut["y"]
    out = []
    for rec in rows:
        rec = dict(rec)
        if not all(rec.get(k) for k in ("D1", "D2", "D3", "D4", "D5", "D6")):
            rec["D7"] = False
            rec["D7_reason"] = "D1_D6_NOT_PASSED"
            out.append(rec)
            continue
        sid = rec["scope_id"]
        glob = bool(rec["global"])
        h = int(rec["horizon"])
        w = int(rec["lookback"])
        metric = rec["metric"]
        y = y60[(h, sid)].reshape(-1)
        self_lag = lag60[sid].reshape(-1)
        ctr = [self_lag] if glob else [self_lag, mkt_lag[sid].reshape(-1)]
        fx = drivers["by_scope"][sid][metric][w].reshape(-1)
        fit = fit_frozen(y, fx, ctr, date_ids, minute_ids, n_d, boot_index)
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
