"""Candidate-balanced + inverse-selection-frequency-weighted calibration (Discovery only)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import DISCOVERY
from .snap import disagree_by_more_than_one_step, snap_ceil, snap_floor
from . import (
    GIVEBACK_GRID_BPS,
    MAX_HOLD_GRID_SEC,
    NO_PROGRESS_GRID_SEC,
    STOP_GRID_BPS,
    TARGET_GRID_BPS,
    TRAIL_ACTIVATION_GRID_BPS,
)


def _q(xs: list[float], q: float) -> Optional[float]:
    arr = np.asarray([x for x in xs if x is not None and x == x], dtype=np.float64)
    if arr.size == 0:
        return None
    return float(np.quantile(arr, q))


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> Optional[float]:
    m = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not np.any(m):
        return None
    v = values[m]
    w = weights[m]
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    cutoff = q * cw[-1]
    idx = int(np.searchsorted(cw, cutoff, side="left"))
    idx = min(max(idx, 0), len(v) - 1)
    return float(v[idx])


def family_member_ids(
    routing_rows: list[dict[str, Any]], family: str,
) -> list[str]:
    """Calibration population = Discovery-tagged masks (alias-free unique reps)."""
    out = []
    for r in routing_rows:
        tags = r.get("all_discovery_tags") or []
        if family in tags:
            out.append(r["candidate_id"])
    return out


def candidate_balanced_metrics(
    *,
    family: str,
    member_ids: list[str],
    features_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Each unique mask = 1 vote. MAE stored as magnitude (abs) for stop sizing."""
    votes = [features_by_id[i] for i in member_ids if i in features_by_id]
    if not votes:
        return {"family": family, "n_masks": 0}

    def collect(key: str, as_abs: bool = False) -> list[float]:
        out = []
        for v in votes:
            x = v.get(key)
            if x is None or x != x:
                continue
            out.append(abs(float(x)) if as_abs else float(x))
        return out

    return {
        "family": family,
        "n_masks": len(votes),
        "pre30_abs_q50": _q(collect("pre30_mae_q", True), 0.50),
        "pre30_abs_q75": _q(collect("pre30_mae_q", True), 0.75),
        "pre50_abs_q50": _q(collect("pre50_mae_q", True), 0.50),
        "pre50_abs_q75": _q(collect("pre50_mae_q", True), 0.75),
        "pre60_abs_q50": _q(collect("pre60_mae_q", True), 0.50),
        "pre60_abs_q75": _q(collect("pre60_mae_q", True), 0.75),
        "mfe300_q25": _q(collect("mfe300_med"), 0.25),
        "mfe300_q50": _q(collect("mfe300_med"), 0.50),
        "mfe900_q25": _q(collect("mfe900_med"), 0.25),
        "mfe900_q50": _q(collect("mfe900_med"), 0.50),
        "mfe1800_q25": _q(collect("mfe1800_med"), 0.25),
        "mfe1800_q50": _q(collect("mfe1800_med"), 0.50),
        "max_gb_300_q25": _q(collect("max_gb_300_med"), 0.25),
        "max_gb_300_q50": _q(collect("max_gb_300_med"), 0.50),
        "max_gb_900_q25": _q(collect("max_gb_900_med"), 0.25),
        "max_gb_900_q50": _q(collect("max_gb_900_med"), 0.50),
        "max_gb_1800_q25": _q(collect("max_gb_1800_med"), 0.25),
        "max_gb_1800_q50": _q(collect("max_gb_1800_med"), 0.50),
        "up30_reach_time_q50": _q(collect("up30_median_reach_time"), 0.50),
        "up50_reach_time_q50": _q(collect("up50_median_reach_time"), 0.50),
        "alias_weight": 0,
        "vote_unit": "unique_mask",
    }


def anchor_weighted_metrics(
    *,
    family: str,
    member_ids: list[str],
    unique_masks: dict[str, np.ndarray],
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    path_ok: np.ndarray,
) -> dict[str, Any]:
    """weight = 1 / (# unique masks in family selecting the anchor)."""
    disc = path_ok & np.isin(dates, list(DISCOVERY))
    n = len(dates)
    select_count = np.zeros(n, dtype=np.float64)
    for mid in member_ids:
        m = unique_masks[mid]
        select_count += (disc & m).astype(np.float64)
    weights = np.zeros(n, dtype=np.float64)
    hit = select_count > 0
    weights[hit] = 1.0 / select_count[hit]

    def wq_abs(arr_key: str, extra_mask: Optional[np.ndarray], q: float) -> Optional[float]:
        vals = np.abs(metrics[arr_key].astype(np.float64))
        m = hit.copy()
        if extra_mask is not None:
            m &= extra_mask
        return _weighted_quantile(vals, weights, q)

    def wq(arr_key: str, extra_mask: Optional[np.ndarray], q: float) -> Optional[float]:
        vals = metrics[arr_key]
        m = hit.copy()
        if extra_mask is not None:
            m &= extra_mask
        return _weighted_quantile(vals, weights, q)

    return {
        "family": family,
        "n_anchors_weighted": int(hit.sum()),
        "pre30_abs_q50": wq_abs("pre_reach_MAE_30_bps", metrics["up_30_reached"], 0.50),
        "pre30_abs_q75": wq_abs("pre_reach_MAE_30_bps", metrics["up_30_reached"], 0.75),
        "pre50_abs_q50": wq_abs("pre_reach_MAE_50_bps", metrics["up_50_reached"], 0.50),
        "pre50_abs_q75": wq_abs("pre_reach_MAE_50_bps", metrics["up_50_reached"], 0.75),
        "pre60_abs_q50": wq_abs("pre_reach_MAE_60_bps", metrics["up_60_reached"], 0.50),
        "pre60_abs_q75": wq_abs("pre_reach_MAE_60_bps", metrics["up_60_reached"], 0.75),
        "mfe300_q25": wq("MFE_300s_bps", metrics["eligible_300s"] & metrics["fresh_ok_300s"], 0.25),
        "mfe300_q50": wq("MFE_300s_bps", metrics["eligible_300s"] & metrics["fresh_ok_300s"], 0.50),
        "mfe900_q25": wq("MFE_900s_bps", metrics["eligible_900s"] & metrics["fresh_ok_900s"], 0.25),
        "mfe900_q50": wq("MFE_900s_bps", metrics["eligible_900s"] & metrics["fresh_ok_900s"], 0.50),
        "mfe1800_q25": wq("MFE_1800s_bps", metrics["eligible_1800s"] & metrics["fresh_ok_1800s"], 0.25),
        "mfe1800_q50": wq("MFE_1800s_bps", metrics["eligible_1800s"] & metrics["fresh_ok_1800s"], 0.50),
        "max_gb_300_q25": wq("max_giveback_after_MFE_300s_bps", metrics["eligible_300s"] & metrics["fresh_ok_300s"], 0.25),
        "max_gb_300_q50": wq("max_giveback_after_MFE_300s_bps", metrics["eligible_300s"] & metrics["fresh_ok_300s"], 0.50),
        "max_gb_900_q25": wq("max_giveback_after_MFE_900s_bps", metrics["eligible_900s"] & metrics["fresh_ok_900s"], 0.25),
        "max_gb_900_q50": wq("max_giveback_after_MFE_900s_bps", metrics["eligible_900s"] & metrics["fresh_ok_900s"], 0.50),
        "max_gb_1800_q25": wq("max_giveback_after_MFE_1800s_bps", metrics["eligible_1800s"] & metrics["fresh_ok_1800s"], 0.25),
        "max_gb_1800_q50": wq("max_giveback_after_MFE_1800s_bps", metrics["eligible_1800s"] & metrics["fresh_ok_1800s"], 0.50),
        "weight_rule": "1/n_family_masks_selecting_anchor",
    }


def abs_stop_from_mae(mae: Optional[float]) -> Optional[float]:
    if mae is None or mae != mae:
        return None
    return abs(float(mae))


def design_family_exits(
    *,
    family: str,
    cand: dict[str, Any],
    anch: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (exit_specs_partial, disagreement_rows). Max 2 variants per family."""
    disagreements: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []

    def stop_pair(abs_q50_key: str, abs_q75_key: str) -> tuple[Optional[float], Optional[float], bool]:
        c_prot = snap_ceil(cand.get(abs_q50_key), STOP_GRID_BPS)
        c_room = snap_ceil(cand.get(abs_q75_key), STOP_GRID_BPS)
        a_prot = snap_ceil(anch.get(abs_q50_key), STOP_GRID_BPS)
        a_room = snap_ceil(anch.get(abs_q75_key), STOP_GRID_BPS)
        disagree = disagree_by_more_than_one_step(c_prot, a_prot, STOP_GRID_BPS) or disagree_by_more_than_one_step(
            c_room, a_room, STOP_GRID_BPS
        )
        if disagree:
            # keep both views as PROTECT/ROOM when views diverge >1 step
            return c_prot or a_prot, a_room or c_room or c_prot, True
        return c_prot, c_room or c_prot, False

    if family == "QUICK_MOVE":
        stop_p, stop_r, dstop = stop_pair("pre30_abs_q50", "pre30_abs_q75")
        if dstop:
            disagreements.append({"family": family, "param": "stop_bps", "note": "cand_vs_anch_>1_grid"})
        tgt_c = snap_floor(cand.get("mfe300_q25") or cand.get("mfe300_q50"), TARGET_GRID_BPS)
        act_c = snap_floor(cand.get("mfe300_q25"), TRAIL_ACTIVATION_GRID_BPS)
        gb_c = snap_ceil(cand.get("max_gb_300_q25"), GIVEBACK_GRID_BPS)
        t_reach = cand.get("up30_reach_time_q50") or 300.0
        hold = 300.0 if t_reach <= 300 else 600.0
        np_t = 180.0 if t_reach <= 180 else 300.0
        exits.append({
            "exit_id": "EXIT_QUICK_TARGET_V1", "path_family": family, "variant": "TARGET",
            "stop_bps": stop_p, "target_bps": tgt_c, "trail_activation_bps": None, "giveback_bps": None,
            "giveback_mode": None, "no_progress_sec": np_t, "max_hold_sec": hold,
            "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
            "source_metrics": {"stop": "abs(pre_reach_MAE_30) q50", "target": "MFE_300 q25/q50"},
            "candidate_balanced_value": {"stop_raw": cand.get("pre30_abs_q50"), "target_raw": cand.get("mfe300_q25")},
            "anchor_weighted_value": {"stop_raw": anch.get("pre30_abs_q50"), "target_raw": anch.get("mfe300_q25")},
        })
        exits.append({
            "exit_id": "EXIT_QUICK_TRAIL_V1", "path_family": family, "variant": "TRAIL",
            "stop_bps": stop_r or stop_p, "target_bps": None, "trail_activation_bps": act_c,
            "giveback_bps": gb_c, "giveback_mode": "from_MFE", "no_progress_sec": np_t, "max_hold_sec": hold,
            "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
            "source_metrics": {"trail_act": "MFE_300 q25", "giveback": "max_gb_after_MFE_300 q25"},
            "candidate_balanced_value": {"stop_raw": cand.get("pre30_abs_q75"), "giveback_raw": cand.get("max_gb_300_q25")},
            "anchor_weighted_value": {"stop_raw": anch.get("pre30_abs_q75"), "giveback_raw": anch.get("max_gb_300_q25")},
        })

    elif family == "PULLBACK_THEN_RISE":
        stop_p, stop_r, dstop = stop_pair("pre50_abs_q50", "pre50_abs_q75")
        if dstop:
            disagreements.append({"family": family, "param": "stop_bps"})
        act = snap_floor(cand.get("mfe900_q25") or cand.get("mfe900_q50"), TRAIL_ACTIVATION_GRID_BPS)
        gb = snap_ceil(cand.get("max_gb_900_q25") or cand.get("max_gb_900_q50"), GIVEBACK_GRID_BPS)
        for eid, var, stop in (
            ("EXIT_PULLBACK_PROTECT_V1", "PROTECT", stop_p),
            ("EXIT_PULLBACK_ROOM_V1", "ROOM", stop_r or stop_p),
        ):
            exits.append({
                "exit_id": eid, "path_family": family, "variant": var,
                "stop_bps": stop, "target_bps": None, "trail_activation_bps": act,
                "giveback_bps": gb, "giveback_mode": "from_MFE",
                "no_progress_sec": 600.0, "max_hold_sec": 900.0,
                "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
                "source_metrics": {"stop": f"abs(pre_reach_MAE_50) {var}", "no_early_np": True},
                "candidate_balanced_value": {"stop_raw": cand.get("pre50_abs_q50" if var == "PROTECT" else "pre50_abs_q75")},
                "anchor_weighted_value": {"stop_raw": anch.get("pre50_abs_q50" if var == "PROTECT" else "pre50_abs_q75")},
            })

    elif family == "CONTINUATION":
        stop_p, stop_r, dstop = stop_pair("pre60_abs_q50", "pre60_abs_q75")
        if dstop:
            disagreements.append({"family": family, "param": "stop_bps"})
        act = snap_floor(cand.get("mfe900_q25") or cand.get("mfe900_q50"), TRAIL_ACTIVATION_GRID_BPS)
        gb = snap_ceil(cand.get("max_gb_900_q25") or cand.get("max_gb_900_q50"), GIVEBACK_GRID_BPS)
        for eid, var, stop in (
            ("EXIT_CONTINUATION_PROTECT_V1", "PROTECT", stop_p),
            ("EXIT_CONTINUATION_ROOM_V1", "ROOM", stop_r or stop_p),
        ):
            exits.append({
                "exit_id": eid, "path_family": family, "variant": var,
                "stop_bps": stop, "target_bps": None, "trail_activation_bps": act,
                "giveback_bps": gb, "giveback_mode": "from_MFE",
                "no_progress_sec": 900.0, "max_hold_sec": 1800.0,
                "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
                "source_metrics": {"stop": f"abs(pre_reach_MAE_60) {var}"},
                "candidate_balanced_value": {"stop_raw": cand.get("pre60_abs_q50" if var == "PROTECT" else "pre60_abs_q75")},
                "anchor_weighted_value": {"stop_raw": anch.get("pre60_abs_q50" if var == "PROTECT" else "pre60_abs_q75")},
            })

    elif family == "DELAYED_MOVE":
        raw = max(
            (x for x in (cand.get("pre50_abs_q75"), cand.get("pre60_abs_q75")) if x is not None),
            default=None,
        )
        stop = snap_ceil(raw, STOP_GRID_BPS)
        raw_a = max(
            (x for x in (anch.get("pre50_abs_q75"), anch.get("pre60_abs_q75")) if x is not None),
            default=None,
        )
        stop_a = snap_ceil(raw_a, STOP_GRID_BPS)
        if disagree_by_more_than_one_step(stop, stop_a, STOP_GRID_BPS):
            disagreements.append({"family": family, "param": "stop_bps", "cand": stop, "anch": stop_a})
            stop_room = stop_a or stop
        else:
            stop_room = stop
        act = snap_floor(cand.get("mfe1800_q25") or cand.get("mfe1800_q50"), TRAIL_ACTIVATION_GRID_BPS)
        gb = snap_ceil(cand.get("max_gb_1800_q25") or cand.get("max_gb_1800_q50"), GIVEBACK_GRID_BPS)
        for eid, var, st in (
            ("EXIT_DELAYED_PROTECT_V1", "PROTECT", stop),
            ("EXIT_DELAYED_ROOM_V1", "ROOM", stop_room),
        ):
            exits.append({
                "exit_id": eid, "path_family": family, "variant": var,
                "stop_bps": st, "target_bps": None, "trail_activation_bps": act,
                "giveback_bps": gb, "giveback_mode": "from_MFE",
                "no_progress_sec": 900.0, "max_hold_sec": 1800.0,
                "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
                "source_metrics": {"forbid_np_before_600": True},
                "candidate_balanced_value": {"stop_raw": raw},
                "anchor_weighted_value": {"stop_raw": raw_a},
            })

    elif family == "SPIKE_AND_GIVEBACK":
        stop_p, stop_r, dstop = stop_pair("pre30_abs_q50", "pre30_abs_q75")
        if dstop:
            disagreements.append({"family": family, "param": "stop_bps"})
        tgt = snap_floor(cand.get("mfe300_q25"), TARGET_GRID_BPS)
        act = snap_floor(cand.get("mfe300_q25"), TRAIL_ACTIVATION_GRID_BPS)
        gb = snap_ceil(cand.get("max_gb_300_q25"), GIVEBACK_GRID_BPS)
        if gb is not None and gb > 30:
            gb = 30.0
        exits.append({
            "exit_id": "EXIT_SPIKE_TARGET_V1", "path_family": family, "variant": "TARGET",
            "stop_bps": stop_p, "target_bps": tgt, "trail_activation_bps": None, "giveback_bps": None,
            "giveback_mode": None, "no_progress_sec": 180.0, "max_hold_sec": 300.0,
            "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
            "source_metrics": {"target": "MFE_300 q25"},
            "candidate_balanced_value": {"stop_raw": cand.get("pre30_abs_q50"), "target_raw": cand.get("mfe300_q25")},
            "anchor_weighted_value": {"stop_raw": anch.get("pre30_abs_q50"), "target_raw": anch.get("mfe300_q25")},
        })
        exits.append({
            "exit_id": "EXIT_SPIKE_TIGHT_TRAIL_V1", "path_family": family, "variant": "TIGHT_TRAIL",
            "stop_bps": stop_r or stop_p, "target_bps": None, "trail_activation_bps": act,
            "giveback_bps": gb, "giveback_mode": "from_MFE", "no_progress_sec": 300.0, "max_hold_sec": 600.0,
            "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0,
            "source_metrics": {"tight_giveback": True},
            "candidate_balanced_value": {"giveback_raw": cand.get("max_gb_300_q25")},
            "anchor_weighted_value": {"giveback_raw": anch.get("max_gb_300_q25")},
        })

    for e in exits:
        if e.get("stop_bps") is None:
            e["stop_bps"] = 20.0
        if e.get("max_hold_sec") is None:
            e["max_hold_sec"] = 900.0

    return exits, disagreements
