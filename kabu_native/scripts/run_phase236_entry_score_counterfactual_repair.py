#!/usr/bin/env python3
"""
Phase236: Entry score counterfactual repair (review only).

Test removing HBRecent:no and/or RollingMAE:mid points on fixed Phase230 shadow sessions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase236_entry_score_counterfactual_repair.json"

SCORE_GE5 = 5
SCORE_GE6 = 6

# Fixed Phase230 shadow sessions (user-specified).
TARGET_SESSIONS = (
    "20260601/live_session_075940",
    "20260601/live_session_122524",
    "phase234/20260521/push_replay_sim",
)

BASELINE_POINTS: dict[str, int] = {
    "HBRecent:no": 2,
    "RollingMAE:mid": 2,
    "Duration:high": 2,
    "Momentum:low": 1,
    "Board:mid": 1,
    "Price:high": 1,
    "TV:mid": 1,
}

SCENARIOS: dict[str, dict[str, int]] = {
    "baseline_current": dict(BASELINE_POINTS),
    "scenario_A_hbrecent_zero": {**BASELINE_POINTS, "HBRecent:no": 0},
    "scenario_B_rollingmae_zero": {**BASELINE_POINTS, "RollingMAE:mid": 0},
    "scenario_C_both_zero": {**BASELINE_POINTS, "HBRecent:no": 0, "RollingMAE:mid": 0},
    "scenario_D_no_hbrecent_no_rollingmae": {
        "Duration:high": 2,
        "TV:mid": 2,
        "Price:high": 1,
        "Momentum:low": 1,
        "Board:mid": 1,
    },
}

BASELINE_GE5_PF = 0.697
BASELINE_GE6_PF = 0.579


def _bootstrap() -> None:
    native = REPO / "kabu_native"
    for p in (native / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if not jsonl.is_file():
        return []
    out: list[dict[str, Any]] = []
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _extract_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[1]:
            accepts[key] = ev

    trades: list[dict[str, Any]] = []
    for key, acc in accepts.items():
        exit_ev: Optional[dict[str, Any]] = None
        for ev in events:
            if ev.get("event_type") != "observer_exit":
                continue
            if (str(ev.get("symbol") or ""), str(ev.get("entry_time") or "")) == key:
                exit_ev = ev
                break
        pnl = _float(exit_ev.get("pnl_pct")) if exit_ev else None
        if pnl is None:
            continue
        reason = str(exit_ev.get("exit_reason") or "") if exit_ev else ""
        stop_hit = bool(exit_ev.get("stop_hit")) if exit_ev else False
        if exit_ev and not stop_hit and reason == "stop_hit":
            stop_hit = True
        trades.append({**acc, "pnl_pct": pnl, "stop_hit": stop_hit})
    return trades


def _split_label(session_id: str, mod: Any) -> str:
    if session_id in mod.IN_SAMPLE:
        return "in_sample"
    if session_id in mod.OOS:
        return "oos"
    day = session_id.split("/")[0]
    live_is = [s for s in mod.IN_SAMPLE if s.split("/")[0] == day and "live" in s]
    live_oos = [s for s in mod.OOS if s.split("/")[0] == day and "live" in s]
    if live_is and not live_oos:
        return "in_sample"
    if live_oos and not live_is:
        return "oos"
    is_n = sum(1 for s in mod.IN_SAMPLE if s.split("/")[0] == day)
    oos_n = sum(1 for s in mod.OOS if s.split("/")[0] == day)
    if is_n > 0 and oos_n == 0:
        return "in_sample"
    if oos_n > 0 and is_n == 0:
        return "oos"
    return "unknown"


def _score_for_trade(trade: dict[str, Any], points: dict[str, int]) -> int:
    from small_paper.entry_expectancy_score_shadow import _feature_token

    total = 0
    for token, pts in points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        tok = _feature_token(lbl, trade)
        if tok == token:
            total += pts
    return total


def _cohort_block(rows: list[dict[str, Any]], min_score: int) -> dict[str, Any]:
    subset = [r for r in rows if int(r.get("_score") or 0) >= min_score]
    pnls = [float(r["pnl_pct"]) for r in subset]
    n = len(subset)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for r in subset if r.get("stop_hit"))
    return {
        "trade_count": n,
        "profit_factor": _pf(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _scenario_metrics(rows: list[dict[str, Any]], mod: Any) -> dict[str, Any]:
    pnls = [float(r["pnl_pct"]) for r in rows]
    n = len(rows)
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    is_pnls = [float(r["pnl_pct"]) for r in rows if r.get("split") == "in_sample"]
    oos_pnls = [float(r["pnl_pct"]) for r in rows if r.get("split") == "oos"]
    all_m = {
        "trade_count": n,
        "profit_factor": _pf(pnls) if pnls else None,
        "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
        "win_rate": round(wins / n, 4) if n else None,
        "stop_rate": round(stops / n, 4) if n else None,
        "IS_trade_count": len(is_pnls),
        "IS_profit_factor": _pf(is_pnls) if is_pnls else None,
        "IS_total_pnl_pct": round(sum(is_pnls), 4) if is_pnls else 0.0,
        "OOS_trade_count": len(oos_pnls),
        "OOS_profit_factor": _pf(oos_pnls) if oos_pnls else None,
        "OOS_total_pnl_pct": round(sum(oos_pnls), 4) if oos_pnls else 0.0,
    }
    ge5_rows = [r for r in rows if int(r.get("_score") or 0) >= SCORE_GE5]
    ge6_rows = [r for r in rows if int(r.get("_score") or 0) >= SCORE_GE6]
    is_ge5 = [r for r in ge5_rows if r.get("split") == "in_sample"]
    oos_ge5 = [r for r in ge5_rows if r.get("split") == "oos"]
    is_ge6 = [r for r in ge6_rows if r.get("split") == "in_sample"]
    oos_ge6 = [r for r in ge6_rows if r.get("split") == "oos"]
    ge5 = _cohort_block(rows, SCORE_GE5)
    ge6 = _cohort_block(rows, SCORE_GE6)
    ge5["IS_profit_factor"] = _pf([float(r["pnl_pct"]) for r in is_ge5]) if is_ge5 else None
    ge5["OOS_profit_factor"] = _pf([float(r["pnl_pct"]) for r in oos_ge5]) if oos_ge5 else None
    ge6["IS_profit_factor"] = _pf([float(r["pnl_pct"]) for r in is_ge6]) if is_ge6 else None
    ge6["OOS_profit_factor"] = _pf([float(r["pnl_pct"]) for r in oos_ge6]) if oos_ge6 else None
    return {
        "all_trades": all_m,
        "score_ge5_cohort": ge5,
        "score_ge6_cohort": ge6,
    }


def _beats_baseline(ge5_pf: Any, ge6_pf: Any) -> dict[str, Any]:
    s5 = ge5_pf if isinstance(ge5_pf, (int, float)) else None
    s6 = ge6_pf if isinstance(ge6_pf, (int, float)) else None
    beats5 = s5 is not None and s5 > BASELINE_GE5_PF
    beats6 = s6 is not None and s6 > BASELINE_GE6_PF
    return {
        "baseline_score_ge5_pf": BASELINE_GE5_PF,
        "baseline_score_ge6_pf": BASELINE_GE6_PF,
        "beats_baseline_score_ge5_pf": beats5,
        "beats_baseline_score_ge6_pf": beats6,
        "beats_both": beats5 and beats6,
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p213 = _load_module(
        "phase213c_loader_p236",
        "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py",
    )

    base = REPO / "kabu_native/results/small_paper"
    all_trades: list[dict[str, Any]] = []
    session_meta: list[dict[str, Any]] = []

    for rel in TARGET_SESSIONS:
        session_dir = base / rel
        if not session_dir.is_dir():
            print(f"WARN missing {session_dir}", flush=True)
            continue
        events = _load_events(session_dir)
        trades = _extract_trades(events)
        split = _split_label(rel, p213)
        for t in trades:
            all_trades.append({**t, "session_id": rel, "split": split})
        session_meta.append({"session_id": rel, "session_dir": str(session_dir), "closed_trades": len(trades)})

    scenario_results: dict[str, Any] = {}
    for scen_name, points in SCENARIOS.items():
        rows: list[dict[str, Any]] = []
        for t in all_trades:
            sc = _score_for_trade(t, points)
            rows.append({**t, "_score": sc})
        metrics = _scenario_metrics(rows, p213)
        verdict = _beats_baseline(
            metrics["score_ge5_cohort"].get("profit_factor"),
            metrics["score_ge6_cohort"].get("profit_factor"),
        )
        scenario_results[scen_name] = {
            "score_points": {k: v for k, v in points.items() if v > 0},
            "score_points_zeroed": [k for k, v in points.items() if v == 0],
            **metrics,
            "verdict_vs_phase230_baseline": verdict,
        }

    best_ge5 = max(
        scenario_results.items(),
        key=lambda x: (x[1]["score_ge5_cohort"].get("profit_factor") or -1, x[1]["score_ge5_cohort"].get("total_pnl_pct") or -1),
    )
    best_ge6 = max(
        scenario_results.items(),
        key=lambda x: (x[1]["score_ge6_cohort"].get("profit_factor") or -1, x[1]["score_ge6_cohort"].get("total_pnl_pct") or -1),
    )

    report = {
        "phase": 236,
        "mode": "entry_score_counterfactual_repair",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "new_feature_exploration_forbidden": True,
            "new_score_exploration_forbidden": True,
        },
        "target_sessions": list(TARGET_SESSIONS),
        "sessions": session_meta,
        "population": {"closed_trades": len(all_trades)},
        "baseline_reference": {
            "source": "Phase230 shadow aggregate (phase235)",
            "score_ge5_pf": BASELINE_GE5_PF,
            "score_ge6_pf": BASELINE_GE6_PF,
        },
        "scenarios": scenario_results,
        "summary": {
            "best_score_ge5_pf_scenario": best_ge5[0],
            "best_score_ge5_pf": best_ge5[1]["score_ge5_cohort"].get("profit_factor"),
            "best_score_ge6_pf_scenario": best_ge6[0],
            "best_score_ge6_pf": best_ge6[1]["score_ge6_cohort"].get("profit_factor"),
            "scenarios_beating_baseline_ge5": [
                k
                for k, v in scenario_results.items()
                if v["verdict_vs_phase230_baseline"]["beats_baseline_score_ge5_pf"]
            ],
            "scenarios_beating_baseline_ge6": [
                k
                for k, v in scenario_results.items()
                if v["verdict_vs_phase230_baseline"]["beats_baseline_score_ge6_pf"]
            ],
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} trades={len(all_trades)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
