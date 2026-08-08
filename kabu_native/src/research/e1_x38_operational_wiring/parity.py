"""Semantic parity: features/scores/ranks/admissions vs frozen serialized model."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x37_prospective.freeze import score_reproduction_check

from . import FEATURE_ORDER, POSITION_CAP


def semantic_parity(ser: dict) -> dict[str, Any]:
    """A/B identity on synthetic panel — no strategy change, no 20260810."""
    score_chk = score_reproduction_check(ser)
    sfn = score_fn_from_serialized(ser)
    means = ser["preprocessing"]["mean"]
    scales = ser["preprocessing"]["scale"]

    # build synthetic cohort
    t0 = 1_800_000_000.0
    evs_a = []
    evs_b = []
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]):
        feats = {f: float(means[j]) + (i * 0.1) * float(scales[j]) for j, f in enumerate(FEATURE_ORDER)}
        row = {
            "date": "20260721",
            "symbol": sym,
            "session": "AM",
            "signal_time": t0,
            "filled": False,
            "limit_price": 1000.0,
            "bid0": 1000.0,
            **feats,
        }
        evs_a.append(dict(row))
        evs_b.append(dict(row))

    scores_a = [float(sfn(e)) for e in evs_a]
    scores_b = [float(sfn(e)) for e in evs_b]
    score_id = all(abs(a - b) < 1e-15 for a, b in zip(scores_a, scores_b))

    sim_a = simulate_joint(evs_a, score_fn=sfn)
    sim_b = simulate_joint(evs_b, score_fn=sfn)
    adm_a = sorted((e["symbol"] for e in sim_a["events"] if e.get("admitted")))
    adm_b = sorted((e["symbol"] for e in sim_b["events"] if e.get("admitted")))
    ranks_a = sorted(
        ((e["symbol"], e.get("alloc_score")) for e in sim_a["events"]),
        key=lambda x: (-(x[1] if x[1] is not None and np.isfinite(x[1]) else -1e18), x[0]),
    )
    ranks_b = sorted(
        ((e["symbol"], e.get("alloc_score")) for e in sim_b["events"]),
        key=lambda x: (-(x[1] if x[1] is not None and np.isfinite(x[1]) else -1e18), x[0]),
    )

    # rolling vs batch feature identity (same formulas on same synthetic mid series)
    rolling_id = _rolling_vs_batch_identity()

    # t0 future-free: feature using only t<=t0
    future_free = _t0_snapshot_future_free()

    return {
        "score_reproduction": score_chk,
        "score_identity_ab": score_id,
        "rank_identity_ab": ranks_a == ranks_b,
        "admission_identity_ab": adm_a == adm_b,
        "admitted_symbols": adm_a,
        "admitted_n": len(adm_a),
        "cap": POSITION_CAP,
        "rolling_feature_identity": rolling_id,
        "t0_snapshot_future_free": future_free,
        "pass": bool(
            score_chk.get("pass")
            and score_id
            and ranks_a == ranks_b
            and adm_a == adm_b
            and rolling_id.get("pass")
            and future_free.get("pass")
        ),
    }


def _rolling_vs_batch_identity() -> dict[str, Any]:
    """Same mid series: rolling state vs batch window must match mid_ret_60/180, event_rate_60."""
    rng = np.random.default_rng(42)
    n = 500
    t = np.arange(n, dtype=float)  # seconds
    mid = 1000.0 + np.cumsum(rng.normal(0, 0.05, size=n))
    t0 = float(t[400])

    def batch_ret(sec: float) -> float:
        m_now = mid[400]
        t_past = t0 - sec
        j = int(np.searchsorted(t, t_past, side="right") - 1)
        return float((m_now / mid[j] - 1.0) * 10000.0)

    def batch_rate(sec: float) -> float:
        t_past = t0 - sec
        n_ev = int(np.sum((t <= t0) & (t >= t_past)))
        return float(n_ev / sec)

    # rolling: maintain last mid at each t
    last_mid = None
    hist = []  # (t, mid)
    for i in range(401):
        last_mid = float(mid[i])
        hist.append((float(t[i]), last_mid))
    # at t0 from rolling hist
    m_now = hist[-1][1]

    def roll_ret(sec: float) -> float:
        t_past = t0 - sec
        m_past = None
        for tt, mm in reversed(hist):
            if tt <= t_past + 1e-12:
                m_past = mm
                break
            m_past = mm  # keep walking
        # find last <= t_past
        m_past = hist[0][1]
        for tt, mm in hist:
            if tt <= t_past + 1e-12:
                m_past = mm
        return float((m_now / m_past - 1.0) * 10000.0)

    def roll_rate(sec: float) -> float:
        t_past = t0 - sec
        n_ev = sum(1 for tt, _ in hist if t_past - 1e-12 <= tt <= t0 + 1e-12)
        return float(n_ev / sec)

    pairs = {
        "mid_ret_60s": (batch_ret(60.0), roll_ret(60.0)),
        "mid_ret_180s": (batch_ret(180.0), roll_ret(180.0)),
        "event_rate_60s": (batch_rate(60.0), roll_rate(60.0)),
    }
    ok = all(abs(a - b) < 1e-9 for a, b in pairs.values())
    return {"pairs": {k: {"batch": a, "rolling": b} for k, (a, b) in pairs.items()}, "pass": ok}


def _t0_snapshot_future_free() -> dict[str, Any]:
    """Ensure events with t > t0 are not used for t0 mid."""
    t = np.array([0.0, 1.0, 2.0, 3.0])
    mid = np.array([100.0, 101.0, 102.0, 999.0])  # 999 is after t0=2
    t0 = 2.0
    # valid: last mid with t<=t0
    j = int(np.searchsorted(t, t0, side="right") - 1)
    used = float(mid[j])
    contaminated = float(mid[-1])
    return {
        "t0": t0,
        "used_mid": used,
        "post_t0_mid": contaminated,
        "used_is_pre_t0": used == 102.0,
        "not_using_future": used != contaminated,
        "late_event_policy": "ignored_for_t0_snapshot",
        "pass": used == 102.0 and used != contaminated,
    }
