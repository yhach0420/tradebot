"""Resolve C14 contractual evaluation end from implementation/config. No RCA-invented horizon."""
from __future__ import annotations

from typing import Any

from research.v1r_exit_v2_asymmetric.states import HORIZONS
from small_paper.v1r_exit_v2_contract import FROZEN_CONTINUATION, FROZEN_GUARD, load_exit_v2_candidate
from small_paper.v1r_live_dual_lane import session_end_for_position


def c14_horizons(c14: dict[str, Any]) -> dict[str, float]:
    """Latest C14 Arch E observation window from frozen EXIT V2 + C14 candidate."""
    cand = load_exit_v2_candidate()
    guard = dict(c14.get("guard") or {})
    monitor = guard.get("monitor_to_sec")
    if monitor is None:
        monitor = (cand.get("guard") or {}).get("monitor_to") or FROZEN_GUARD.get("monitor_to")
    decision = float(max(h for h in HORIZONS if float(h) <= 600.0 + 1e-12))
    extend = float(max(HORIZONS))
    if abs(extend - 750.0) > 1e-9:
        raise ValueError(f"C14 extend horizon drifted: {extend}")
    if abs(decision - 600.0) > 1e-9:
        raise ValueError(f"C14 decision horizon drifted: {decision}")
    return {
        "monitor_to_sec": float(monitor),
        "decision_sec": float(decision),
        "extend_sec": float(extend),
        "continuation_id": str((cand.get("continuation") or FROZEN_CONTINUATION).get("id") or ""),
        "guard_id": str((cand.get("guard") or FROZEN_GUARD).get("id") or guard.get("id") or ""),
        "architecture": str(cand.get("frozen_architecture") or "E"),
    }


def canonical_eval_end(*, date: str, session: str, fill_t: float, horizons: dict[str, float]) -> float:
    sess_end = float(session_end_for_position(date=date, session=session, fill_time=float(fill_t)))
    contract_end = float(fill_t) + float(horizons["extend_sec"])
    return float(min(sess_end, contract_end))
