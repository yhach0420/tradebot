"""Apply EXIT candidates to precomputed fill paths (+ optional board series)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit


def attach_board_series(path: dict[str, Any], board: dict[str, np.ndarray]) -> dict[str, Any]:
    """Align board features to each path tick (causal at that tick). Vectorized."""
    if not path.get("ok") or path["offs"].size == 0:
        path["imb"] = np.array([])
        path["spread"] = np.array([])
        path["bid_qty"] = np.array([])
        path["ask_qty"] = np.array([])
        path["event_rate"] = np.array([])
        path["imb0"] = path["spread0"] = path["bid_qty0"] = path["er0"] = None
        return path

    times = path["times"]
    t = board["t"]
    n = times.size
    if t.size == 0:
        path["imb"] = np.full(n, np.nan)
        path["spread"] = np.full(n, np.nan)
        path["bid_qty"] = np.full(n, np.nan)
        path["ask_qty"] = np.full(n, np.nan)
        path["event_rate"] = np.full(n, np.nan)
        path["imb0"] = path["spread0"] = path["bid_qty0"] = path["er0"] = None
        return path

    # last index at/before each path time
    idx = np.searchsorted(t, times, side="right") - 1
    idx = np.clip(idx, -1, t.size - 1)

    bid = board["bid"]
    ask = board["ask"]
    bq = board["bid_qty"]
    aq = board["ask_qty"]
    special = board["special"]
    fresh = board["fresh_sec"]

    # build last-valid causal quote arrays via forward fill of valid rows
    valid = (
        (~special.astype(bool))
        & np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > 0)
        & np.isfinite(bq) & np.isfinite(aq) & (bq >= MIN_QTY) & (aq >= MIN_QTY)
        & (np.where(np.isfinite(fresh), fresh, 0.0) <= BOARD_FRESHNESS_SEC + 1e-12)
    )
    # last valid index at each board row (inclusive)
    last_valid = np.full(t.size, -1, dtype=np.int64)
    cur = -1
    for i in range(t.size):
        if valid[i]:
            cur = i
        last_valid[i] = cur

    j = np.where(idx >= 0, last_valid[idx], -1)
    imb = np.full(n, np.nan)
    spr = np.full(n, np.nan)
    bqv = np.full(n, np.nan)
    aqv = np.full(n, np.nan)
    okm = j >= 0
    if np.any(okm):
        jj = j[okm]
        bb = bid[jj].astype(float)
        aa = ask[jj].astype(float)
        bqq = bq[jj].astype(float)
        aqq = aq[jj].astype(float)
        mid = (bb + aa) / 2.0
        imb[okm] = (bqq - aqq) / (bqq + aqq)
        spr[okm] = (aa - bb) / mid * 10000.0
        bqv[okm] = bqq
        aqv[okm] = aqq

    # event rate last 30s ending at each path time
    i1 = np.searchsorted(t, times, side="right")
    i0 = np.searchsorted(t, times - 30.0, side="left")
    er = (i1 - i0).astype(float) / 30.0

    path["imb"] = imb
    path["spread"] = spr
    path["bid_qty"] = bqv
    path["ask_qty"] = aqv
    path["event_rate"] = er
    path["imb0"] = float(imb[0]) if np.isfinite(imb[0]) else None
    path["spread0"] = float(spr[0]) if np.isfinite(spr[0]) else None
    path["bid_qty0"] = float(bqv[0]) if np.isfinite(bqv[0]) else None
    path["er0"] = float(er[0]) if np.isfinite(er[0]) else None
    return path


def _prefix_stats(rets: np.ndarray, upto: int) -> tuple[float, float, float]:
    rr = rets[: upto + 1]
    mfe = float(np.max(rr))
    mae = float(np.min(rr))
    return mfe, mae, float(rr[-1])


def apply_candidate(path: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """Walk path; return first exit matching candidate rules, else FIXED fallback."""
    if not path.get("ok") or path["offs"].size == 0:
        return {"ok": False}
    offs, rets, times = path["offs"], path["rets"], path["times"]
    hold = float(cand.get("fixed_hold_sec") or 600.0)
    peak = -1e18
    mae_hit_i: Optional[int] = None
    imb = path.get("imb")
    spr = path.get("spread")
    bq = path.get("bid_qty")
    er = path.get("event_rate")
    spr0 = path.get("spread0")
    bq0 = path.get("bid_qty0")
    er0 = path.get("er0")

    trail_min = float(cand.get("trail_min_hold_sec") or 0.0)
    abs_gb = cand.get("trail_giveback_abs_bps")
    act = cand.get("trail_activate_bps")
    gbf = cand.get("trail_giveback_frac")

    needs_board = any(
        cand.get(k) is not None
        for k in (
            "imb_threshold", "bid_depth_drop_frac", "spread_mult",
            "spread_abs_bps", "er_frac",
        )
    )

    for i in range(offs.size):
        o = float(offs[i])
        r = float(rets[i])
        if r > peak:
            peak = r

        hits: list[str] = []

        if cand.get("hard_stop_bps") is not None and r <= -abs(float(cand["hard_stop_bps"])) + 1e-12:
            hits.append("HARD_STOP")
        if cand.get("profit_target_bps") is not None and r >= float(cand["profit_target_bps"]) - 1e-12:
            hits.append("PROFIT_TARGET")

        if (
            act is not None and gbf is not None and abs_gb is None
            and peak >= float(act) - 1e-12
            and o >= trail_min - 1e-12
            and (peak - r) >= peak * float(gbf) - 1e-12
        ):
            hits.append("TRAIL")

        if (
            act is not None and abs_gb is not None
            and peak >= float(act) - 1e-12
            and (peak - r) >= float(abs_gb) - 1e-12
        ):
            hits.append("ABS_GIVEBACK")

        if cand.get("no_progress_sec") is not None and o >= float(cand["no_progress_sec"]) - 1e-12:
            if cand.get("no_progress_min_mfe") is not None and peak < float(cand["no_progress_min_mfe"]) - 1e-12:
                hits.append("NO_PROGRESS")
            if cand.get("no_progress_min_ret") is not None and r < float(cand["no_progress_min_ret"]) - 1e-12:
                hits.append("NO_PROGRESS")

        if cand.get("early_off_sec") is not None:
            eoff = float(cand["early_off_sec"])
            if o >= eoff - 1e-12 and (i == 0 or float(offs[i - 1]) < eoff - 1e-12):
                mfe_p, mae_p, ret_p = _prefix_stats(rets, i)
                ok_fail = mae_p <= -abs(float(cand.get("early_mae_bps") or 0)) + 1e-12
                ok_fail = ok_fail and ret_p <= -abs(float(cand.get("early_ret_bps") or 0)) + 1e-12
                if cand.get("require_no_rebound") and mfe_p >= 20:
                    ok_fail = False
                if ok_fail:
                    hits.append("EARLY_FAIL")

        if cand.get("seq_t1") is not None and cand.get("seq_t2") is not None:
            t2 = float(cand["seq_t2"])
            if o >= t2 - 1e-12 and (i == 0 or float(offs[i - 1]) < t2 - 1e-12):
                t1 = float(cand["seq_t1"])
                j1 = int(np.searchsorted(offs, t1, side="left"))
                j1 = min(j1, i)
                drop = float(cand["seq_drop_bps"])
                if float(rets[j1]) <= -drop + 1e-12 and r <= -drop + 1e-12 and peak < 15:
                    hits.append("STATE_SEQ")

        if cand.get("mae_trigger_bps") is not None:
            thr = -abs(float(cand["mae_trigger_bps"]))
            if mae_hit_i is None and r <= thr + 1e-12:
                mae_hit_i = i
            if mae_hit_i is not None:
                win = float(cand.get("mae_recovery_window_sec") or 30)
                need = float(cand.get("mae_recovery_need_bps") or 0)
                if o - float(offs[mae_hit_i]) >= win - 1e-12:
                    recovered = bool(np.any(rets[mae_hit_i: i + 1] >= need - 1e-12))
                    if not recovered:
                        hits.append("MAE_RECOVERY_FAIL")
                        mae_hit_i = None

        if needs_board and imb is not None and imb.size == offs.size:
            if cand.get("imb_threshold") is not None:
                thr_i = float(cand["imb_threshold"])
                pers = float(cand.get("imb_persist_sec") or 10)
                if np.isfinite(imb[i]) and imb[i] <= thr_i + 1e-12 and o >= pers - 1e-12:
                    t0 = o - pers
                    k0 = int(np.searchsorted(offs, t0, side="left"))
                    if k0 <= i and np.all(imb[k0: i + 1] <= thr_i + 1e-12):
                        hits.append("IMBALANCE")

            if cand.get("bid_depth_drop_frac") is not None and bq is not None and bq0:
                frac = float(cand["bid_depth_drop_frac"])
                pers = float(cand.get("bid_depth_persist_sec") or 10)
                lim = bq0 * (1.0 - frac)
                if np.isfinite(bq[i]) and bq[i] <= lim + 1e-12 and o >= pers - 1e-12:
                    t0 = o - pers
                    k0 = int(np.searchsorted(offs, t0, side="left"))
                    if k0 <= i and np.all(bq[k0: i + 1] <= lim + 1e-12):
                        hits.append("BID_DEPTH")

            if spr is not None and spr.size == offs.size and np.isfinite(spr[i]):
                if cand.get("spread_mult") is not None and spr0 and spr0 > 0:
                    if spr[i] >= float(cand["spread_mult"]) * spr0 - 1e-12:
                        hits.append("SPREAD")
                if cand.get("spread_abs_bps") is not None:
                    if spr[i] >= float(cand["spread_abs_bps"]) - 1e-12:
                        hits.append("SPREAD")

            if cand.get("er_frac") is not None and er is not None and er0 and er0 > 0:
                win = float(cand.get("er_window_sec") or 30)
                if o >= win - 1e-12 and np.isfinite(er[i]) and er[i] <= er0 * float(cand["er_frac"]) + 1e-12:
                    hits.append("EVENT_DECAY")

        if cand.get("mom_look_sec") is not None and peak >= float(cand.get("require_prior_mfe") or 0) - 1e-12:
            look = float(cand["mom_look_sec"])
            j0 = int(np.searchsorted(offs, o - look, side="left"))
            short = r - float(rets[j0])
            if short <= float(cand.get("mom_ret_bps") or 0) + 1e-12 and o >= look - 1e-12:
                hits.append("MOM_FADE")

        if o >= hold - 1e-12:
            hits.append("FIXED_HOLD")

        priority = (
            "HARD_STOP", "MAE_RECOVERY_FAIL", "EARLY_FAIL", "STATE_SEQ",
            "NO_PROGRESS", "IMBALANCE", "BID_DEPTH", "SPREAD", "EVENT_DECAY", "MOM_FADE",
            "PROFIT_TARGET", "TRAIL", "ABS_GIVEBACK", "FIXED_HOLD",
        )
        for reason in priority:
            if reason in hits:
                return {
                    "ok": True,
                    "exit_ret_bps": r,
                    "hold_sec": o,
                    "exit_time": float(times[i]),
                    "reason": reason,
                    "triggered": reason != "FIXED_HOLD",
                    "mfe_at_exit": peak,
                }

    ex = canonical_fixed_exit(path, hold)
    if ex.get("ok"):
        return {
            "ok": True,
            "exit_ret_bps": float(ex["exit_ret_bps"]),
            "hold_sec": float(ex["exit_off"]),
            "exit_time": float(ex["exit_time"]),
            "reason": "FIXED_HOLD",
            "triggered": False,
            "mfe_at_exit": float(np.max(rets)),
        }
    return {
        "ok": True,
        "exit_ret_bps": float(rets[-1]),
        "hold_sec": float(offs[-1]),
        "exit_time": float(times[-1]),
        "reason": "SESSION_CLOSE",
        "triggered": False,
        "mfe_at_exit": float(np.max(rets)),
    }
