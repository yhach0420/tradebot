"""Lightweight OBSERVER_ONLY hook — never blocks ENTRY; opt-in only."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from .measure import MeasurementInput, measure

ENV_FLAG = "E1_X13_EXECUTION_RISK_OBSERVER"


def observer_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def observe_candidate(
    cand: dict[str, Any],
    *,
    rolling: Optional[dict[str, Any]] = None,
    telemetry_sink: Optional[list] = None,
) -> dict[str, Any]:
    """Attach observer telemetry. On exception: error telemetry only; never raises to ENTRY path."""
    rolling = rolling or {}
    try:
        t0 = time.perf_counter()
        inp = MeasurementInput(
            symbol=str(cand.get("symbol") or ""),
            event_time=cand.get("event_time") or cand.get("ts"),
            best_bid=_f(cand.get("best_bid")),
            best_ask=_f(cand.get("best_ask")),
            best_bid_qty=_f(cand.get("best_bid_qty")),
            best_ask_qty=_f(cand.get("best_ask_qty")),
            bid_time=cand.get("bid_time"),
            ask_time=cand.get("ask_time"),
            reference_price=_f(cand.get("reference_price")),
            tick_size=_f(cand.get("tick_size")),
            board_age_sec=_f(cand.get("board_age_sec")),
            rolling_spread_cost_p95=_f(rolling.get("rolling_spread_cost_p95")),
            rolling_down_bid_jump_p95=_f(rolling.get("rolling_down_bid_jump_p95")),
            rolling_executable_loss_5s_p95=_f(rolling.get("rolling_executable_loss_5s_p95")),
        )
        out = measure(inp)
        tel = {
            "candidate_id": cand.get("candidate_id"),
            "event_time": out.event_time,
            "symbol": out.symbol,
            "one_lot_notional_yen": out.one_lot_notional_yen,
            "one_tick_risk_yen_100": out.one_tick_risk_yen_100,
            "current_spread_cost_yen_100": out.current_spread_cost_yen_100,
            "estimated_execution_risk_yen": out.estimated_execution_risk_yen,
            "history_support_status": rolling.get("history_support_status", "UNKNOWN"),
            "best_bid_qty": cand.get("best_bid_qty"),
            "best_ask_qty": cand.get("best_ask_qty"),
            "board_age_sec": out.board_age_sec,
            "measurement_status": out.measurement_status,
            "reason_codes": out.reason_codes,
            "capital_policy_status": "CAPITAL_POLICY_NOT_EVALUATED",
            "execution_risk": out.execution_risk,
            "strategy_loss_risk": out.strategy_loss_risk,
            "total_trade_risk": out.total_trade_risk,
            "observer_latency_ms": (time.perf_counter() - t0) * 1000.0,
            "entry_blocking": False,
            "enforcement": False,
        }
        if telemetry_sink is not None:
            telemetry_sink.append(tel)
        # mutate copy-friendly: attach without changing decision fields
        cand.setdefault("execution_risk_observer", tel)
        return cand
    except Exception as e:  # noqa: BLE001 — observer must never break ENTRY
        err = {
            "candidate_id": cand.get("candidate_id"),
            "symbol": cand.get("symbol"),
            "measurement_status": "EXECUTION_RISK_OBSERVER_ERROR",
            "reason_codes": ["OBSERVER_EXCEPTION"],
            "error": str(e),
            "capital_policy_status": "CAPITAL_POLICY_NOT_EVALUATED",
            "entry_blocking": False,
        }
        if telemetry_sink is not None:
            telemetry_sink.append(err)
        cand.setdefault("execution_risk_observer", err)
        return cand


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def decision_parity_fixture(
    candidates: list[dict[str, Any]],
    *,
    rolling: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """OFF vs ON: ENTRY accept/reject counts must match (observer never rejects)."""
    off = [dict(c) for c in candidates]
    on = [dict(c) for c in candidates]
    sink: list = []
    for c in on:
        observe_candidate(c, rolling=rolling, telemetry_sink=sink)

    def counts(rows: list[dict]) -> dict[str, int]:
        return {
            "entry_candidate_count": len(rows),
            "entry_accept_count": sum(1 for r in rows if r.get("decision") == "ACCEPT"),
            "entry_reject_count": sum(1 for r in rows if r.get("decision") == "REJECT"),
            "exit_count": sum(1 for r in rows if r.get("exit")),
        }

    c_off, c_on = counts(off), counts(on)
    # decisions unchanged
    decisions_match = all(
        off[i].get("decision") == on[i].get("decision")
        and off[i].get("exit_reason") == on[i].get("exit_reason")
        for i in range(len(off))
    )
    latencies = [t.get("observer_latency_ms") or 0.0 for t in sink]
    return {
        "off": c_off,
        "on": c_on,
        "decision_parity_pass": c_off == c_on and decisions_match,
        "telemetry_n": len(sink),
        "mean_observer_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "max_observer_latency_ms": max(latencies) if latencies else 0.0,
        "performance_ok": (max(latencies) if latencies else 0.0) < 50.0,
    }


def append_observer_fields_to_jsonl_record(rec: dict[str, Any], observer_tel: dict[str, Any]) -> dict[str, Any]:
    """Merge observer fields into existing single telemetry JSONL record (no new file)."""
    out = dict(rec)
    for k in (
        "one_lot_notional_yen", "one_tick_risk_yen_100", "current_spread_cost_yen_100",
        "estimated_execution_risk_yen", "history_support_status", "best_bid_qty", "best_ask_qty",
        "board_age_sec", "measurement_status", "reason_codes", "capital_policy_status",
    ):
        if k in observer_tel:
            out[k] = observer_tel[k]
    out["execution_risk_observer"] = observer_tel
    return out
