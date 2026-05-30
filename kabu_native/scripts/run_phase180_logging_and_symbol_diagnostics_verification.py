#!/usr/bin/env python3
"""
Phase180: Verify logging fields, summary audit metadata, and symbol diagnostics robustness.

Writes:
  kabu_native/results/reports/phase180_logging_and_symbol_diagnostics_verification.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


OUT = Path("kabu_native/results/reports/phase180_logging_and_symbol_diagnostics_verification.json")
PILOT_RUNNER = Path("kabu_native/src/small_paper/pilot_runner.py")
DIAG_MODULE = Path("kabu_native/src/small_paper/phase180_symbol_diagnostics.py")


def _run(cmd: list[str]) -> dict[str, Any]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
        "ok": p.returncode == 0,
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    for p in (repo / "kabu_native" / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from small_paper.observer_position_tracker import OBSERVER_EXIT, ObserverJudgmentEvent
    from small_paper.phase180_symbol_diagnostics import aggregate_symbol_diagnostics
    from small_paper.pilot_runner import (
        EVENT_FIELDS,
        _enrich_accept_audit_fields,
        _execution_audit_fields,
        _observer_exit_event_row,
    )
    from small_paper.config import SmallPaperPilotConfig

    src = PILOT_RUNNER.read_text(encoding="utf-8")
    checks: dict[str, bool] = {
        "event_fields_observer_exit_columns": all(
            k in EVENT_FIELDS
            for k in (
                "hold_sec",
                "entry_price",
                "exit_price",
                "structural_exit_reason",
                "peak_mfe_pct",
                "trailing_mfe_activated",
                "stop_hit",
                "session_close",
                "overlap_replaced_review",
                "tick_size",
            )
        ),
        "log_and_dispatch_observer_events_defined": "_log_and_dispatch_observer_events" in src,
        "enrich_accept_audit_fields_defined": "_enrich_accept_audit_fields" in src,
        "execution_audit_fields_defined": "_execution_audit_fields" in src,
        "accepted_suitability_copy_on_accept": "if decision.accept:" in src
        and "daytrade_suitability_score" in src,
        "daily_runner_config_sha256": "config_sha256" in (
            repo / "kabu_native/src/runner/am_pm_daily_runner.py"
        ).read_text(encoding="utf-8"),
    }

    ev = ObserverJudgmentEvent(
        kind=OBSERVER_EXIT,
        symbol="1234.T",
        context={
            "exit_reason": "trailing_mfe_exit",
            "is_structural_exit": True,
            "entry_time": "2026-05-28T09:01:00+09:00",
            "exit_time": "2026-05-28T09:05:00+09:00",
            "hold_sec": 240.0,
            "entry_price": 1000.0,
            "current_price": 1008.0,
            "unrealized_pnl_pct": 0.8,
            "peak_mfe_pct": 1.2,
            "rolling_mae_pct": -0.3,
            "trailing_mfe_activated": True,
            "stop_hit": False,
            "session_close": False,
            "overlap_replaced_review": False,
            "structural_exit_reason": "trailing_mfe_exit",
        },
    )
    row = _observer_exit_event_row(ev, source="test", message_index=1, profile="q070")
    checks["observer_exit_row_event_type"] = row.get("event_type") == "observer_exit"
    checks["observer_exit_row_trailing"] = row.get("exit_reason") == "trailing_mfe_exit"

    cfg = SmallPaperPilotConfig(
        profile="q070",
        structural_exit_policy="combined_structural_exit_v1_trailing_mfe_shadow",
        low_liquidity_shadow_enabled=True,
        shadow_only=True,
    )
    audit = _execution_audit_fields(cfg)
    checks["execution_audit_order_disabled"] = audit.get("order_enabled") is False
    checks["execution_audit_low_liq"] = audit.get("low_liquidity_shadow") is True
    checks["execution_audit_exit_shadow"] = audit.get("exit_policy_shadow") == "trailing-mfe"

    with tempfile.TemporaryDirectory() as td:
        session = Path(td) / "sess"
        session.mkdir()
        legacy = [
            {
                "event_time": "2026-05-28T09:00:00+09:00",
                "event_type": "accepted",
                "symbol": "9999.T",
                "gate_accept": True,
            },
            {
                "event_time": "2026-05-28T09:10:00+09:00",
                "event_type": "candidate",
                "symbol": "9999.T",
            },
        ]
        (session / "small_paper_events.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in legacy) + "\n",
            encoding="utf-8",
        )
        agg = aggregate_symbol_diagnostics([session])
        sym = (agg.get("symbols") or [{}])[0]
        checks["legacy_events_no_crash"] = sym.get("symbol") == "9999.T"
        checks["legacy_missing_observer_exit_ok"] = sym.get("observer_exit_count") == 0

    compile_res = _run([sys.executable, "-m", "py_compile", str(PILOT_RUNNER), str(DIAG_MODULE)])
    t_phase180 = _run(
        [sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_phase180_logging"]
    )
    t_intraday = _run([sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_intraday_refresh"])

    verdict = "pass"
    if not all(checks.values()):
        verdict = "fail_checks"
    elif not compile_res.get("ok"):
        verdict = "fail_py_compile"
    elif not t_phase180.get("ok"):
        verdict = "fail_tests"

    report = {
        "phase": 180,
        "verdict": verdict,
        "checks": checks,
        "py_compile": compile_res,
        "tests": {
            "test_phase180_logging": t_phase180,
            "test_intraday_refresh": t_intraday,
        },
        "sample_observer_exit_row": row,
        "execution_audit_sample": audit,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdict={verdict} wrote {OUT}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
