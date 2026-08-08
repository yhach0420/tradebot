"""E1_X13 run: historical replay + parity + tests + optional observer gate."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    CAPITAL_POLICY_STATUS,
    DOCUMENT_ID,
    FORBIDDEN_ALPHA_DAYS,
    RISK_ONLY_DAY,
    VERDICT_INTEGRATION_REJECTED,
    VERDICT_MISMATCH,
    VERDICT_READY,
)
from .observer import decision_parity_fixture, observer_enabled
from .parity import compare_replay_parity
from .publish import publish
from .replay import run_historical_replay

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x13_execution_risk_observer"
PKG = Path(__file__).resolve().parent


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x13_execution_risk_observer.py"
    if not test_path.exists():
        return {"exit_code": 1, "passed": 0, "failed": 1, "total": 1,
                "rows": [{"test": "missing", "outcome": "FAILED"}]}
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=no"],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
        env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    # parse "N passed"
    passed = failed = total = 0
    import re
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    total = passed + failed
    rows = [{"test": "pytest_suite", "outcome": "PASSED" if p.returncode == 0 else "FAILED", "detail": out[-2000:]}]
    return {"exit_code": p.returncode, "passed": passed, "failed": failed, "total": total or 1, "rows": rows, "raw": out[-4000:]}


def run(*, label: str = "A") -> dict[str, Any]:
    run_id = f"e1x13_execrisk_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"
    print("=== Historical replay ===", flush=True)
    replay_a = run_historical_replay()
    print("=== Replay B (determinism) ===", flush=True)
    replay_b = run_historical_replay()
    ab_match = sha256_obj(replay_a["daily"]) == sha256_obj(replay_b["daily"])

    print("=== Parity ===", flush=True)
    parity = compare_replay_parity(replay_a)

    print("=== Tests ===", flush=True)
    tests = _run_tests()

    # Decision parity fixture (Phase C gate input)
    fixture_cands = [
        {"candidate_id": "c1", "symbol": "285A", "decision": "ACCEPT", "best_bid": 50000, "best_ask": 50010,
         "best_bid_qty": 200, "best_ask_qty": 200, "reference_price": 50000, "tick_size": 50, "board_age_sec": 0.5},
        {"candidate_id": "c2", "symbol": "2354", "decision": "REJECT", "exit_reason": "spread",
         "best_bid": 1300, "best_ask": 1305, "best_bid_qty": 50, "best_ask_qty": 100,
         "reference_price": 1300, "tick_size": 1, "board_age_sec": 0.2},
    ]
    roll = {"rolling_spread_cost_p95": 1000, "rolling_down_bid_jump_p95": 2000,
            "rolling_executable_loss_5s_p95": 3000, "history_support_status": "OK"}
    dec = decision_parity_fixture(fixture_cands, rolling=roll)

    pnl_indep = all(
        r.get("strategy_loss_risk") == "unresolved" and r.get("total_trade_risk") == "unresolved"
        for r in replay_a["daily"]
    )
    reserved_ok = (
        replay_a["forbidden_days_opened"] is False
        and replay_a["risk_only_alpha_used"] is False
        and all(r["date"] not in FORBIDDEN_ALPHA_DAYS for r in replay_a["daily"])
    )

    phase_b_pass = (
        parity["pass"]
        and ab_match
        and pnl_indep
        and reserved_ok
        and tests["exit_code"] == 0
    )

    runtime = {
        "integrated": False,
        "reason": "phase_b_not_pass",
        "hook": "research.e1_x13_execution_risk_observer.observer.observe_candidate",
        "env_flag": "E1_X13_EXECUTION_RISK_OBSERVER",
        "default_enabled": False,
        "entry_blocking": False,
    }
    verdict = VERDICT_MISMATCH
    if phase_b_pass:
        if dec["decision_parity_pass"] and dec["performance_ok"]:
            runtime = {
                "integrated": True,
                "reason": "parity_and_decision_and_perf_pass",
                "hook": "opt-in via E1_X13_EXECUTION_RISK_OBSERVER=1; never blocks ENTRY",
                "env_flag": "E1_X13_EXECUTION_RISK_OBSERVER",
                "default_enabled": False,
                "entry_blocking": False,
                "pilot_runner_wired": True,
                "production_yaml_changed": False,
            }
            verdict = VERDICT_READY
        else:
            runtime = {
                "integrated": False,
                "reason": "decision_parity_or_perf_failed",
                "offline_measurement_only": True,
                "decision_parity_pass": dec["decision_parity_pass"],
                "performance_ok": dec["performance_ok"],
            }
            verdict = VERDICT_INTEGRATION_REJECTED

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "mode": "OBSERVER_ONLY",
        "enforcement": False,
        "entry_blocking": False,
        "verdict": verdict,
        "source_identity": {
            "x10": "results/research/e1_x10_risk_universe",
            "x11_v2": "results/research/e1_x11_policy_gate_v2",
            "x12_eod": "results/research/e1_x12_risk_history",
            "forbidden_days": list(FORBIDDEN_ALPHA_DAYS),
            "risk_only_day": RISK_ONLY_DAY,
        },
        "measurement_contract": {
            "one_lot_notional_yen": "best_ask × 100",
            "one_tick_risk_yen_100": "tick_size × 100",
            "current_spread_cost_yen_100": "max(ask-bid,0) × 100",
            "estimated_execution_risk_yen": "max(rolling_spread_p95, rolling_down_bid_jump_p95, rolling_exec5s_p95)",
            "execution_risk_separated": True,
            "strategy_loss_risk": "unresolved",
            "total_trade_risk": "unresolved",
        },
        "replay": {
            "n_daily": len(replay_a["daily"]),
            "days": replay_a["days"],
            "symbols": replay_a["symbols"],
            "rounding_contract": replay_a["rounding_contract"],
        },
        "parity": parity,
        "runtime_observer": runtime,
        "decision_parity": dec,
        "capital_policy_status": CAPITAL_POLICY_STATUS,
        "configured_risk_capital_cap_yen": None,
        "safety": {
            "submit_cancel_live": "0/0/0",
            "mainline_ENTRY_changed": False,
            "mainline_EXIT_changed": False,
            "Universe_changed": False,
            "production_YAML_changed": False,
            "real_order_route_enabled": False,
            "opened_20260803": False,
            "opened_20260804": False,
            "alpha_used_20260805": False,
        },
        "_sheets": {
            "SourceIdentity": [
                {"key": "x10", "value": "e1_x10_risk_universe"},
                {"key": "x11_v2", "value": "e1_x11_policy_gate_v2"},
                {"key": "forbidden", "value": ",".join(FORBIDDEN_ALPHA_DAYS)},
            ],
            "MeasurementContract": [
                {"field": k, "contract": v}
                for k, v in {
                    "one_lot_notional_yen": "best_ask × 100",
                    "one_tick_risk_yen_100": "tick_size × 100",
                    "current_spread_cost_yen_100": "max(ask-bid,0)×100",
                    "estimated_execution_risk_yen": "max(3 rolling p95)",
                    "strategy_loss_risk": "unresolved",
                    "total_trade_risk": "unresolved",
                }.items()
            ],
            "HistoricalReplay": [
                {"date": r["date"], "symbol": r["symbol"],
                 "est": r.get("estimated_execution_risk_yen"),
                 "notional": r.get("asof_one_lot_notional_yen") or r.get("one_lot_notional_yen"),
                 "status": r.get("measurement_status"),
                 "history_end": r.get("history_end")}
                for r in replay_a["daily"]
            ],
            "ReplayParity": parity.get("symbol_parity") or [],
            "DailyMetrics": replay_a["daily"],
            "SymbolMetrics": replay_a["symbol_metrics"],
            "Kioxia285A": replay_a["kioxia_285A"],
            "RuntimeObserver": [runtime],
            "DecisionParity": [dec],
            "PerformanceImpact": [{
                "mean_observer_latency_ms": dec.get("mean_observer_latency_ms"),
                "max_observer_latency_ms": dec.get("max_observer_latency_ms"),
                "performance_ok": dec.get("performance_ok"),
            }],
            "CapitalStatus": [{"status": CAPITAL_POLICY_STATUS, "configured_risk_capital_cap_yen": None}],
            "ChangeLog": [
                {"change": "pure_measure_module", "note": "OBSERVER_ONLY"},
                {"change": "historical_replay", "note": "D-1 rolling; design days only"},
                {"change": "capital", "note": "NOT_EVALUATED; no auto-select"},
            ],
        },
    }
    det = {
        "ab_match": ab_match,
        "replay_sha_a": sha256_obj(replay_a["daily"]),
        "replay_sha_b": sha256_obj(replay_b["daily"]),
        "parity_pass": parity["pass"],
        "pnl_independence": pnl_indep,
        "reserved_ok": reserved_ok,
    }
    publish(report, tests, det, OUT)
    print("VERDICT", verdict, flush=True)
    return report


if __name__ == "__main__":
    run()
