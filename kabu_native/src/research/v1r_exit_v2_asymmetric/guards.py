"""Early Failure Guard candidates (small, concept-limited grid)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.v1r_exit_v2_asymmetric.states import exit_at_horizon


def generate_guards() -> list[dict[str, Any]]:
    """Limited families only — not a global search."""
    guards: list[dict[str, Any]] = []

    # Control: simple STOP
    for s in (50, 75, 100, 150):
        guards.append({
            "family": "STOP",
            "id": f"STOP_{s}",
            "kind": "stop",
            "stop_bps": float(s),
            "monitor_to": 120.0,
        })

    # MAE recovery failure
    for mae_t in (30, 40, 50, 75):
        for win in (20, 30, 45):
            for need in (0, 10):
                guards.append({
                    "family": "MAE_RECOVERY",
                    "id": f"MAER_m{mae_t}_w{win}_r{need}",
                    "kind": "mae_recovery",
                    "mae_trigger_bps": float(mae_t),
                    "window_sec": float(win),
                    "need_bps": float(need),
                    "monitor_to": 120.0,
                })

    # Imbalance persistence
    for pers in (5, 10, 20):
        for thr in (-0.1, -0.2, -0.3):
            guards.append({
                "family": "IMBALANCE",
                "id": f"IMB_p{pers}_t{int(thr*100)}",
                "kind": "imbalance",
                "persist_sec": float(pers),
                "imb_threshold": float(thr),
                "monitor_to": 120.0,
            })

    # Sell-fail / early state
    for off in (20, 30, 45, 60):
        for mae_t in (30, 40, 50):
            for ret_t in (15, 25, 35):
                guards.append({
                    "family": "SELL_FAIL",
                    "id": f"SF_o{off}_m{mae_t}_r{ret_t}",
                    "kind": "sell_fail",
                    "decision_off": float(off),
                    "mae_bps": float(mae_t),
                    "ret_bps": float(ret_t),
                    "require_no_rebound": True,
                    "monitor_to": 120.0,
                })

    # State sequence: drop at t1, still down at t2, no rebound
    for t1, t2 in ((10, 30), (20, 45), (30, 60)):
        for drop in (30, 40, 50):
            guards.append({
                "family": "STATE_SEQ",
                "id": f"SEQ_{t1}_{t2}_d{drop}",
                "kind": "state_seq",
                "t1": float(t1),
                "t2": float(t2),
                "drop_bps": float(drop),
                "monitor_to": 120.0,
            })

    seen = set()
    out = []
    for g in guards:
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        out.append(g)
    return out


def detect_guard_trigger(bundle: dict[str, Any], guard: dict[str, Any]) -> dict[str, Any]:
    """
    Walk path causally within monitor window; return first trigger off (decision time),
    then executable exit via FIRST_VALID_BUY1_AT_OR_AFTER.
    """
    path = bundle["path"]
    if not path.get("ok") or path["offs"].size == 0:
        return {"hit": False}
    offs, rets = path["offs"], path["rets"]
    monitor_to = float(guard.get("monitor_to") or 120.0)
    kind = guard["kind"]
    peak = -1e18
    mae_hit_i: Optional[int] = None
    imb = path.get("imb")

    for i in range(offs.size):
        o = float(offs[i])
        if o > monitor_to + 1e-12:
            break
        r = float(rets[i])
        if r > peak:
            peak = r

        hit = False
        reason = None

        if kind == "stop":
            if r <= -abs(float(guard["stop_bps"])) + 1e-12:
                hit, reason = True, "HARD_STOP"

        elif kind == "mae_recovery":
            thr = -abs(float(guard["mae_trigger_bps"]))
            if mae_hit_i is None and r <= thr + 1e-12:
                mae_hit_i = i
            if mae_hit_i is not None:
                win = float(guard["window_sec"])
                need = float(guard["need_bps"])
                if o - float(offs[mae_hit_i]) >= win - 1e-12:
                    recovered = bool(np.any(rets[mae_hit_i: i + 1] >= need - 1e-12))
                    if not recovered:
                        hit, reason = True, "MAE_RECOVERY_FAIL"

        elif kind == "imbalance":
            if imb is not None and imb.size == offs.size:
                thr = float(guard["imb_threshold"])
                pers = float(guard["persist_sec"])
                if np.isfinite(imb[i]) and imb[i] <= thr + 1e-12 and o >= pers - 1e-12:
                    k0 = int(np.searchsorted(offs, o - pers, side="left"))
                    if k0 <= i and np.all(np.isfinite(imb[k0: i + 1])) and np.all(imb[k0: i + 1] <= thr + 1e-12):
                        hit, reason = True, "IMBALANCE"

        elif kind == "sell_fail":
            eoff = float(guard["decision_off"])
            if o >= eoff - 1e-12 and (i == 0 or float(offs[i - 1]) < eoff - 1e-12):
                rr = rets[: i + 1]
                mfe_p = float(np.max(rr))
                mae_p = float(np.min(rr))
                ok = mae_p <= -abs(float(guard["mae_bps"])) + 1e-12
                ok = ok and r <= -abs(float(guard["ret_bps"])) + 1e-12
                if guard.get("require_no_rebound") and mfe_p >= 20:
                    ok = False
                if ok:
                    hit, reason = True, "SELL_FAIL"

        elif kind == "state_seq":
            t2 = float(guard["t2"])
            if o >= t2 - 1e-12 and (i == 0 or float(offs[i - 1]) < t2 - 1e-12):
                j1 = int(np.searchsorted(offs, float(guard["t1"]), side="left"))
                j1 = min(j1, i)
                drop = float(guard["drop_bps"])
                if float(rets[j1]) <= -drop + 1e-12 and r <= -drop + 1e-12 and peak < 15:
                    hit, reason = True, "STATE_SEQ"

        if hit:
            ex = exit_at_horizon(path, o)
            if not ex.get("ok"):
                return {"hit": False}
            return {
                "hit": True,
                "trigger_off": o,
                "reason": reason,
                "exit_off": ex["exit_off"],
                "exit_time": ex["exit_time"],
                "exit_ret_bps": ex["exit_ret_bps"],
                "executable": True,
            }

    return {"hit": False}
