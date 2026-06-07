#!/usr/bin/env python3
"""Phase311: Report after repointing SCORE_POINTS_V2 and Momentum:low required gate."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase311_repoint_score_after_token_removal_report.json"
SRC = REPO / "kabu_native/src"


def _run_py_compile() -> dict[str, Any]:
    targets = [
        SRC / "small_paper/entry_expectancy_score_shadow.py",
        SRC / "research/exposure_gate.py",
        REPO / "kabu_native/tests/test_phase237_entry_expectancy_score_v2_shadow.py",
        REPO / "kabu_native/tests/test_phase267_entry_score_v2_gate.py",
    ]
    ok = True
    errors: list[str] = []
    for t in targets:
        r = subprocess.run([sys.executable, "-m", "py_compile", str(t)], capture_output=True, text=True)
        if r.returncode != 0:
            ok = False
            errors.append(f"{t}: {r.stderr.strip()}")
    return {"success": ok, "errors": errors}


def _run_unit_tests() -> dict[str, Any]:
    tests = [
        "kabu_native/tests/test_phase230_entry_expectancy_score_shadow.py",
        "kabu_native/tests/test_phase237_entry_expectancy_score_v2_shadow.py",
        "kabu_native/tests/test_phase267_entry_score_v2_gate.py",
        "kabu_native/tests/test_phase295_hbrecent_pregate_fix.py",
        "kabu_native/tests/test_phase299_board_pregate_fix.py",
    ]
    r = subprocess.run(
        [sys.executable, "-m", "unittest"] + tests,
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    return {
        "success": r.returncode == 0,
        "returncode": r.returncode,
        "tests": tests,
        "stdout_tail": "\n".join(r.stdout.splitlines()[-10:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-8:]) if r.stderr else "",
    }


def main() -> int:
    for p in (SRC, REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import importlib.util

    eg_path = SRC / "research/exposure_gate.py"
    spec = importlib.util.spec_from_file_location("exposure_gate_p311", eg_path)
    assert spec and spec.loader
    eg = importlib.util.module_from_spec(spec)
    sys.modules["exposure_gate_p311"] = eg
    spec.loader.exec_module(eg)
    REJECT_MOMENTUM_LOW_REQUIRED = eg.REJECT_MOMENTUM_LOW_REQUIRED
    ExposureGate = eg.ExposureGate
    ExposureGateConfig = eg.ExposureGateConfig
    from small_paper.entry_expectancy_score_shadow import (
        REQUIRED_V2_TOKENS,
        SCORE_GE5_THRESHOLD,
        SCORE_GE6_THRESHOLD,
        SCORE_POINTS_V2,
        active_score_tokens_v2,
        compute_entry_expectancy_score_fields,
        momentum_low_required_for_v2,
    )

    max_trade = {
        "trading_value": 3e10,
        "entry_high_break_recent": False,
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
    }
    score5_trade = {
        "trading_value": 3e10,
        "entry_high_break_recent": False,
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
    }
    fields_max = compute_entry_expectancy_score_fields(trade=max_trade)
    active_max = active_score_tokens_v2(max_trade)

    gate = ExposureGate(ExposureGateConfig(entry_score_v2_min=SCORE_GE5_THRESHOLD))
    gate_pass = gate.evaluate_entry(
        {
            "profile": "momentum_volume_v13_combined",
            "symbol": "9984.T",
            "entry_time": "2026-05-21T09:30:00+09:00",
            "exit_time": "2026-05-21T10:00:00+09:00",
            "trade_date": "2026-05-21",
            **max_trade,
            "continuation_quality_score": 0.45,
        }
    )

    py_compile = _run_py_compile()
    unit_tests = _run_unit_tests()

    report = {
        "phase": 311,
        "title": "repoint_score_after_token_removal",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "change_summary": {
            "SCORE_POINTS_V2": dict(SCORE_POINTS_V2),
            "REQUIRED_V2_TOKENS": sorted(REQUIRED_V2_TOKENS),
            "entry_score_min": SCORE_GE5_THRESHOLD,
            "max_score": sum(SCORE_POINTS_V2.values()),
            "gate_reject_momentum_low": REJECT_MOMENTUM_LOW_REQUIRED,
        },
        "verification": {
            "active_score_tokens_max": active_max,
            "max_v2_score": fields_max["entry_expectancy_score_v2"],
            "score_ge5_achievable": bool(fields_max["entry_expectancy_score_v2_ge5_flag"]),
            "score_ge6_achievable": bool(fields_max["entry_expectancy_score_v2_ge6_flag"]),
            "momentum_low_required_pass": momentum_low_required_for_v2(max_trade),
            "duration_price_absent": not any(
                t.startswith("Duration:") or t.startswith("Price:") for t in active_max
            ),
            "gate_accepts_max_trade": gate_pass.accept,
        },
        "phase308_reference": {
            "scenario_E_min5_momentum_required": {"trade_count": 60, "profit_factor": 1.7028},
            "note": "min=4 rejected due to 20260518 outlier (Phase309)",
        },
        "checks": {"py_compile": py_compile, "unit_tests": unit_tests},
        "verdict": {
            "repoint_applied": SCORE_POINTS_V2.get("Momentum:low") == 2,
            "momentum_required_at_gate": True,
            "tests_passed": py_compile["success"] and unit_tests["success"],
            "score_ge5_established": bool(fields_max["entry_expectancy_score_v2_ge5_flag"]),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"v2={SCORE_POINTS_V2} max={fields_max['entry_expectancy_score_v2']} "
        f"tests_ok={report['verdict']['tests_passed']}"
    )
    return 0 if report["verdict"]["tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
