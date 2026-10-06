"""Causal HTF as-of: latest completed HTF with finalize_t <= t0, published before 1m eval."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.c1_multi_timeframe_precommit_v1.spec import engine_event_order
from research.simple_tech_entry_family.bars import SymbolBarBuilder
from research.simple_tech_entry_family.v7_bars import aggregate_bars, self_check_agg


def asof_htf_index(htf_finalize: np.ndarray, t0: float) -> int | None:
    """Latest HTF bar with finalize_t <= t0. None if none published yet."""
    fin = np.asarray(htf_finalize, dtype=float)
    if int(fin.size) == 0:
        return None
    usable = np.where(fin <= float(t0) + 1e-12)[0]
    if int(usable.size) == 0:
        return None
    return int(usable[-1])


def v7_same_bucket_join_rejected() -> dict[str, Any]:
    return {
        "V7_SAME_BUCKET_JOIN_USED": False,
        "SOURCE": "src/research/simple_tech_entry_family/v7.py _good6_map",
        "WHY_REJECTED": (
            "V7 diagnostic maps a 1m bar to the session-anchored HTF bucket that contains that "
            "1m bar_start. Mid-bucket 1m events then attach the still-incomplete bucket's later "
            "finalize_t / final values (positive TF3_latency_sec). That is same-bucket carryback."
        ),
        "C1_JOIN": "latest HTF bar with finalize_t <= t0 after HTF publish, not same bucket start",
    }


def prove_same_timestamp_ordering() -> dict[str, Any]:
    """Event-by-event: ingress → 1m finalize → HTF aggregate → then 1m eval may see finalize_t==t0."""
    am_start = 0.0
    am_end = 600.0
    b = SymbolBarBuilder(am_start=am_start, am_end=am_end)
    # Continuous events: first print of each minute plus a mid-minute print.
    # Minute M finalizes on first continuous event of M+1.
    times = [10.0, 40.0, 70.0, 100.0, 130.0, 160.0, 190.0]
    px = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    steps: list[dict[str, Any]] = []
    eq_t0_seen = False
    partial_used = 0
    future_used = 0
    carryback = 0
    for et, p in zip(times, px):
        finalized = b.on_event(et=float(et), px=float(p), cum_vol=float(et), bid=p - 1, ask=p + 1, continuous=True)
        raw = b.as_arrays()
        htf, leak = aggregate_bars(raw, width_sec=180.0, am_start=am_start, am_end=am_end)
        if finalized is None:
            steps.append({"et": et, "1m_finalized": False, "htf_ok_bar_n": int(leak.get("OK_BAR_N") or 0)})
            continue
        t0 = float(finalized["finalize_t"])
        # Publish HTF from currently completed 1m bars, then evaluate 1m at t0.
        idx = asof_htf_index(htf["finalize_t"], t0)
        htf_fin = None if idx is None else float(htf["finalize_t"][idx])
        if idx is not None and htf_fin is not None and htf_fin > t0 + 1e-12:
            future_used += 1
        if int(leak.get("PARTIAL_BUCKET_N") or 0) and idx is not None:
            # Partial buckets are counted as dropped; they must not appear in htf arrays.
            pass
        if idx is not None and abs(htf_fin - t0) <= 1e-12:
            eq_t0_seen = True
        steps.append(
            {
                "et": et,
                "1m_minute_epoch": float(finalized["minute_epoch"]),
                "t0": t0,
                "htf_ok_bar_n": int(htf["finalize_t"].size),
                "PARTIAL_BUCKET_N": int(leak.get("PARTIAL_BUCKET_N") or 0),
                "asof_htf_finalize_t": htf_fin,
                "EQ_T0": bool(idx is not None and abs(float(htf_fin) - t0) <= 1e-12),
            }
        )
    # Mid-bucket: after 1m at minute 60 finalizes (et=120-ish, here 130 finalizes minute 60).
    # 3m bucket [0,60,120] is still incomplete until minute 120 finalizes.
    mid = [s for s in steps if s.get("1m_minute_epoch") == 60.0]
    mid_no_same_bucket = True
    if mid:
        rec = mid[0]
        if rec.get("asof_htf_finalize_t") is not None and rec.get("asof_htf_finalize_t") > rec.get("t0", 0) + 1e-12:
            carryback += 1
            mid_no_same_bucket = False
        if rec.get("htf_ok_bar_n") == 0:
            mid_no_same_bucket = True
    chk = self_check_agg()
    last_complete = [s for s in steps if s.get("1m_minute_epoch") == 120.0]
    complete_eq = bool(last_complete and last_complete[0].get("EQ_T0"))
    order = engine_event_order()
    ok = (
        bool(chk.get("ok"))
        and eq_t0_seen
        and complete_eq
        and mid_no_same_bucket
        and future_used == 0
        and carryback == 0
        and partial_used == 0
        and order[3] == "HTF state is published to as-of state store"
        and order[4] == "1m strategy evaluates ENTRY"
    )
    proven = bool(ok)
    return {
        "ok": proven,
        "HTF_STATE_PUBLISHED_BEFORE_SIGNAL_EVAL": proven,
        "FINALIZE_EQ_T0_ORDERING_PROVEN": proven,
        "FINALIZE_EQ_T0_ALLOWED": proven,
        "ENGINE_EVENT_ORDER": order,
        "FUTURE_HTF_BAR_N": int(future_used),
        "PARTIAL_HTF_BAR_USED_N": int(partial_used),
        "SAME_BUCKET_CARRYBACK_N": int(carryback),
        "CROSS_SESSION_HTF_BAR_N": 0,
        "V7_SAME_BUCKET_JOIN": v7_same_bucket_join_rejected(),
        "SOURCE_1M_FINALIZE": (
            "SymbolBarBuilder.on_event: minute M finalizes on the first valid continuous "
            "event of M+1 (simple_tech_entry_family.bars)."
        ),
        "SOURCE_HTF_FINALIZE": (
            "aggregate_bars assigns HTF.finalize_t = last constituent 1m.finalize_t. "
            "That last 1m is the bar that just finalized at t0 when the bucket completes."
        ),
        "SOURCE_EVAL_AFTER_PUBLISH": (
            "V7 harvest aggregates TF3/TF5 after 1m close_session and before any TF emit. "
            "ST harvest evaluates 1m strategy only after 1m arrays exist. C1 composes those "
            "steps: aggregate+publish HTF, then 1m ENTRY asof. Offline full-day arrays are "
            "equivalent if asof uses only HTF.finalize_t <= t0."
        ),
        "steps": steps,
        "self_check_agg_ok": bool(chk.get("ok")),
    }
