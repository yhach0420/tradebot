"""Input-only feasibility under the three peer-control regimes. No log returns."""
from __future__ import annotations

from math import ceil
from typing import Any

import numpy as np

from research.causal_driver_pb1 import FV_FIRST, FV_LAST
from research.causal_driver_pb1.cross_sectional_discovery.features import CLOCK_AM
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, hhmm_to_min
from research.causal_driver_pb1.phase2_precommit.eligibility import split_count_ordered
from research.causal_driver_pb1.sector_breadth_discovery import GLOBAL_MIN_FRAC, GLOBAL_MIN_N, HORIZON_LAST_T, SECTOR_MIN_FRAC, SECTOR_MIN_N
from research.causal_driver_pb1.sector_breadth_precommit import DAY_COVERAGE_MIN, GLOBAL_MIN_VALID
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import OLD_MKT_EX_MIN_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import required_ex_sector_valid_n
from research.causal_driver_pb1.sector_state_transmission_precommit import DISCOVERY_MIN_VALID_DATES, FV_MIN_INPUT_VALID_DATES
from research.causal_driver_pb1.sector_state_transmission_precommit.presence import fresh_age_le, load_presence
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import BLOCKED_SYMBOLS
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.regimes import REGIME_MULTI, peer_control_ok


def _family_eligible(fresh60: np.ndarray, listed: np.ndarray, dates: list[str]) -> list[str]:
    valid = listed[:, :, None] & fresh60
    n_valid = valid.sum(axis=0)
    n_listed = listed.sum(axis=0).astype(np.float64)
    minute_ok = (n_valid >= int(GLOBAL_MIN_VALID)) & (n_valid >= (float(GLOBAL_MIN_FRAC) * n_listed)[:, None])
    cov = minute_ok.mean(axis=1)
    return [dates[i] for i, c in enumerate(cov) if float(c) >= float(DAY_COVERAGE_MIN) and FV_FIRST <= dates[i] <= FV_LAST]


def _clocks(horizon: int) -> list[int]:
    last = hhmm_to_min(HORIZON_LAST_T[str(int(horizon))])
    return [i for i, t in enumerate(CLOCK_MINS) if int(t) <= last]


def prove_inputs(
    *,
    symbols: list[str],
    sector_of: dict[str, str],
    sector3650: list[str],
    family_rows: list[dict[str, Any]],
    discovery_dates: list[str],
    mechanisms: list[dict[str, Any]],
    assignment_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _ = sector3650
    print("TRANSMISSION_V11_PRESENCE_GRID", flush=True)
    packed = load_presence(symbols=symbols, discovery_dates=list(discovery_dates))
    present = packed["present"]
    dates = list(packed["dates"])
    pos = {d: i for i, d in enumerate(dates)}
    fresh60 = fresh_age_le(present, 1)
    fresh120 = fresh_age_le(present, 2)
    listing = packed["listing_start"]
    darr = np.array(dates)
    starts = np.array([str(listing.get(s) or "99999999") for s in symbols])
    listed = darr[None, :] >= starts[:, None]
    fv_eligible = _family_eligible(fresh60, listed, dates)
    if any(d < FV_FIRST or d > FV_LAST for d in fv_eligible):
        raise RuntimeError("fv_eligible_outside_window")
    fv_folds = split_count_ordered(fv_eligible, 3) if len(fv_eligible) >= 3 else [[], [], []]
    fold_payload = {
        "split_rule": "timestamp_sorted_date_count_equal_split_remainder_to_last_fold",
        "FV_EARLY": fv_folds[0],
        "FV_MIDDLE": fv_folds[1],
        "FV_LATE": fv_folds[2],
        "outcome_used": False,
    }
    fv_fold_sha = sha256_obj(fold_payload)
    sym_i = {s: i for i, s in enumerate(symbols)}
    sectors = sorted({sector_of[s] for s in symbols})
    members = {sid: np.array([sym_i[s] for s in symbols if sector_of[s] == sid], dtype=np.int32) for sid in sectors}
    m3650 = members["3650"]
    disc_ix = np.array([pos[d] for d in discovery_dates if d in pos], dtype=np.int32)
    fv_ix = np.array([pos[d] for d in fv_eligible if d in pos], dtype=np.int32)
    n_s = len(symbols)
    n_am = int(present.shape[2])
    req_lookup = np.array([required_ex_sector_valid_n(i) for i in range(n_s + 1)], dtype=np.int32)
    sector_thresh = np.array(
        [max(int(SECTOR_MIN_N), int(ceil(float(SECTOR_MIN_FRAC) * i))) for i in range(len(m3650) + 1)],
        dtype=np.int32,
    )
    by_mech: dict[str, np.ndarray] = {str(m["parent_mechanism_id"]): np.zeros((len(dates), n_s), dtype=np.bool_) for m in mechanisms}
    seen_wh: set[tuple[int, int]] = set()
    for mech in mechanisms:
        wh = (int(mech["lookback"]), int(mech["horizon"]))
        if wh in seen_wh:
            continue
        seen_wh.add(wh)
        w, h = wh
        users = [m for m in mechanisms if int(m["lookback"]) == w and int(m["horizon"]) == h]
        for ci in _clocks(h):
            ai = int(CLOCK_AM[ci])
            a0 = ai - w
            ap = ai - 5
            ah = ai + h
            if a0 < 0 or ap < 0 or ah >= n_am:
                continue
            listed_i = listed.astype(np.int16)
            driver = listed & fresh60[:, :, ai] & fresh60[:, :, a0]
            past = listed & fresh60[:, :, ai] & fresh60[:, :, ap]
            y_ok = listed & fresh120[:, :, ai] & fresh120[:, :, ah]
            peer_ok = np.zeros((n_s, len(dates)), dtype=np.bool_)
            mkt_ok = np.zeros((n_s, len(dates)), dtype=np.bool_)
            n_listed = listed_i.sum(axis=0)
            n_past_all = past.sum(axis=0).astype(np.int32)
            for mem in members.values():
                past_m = past[mem]
                listed_m = listed[mem]
                peer_ok[mem] = peer_control_ok(listed_m, past_m)
                n_past = past_m.sum(axis=0).astype(np.int32)
                n_sec = listed_i[mem].sum(axis=0).astype(np.int32)
                n_mkt = n_past_all - n_past
                n_mex = n_listed - n_sec
                req = req_lookup[np.clip(n_mex, 0, n_s)]
                good = (n_mex > 0) & (n_mkt >= req) & (n_mkt >= 0.80 * n_mex)
                mkt_ok[mem] = good[None, :]
            controls = past & y_ok & peer_ok & mkt_ok
            need_global = any(bool(m["global"]) for m in users)
            need_sector = any(not bool(m["global"]) for m in users)
            drv_global = None
            drv_sector = np.zeros((n_s, len(dates)), dtype=np.bool_)
            if need_global:
                n_drv = driver.sum(axis=0).astype(np.int32)
                n_ex = n_drv[None, :] - driver.astype(np.int16)
                n_lex = n_listed[None, :] - listed_i
                drv_global = listed & (n_ex >= int(GLOBAL_MIN_N)) & (n_ex >= float(GLOBAL_MIN_FRAC) * np.maximum(n_lex, 0))
            if need_sector:
                base = driver[m3650]
                listed_m = listed_i[m3650]
                n_drv = base.sum(axis=0).astype(np.int32)
                n_list = listed_m.sum(axis=0).astype(np.int32)
                n_ex = n_drv[None, :] - base.astype(np.int16)
                n_lex = n_list[None, :] - listed_m
                thresh = sector_thresh[np.clip(n_lex, 0, len(m3650))]
                drv_sector[m3650] = listed[m3650] & (n_lex > 0) & (n_ex >= thresh) & (n_ex >= float(SECTOR_MIN_FRAC) * n_lex)
            hit = controls
            for user in users:
                drv = drv_global if bool(user["global"]) else drv_sector
                by_mech[str(user["parent_mechanism_id"])] |= (drv & hit).T
        if int(OLD_MKT_EX_MIN_N) != 80:
            raise RuntimeError("mkt_ex_floor_changed")
    blocked = set(BLOCKED_SYMBOLS)
    rows = []
    fail = []
    for rec in family_rows:
        si = sym_i[str(rec["target_symbol"])]
        mid = str(rec["parent_mechanism_id"])
        spec = assignment_by_symbol[str(rec["target_symbol"])]
        d_n = int(by_mech[mid][disc_ix, si].sum()) if disc_ix.size else 0
        f_n = int(by_mech[mid][fv_ix, si].sum()) if fv_ix.size else 0
        good = d_n >= int(DISCOVERY_MIN_VALID_DATES) and f_n >= int(FV_MIN_INPUT_VALID_DATES)
        if spec["control_regime"] != REGIME_MULTI and bool(rec["global"]) is False:
            good = False
        row = {
            "hypothesis_id": rec["hypothesis_id"],
            "parent_mechanism_id": mid,
            "target_symbol": rec["target_symbol"],
            "target_sector": spec["target_sector"],
            "pool_n_peer_pit": spec["pool_n_peer_pit"],
            "control_regime": spec["control_regime"],
            "sole_peer": spec["sole_peer"],
            "sector_control_status": spec["sector_control_status"],
            "old_v1_blocked": rec["target_symbol"] in blocked and bool(rec["global"]),
            "discovery_input_valid_n": d_n,
            "fv_input_valid_n": f_n,
            "pass": good,
            "log_return_computed": False,
        }
        rows.append(row)
        if not good:
            fail.append(rec["hypothesis_id"])
    old = [r for r in rows if r["old_v1_blocked"]]
    return {
        "pass": not fail and len(rows) == len(family_rows),
        "rows": rows,
        "fail_ids": fail,
        "fail_n": len(fail),
        "old_blocked_hypothesis_n": len(old),
        "old_blocked_resolved_n": sum(1 for r in old if r["pass"]),
        "fv_eligible_dates": fv_eligible,
        "fv_eligible_day_sha256": sha256_obj(fv_eligible),
        "fv_eligible_n": len(fv_eligible),
        "fv_folds": {
            "FV_EARLY_n": len(fv_folds[0]),
            "FV_MIDDLE_n": len(fv_folds[1]),
            "FV_LATE_n": len(fv_folds[2]),
            "FV_EARLY_first": fv_folds[0][0] if fv_folds[0] else None,
            "FV_EARLY_last": fv_folds[0][-1] if fv_folds[0] else None,
            "FV_MIDDLE_first": fv_folds[1][0] if fv_folds[1] else None,
            "FV_MIDDLE_last": fv_folds[1][-1] if fv_folds[1] else None,
            "FV_LATE_first": fv_folds[2][0] if fv_folds[2] else None,
            "FV_LATE_last": fv_folds[2][-1] if fv_folds[2] else None,
            "FV_EARLY": fv_folds[0],
            "FV_MIDDLE": fv_folds[1],
            "FV_LATE": fv_folds[2],
            "fold_sha256": fv_fold_sha,
            "outcome_used": False,
        },
        "log_return_computed": False,
        "close_stored": False,
        "prospective_opened": False,
    }
