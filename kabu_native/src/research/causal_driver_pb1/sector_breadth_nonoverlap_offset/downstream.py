"""Frozen downstream gates. Run only for corrected-offset passers. No gate redesign."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery.confirm import driver_date_map, merge_leader_fx_maps, shuffle_clock
from research.causal_driver_pb1.cross_sectional_discovery.features import basket_future_and_lag, build_am, listed_mask
from research.causal_driver_pb1.cross_sectional_discovery.panels import load_equity_stage
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.phase2_discovery.clock import N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import date_minute_ids, point_b_fx
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.sector_breadth_corrected_discovery.confirm import (
    _controls,
    _min_n_frac,
    _obs_pack,
    common_factor,
    driver_concentration,
    identity_gate,
    target_concentration,
)
from research.causal_driver_pb1.sector_breadth_corrected_discovery.mkt_ex import mkt_ex_past_5m
from research.causal_driver_pb1.sector_breadth_discovery import DRIVER_AGE_SEC, EXPECTED_PERMUTATION_SHA256, TARGET_AGE_SEC
from research.causal_driver_pb1.sector_breadth_discovery.features import listing_from_px, merge_listing, mkt_ex_members, precompute_drivers
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import EXPECTED_PERMUTATION_SHA256 as RUN_PERM_SHA
from research.causal_driver_pb1.sector_breadth_precommit.family import build_scopes
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import CANDIDATE_LIST_SHA256, EXPECTED_DEV_CANDIDATE_N


def _not_run(test_id: str) -> dict[str, Any]:
    return {"test_id": test_id, "status": "NOT_RUN"}


def run_downstream(
    *,
    passers: list[dict[str, Any]],
    records: list[dict[str, Any]],
    panel: dict[str, Any],
    c1_dates: list[str],
    development_dates: list[str],
    eligible_dates: list[str],
    c1_primary_b_fx: dict[str, Any],
) -> dict[str, Any]:
    if not passers:
        ids = [str(r.get("test_id")) for r in records]
        return {
            "ran": False,
            "blockers": [],
            "rows": [],
            "day_shuffle": [_not_run(t) for t in ids],
            "sector_identity": [_not_run(t) for t in ids],
            "driver_concentration": [_not_run(t) for t in ids],
            "target_concentration": [_not_run(t) for t in ids],
            "common_factor": [_not_run(t) for t in ids],
        }
    sectors = bind_sector_mapping()
    scopes = build_scopes(list(sectors.get("eligible_sectors") or []))
    symbols = tuple(panel["symbols"])
    sector_of = dict(panel["sector_of"])
    print("DOWNSTREAM_DEV_PANEL", flush=True)
    ledger = StageLedger()
    ledger.freeze_candidates(CANDIDATE_LIST_SHA256, EXPECTED_DEV_CANDIDATE_N)
    dev_stock = load_equity_stage(symbols=symbols, dates=list(development_dates), ledger=ledger, stage="STAGE_A")
    am_dev = build_am(close=dev_stock["close"])
    listing_dev = listing_from_px(px=am_dev["px"], symbols=list(symbols), dates=list(development_dates))
    listing = merge_listing(listing_dev, dict(panel["listing"]))
    listed_c1 = listed_mask(symbols=list(symbols), dates=list(c1_dates), listing_start=listing)
    listed_dev = listed_mask(symbols=list(symbols), dates=list(development_dates), listing_start=listing)
    print("DOWNSTREAM_DRIVERS_C1", flush=True)
    drivers_c1 = precompute_drivers(
        px=panel["px"], age=panel["age"], listed=listed_c1, scopes=scopes, symbols=list(symbols), sector_of=sector_of
    )
    print("DOWNSTREAM_DRIVERS_DEV", flush=True)
    drivers_dev = precompute_drivers(
        px=am_dev["px"], age=am_dev["age"], listed=listed_dev, scopes=scopes, symbols=list(symbols), sector_of=sector_of
    )
    shuffle = generate_shuffle_permutations(list(eligible_dates), return_maps=True)
    if shuffle.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256 or shuffle.get("permutation_sha256") != RUN_PERM_SHA:
        return {
            "ran": False,
            "blockers": ["PERMUTATION_SHA_MISMATCH_AT_DOWNSTREAM"],
            "rows": [],
            "day_shuffle": [],
            "sector_identity": [],
            "driver_concentration": [],
            "target_concentration": [],
            "common_factor": [],
        }
    n_d = len(c1_dates)
    _, minute_ids = date_minute_ids(n_d)
    date_str = np.repeat(np.array(c1_dates, dtype=object), N_CLOCK)
    clock_idx = np.tile(np.arange(N_CLOCK, dtype=np.int32), n_d)
    maps = list(shuffle.get("maps") or [])
    unsh = set(shuffle.get("unshufflable") or [])
    by_id = {str(r.get("test_id")): r for r in records}
    passer_ids = {str(r.get("test_id")) for r in passers}
    lag_cache: dict[str, np.ndarray] = {}
    mkt_cache: dict[str, np.ndarray] = {}
    y_cache: dict[tuple[int, str], np.ndarray] = {}

    def basket(sid: str, h: int):
        glob = sid == "GLOBAL_105"
        mem = drivers_c1["mems"][sid]
        min_n, min_frac = _min_n_frac(glob)
        if sid not in lag_cache:
            lag_pack = basket_future_and_lag(
                px=panel["px"], age=panel["age"], members=mem, listed=listed_c1, horizon=1, max_age=DRIVER_AGE_SEC, min_n=min_n, min_frac=min_frac
            )
            lag_cache[sid] = lag_pack["lag"]
            if not glob:
                mex = mkt_ex_members(symbols=list(symbols), sector_of=sector_of, sector_id=sid.replace("SECTOR_", ""))
                mkt_cache[sid] = mkt_ex_past_5m(px=panel["px"], age=panel["age"], members=mex, listed=listed_c1, max_age=DRIVER_AGE_SEC)["lag"]
        if (int(h), sid) not in y_cache:
            fut = basket_future_and_lag(
                px=panel["px"],
                age=panel["age"],
                members=mem,
                listed=listed_c1,
                horizon=int(h),
                max_age=TARGET_AGE_SEC,
                min_n=min_n,
                min_frac=min_frac,
            )
            y_cache[(int(h), sid)] = fut["y"]
        return y_cache[(int(h), sid)], lag_cache[sid], None if glob else mkt_cache[sid]

    out_rows = []
    blockers: list[str] = []
    for rec in records:
        tid = str(rec.get("test_id"))
        if tid not in passer_ids:
            out_rows.append(
                {
                    "test_id": tid,
                    "offset_pass": False,
                    "shuffle": _not_run(tid),
                    "sector_identity": _not_run(tid),
                    "driver_concentration": _not_run(tid),
                    "target_concentration": _not_run(tid),
                    "common_factor": _not_run(tid),
                    "shuffle_pass": False,
                    "sector_identity_pass": False,
                    "driver_concentration_pass": False,
                    "target_concentration_pass": False,
                    "common_factor_pass": False,
                    "final_pass": False,
                    "failed_at": "OFFSET",
                }
            )
            continue
        base = by_id[tid]
        sid = str(base["scope_id"])
        glob = sid == "GLOBAL_105"
        w = int(base["lookback"])
        h = int(base["horizon"])
        metric = str(base["metric"])
        dev_b = float(base["DEV_beta_primary"])
        dev_sign = int(np.sign(dev_b))
        published = c1_primary_b_fx.get(tid)
        y, self_lag, mlag = basket(sid, h)
        ctr = _controls(sid=sid, glob=glob, lag_self=self_lag, mkt_lag={sid: mlag} if mlag is not None else {})
        yv = y.reshape(-1)
        fx = drivers_c1["by_scope"][sid][metric][w].reshape(-1)
        rebuilt = point_b_fx(yv, fx, ctr, minute_ids)
        if published is None or rebuilt is None or abs(float(rebuilt) - float(published)) > 1e-6:
            blockers.append(f"C1_PRIMARY_BETA_RECONSTRUCTION_MISMATCH:{tid}")
            out_rows.append({"test_id": tid, "failed_at": "C1_PRIMARY_BETA_RECONSTRUCTION_MISMATCH", "final_pass": False})
            continue
        real_abs = abs(float(published))
        fx_map = merge_leader_fx_maps(
            driver_date_map(drivers_dev["by_scope"][sid][metric][w], list(development_dates)),
            driver_date_map(drivers_c1["by_scope"][sid][metric][w], list(c1_dates)),
        )
        controls = np.column_stack(ctr) if ctr else np.zeros((yv.shape[0], 0))
        mask = _obs_pack(yv, fx, ctr)
        print(f"DAY_SHUFFLE {tid}", flush=True)
        sh = shuffle_clock(
            y=yv[mask],
            fx_by_date=fx_map,
            date_str=date_str[mask],
            clock_idx=clock_idx[mask],
            controls=controls[mask] if controls.size else controls,
            minute_ids=minute_ids[mask],
            maps=maps,
            unshufflable=unsh,
            real_abs=real_abs,
        )
        sh = {**sh, "test_id": tid, "status": "RUN", "permutation_sha256": EXPECTED_PERMUTATION_SHA256}
        failed = None if sh.get("ok") else "SHUFFLE"
        ident = _not_run(tid)
        dconc = _not_run(tid)
        tconc = _not_run(tid)
        cf = _not_run(tid)
        if failed is None:
            ident = identity_gate(
                y=yv,
                ctr=ctr,
                minute_ids=minute_ids,
                glob=glob,
                metric=metric,
                w=w,
                sid=sid,
                drivers=drivers_c1,
                scopes=scopes,
                actual_beta=float(published),
            )
            ident = {**ident, "test_id": tid, "status": "RUN"}
            if not ident.get("ok"):
                failed = "IDENTITY"
        if failed is None:
            dconc = driver_concentration(
                ret=drivers_c1["rets"][int(w)],
                mem=drivers_c1["mems"][sid],
                listed=listed_c1,
                glob=glob,
                metric=metric,
                y=yv,
                ctr=ctr,
                minute_ids=minute_ids,
                fx_full=drivers_c1["by_scope"][sid][metric][int(w)],
                dev_sign=dev_sign,
                symbols=list(symbols),
            )
            dconc = {**dconc, "test_id": tid, "status": "RUN"}
            if not dconc.get("ok"):
                failed = "DRIVER_CONCENTRATION"
        if failed is None:
            tconc = target_concentration(
                px=panel["px"],
                age=panel["age"],
                mem=drivers_c1["mems"][sid],
                listed=listed_c1,
                glob=glob,
                h=h,
                y_primary=yv,
                fx=fx,
                ctr=ctr,
                minute_ids=minute_ids,
                dev_sign=dev_sign,
            )
            tconc = {**tconc, "test_id": tid, "status": "RUN"}
            if not tconc.get("ok"):
                failed = "TARGET_CONCENTRATION"
        if failed is None:
            cf = common_factor(y=yv, fx=fx, ctr=ctr, minute_ids=minute_ids, glob=glob, dev_sign=dev_sign)
            cf = {**cf, "test_id": tid, "status": "RUN"}
            if not cf.get("ok"):
                failed = "COMMON_FACTOR"
        out_rows.append(
            {
                "test_id": tid,
                "offset_pass": True,
                "shuffle": sh,
                "sector_identity": ident,
                "driver_concentration": dconc,
                "target_concentration": tconc,
                "common_factor": cf,
                "shuffle_pass": bool(sh.get("ok")),
                "sector_identity_pass": bool(isinstance(ident, dict) and ident.get("ok")),
                "driver_concentration_pass": bool(isinstance(dconc, dict) and dconc.get("ok")),
                "target_concentration_pass": bool(isinstance(tconc, dict) and tconc.get("ok")),
                "common_factor_pass": bool(isinstance(cf, dict) and cf.get("ok")),
                "final_pass": failed is None,
                "failed_at": failed,
                "published_c1_b_fx": float(published),
                "rebuilt_primary_b_fx": float(rebuilt),
            }
        )
    return {
        "ran": not blockers,
        "blockers": blockers,
        "rows": out_rows,
        "day_shuffle": [r.get("shuffle") for r in out_rows],
        "sector_identity": [r.get("sector_identity") for r in out_rows],
        "driver_concentration": [r.get("driver_concentration") for r in out_rows],
        "target_concentration": [r.get("target_concentration") for r in out_rows],
        "common_factor": [r.get("common_factor") for r in out_rows],
    }
