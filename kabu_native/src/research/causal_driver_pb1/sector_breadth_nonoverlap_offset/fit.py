"""Fit the 7 offset point betas on the exact precommitted common sample. No bootstrap."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.phase2_discovery.infer import date_minute_ids, point_b_fx
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import COMMON_SAMPLE
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.gates import apply_offset_gates
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import (
    driver_starts_after_target,
    future_placebo_offsets,
    overlap_minutes,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.sample import build_common_samples


def _public_sample_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not str(k).startswith("_")}


def fit_offsets(*, records: list[dict[str, Any]], c1_dates: list[str]) -> dict[str, Any]:
    built = build_common_samples(records=records, c1_dates=c1_dates, return_fit=True)
    blockers: list[str] = list(built.get("blockers") or [])
    rows_in = list(built.get("rows") or [])
    by_id = {str(r.get("test_id")): r for r in rows_in}
    verified: list[dict[str, Any]] = []
    for rec in records:
        tid = str(rec.get("test_id"))
        got = by_id.get(tid) or {}
        spec = COMMON_SAMPLE[tid]
        n = int(got.get("offset_common_sample_n") or -1)
        sha = str(got.get("offset_common_sample_sha256") or "")
        if n != int(spec["n"]) or sha != str(spec["sha256"]):
            blockers.append(f"RECONSTRUCTED_SAMPLE_MISMATCH:{tid}")
        if list(got.get("offsets") or []) != list(spec["offsets"]):
            blockers.append(f"RECONSTRUCTED_OFFSET_MISMATCH:{tid}")
        verified.append(
            {
                "test_id": tid,
                "common_sample_n": n,
                "common_sample_sha256": sha,
                "expected_n": int(spec["n"]),
                "expected_sha256": str(spec["sha256"]),
                "sha_match": sha == str(spec["sha256"]) and n == int(spec["n"]),
                "offsets": list(got.get("offsets") or []),
            }
        )
    overlap_rows = []
    overlap_ok = True
    for rec in records:
        w = int(rec["lookback"])
        h = int(rec["horizon"])
        k1, k2, k3 = future_placebo_offsets(lookback=w, horizon=h)
        for k in (k1, k2, k3):
            ov = overlap_minutes(t_min=0, lookback=w, horizon=h, k=int(k))
            after = driver_starts_after_target(t_min=0, lookback=w, horizon=h, k=int(k))
            row = {
                "test_id": rec.get("test_id"),
                "lookback": w,
                "horizon": h,
                "k": int(k),
                "overlap_minutes": ov,
                "driver_window_start_gt_target_end": after,
            }
            overlap_rows.append(row)
            if ov != 0 or not after:
                overlap_ok = False
                blockers.append(f"FUTURE_WINDOW_OVERLAP:{rec.get('test_id')}:{k}")
    if blockers or not overlap_ok or not built.get("pass"):
        return {
            "pass": False,
            "blocked": True,
            "blockers": list(dict.fromkeys(blockers)),
            "sample_verification": verified,
            "overlap_rows": overlap_rows,
            "offset_results": [],
            "betas_estimated": False,
            "panel": None,
        }

    n_d = len(c1_dates)
    _, minute_ids = date_minute_ids(n_d)
    results = []
    for rec in records:
        tid = str(rec.get("test_id"))
        src = by_id[tid]
        w = int(rec["lookback"])
        h = int(rec["horizon"])
        k1, k2, k3 = future_placebo_offsets(lookback=w, horizon=h)
        flat = np.asarray(src["_mask"], dtype=bool).reshape(-1)
        if int(flat.sum()) != int(COMMON_SAMPLE[tid]["n"]):
            blockers.append(f"MASK_N_MISMATCH:{tid}")
            continue
        y = np.asarray(src["_y"], dtype=np.float64).reshape(-1)[flat]
        ctr = [np.asarray(c, dtype=np.float64).reshape(-1)[flat] for c in src["_ctr"]]
        mm = minute_ids[flat]
        betas: dict[int, float | None] = {}
        finite_ok = True
        for k in (-5, -3, -1, 0, k1, k2, k3):
            fx = np.asarray(src["_fx"][int(k)], dtype=np.float64).reshape(-1)[flat]
            if not (np.isfinite(y).all() and np.isfinite(fx).all() and all(np.isfinite(c).all() for c in ctr)):
                finite_ok = False
                blockers.append(f"OFFSET_SPECIFIC_NONFINITE:{tid}:{k}")
                break
            if int(y.shape[0]) != int(COMMON_SAMPLE[tid]["n"]):
                finite_ok = False
                blockers.append(f"ROW_DROP:{tid}:{k}")
                break
            betas[int(k)] = point_b_fx(y, fx, ctr, mm)
        if not finite_ok:
            continue
        gates = apply_offset_gates(
            beta_m5=betas.get(-5),
            beta_m3=betas.get(-3),
            beta_m1=betas.get(-1),
            beta_0=betas.get(0),
            beta_k1=betas.get(k1),
            beta_k2=betas.get(k2),
            beta_k3=betas.get(k3),
            overlap_minutes_k1=0,
            overlap_minutes_k2=0,
            overlap_minutes_k3=0,
        )
        results.append(
            {
                "test_id": tid,
                "metric": rec.get("metric"),
                "scope_id": rec.get("scope_id"),
                "sector_id": rec.get("sector_id"),
                "global": bool(rec.get("global") or rec.get("scope_id") == "GLOBAL_105"),
                "direction": rec.get("direction"),
                "w": w,
                "h": h,
                "lookback": w,
                "horizon": h,
                "K1": k1,
                "K2": k2,
                "K3": k3,
                "common_sample_n": int(COMMON_SAMPLE[tid]["n"]),
                "common_sample_sha256": str(COMMON_SAMPLE[tid]["sha256"]),
                "beta_-5": betas.get(-5),
                "beta_-3": betas.get(-3),
                "beta_-1": betas.get(-1),
                "beta_0": betas.get(0),
                "beta_K1": betas.get(k1),
                "beta_K2": betas.get(k2),
                "beta_K3": betas.get(k3),
                "max_future_abs": gates["max_future_abs"],
                "abs0_to_max_future_ratio": gates["abs0_to_max_future_ratio"],
                "O1": gates["O1"],
                "O2": gates["O2"],
                "O3": gates["O3"],
                "O4": gates["O4"],
                "offset_pass": gates["offset_pass"],
                "failed_at": gates["failed_at"],
                "same_rows_all_offsets": True,
                "bootstrap_not_used": True,
                "old_offset_magnitudes_not_used": True,
            }
        )
    if blockers:
        return {
            "pass": False,
            "blocked": True,
            "blockers": list(dict.fromkeys(blockers)),
            "sample_verification": verified,
            "overlap_rows": overlap_rows,
            "offset_results": [],
            "betas_estimated": False,
            "panel": None,
        }
    return {
        "pass": True,
        "blocked": False,
        "blockers": [],
        "sample_verification": verified,
        "overlap_rows": overlap_rows,
        "offset_results": results,
        "betas_estimated": True,
        "panel": built.get("panel"),
        "sample_rows_public": [_public_sample_row(r) for r in rows_in],
    }
