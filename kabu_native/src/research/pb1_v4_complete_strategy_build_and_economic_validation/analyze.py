"""Binding/state-machine freeze. Development PnL is recorded, never a freeze gate."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    CAP,
    CASE_FAIL,
    CASE_READY,
    NEXT_CONF1,
    NEXT_STOP,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import (
    COMPLETE_STRATEGY_IDENTITY,
    complete_strategy_sha256,
    strategy_contract,
)


def _nums(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "sum": 0.0, "mean": None, "median": None}
    return {
        "n": len(xs),
        "sum": float(sum(xs)),
        "mean": float(sum(xs) / len(xs)),
        "median": float(median(xs)),
    }


def development_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(t.get("net_pnl_yen") or 0.0) for t in trades]
    gross = [float(t.get("gross_pnl_yen") or 0.0) for t in trades]
    tax = [float(t.get("execution_cost_yen") or 0.0) for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    hold = [int(t.get("holding_min") or 0) for t in trades]
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    for t in trades:
        eq += float(t.get("net_pnl_yen") or 0.0)
        peak = max(peak, eq)
        dd = eq - peak
        max_dd = min(max_dd, dd)
        curve.append({"date": t.get("date"), "entry_t": t.get("entry_t"), "symbol": t.get("symbol"), "equity": eq, "dd": dd})
    sessions: dict[str, float] = defaultdict(float)
    for t in trades:
        sessions[str(t.get("date") or "")] += float(t.get("net_pnl_yen") or 0.0)
    sess_vals = list(sessions.values())
    e0 = [t for t in trades if str(t.get("entry_type") or "") == "E0"]
    e1 = [t for t in trades if str(t.get("entry_type") or "") == "E1"]
    by_exit: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_exit[str(t.get("exit_reason") or "")].append(float(t.get("net_pnl_yen") or 0.0))
    pf = None
    if losses:
        pf = float(sum(wins) / abs(sum(losses))) if wins else 0.0
    elif wins:
        pf = None
    return {
        "role": "DEVELOPMENT_BINDING_NOT_CERTIFICATION",
        "used_for_freeze": False,
        "trade_n": len(trades),
        "gross_pnl_yen": float(sum(gross)),
        "execution_cost_yen": float(sum(tax)),
        "net_pnl_yen": float(sum(nets)),
        "mean_net_pnl_per_trade": (float(sum(nets) / len(nets)) if nets else None),
        "median_net_pnl_per_trade": (float(median(nets)) if nets else None),
        "profit_factor": pf,
        "win_rate": (float(len(wins) / len(nets)) if nets else None),
        "average_win": (float(sum(wins) / len(wins)) if wins else None),
        "average_loss": (float(sum(losses) / len(losses)) if losses else None),
        "max_drawdown": float(max_dd),
        "holding_time": _nums([float(x) for x in hold]),
        "session_positive_n": sum(1 for v in sess_vals if v > 0),
        "session_negative_n": sum(1 for v in sess_vals if v < 0),
        "session_n": len(sess_vals),
        "turnover_trades": len(trades),
        "E0": {"n": len(e0), "net_pnl_yen": float(sum(float(t.get("net_pnl_yen") or 0.0) for t in e0))},
        "E1": {"n": len(e1), "net_pnl_yen": float(sum(float(t.get("net_pnl_yen") or 0.0) for t in e1))},
        "exit_reasons": {k: {"n": len(v), "net_pnl_yen": float(sum(v))} for k, v in by_exit.items()},
        "reentry_n": sum(1 for t in trades if int(t.get("reentry_n") or 0) > 0),
        "equity_tail": curve[-8:] if curve else [],
        "sessions": [{"date": d, "net_pnl_yen": float(sessions[d])} for d in sorted(sessions)],
    }


def freeze_decision(
    *,
    identity: dict[str, Any],
    roles: dict[str, Any],
    walked: dict[str, Any],
    replay: dict[str, Any],
    exec_inv: dict[str, Any],
    exit_inv: dict[str, Any],
) -> dict[str, Any]:
    trades = list(replay.get("trades") or [])
    occ = dict(replay.get("occupancy") or {})
    skip = dict(replay.get("skip") or {})
    leak_dates = [t for t in trades if str(t.get("date") or "") < "20240917" or str(t.get("date") or "") > "20251126"]
    same_bar = sum(1 for t in trades if t.get("same_bar_entry") or t.get("same_bar_exit"))
    mid = sum(1 for t in trades if t.get("used_mid"))
    lunch_fill = sum(1 for t in trades if str(t.get("entry_t") or "") >= "11:30" and str(t.get("entry_t") or "") < "12:30")
    clock_ok = all(
        str(t.get("signal_t") or "") < str(t.get("entry_t") or "") < str(t.get("exit_t") or "") for t in trades
    )
    approx = bool((exec_inv.get("historical_research_approximation") or {}).get("RESEARCH_EXECUTION_APPROXIMATION"))
    conf1 = dict(roles.get("economic_confirmation_1") or {})
    conf2 = dict(roles.get("economic_confirmation_2") or {})
    structural = {
        "identity_ok": bool(identity.get("ok")),
        "roles_ok": bool(roles.get("ok")),
        "walked_ok": bool(walked.get("ok")),
        "replay_ok": bool(replay.get("ok")),
        "fill_px_mismatch_n": int(replay.get("fill_px_mismatch_n") or 0),
        "missing_bars_n": int(skip.get("missing_bars") or 0),
        "same_bar_n": int(same_bar) + int(walked.get("same_bar_entry_n") or 0),
        "mid_n": int(mid),
        "lunch_fill_n": int(lunch_fill),
        "leak_trade_n": len(leak_dates),
        "clock_order_ok": bool(clock_ok),
        "cap_violation_n": int(replay.get("cap_violation_n") or occ.get("cap_violation_n") or 0),
        "same_symbol_overlap_violation_n": int(replay.get("same_symbol_overlap_violation_n") or 0),
        "max_concurrent": int(replay.get("max_concurrent") or 0),
        "cap": int(CAP),
        "trade_n": len(trades),
        "candidate_n": int(replay.get("candidate_n") or 0),
        "approximation_documented": approx,
        "economic_confirmation_1_unopened": bool(conf1.get("ECONOMIC_OUTCOME_UNOPENED")) and not bool(conf1.get("opened_this_task")),
        "economic_confirmation_2_unopened": bool(conf2.get("ECONOMIC_OUTCOME_UNOPENED")) and not bool(conf2.get("opened_this_task")),
        "pnl_used_for_freeze": False,
        "technical_exit_id": (exit_inv.get("selected_technical_exit") or {}).get("id"),
    }
    required = (
        structural["identity_ok"],
        structural["roles_ok"],
        structural["walked_ok"],
        structural["replay_ok"],
        structural["fill_px_mismatch_n"] == 0,
        structural["missing_bars_n"] == 0,
        structural["same_bar_n"] == 0,
        structural["mid_n"] == 0,
        structural["lunch_fill_n"] == 0,
        structural["leak_trade_n"] == 0,
        structural["clock_order_ok"],
        structural["cap_violation_n"] == 0,
        structural["same_symbol_overlap_violation_n"] == 0,
        structural["max_concurrent"] <= int(CAP),
        structural["trade_n"] > 0,
        structural["candidate_n"] > 0,
        structural["approximation_documented"],
        structural["economic_confirmation_1_unopened"],
        structural["economic_confirmation_2_unopened"],
        structural["pnl_used_for_freeze"] is False,
    )
    ok = all(required)
    machine_sha = str(identity.get("machine_sha") or "")
    inv_sha = str(identity.get("source_inventory_sha") or "")
    sha = complete_strategy_sha256(machine_sha=machine_sha, source_inventory_sha=inv_sha) if ok else ""
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_FAIL,
        "NEXT": NEXT_CONF1 if ok else NEXT_STOP,
        "complete_strategy_identity": COMPLETE_STRATEGY_IDENTITY if ok else None,
        "COMPLETE_STRATEGY_SHA256": sha,
        "contract": strategy_contract(machine_sha=machine_sha, source_inventory_sha=inv_sha),
        "structural": structural,
        "freeze_basis": "structure_causality_execution_state_machine",
        "development_pnl_did_not_gate_freeze": True,
        "reason": None if ok else "complete_strategy_binding_incomplete",
    }
