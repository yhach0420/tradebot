"""ENTRY×EXIT joint search with absolute TRAIN/VALIDATION gates (zero candidates allowed)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.cap5 import CapTrade, replay_cap5
from research.canonical_zero_base_v2.constants import JOINT_ENTRY_CAP, JOINT_EXIT_CAP, JOINT_PAIR_CAP
from research.canonical_zero_base_v2.dependency import dependency_metrics
from research.canonical_zero_base_v2.entry_rules import EntryRule, collect_entries, opportunity_pf
from research.canonical_zero_base_v2.episodes import Episode
from research.canonical_zero_base_v2.execution import evaluate_latency_pairs
from research.canonical_zero_base_v2.exit_rules import ExitRule, simulate_exit, strategy_exit_candidates
from research.canonical_zero_base_v2.loader import Tick


def _to_cap_trades(entries: Sequence[dict], streams: dict[str, list[Tick]], exit_rule: ExitRule) -> list[CapTrade]:
    out: list[CapTrade] = []
    for e in entries:
        ticks = streams[e["stream_key"]]
        ex = simulate_exit(
            ticks, e["entry_idx"], e["entry_ask"],
            strategy_id=e["strategy_id"], exit_rule=exit_rule, levels=e.get("levels") or {},
        )
        if not ex.get("evaluable"):
            continue
        hold = (ex["exit_time"] - e["entry_time"]).total_seconds()
        stop = ex["exit_reason"] == "hard_stop"
        out.append(CapTrade(
            day=e["day"], symbol=e["symbol"], episode_id=e["episode_id"],
            entry_time=e["entry_time"], exit_time=ex["exit_time"],
            entry_price=e["entry_ask"], exit_price=float(ex["exit_bid"]),
            pnl_5bps=float(ex["pnl_5bps"]), exit_reason=str(ex["exit_reason"]),
            strategy_id=e["strategy_id"], setup_id=f"{e['rule_id']}:{exit_rule.exit_id}",
            session="AM" if e["entry_time"].hour < 12 else "PM",
            mfe=float(ex["mfe"]), mae=float(ex["mae"]),
            stop=stop, early_stop=stop and hold <= 60,
            no_progress=float(ex["mfe"]) < 0.25 and "session" in str(ex["exit_reason"]),
            winner=float(ex["pnl_5bps"]) > 0 and float(ex["mfe"]) >= 0.5,
        ))
    return out


def train_entry_gate(opp: dict[str, Any], *, base_never: float, base_early: float) -> tuple[bool, str]:
    n = opp.get("n") or 0
    if n < 30:
        return False, "TRAIN_REJECT_n<30"
    pf = opp.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1 and pf != float("inf")):
        return False, "TRAIN_REJECT_pf<=1"
    if (opp.get("mean") or 0) <= 0:
        return False, "TRAIN_REJECT_mean<=0"
    if (opp.get("pnl") or 0) <= 0:
        return False, "TRAIN_REJECT_pnl<=0"
    if (opp.get("winner_capture") or 0) <= 0:
        return False, "TRAIN_REJECT_no_winner"
    if opp.get("never_rate") is not None and base_never is not None and opp["never_rate"] >= base_never:
        return False, "TRAIN_REJECT_never_not_improved"
    if opp.get("early_stop_rate") is not None and base_early is not None and opp["early_stop_rate"] >= base_early:
        return False, "TRAIN_REJECT_early_not_improved"
    return True, "TRAIN_PASS"


def val_entry_gate(opp: dict[str, Any]) -> tuple[bool, str]:
    if (opp.get("n") or 0) < 5:
        return False, "VALIDATION_REJECT_n"
    if (opp.get("pnl") or 0) <= 0:
        return False, "VALIDATION_REJECT_pnl"
    pf = opp.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1 and pf != float("inf")):
        return False, "VALIDATION_REJECT_pf"
    return True, "VALIDATION_PASS"


def train_pair_gate(cap: dict[str, Any], dep: dict[str, Any]) -> tuple[bool, str]:
    if (cap.get("trades") or 0) < 30:
        return False, "TRAIN_PAIR_REJECT_n"
    if (cap.get("pnl_5bps") or 0) <= 0:
        return False, "TRAIN_PAIR_REJECT_pnl"
    pf = cap.get("PF_5bps")
    if pf is None or (isinstance(pf, float) and pf <= 1 and pf != float("inf")):
        return False, "TRAIN_PAIR_REJECT_pf"
    if (cap.get("pos_days") or 0) < 1:
        return False, "TRAIN_PAIR_REJECT_no_pos_day"
    if (cap.get("winner_rate") or 0) <= 0:
        return False, "TRAIN_PAIR_REJECT_winner"
    if dep.get("DEPENDENCY_BLOCKED") and (dep.get("top1_symbol_profit_ratio") or 0) >= 0.9:
        return False, "TRAIN_PAIR_REJECT_dependency"
    return True, "TRAIN_PAIR_PASS"


def val_pair_gate(cap: dict[str, Any]) -> tuple[bool, str]:
    if (cap.get("trades") or 0) < 3:
        return False, "VALIDATION_PAIR_REJECT_n"
    if (cap.get("pnl_5bps") or 0) <= 0:
        return False, "VALIDATION_PAIR_REJECT_pnl"
    pf = cap.get("PF_5bps")
    if pf is None or (isinstance(pf, float) and pf <= 1 and pf != float("inf")):
        return False, "VALIDATION_PAIR_REJECT_pf"
    return True, "VALIDATION_PAIR_PASS"


def run_strategy_lane(
    strategy_id: str,
    *,
    entry_rules: Sequence[EntryRule],
    episodes_train: Sequence[Episode],
    episodes_val: Sequence[Episode],
    episodes_oos: Sequence[Episode],
    streams: dict[str, list[Tick]],
    base_never: float,
    base_early: float,
    insufficient_oos: bool,
) -> dict[str, Any]:
    exit_cands = strategy_exit_candidates(strategy_id)
    # ENTRY absolute gate first
    train_entry_pass: list[dict[str, Any]] = []
    entry_gate_log = []
    for rule in entry_rules[:JOINT_ENTRY_CAP]:
        ents = collect_entries(strategy_id, rule, episodes_train, streams)
        opp = opportunity_pf(ents, streams, horizon=float(rule.expected_horizon_sec))
        ok, reason = train_entry_gate(opp, base_never=base_never, base_early=base_early)
        entry_gate_log.append({"rule_id": rule.rule_id, "ok": ok, "reason": reason, **{k: opp.get(k) for k in ("n", "pnl", "pf", "winner_capture")}})
        if ok:
            train_entry_pass.append({"rule": rule, "opp": opp, "entries_n": len(ents)})

    val_entry_pass: list[dict[str, Any]] = []
    val_entry_log = []
    for row in train_entry_pass:
        rule = row["rule"]
        ents = collect_entries(strategy_id, rule, episodes_val, streams)
        opp = opportunity_pf(ents, streams, horizon=float(rule.expected_horizon_sec))
        ok, reason = val_entry_gate(opp)
        val_entry_log.append({"rule_id": rule.rule_id, "ok": ok, "reason": reason, **{k: opp.get(k) for k in ("n", "pnl", "pf")}})
        if ok:
            val_entry_pass.append({"rule": rule, "opp": opp})

    if not train_entry_pass:
        return _empty_lane(strategy_id, "NO_TRAIN_ENTRY_CANDIDATE", entry_rules, exit_cands, entry_gate_log, val_entry_log)
    if not val_entry_pass:
        return _empty_lane(strategy_id, "NO_VALIDATED_ENTRY_CANDIDATE", entry_rules, exit_cands, entry_gate_log, val_entry_log)

    # Joint pairs — only validated entries
    exits = [x for x in exit_cands if not x.is_control][:JOINT_EXIT_CAP]
    # keep one control for comparison
    controls = [x for x in exit_cands if x.is_control][:1]
    exit_pool = exits + controls
    raw_pairs = 0
    train_pair_pass = []
    pair_log = []
    for erow in val_entry_pass[:JOINT_ENTRY_CAP]:
        rule = erow["rule"]
        ents_tr = collect_entries(strategy_id, rule, episodes_train, streams)
        for xr in exit_pool:
            if raw_pairs >= JOINT_PAIR_CAP:
                break
            raw_pairs += 1
            trades = _to_cap_trades(ents_tr, streams, xr)
            cap = replay_cap5(trades, portfolio_id=f"TR_{rule.rule_id}_{xr.exit_id}")
            dep = dependency_metrics(trades)
            ok, reason = train_pair_gate(cap, dep)
            pair_log.append({
                "entry": rule.rule_id, "exit": xr.exit_id, "ok": ok, "reason": reason,
                "pnl": cap.get("pnl_5bps"), "PF": cap.get("PF_5bps"), "n": cap.get("trades"),
            })
            if ok:
                train_pair_pass.append({"rule": rule, "exit": xr, "cap": cap, "dep": dep})
        if raw_pairs >= JOINT_PAIR_CAP:
            break

    if not train_pair_pass:
        return {
            **_empty_lane(strategy_id, "NO_TRAIN_ENTRY_EXIT_PAIR", entry_rules, exit_cands, entry_gate_log, val_entry_log),
            "raw_pairs": raw_pairs,
            "pair_log_top": pair_log[:30],
            "train_entry_pass_n": len(train_entry_pass),
            "val_entry_pass_n": len(val_entry_pass),
        }

    val_pair_pass = []
    val_pair_log = []
    for prow in train_pair_pass:
        rule, xr = prow["rule"], prow["exit"]
        ents = collect_entries(strategy_id, rule, episodes_val, streams)
        trades = _to_cap_trades(ents, streams, xr)
        cap = replay_cap5(trades, portfolio_id=f"VA_{rule.rule_id}_{xr.exit_id}")
        ok, reason = val_pair_gate(cap)
        val_pair_log.append({"entry": rule.rule_id, "exit": xr.exit_id, "ok": ok, "reason": reason, "pnl": cap.get("pnl_5bps"), "PF": cap.get("PF_5bps")})
        if ok:
            val_pair_pass.append({"rule": rule, "exit": xr, "cap": cap})

    if not val_pair_pass:
        return {
            **_empty_lane(strategy_id, "NO_VALIDATED_ENTRY_EXIT_PAIR", entry_rules, exit_cands, entry_gate_log, val_entry_log),
            "raw_pairs": raw_pairs,
            "train_pair_pass_n": len(train_pair_pass),
            "pair_log_top": pair_log[:30],
            "train_entry_pass_n": len(train_entry_pass),
            "val_entry_pass_n": len(val_entry_pass),
        }

    # OOS — freeze validated pairs only; do not re-rank by OOS
    oos_results = []
    for prow in val_pair_pass:
        rule, xr = prow["rule"], prow["exit"]
        ents = collect_entries(strategy_id, rule, episodes_oos, streams)
        trades = _to_cap_trades(ents, streams, xr)
        cap = replay_cap5(trades, portfolio_id=f"OOS_{rule.rule_id}_{xr.exit_id}")
        dep = dependency_metrics(trades)
        exec_stats = evaluate_latency_pairs(ents, streams, hold_sec=float(rule.expected_horizon_sec))
        oos_results.append({
            "rule": _rule_dict(rule),
            "exit": _exit_dict(xr),
            "cap": {k: cap.get(k) for k in (
                "pnl_5bps", "PF_5bps", "trades", "trades_per_day", "stop_rate", "early_stop_rate",
                "no_progress_rate", "winner_rate", "avg_mfe", "avg_mae", "pos_days", "neg_days",
                "trade_sequence_dd", "blocked_n",
            )},
            "dependency": dep,
            "execution": exec_stats,
            "trades": trades,
            "false_warning_rate": _fw_rate(ents, streams, xr),
            "true_invalidation_rate": _ti_rate(ents, streams, xr),
        })

    final = oos_results[0] if oos_results else None
    judgment = f"{strategy_id}_ENTRY_EXIT_REJECT"
    if final and not insufficient_oos:
        c = final["cap"]
        pf = c.get("PF_5bps")
        if (c.get("pnl_5bps") or 0) > 0 and isinstance(pf, (int, float)) and pf > 1:
            judgment = f"{strategy_id}_ENTRY_EXIT_CANDIDATE"
    if insufficient_oos:
        judgment = f"{strategy_id}_ENTRY_EXIT_REJECT"  # cannot promote under insufficient OOS

    return {
        "strategy_id": strategy_id,
        "status": "COMPLETE",
        "judgment": judgment,
        "entry_rules_n": len(entry_rules),
        "exit_candidates_n": len(exit_cands),
        "train_entry_pass_n": len(train_entry_pass),
        "val_entry_pass_n": len(val_entry_pass),
        "raw_pairs": raw_pairs,
        "train_pair_pass_n": len(train_pair_pass),
        "val_pair_pass_n": len(val_pair_pass),
        "oos_pairs_n": len(oos_results),
        "entry_gate_log": entry_gate_log[:50],
        "val_entry_log": val_entry_log[:50],
        "pair_log_top": pair_log[:40],
        "val_pair_log": val_pair_log[:40],
        "oos_results": [{k: v for k, v in r.items() if k != "trades"} for r in oos_results],
        "final": final,
        "exit_ids": [x.exit_id for x in exit_cands],
    }


def _fw_rate(entries, streams, xr) -> Optional[float]:
    if not entries:
        return None
    s = n = 0
    for e in entries:
        ex = simulate_exit(streams[e["stream_key"]], e["entry_idx"], e["entry_ask"], strategy_id=e["strategy_id"], exit_rule=xr, levels=e.get("levels") or {})
        if ex.get("evaluable"):
            n += 1
            s += int(ex.get("false_warning") or 0)
    return (s / n) if n else None


def _ti_rate(entries, streams, xr) -> Optional[float]:
    if not entries:
        return None
    s = n = 0
    for e in entries:
        ex = simulate_exit(streams[e["stream_key"]], e["entry_idx"], e["entry_ask"], strategy_id=e["strategy_id"], exit_rule=xr, levels=e.get("levels") or {})
        if ex.get("evaluable"):
            n += 1
            s += 1 if (ex.get("true_invalidation") or 0) > 0 else 0
    return (s / n) if n else None


def _rule_dict(rule: EntryRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "strategy_id": rule.strategy_id,
        "features": rule.features,
        "thresholds": rule.thresholds,
        "directions": rule.directions,
        "groups": rule.groups,
        "state_requirements": rule.state_requirements,
        "complexity": rule.complexity,
        "invalidation_premise": rule.invalidation_premise,
        "expected_horizon_sec": rule.expected_horizon_sec,
    }


def _exit_dict(xr: ExitRule) -> dict[str, Any]:
    return {
        "exit_id": xr.exit_id,
        "kind": xr.kind,
        "persistence_events": xr.persistence_events,
        "use_flow": xr.use_flow,
        "use_board": xr.use_board,
        "use_volume": xr.use_volume,
        "use_trailing": xr.use_trailing,
        "use_exhaustion": xr.use_exhaustion,
        "is_control": xr.is_control,
    }


def _empty_lane(sid, status, entry_rules, exit_cands, entry_log, val_log) -> dict[str, Any]:
    return {
        "strategy_id": sid,
        "status": status,
        "judgment": f"{sid}_ENTRY_EXIT_REJECT",
        "entry_rules_n": len(entry_rules),
        "exit_candidates_n": len(exit_cands),
        "train_entry_pass_n": 0,
        "val_entry_pass_n": 0,
        "raw_pairs": 0,
        "train_pair_pass_n": 0,
        "val_pair_pass_n": 0,
        "oos_pairs_n": 0,
        "entry_gate_log": entry_log[:50],
        "val_entry_log": val_log[:50],
        "final": None,
        "oos_results": [],
    }
