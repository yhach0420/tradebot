"""Presence/timestamp coverage reconstruction. No beta. No USDJPY y as a research result."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.phase2_discovery import PAST_CONTROL_MIN, TARGET_SCOPES
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, bar_index_for_available_t, clock_ok_for_horizon
from research.causal_driver_pb1.phase2_discovery.infer import basket_maps
from research.causal_driver_pb1.phase2_precommit import MKT105_MIN_VALID_SYMBOLS, SECTOR_CLOCK_COVERAGE_MIN
from research.causal_driver_pb1.phase2_corrected_rerun import RCA_ASOF_USABLE, RCA_EXACT_USABLE, RCA_RECOVERED_CLOCKS
from research.causal_driver_pb1.response.asof import locf_same_session, session_open_grid_index

H5 = 5
BARS = np.array([bar_index_for_available_t(t) for t in CLOCK_MINS], dtype=np.int32)
T0 = BARS
T1 = BARS + H5
TLAG = BARS - int(PAST_CONTROL_MIN)
CLOCK_H5_OK = np.array([clock_ok_for_horizon(t, H5) for t in CLOCK_MINS], dtype=np.bool_)


def _usable_presence(present: np.ndarray, idx: np.ndarray, *, is_mkt: bool) -> np.ndarray:
    sub = present[idx]
    n_const = int(idx.size)
    thr = MKT105_MIN_VALID_SYMBOLS if is_mkt else int(np.ceil(SECTOR_CLOCK_COVERAGE_MIN * n_const))
    n_d = present.shape[1]
    n_g = present.shape[2]
    keep = np.zeros((n_d, N_CLOCK), dtype=np.bool_)
    for ci in range(N_CLOCK):
        if not CLOCK_H5_OK[ci]:
            continue
        if T1[ci] >= n_g or TLAG[ci] < 0 or T0[ci] < 0:
            continue
        v0 = sub[:, :, T0[ci]]
        v1 = sub[:, :, T1[ci]]
        vl = sub[:, :, TLAG[ci]]
        keep[:, ci] = ((v0 & v1).sum(axis=0) >= thr) & ((v0 & vl).sum(axis=0) >= thr)
    return keep


def reconstruct_coverage(*, close: np.ndarray, valid: np.ndarray, sectors: dict[str, Any]) -> dict[str, Any]:
    """Compare exact-print (invalid) vs last-completed as-of. Presence of resolved prices only."""
    present = valid & np.isfinite(close) & (close > 0)
    exact_px = np.where(present, close, np.nan)
    rca_mask = np.logical_or.accumulate(present, axis=2)
    sess_present = present.copy()
    g0 = session_open_grid_index()
    if g0 > 0:
        sess_present[:, :, :g0] = False
    sess_mask = np.logical_or.accumulate(sess_present, axis=2)
    sess_asof, src = locf_same_session(exact_px)
    bmap = basket_maps(sectors)
    mkt = bmap["MKT105_EQW"]
    exact_keep = _usable_presence(present, mkt, is_mkt=True)
    rca_keep = _usable_presence(rca_mask, mkt, is_mkt=True)
    sess_keep = _usable_presence(sess_mask, mkt, is_mkt=True)
    recovered_rca = int((~exact_keep & rca_keep).sum())
    recovered_sess = int((~exact_keep & sess_keep).sum())
    exact_rate = float(exact_keep.mean())
    rca_rate = float(rca_keep.mean())
    sess_rate = float(sess_keep.mean())
    exact_match = abs(exact_rate - RCA_EXACT_USABLE) < 0.005
    rca_match = abs(rca_rate - RCA_ASOF_USABLE) < 0.005 and exact_match
    recovered_match = abs(recovered_rca - RCA_RECOVERED_CLOCKS) <= 50
    delta_session = float(sess_rate - rca_rate)
    explain = (
        "same_session_09:00 as-of vs RCA 08:00-grid presence accumulate; "
        "precommit same_session_only excludes pre-09:00 bars as sources"
    )
    if abs(delta_session) < 1e-12:
        explain = "session 09:00 as-of usable equals RCA 08:00 accumulate on this panel"
    data_mismatch = not exact_match
    proceed = bool(sess_rate >= 0.95 and exact_match and not data_mismatch)
    if not rca_match:
        explain += (
            f"; RCA replica asof {rca_rate:.6f} vs published {RCA_ASOF_USABLE:.6f} "
            f"(exact {exact_rate:.6f} vs {RCA_EXACT_USABLE:.6f})"
        )
    sector_rows = []
    for scope in TARGET_SCOPES:
        idx = bmap[scope]
        is_mkt = scope == "MKT105_EQW"
        sector_rows.append(
            {
                "target_scope": scope,
                "exact_usable": float(_usable_presence(present, idx, is_mkt=is_mkt).mean()),
                "rca_asof_usable": float(_usable_presence(rca_mask, idx, is_mkt=is_mkt).mean()),
                "session_asof_usable": float(_usable_presence(sess_mask, idx, is_mkt=is_mkt).mean()),
            }
        )
    return {
        "exact_usable_rate": exact_rate,
        "rca_asof_usable_rate": rca_rate,
        "session_asof_usable_rate": sess_rate,
        "rca_expected_asof_usable": RCA_ASOF_USABLE,
        "rca_expected_recovered_clocks": RCA_RECOVERED_CLOCKS,
        "recovered_clocks_rca_asof": recovered_rca,
        "recovered_clocks_session_asof": recovered_sess,
        "rca_match": rca_match,
        "exact_match": exact_match,
        "recovered_match": recovered_match,
        "session_vs_rca_delta": delta_session,
        "session_semantics_note": explain,
        "proceed_to_dev_rerun": proceed,
        "sector_rows": sector_rows,
        "asof": sess_asof,
        "src_idx": src,
        "future_returns_computed": False,
        "stale_threshold_invented": False,
    }
