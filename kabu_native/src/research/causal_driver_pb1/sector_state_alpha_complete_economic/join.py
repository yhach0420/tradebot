"""Alpha-first admission and first-causal exit. Uses the frozen runner and clock helpers."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.runner import (
    FINAL_ALPHA_CLOCK,
    Q60_OFF,
    admission_decision,
    final_clock_actions,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation import TECHNICAL_EXIT_ID
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import flatten_open, next_open_after


def _signal(entry_t: str) -> str:
    prev = hhmm_add(str(entry_t)[:5], -1)
    return str(prev or entry_t)[:5]


def alpha_live(clocks: dict[str, dict[str, Any]], hhmm: str) -> bool:
    row = clocks.get(str(hhmm)[:5])
    if not row or not row.get("available"):
        return False
    if row.get("state") != "ACTIVE":
        return False
    start = str(row.get("episode_start") or "")
    return bool(start) and start <= str(hhmm)[:5]


def _bar_after(rec: dict[str, Any], decision_t: str) -> dict[str, Any] | None:
    nxt = next_open_after(rec, after_t=decision_t, allow_pm=True)
    if nxt:
        return {"exit_t": nxt["t"], "exit_px": float(nxt["px"]), "ops_flatten": False}
    flat = flatten_open(rec, fill_t=decision_t)
    if not flat:
        return None
    return {"exit_t": flat["t"], "exit_px": float(flat["px"]), "ops_flatten": str(flat["t"])[:5] == "15:20"}


def alpha_decision(clocks: dict[str, dict[str, Any]], *, fill_t: str, symbol: str) -> dict[str, str] | None:
    fill = str(fill_t)[:5]
    for hhmm in sorted(clocks):
        if hhmm < fill:
            continue
        row = clocks[hhmm]
        if hhmm == FINAL_ALPHA_CLOCK:
            fresh = fill == FINAL_ALPHA_CLOCK
            actions = final_clock_actions(
                driver_available=bool(row.get("available")),
                driver_value=row.get("value"),
                open_positions=[] if fresh else [{"symbol": symbol}],
                fills_from_1124=[{"symbol": symbol}] if fresh else [],
            )
            if not actions:
                return None
            action = actions[0]
            return {"decision_t": hhmm, "event": action["event"], "reason": action["reason"]}
        if row.get("available") and row.get("value") is not None and float(row["value"]) <= float(Q60_OFF):
            return {"decision_t": hhmm, "event": "ALPHA_THESIS_INVALIDATION", "reason": "Q60_STATE_LOSS"}
    return None


def classify_event(
    *,
    event: dict[str, Any],
    clocks: dict[str, dict[str, Any]],
    lost: bool,
    lost_at: str | None,
) -> dict[str, Any]:
    entry_t = str(event.get("entry_t") or "")[:5]
    signal = _signal(entry_t)
    side = str(event.get("direction") or "")
    long = side == "bull" or int(event.get("DIR") or 0) > 0
    clock = clocks.get(signal) or {}
    base = {
        "symbol": str(event.get("symbol") or ""),
        "date": str(event.get("date") or ""),
        "entry_type": str(event.get("entry_type") or ""),
        "signal_t": signal,
        "entry_t": entry_t,
        "entry_px": event.get("entry_px"),
        "side": "long" if long else "short",
        "episode_id": str(clock.get("episode_id") or ""),
    }
    if not long:
        if alpha_live(clocks, signal):
            return {**base, "status": "ALPHA_DIRECTION_MISMATCH"}
        return {**base, "status": "PB1_NOT_CONSUMED"}
    if not alpha_live(clocks, signal):
        return {**base, "status": "STALE_OR_NO_ALPHA"}
    gate = admission_decision(qualification_clock=signal)
    if not gate.get("admitted"):
        return {**base, "status": str(gate.get("reason") or "REJECT")}
    if event.get("same_bar_entry") or in_lunch(entry_t) or entry_t >= "15:20":
        return {**base, "status": "ILLEGAL_FILL"}
    if lost and lost_at and str(lost_at)[:5] < entry_t:
        return {**base, "status": "LOST_BEFORE_FILL"}
    return {**base, "status": "ADMIT_ATTEMPT", "thesis_lost": bool(lost), "thesis_lost_at": str(lost_at or "")[:5]}


def attach_exit(row: dict[str, Any], *, clocks: dict[str, dict[str, Any]], rec: dict[str, Any]) -> dict[str, Any]:
    alpha = alpha_decision(clocks, fill_t=str(row["entry_t"]), symbol=str(row["symbol"]))
    lost_at = str(row.get("thesis_lost_at") or "")[:5]
    pb1_t = lost_at if row.get("thesis_lost") and lost_at and lost_at >= str(row["entry_t"])[:5] else ""
    reasons: list[str] = []
    decision = ""
    if alpha and pb1_t:
        if pb1_t < alpha["decision_t"]:
            decision = pb1_t
            reasons = ["PB1_THESIS_LOST"]
        elif pb1_t > alpha["decision_t"]:
            decision = alpha["decision_t"]
            reasons = [alpha["reason"]]
        else:
            decision = pb1_t
            reasons = ["PB1_THESIS_LOST", alpha["reason"]]
    elif alpha:
        decision = alpha["decision_t"]
        reasons = [alpha["reason"]]
    elif pb1_t:
        decision = pb1_t
        reasons = ["PB1_THESIS_LOST"]
    else:
        return {**row, "status": "NO_EXIT_DECISION"}
    bar = _bar_after(rec, decision)
    if bar is None or str(bar["exit_t"])[:5] <= str(row["entry_t"])[:5]:
        return {**row, "status": "NO_EXIT_BAR"}
    exit_reason = "SAME_CLOCK_DUAL" if len(reasons) > 1 else reasons[0]
    if exit_reason == "PB1_THESIS_LOST":
        exit_reason = TECHNICAL_EXIT_ID
    return {
        **row,
        "status": "CANDIDATE",
        "decision_t": decision,
        "exit_reasons": reasons,
        "exit_reason": exit_reason,
        "exit_t": bar["exit_t"],
        "exit_px": bar["exit_px"],
        "same_clock_dual": len(reasons) > 1,
        "session_flat_fill": str(bar["exit_t"])[:5] == "15:20",
    }
