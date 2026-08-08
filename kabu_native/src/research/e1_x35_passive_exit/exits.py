"""EXIT families on executable bid path — train-derived thresholds only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import PRIORITY


def simulate_exit(
    path: dict[str, Any],
    *,
    hard_stop_bps: Optional[float] = None,
    profit_target_bps: Optional[float] = None,
    trail_activate_bps: Optional[float] = None,
    trail_giveback_frac: Optional[float] = None,
    no_progress_sec: Optional[float] = None,
    no_progress_min_mfe: Optional[float] = None,
    fixed_hold_sec: Optional[float] = None,
    max_hold_sec: Optional[float] = None,
) -> dict[str, Any]:
    """
    Walk path; first matching condition by PRIORITY at each tick.
    Exit price = that tick's executable bid return (already in rets).
    """
    if not path.get("ok") or path["offs"].size == 0:
        return {"ok": False}
    offs, rets, times = path["offs"], path["rets"], path["times"]
    entry_t = float(path["entry_t"])
    sess_end = float(path["sess_end"])
    peak = -1e18
    peak_t = 0.0

    for i in range(offs.size):
        o = float(offs[i])
        r = float(rets[i])
        ti = float(times[i])
        if r > peak:
            peak = r
            peak_t = o

        # collect triggers at this tick
        hits: list[str] = []

        if hard_stop_bps is not None and r <= -abs(hard_stop_bps) + 1e-12:
            hits.append("HARD_STOP")
        if profit_target_bps is not None and r >= profit_target_bps - 1e-12:
            hits.append("PROFIT_TARGET")
        # Activated trailing: peak reached activate, then gave back giveback_frac of peak
        if (
            trail_activate_bps is not None
            and trail_giveback_frac is not None
            and peak >= trail_activate_bps - 1e-12
            and (peak - r) >= peak * trail_giveback_frac - 1e-12
        ):
            hits.append("ACTIVATED_TRAILING")

        if no_progress_sec is not None and o >= no_progress_sec - 1e-12:
            min_mfe = no_progress_min_mfe if no_progress_min_mfe is not None else 0.0
            if peak < min_mfe - 1e-12:
                hits.append("NO_PROGRESS")

        if fixed_hold_sec is not None and o >= fixed_hold_sec - 1e-12:
            hits.append("FIXED_HOLD")
        if max_hold_sec is not None and o >= max_hold_sec - 1e-12:
            hits.append("FIXED_HOLD")

        # resolve by priority
        for reason in PRIORITY:
            if reason in hits:
                return {
                    "ok": True,
                    "exit_ret_bps": r,
                    "hold_sec": o,
                    "exit_time": ti,
                    "reason": reason,
                    "mfe_at_exit": peak,
                    "path_mfe": peak,
                }

    # session close at last available bid
    return {
        "ok": True,
        "exit_ret_bps": float(rets[-1]),
        "hold_sec": float(offs[-1]),
        "exit_time": float(times[-1]),
        "reason": "SESSION_CLOSE",
        "mfe_at_exit": float(np.max(rets)),
        "path_mfe": float(np.max(rets)),
    }


def build_catalog(train_eps: list[dict]) -> list[dict[str, Any]]:
    """Train-only quantile-derived EXIT candidates (limited semantic families)."""
    mfes = [float(e["metrics"]["mfe"]) for e in train_eps if e["metrics"].get("ok")]
    maes = [float(e["metrics"]["mae"]) for e in train_eps if e["metrics"].get("ok")]
    t_mfes = [float(e["metrics"]["time_to_mfe"]) for e in train_eps if e["metrics"].get("ok")]
    if len(mfes) < 20:
        return []

    mfe_a = np.asarray(mfes)
    mae_a = np.asarray(maes)
    tm_a = np.asarray(t_mfes)

    mfe_q50 = float(np.quantile(mfe_a, 0.50))
    mfe_q70 = float(np.quantile(mfe_a, 0.70))
    # stop candidates: abs of MAE quantiles (positive magnitudes)
    stop_q30 = float(abs(np.quantile(mae_a, 0.30)))
    stop_q50 = float(abs(np.quantile(mae_a, 0.50)))
    stop_q70 = float(abs(np.quantile(mae_a, 0.70)))
    # clamp stops to sensible range
    stops = sorted({max(10.0, min(80.0, s)) for s in (stop_q30, stop_q50, stop_q70, 20.0, 30.0)})
    targets = sorted({max(10.0, min(100.0, t)) for t in (mfe_q50, mfe_q70, 20.0, 30.0, 50.0)})
    np_secs = sorted({max(30.0, min(600.0, float(np.quantile(tm_a, q)))) for q in (0.30, 0.50, 0.70)})
    np_secs = sorted(set(list(np_secs) + [60.0, 120.0, 180.0, 300.0]))

    cats: list[dict[str, Any]] = []

    # E0 fixed horizons
    for H in (180, 300, 600, 900):
        cats.append({
            "id": f"E0_FIXED_{H}",
            "family": "E0_FIXED",
            "fixed_hold_sec": float(H),
        })

    # E1 hard stop + max hold 900
    for s in stops[:4]:
        cats.append({
            "id": f"E1_STOP_{s:.0f}",
            "family": "E1_HARD_STOP",
            "hard_stop_bps": float(s),
            "max_hold_sec": 900.0,
        })

    # E2 profit target + max hold 900
    for t in targets[:4]:
        cats.append({
            "id": f"E2_TARGET_{t:.0f}",
            "family": "E2_PROFIT_TARGET",
            "profit_target_bps": float(t),
            "max_hold_sec": 900.0,
        })

    # E3 activated trailing
    for act in (mfe_q50, max(15.0, mfe_q50 * 0.7), 20.0):
        act = float(max(10.0, min(80.0, act)))
        for gb in (0.30, 0.50):
            cats.append({
                "id": f"E3_TRAIL_act{act:.0f}_gb{int(gb*100)}",
                "family": "E3_ACTIVATED_TRAILING",
                "trail_activate_bps": act,
                "trail_giveback_frac": float(gb),
                "max_hold_sec": 900.0,
            })

    # E4 no progress
    for sec in np_secs[:5]:
        for min_mfe in (0.0, 10.0):
            cats.append({
                "id": f"E4_NOPROG_{sec:.0f}_mfe{min_mfe:.0f}",
                "family": "E4_NO_PROGRESS",
                "no_progress_sec": float(sec),
                "no_progress_min_mfe": float(min_mfe),
                "max_hold_sec": 900.0,
            })

    # E5 hybrid: stop + trail + no progress
    s0 = stops[min(1, len(stops) - 1)]
    act0 = float(max(15.0, min(50.0, mfe_q50)))
    for gb in (0.30, 0.50):
        for np_sec in (120.0, 180.0, 300.0):
            cats.append({
                "id": f"E5_HYB_s{s0:.0f}_a{act0:.0f}_gb{int(gb*100)}_np{np_sec:.0f}",
                "family": "E5_HYBRID",
                "hard_stop_bps": float(s0),
                "trail_activate_bps": act0,
                "trail_giveback_frac": float(gb),
                "no_progress_sec": float(np_sec),
                "no_progress_min_mfe": 10.0,
                "max_hold_sec": 900.0,
            })

    # de-dupe by id
    seen = set()
    uniq = []
    for c in cats:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    return uniq


def run_spec(ep: dict, spec: dict) -> dict[str, Any]:
    kwargs = {k: v for k, v in spec.items() if k not in ("id", "family")}
    return simulate_exit(ep["path"], **kwargs)
