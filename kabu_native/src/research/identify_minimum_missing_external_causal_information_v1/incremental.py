"""BASE HM1 vs BASE+external. Characterize first. No threshold rescue. Discovery only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

DELTA_X0_MIN = 2.0
DELTA_P_MIN = 0.03
BLOCK_AGREE_MIN = 3
MATCHED_REMAIN_MIN = 1.0
CORR_DUP = 0.40


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mean(xs: list[float]) -> float | None:
    return float(np.mean(xs)) if xs else None


def _pf(xs: list[float]) -> float | None:
    arr = np.asarray(xs, dtype=float)
    if arr.size == 0:
        return None
    pos = float(np.sum(arr[arr > 0]))
    neg = float(-np.sum(arr[arr < 0]))
    if neg <= 0:
        return None
    return pos / neg


def _p_first(rows: list[dict[str, Any]]) -> float | None:
    ys = [1.0 if bool(r.get("mfe_before_mae")) else 0.0 for r in rows if r.get("mfe_before_mae") is not None]
    return float(np.mean(ys)) if ys else None


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    x0 = [float(r["x0_bps"]) for r in rows if _finite(r.get("x0_bps"))]
    by_block: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("block") and _finite(r.get("x0_bps")):
            by_block[str(r["block"])].append(float(r["x0_bps"]))
    return {
        "n": len(rows),
        "mean_x0_bps": _mean(x0),
        "profit_factor": _pf(x0),
        "p_mfe_before_mae": _p_first(rows),
        "block_mean_x0": {k: float(np.mean(vs)) for k, vs in by_block.items()},
        "block_p_first": {
            k: _p_first([r for r in rows if str(r.get("block")) == k]) for k in by_block
        },
        "day_n": len({str(r.get("date")) for r in rows}),
    }


def _tercile(vals: list[float], x: float) -> int:
    a = np.asarray(vals, dtype=float)
    q1, q2 = np.quantile(a, [1 / 3, 2 / 3])
    if x <= q1:
        return 1
    if x <= q2:
        return 2
    return 3


def _quintiles(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    scored = [r for r in rows if _finite(r.get(key))]
    if len(scored) < 20:
        return []
    xs = np.asarray([float(r[key]) for r in scored], dtype=float)
    edges = np.quantile(xs, [0.2, 0.4, 0.6, 0.8])
    bins = np.digitize(xs, edges, right=True)
    out = []
    for b in range(5):
        sl = [scored[i] for i, bb in enumerate(bins) if bb == b]
        st = _stats(sl)
        st["quintile"] = b + 1
        st["feature"] = key
        st["feature_mean"] = _mean([float(r[key]) for r in sl])
        out.append(st)
    return out


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 20 or len(a) != len(b):
        return None
    ra = np.argsort(np.argsort(np.asarray(a, dtype=float)))
    rb = np.argsort(np.argsort(np.asarray(b, dtype=float)))
    if float(np.std(ra)) == 0 or float(np.std(rb)) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate_incremental(*, trades: list[dict[str, Any]]) -> dict[str, Any]:
    base = _stats(trades)
    joined = [t for t in trades if t.get("external_join_ok") and t.get("causal_ok")]
    nk_pos = [t for t in joined if t.get("nk_sign_60s") == 1]
    nk_neg = [t for t in joined if t.get("nk_sign_60s") == -1]
    tx_pos = [t for t in joined if t.get("topix_sign_60s") == 1]
    tx_neg = [t for t in joined if t.get("topix_sign_60s") == -1]
    states = {}
    for name in ("both_up", "both_down", "nk_up_topix_down", "nk_down_topix_up", "both_neutral", "partial_neutral", "missing"):
        states[name] = _stats([t for t in joined if t.get("agreement_state") == name])
    nk_pos_s = _stats(nk_pos)
    nk_neg_s = _stats(nk_neg)
    tx_pos_s = _stats(tx_pos)
    tx_neg_s = _stats(tx_neg)
    delta_x0 = None
    delta_p = None
    if nk_pos_s.get("mean_x0_bps") is not None and nk_neg_s.get("mean_x0_bps") is not None:
        delta_x0 = float(nk_pos_s["mean_x0_bps"]) - float(nk_neg_s["mean_x0_bps"])
    if nk_pos_s.get("p_mfe_before_mae") is not None and nk_neg_s.get("p_mfe_before_mae") is not None:
        delta_p = float(nk_pos_s["p_mfe_before_mae"]) - float(nk_neg_s["p_mfe_before_mae"])
    block_sign = []
    for blk in ("D1", "D2", "D3", "D4"):
        a = nk_pos_s.get("block_mean_x0") or {}
        b = nk_neg_s.get("block_mean_x0") or {}
        if blk in a and blk in b:
            block_sign.append(1 if float(a[blk]) - float(b[blk]) > 0 else (-1 if float(a[blk]) - float(b[blk]) < 0 else 0))
    overall_sign = 1 if (delta_x0 or 0) > 0 else (-1 if (delta_x0 or 0) < 0 else 0)
    block_agree_n = sum(1 for s in block_sign if s == overall_sign and s != 0)

    rs_vals = [float(t["rs_5m"]) for t in joined if _finite(t.get("rs_5m"))]
    sec_vals = [float(t["sector_rs"]) for t in joined if _finite(t.get("sector_rs"))]
    matched_deltas = []
    matched_p = []
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    if rs_vals and sec_vals:
        for t in joined:
            if not (_finite(t.get("rs_5m")) and _finite(t.get("sector_rs")) and t.get("nk_sign_60s") in (1, -1)):
                continue
            hour = str(t.get("event_time") or "??")[:2]
            key = (hour, _tercile(rs_vals, float(t["rs_5m"])), _tercile(sec_vals, float(t["sector_rs"])))
            groups[key].append(t)
        for xs in groups.values():
            pos = [r for r in xs if r.get("nk_sign_60s") == 1]
            neg = [r for r in xs if r.get("nk_sign_60s") == -1]
            if len(pos) < 3 or len(neg) < 3:
                continue
            px = _mean([float(r["x0_bps"]) for r in pos if _finite(r.get("x0_bps"))])
            nx = _mean([float(r["x0_bps"]) for r in neg if _finite(r.get("x0_bps"))])
            pp = _p_first(pos)
            np_ = _p_first(neg)
            if px is not None and nx is not None:
                matched_deltas.append((px - nx) * (len(pos) + len(neg)))
            if pp is not None and np_ is not None:
                matched_p.append((pp - np_) * (len(pos) + len(neg)))
    n_w = sum(len(v) for v in groups.values() if len([r for r in v if r.get("nk_sign_60s") == 1]) >= 3 and len([r for r in v if r.get("nk_sign_60s") == -1]) >= 3)
    matched_delta_x0 = (sum(matched_deltas) / n_w) if n_w and matched_deltas else None
    # n_w above is approximate; use explicit weights stored in matched_deltas already as delta*n
    if matched_deltas:
        w = 0.0
        acc = 0.0
        for xs in groups.values():
            pos = [r for r in xs if r.get("nk_sign_60s") == 1]
            neg = [r for r in xs if r.get("nk_sign_60s") == -1]
            if len(pos) < 3 or len(neg) < 3:
                continue
            px = _mean([float(r["x0_bps"]) for r in pos if _finite(r.get("x0_bps"))])
            nx = _mean([float(r["x0_bps"]) for r in neg if _finite(r.get("x0_bps"))])
            if px is None or nx is None:
                continue
            ww = float(len(pos) + len(neg))
            acc += (px - nx) * ww
            w += ww
        matched_delta_x0 = (acc / w) if w else None
    pairs = [(float(t["nk_ret_60s_bps"]), float(t["rs_5m"])) for t in joined if _finite(t.get("nk_ret_60s_bps")) and _finite(t.get("rs_5m"))]
    pairs_s = [(float(t["nk_ret_60s_bps"]), float(t["sector_rs"])) for t in joined if _finite(t.get("nk_ret_60s_bps")) and _finite(t.get("sector_rs"))]
    rho_rs = _spearman([a for a, _ in pairs], [b for _, b in pairs])
    rho_sec = _spearman([a for a, _ in pairs_s], [b for _, b in pairs_s])
    duplicate = bool(
        (rho_rs is not None and abs(rho_rs) >= CORR_DUP)
        and (matched_delta_x0 is None or abs(matched_delta_x0) < MATCHED_REMAIN_MIN)
    )
    structure = bool(
        delta_x0 is not None
        and delta_p is not None
        and abs(float(delta_x0)) >= DELTA_X0_MIN
        and abs(float(delta_p)) >= DELTA_P_MIN
        and block_agree_n >= BLOCK_AGREE_MIN
        and not duplicate
        and (matched_delta_x0 is None or (np.sign(matched_delta_x0) == np.sign(delta_x0) and abs(float(matched_delta_x0)) >= MATCHED_REMAIN_MIN))
    )
    role = None
    roles_eval = {
        "DIRECTION_GATE": False,
        "VETO": False,
        "REGIME": False,
        "TIE_BREAKER": False,
    }
    if structure and (delta_x0 or 0) > 0:
        roles_eval["DIRECTION_GATE"] = True
        role = "DIRECTION_GATE"
    if structure and nk_neg_s.get("mean_x0_bps") is not None and base.get("mean_x0_bps") is not None:
        if float(nk_neg_s["mean_x0_bps"]) + 2.0 < float(base["mean_x0_bps"]):
            roles_eval["VETO"] = True
            if role is None:
                role = "VETO"
    both_up = states.get("both_up") or {}
    both_down = states.get("both_down") or {}
    if (
        structure
        and both_up.get("mean_x0_bps") is not None
        and both_down.get("mean_x0_bps") is not None
        and float(both_up["mean_x0_bps"]) - float(both_down["mean_x0_bps"]) >= DELTA_X0_MIN
        and int(both_up.get("n") or 0) >= 40
        and int(both_down.get("n") or 0) >= 40
    ):
        roles_eval["REGIME"] = True
        if role is None:
            role = "REGIME"
    if not structure:
        roles_eval["DIRECTION_GATE"] = False
        roles_eval["VETO"] = False
        roles_eval["REGIME"] = False
        role = None
    roles_eval["TIE_BREAKER"] = False
    roles_eval["tie_breaker_note"] = "HM1 Top1 is already unique per clock; tie-breaker among simultaneous candidates is not identified at this occupancy."
    roles_eval["descriptive_only_unless_structure_gate"] = True

    d1d4_stable = bool(block_agree_n >= BLOCK_AGREE_MIN and overall_sign != 0)
    return {
        "base": base,
        "joined_n": len(joined),
        "nk_sign": {"pos": nk_pos_s, "neg": nk_neg_s, "delta_x0": delta_x0, "delta_p_first": delta_p},
        "topix_sign": {
            "pos": tx_pos_s,
            "neg": tx_neg_s,
            "delta_x0": (
                float(tx_pos_s["mean_x0_bps"]) - float(tx_neg_s["mean_x0_bps"])
                if tx_pos_s.get("mean_x0_bps") is not None and tx_neg_s.get("mean_x0_bps") is not None
                else None
            ),
            "delta_p_first": (
                float(tx_pos_s["p_mfe_before_mae"]) - float(tx_neg_s["p_mfe_before_mae"])
                if tx_pos_s.get("p_mfe_before_mae") is not None and tx_neg_s.get("p_mfe_before_mae") is not None
                else None
            ),
        },
        "agreement_states": states,
        "nk_ret_60s_quintiles": _quintiles(joined, "nk_ret_60s_bps"),
        "topix_ret_60s_quintiles": _quintiles(joined, "topix_ret_60s_bps"),
        "nk_minus_topix_quintiles": _quintiles(joined, "nk_minus_topix_60s_bps"),
        "matched_comparison": {
            "group_n": len(groups),
            "delta_x0_nk_sign_within_stock_state": matched_delta_x0,
            "hour_rs_sector_terciles": True,
        },
        "duplicate_of_stock_momentum": {
            "spearman_nk60_vs_rs_5m": rho_rs,
            "spearman_nk60_vs_sector_rs": rho_sec,
            "flag": duplicate,
        },
        "structure_precommitted_gate": {
            "delta_x0_min_bps": DELTA_X0_MIN,
            "delta_p_min": DELTA_P_MIN,
            "block_agree_min": BLOCK_AGREE_MIN,
            "not_a_threshold_search": True,
            "structure_exists": structure,
            "d1_d4_same_sign_n": block_agree_n,
            "block_signs": block_sign,
        },
        "roles": roles_eval,
        "simple_causal_role": role,
        "d1_d4_stable": d1d4_stable,
        "enough_for_external_conditioned_complete_strategy": False,
        "did_not_search_thresholds": True,
        "did_not_use_frozen_validation": True,
        "did_not_use_old_confirmation_to_design": True,
        "lookbacks_precommitted": [60, 180, 300],
        "primary_feature": "nk_proxy_ret_60s_sign",
        "filtered_if_direction_gate": nk_pos_s if structure and roles_eval["DIRECTION_GATE"] else None,
    }
