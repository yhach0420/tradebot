"""C1 common offset sample membership. Finite y/driver/controls only. No OLS, no betas."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery.features import basket_future_and_lag, build_am, listed_mask
from research.causal_driver_pb1.cross_sectional_discovery.panels import load_equity_stage
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, min_to_hhmm
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.sector_breadth_corrected_discovery.mkt_ex import mkt_ex_past_5m
from research.causal_driver_pb1.sector_breadth_discovery import (
    DRIVER_AGE_SEC,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_N,
    SECTOR_MIN_FRAC,
    SECTOR_MIN_N,
    TARGET_AGE_SEC,
)
from research.causal_driver_pb1.sector_breadth_discovery.features import (
    clock_ok_for_horizon,
    constituent_ret_offset,
    driver_from_ret,
    listing_from_px,
    mkt_ex_members,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import CANDIDATE_LIST_SHA256, EXPECTED_DEV_CANDIDATE_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import all_offsets, feasible_clock_mins


def _min_n_frac(global_scope: bool) -> tuple[int, float]:
    if global_scope:
        return GLOBAL_MIN_N, GLOBAL_MIN_FRAC
    return SECTOR_MIN_N, SECTOR_MIN_FRAC


def _finite_pack(y: np.ndarray, fx_list: list[np.ndarray], ctr: list[np.ndarray]) -> np.ndarray:
    mask = np.isfinite(y)
    for fx in fx_list:
        mask = mask & np.isfinite(fx)
    for c in ctr:
        mask = mask & np.isfinite(c)
    return mask


def build_common_samples(*, records: list[dict[str, Any]], c1_dates: list[str], return_fit: bool = False) -> dict[str, Any]:
    sectors = bind_sector_mapping()
    symbols = tuple(str(r["symbol"]) for r in (sectors.get("rows") or []))
    sector_of = {str(r["symbol"]): str(r["sector_id"]) for r in (sectors.get("rows") or [])}
    ledger = StageLedger()
    ledger.freeze_candidates(CANDIDATE_LIST_SHA256, EXPECTED_DEV_CANDIDATE_N)
    ledger.enter_stage_b()
    stock = load_equity_stage(symbols=symbols, dates=list(c1_dates), ledger=ledger, stage="STAGE_B")
    am = build_am(close=stock["close"])
    listing = listing_from_px(px=am["px"], symbols=list(symbols), dates=list(c1_dates))
    listed = listed_mask(symbols=list(symbols), dates=list(c1_dates), listing_start=listing)
    dates = list(c1_dates)
    n_d = len(dates)
    ret_cache: dict[tuple[int, int], np.ndarray] = {}
    rows_out: list[dict[str, Any]] = []
    blockers: list[str] = []
    any_beta = False
    for rec in records:
        sid = str(rec["scope_id"])
        glob = bool(rec.get("global") or sid == "GLOBAL_105")
        w = int(rec["lookback"])
        h = int(rec["horizon"])
        metric = str(rec["metric"])
        mem = np.array(
            [True] * len(symbols) if glob else [sector_of.get(s) == str(rec.get("sector_id")) for s in symbols],
            dtype=np.bool_,
        )
        min_n, min_frac = _min_n_frac(glob)
        fut = basket_future_and_lag(
            px=am["px"], age=am["age"], members=mem, listed=listed, horizon=h, max_age=TARGET_AGE_SEC, min_n=min_n, min_frac=min_frac
        )
        lag = basket_future_and_lag(
            px=am["px"], age=am["age"], members=mem, listed=listed, horizon=1, max_age=DRIVER_AGE_SEC, min_n=min_n, min_frac=min_frac
        )
        y = fut["y"]
        self_lag = lag["lag"]
        if glob:
            ctr = [self_lag]
        else:
            mex = mkt_ex_members(symbols=list(symbols), sector_of=sector_of, sector_id=str(rec.get("sector_id")))
            mlag = mkt_ex_past_5m(px=am["px"], age=am["age"], members=mex, listed=listed, max_age=DRIVER_AGE_SEC)["lag"]
            ctr = [self_lag, mlag]
        ks = all_offsets(lookback=w, horizon=h)
        fx_list = []
        for k in ks:
            key = (int(w), int(k))
            if key not in ret_cache:
                ret_cache[key] = constituent_ret_offset(px=am["px"], age=am["age"], w=int(w), k=int(k))
            st = driver_from_ret(ret=ret_cache[key], mem=mem, listed=listed, global_scope=glob)
            fx_list.append(st[str(metric)])
        feas = set(feasible_clock_mins(lookback=w, horizon=h))
        clock_ok = np.array(
            [int(t) in feas and clock_ok_for_horizon(int(t), int(h)) for t in CLOCK_MINS],
            dtype=np.bool_,
        )
        pack = _finite_pack(y.reshape(-1), [f.reshape(-1) for f in fx_list], [c.reshape(-1) for c in ctr])
        pack = pack.reshape(n_d, N_CLOCK) & clock_ok[None, :]
        pairs = []
        date_ok = set()
        for di in range(n_d):
            for ci in range(N_CLOCK):
                if not bool(pack[di, ci]):
                    continue
                pairs.append({"date": dates[di], "t": min_to_hhmm(int(CLOCK_MINS[ci]))})
                date_ok.add(dates[di])
        digest = sha256_obj({"test_id": rec.get("test_id"), "pairs": pairs})
        if any(k in rec for k in ("b_fx", "offset_b", "beta_K1")):
            any_beta = True
        row = {
            "test_id": rec.get("test_id"),
            "metric": metric,
            "scope_id": sid,
            "lookback": w,
            "horizon": h,
            "offsets": list(ks),
            "offset_common_sample_n": len(pairs),
            "offset_common_date_n": len(date_ok),
            "offset_common_sample_sha256": digest,
            "session_feasible_clock_n": len(feas),
            "beta_computed": False,
        }
        if len(pairs) <= 0:
            blockers.append(f"COMMON_SAMPLE_EMPTY:{rec.get('test_id')}")
        if return_fit:
            row["_mask"] = pack
            row["_y"] = y
            row["_ctr"] = ctr
            row["_fx"] = {int(k): fx for k, fx in zip(ks, fx_list)}
        rows_out.append(row)
    access = ledger.snapshot()
    if int(access.get("C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE") or 0) != 0:
        blockers.append("C1_ROWS_BEFORE_FREEZE")
    if any_beta:
        blockers.append("BETA_LEAKAGE")
    out = {
        "pass": not blockers and all(int(r["offset_common_sample_n"]) > 0 for r in rows_out),
        "blockers": blockers,
        "rows": rows_out,
        "access": access,
        "beta_computed": False,
        "ols_not_run": True,
    }
    if return_fit:
        out["panel"] = {
            "px": am["px"],
            "age": am["age"],
            "listed": listed,
            "symbols": list(symbols),
            "sector_of": sector_of,
            "dates": dates,
            "listing": listing,
        }
    return out
