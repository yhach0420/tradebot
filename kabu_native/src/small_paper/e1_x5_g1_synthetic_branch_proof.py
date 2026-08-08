"""Synthetic fixtures proving C1/C2/C3 and STATE_REARM take distinct branches."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def run_synthetic_g1_branch_tests() -> dict[str, Any]:
    from small_paper.e1_x5_forward_shadow import THRESHOLD
    from small_paper.e1_x5_g1_confirmation_guard import E1X5GuardSession, GuardVariant

    t0 = datetime(2026, 7, 23, 10, 0, 0, tzinfo=JST)
    score = THRESHOLD + 0.5
    common = dict(bid=100.0, ask=100.05, score=score, spread_bps=3.0, day="20260723", mid=100.025)
    results: dict[str, Any] = {}

    s1 = E1X5GuardSession(enabled=True, variant=GuardVariant.C1_NEXT_PUSH_HOLD, state_rearm=False)
    r1 = s1.try_entry(symbol="1001.T", ts=t0, event_sequence=1, **common)
    results["C1_arms"] = r1 == "PENDING_CONFIRMATION" and "1001.T" in s1.pending and s1.arm_count == 1

    s2 = E1X5GuardSession(enabled=True, variant=GuardVariant.C2_NO_LOWER_BID, state_rearm=False)
    r2 = s2.try_entry(symbol="1002.T", ts=t0, event_sequence=1, **common)
    results["C2_arms"] = r2 == "PENDING_CONFIRMATION" and s2.arm_count == 1
    results["C1_C2_fingerprints_differ"] = s1.config_hash() != s2.config_hash()

    s3 = E1X5GuardSession(enabled=True, variant=GuardVariant.C3_BID_REBOUND, state_rearm=False)
    r3 = s3.try_entry(symbol="1003.T", ts=t0, event_sequence=1, **common)
    results["C3_arms"] = r3 == "PENDING_CONFIRMATION" and s3.arm_count == 1
    results["C3_fingerprint_distinct"] = s3.config_hash() not in {s1.config_hash(), s2.config_hash()}

    # C2: independent push with lower bid → cancel
    s2b = E1X5GuardSession(enabled=True, variant=GuardVariant.C2_NO_LOWER_BID, state_rearm=False)
    s2b.try_entry(symbol="2002.T", ts=t0, event_sequence=10, **common)
    s2b.confirm_on_independent_push(
        symbol="2002.T",
        ts=t0 + timedelta(seconds=1),
        bid=99.5,
        ask=99.55,
        sequence=11,
        observe_kind="SCORE",
        score=score,
        spread_bps=3.0,
        day="20260723",
    )
    results["C2_cancel_on_lower_bid"] = (
        s2b.cancel_count >= 1
        and "BID_LOWER_THAN_ARM" in s2b.cancel_reasons
        and "2002.T" not in s2b.pending
    )

    # C1: same lower-bid independent push does NOT cancel via BID_LOWER_THAN_ARM
    s1b = E1X5GuardSession(enabled=True, variant=GuardVariant.C1_NEXT_PUSH_HOLD, state_rearm=False)
    s1b.try_entry(symbol="2001.T", ts=t0, event_sequence=20, **common)
    s1b.confirm_on_independent_push(
        symbol="2001.T",
        ts=t0 + timedelta(seconds=1),
        bid=99.5,
        ask=99.55,
        sequence=21,
        observe_kind="SCORE",
        score=score,
        spread_bps=3.0,
        day="20260723",
    )
    results["C1_branch_differs_from_C2"] = "BID_LOWER_THAN_ARM" not in s1b.cancel_reasons and (
        s1b.confirm_count >= 1 or "2001.T" in s1b.positions or "2001.T" in s1b.pending
    )

    off = E1X5GuardSession(enabled=True, variant=GuardVariant.C1_NEXT_PUSH_HOLD, state_rearm=False)
    on = E1X5GuardSession(enabled=True, variant=GuardVariant.C1_NEXT_PUSH_HOLD, state_rearm=True)
    results["rearm_fingerprint_differs"] = off.config_hash() != on.config_hash()

    on.disarmed_after_stop.add("3001.T")
    blocked = on.try_entry(symbol="3001.T", ts=t0, event_sequence=30, **common)
    results["rearm_on_blocks_until_edge"] = blocked == "DISARMED_AFTER_STOP"
    results["rearm_off_allows"] = off._rearm_allowed("3001.T", score=score, spread_bps=3.0) is True

    base = E1X5GuardSession(enabled=True, variant=GuardVariant.BASE, state_rearm=False)
    br = base.try_entry(symbol="4001.T", ts=t0, event_sequence=40, **common)
    results["BASE_immediate_entry"] = br is None and "4001.T" in base.positions
    results["guards_not_all_base"] = bool(results["C1_arms"]) and bool(results["BASE_immediate_entry"])

    # variant_id reaches session
    results["variant_id_on_session"] = (
        s1.config_id() == "C1_NEXT_PUSH_HOLD"
        and s2.config_id() == "C2_NO_LOWER_BID"
        and s3.config_id() == "C3_BID_REBOUND"
        and on.config_id() == "C1_NEXT_PUSH_HOLD+STATE_REARM"
    )

    ok = all(bool(v) for v in results.values())
    return {"ok": ok, "results": results, "message": "synthetic G1 branch coverage"}
