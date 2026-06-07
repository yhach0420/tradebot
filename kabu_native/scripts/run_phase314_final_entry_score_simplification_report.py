#!/usr/bin/env python3
"""Phase314: Report after final Momentum+Board entry score simplification."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase314_final_entry_score_simplification_report.json"
SRC = REPO / "kabu_native/src"
CFG = REPO / "kabu_native/configs/small_paper_pilot_q070_cap3.yaml"


def _run_py_compile() -> dict[str, Any]:
    targets = [
        SRC / "small_paper/entry_expectancy_score_shadow.py",
        SRC / "research/exposure_gate.py",
        SRC / "small_paper/live_observer_readiness.py",
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
        "stdout_tail": "\n".join(r.stdout.splitlines()[-12:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-8:]) if r.stderr else "",
    }


def main() -> int:
    for p in (SRC, REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    from research.exposure_gate import (
        REJECT_ENTRY_SCORE_V2_BELOW,
        REJECT_MOMENTUM_LOW_REQUIRED,
        ExposureGate,
        ExposureGateConfig,
    )
    from small_paper.config import load_pilot_config
    from small_paper.entry_expectancy_score_shadow import (
        ENTRY_SCORE_V2_GATE_MIN,
        REQUIRED_V2_TOKENS,
        SCORE_POINTS_V2,
        active_score_tokens_v2,
        compute_entry_expectancy_score_fields,
        momentum_low_required_for_v2,
    )
    from small_paper.live_observer_readiness import EXPECTED_ENTRY_SCORE_V2_MIN

    full_trade = {
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
        "entry_high_break_recent": False,
        "trading_value": 3e10,
        "current_price": 5000.0,
        "max_continuation_duration": 500.0,
    }
    momentum_only = {
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.40,
    }
    no_momentum = {
        "momentum_continuation_score": 0.35,
        "entry_order_book_imbalance": 0.50,
    }

    fields_full = compute_entry_expectancy_score_fields(trade=full_trade)
    active_full = active_score_tokens_v2(full_trade)

    gate = ExposureGate(ExposureGateConfig(entry_score_v2_min=ENTRY_SCORE_V2_GATE_MIN))
    base = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "9984.T",
        "entry_time": "2026-05-21T09:30:00+09:00",
        "exit_time": "2026-05-21T10:00:00+09:00",
        "trade_date": "2026-05-21",
        "continuation_quality_score": 0.45,
    }
    d_pass = gate.evaluate_entry({**base, **full_trade})
    d_mom_only = gate.evaluate_entry({**base, **momentum_only})
    d_no_mom = gate.evaluate_entry({**base, **no_momentum})

    cfg = load_pilot_config(CFG)
    py_compile = _run_py_compile()
    unit_tests = _run_unit_tests()

    report = {
        "phase": 314,
        "title": "final_entry_score_simplification",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "change_summary": {
            "removed_from_SCORE_POINTS_V2": [
                "HBRecent:no",
                "TV:mid",
                "Duration:high",
                "Price:high",
            ],
            "SCORE_POINTS_V2": dict(SCORE_POINTS_V2),
            "REQUIRED_V2_TOKENS": sorted(REQUIRED_V2_TOKENS),
            "ENTRY_SCORE_V2_GATE_MIN": ENTRY_SCORE_V2_GATE_MIN,
            "max_score": sum(SCORE_POINTS_V2.values()),
            "config_entry_score_v2_min": cfg.entry_score_v2_min,
            "EXPECTED_ENTRY_SCORE_V2_MIN": EXPECTED_ENTRY_SCORE_V2_MIN,
        },
        "verification": {
            "active_score_tokens_full": active_full,
            "removed_tokens_absent": not any(
                t.startswith(("Duration:", "Price:", "TV:", "HBRecent:")) for t in active_full
            ),
            "max_v2_score": fields_full["entry_expectancy_score_v2"],
            "gate_pass_momentum_and_board": d_pass.accept,
            "gate_reject_momentum_only_reason": d_mom_only.reason,
            "gate_reject_no_momentum_reason": d_no_mom.reason,
        },
        "gate_matrix": {
            "momentum_and_board": {
                "accept": d_pass.accept,
                "reason": d_pass.reason,
                "score": d_pass.entry_expectancy_score_v2,
            },
            "momentum_only_board_missing": {
                "accept": d_mom_only.accept,
                "reason": d_mom_only.reason,
                "expected_reason": REJECT_ENTRY_SCORE_V2_BELOW,
            },
            "no_momentum": {
                "accept": d_no_mom.accept,
                "reason": d_no_mom.reason,
                "expected_reason": REJECT_MOMENTUM_LOW_REQUIRED,
            },
        },
        "phase313_hbrecent_review_reference": {"HBRecent_required": False},
        "checks": {"py_compile": py_compile, "unit_tests": unit_tests},
        "verdict": {
            "simplified_to_momentum_board": set(SCORE_POINTS_V2.keys())
            == {"Momentum:low", "Board:mid"},
            "max_score_is_3": sum(SCORE_POINTS_V2.values()) == 3,
            "gate_min_is_3": cfg.entry_score_v2_min == 3,
            "tests_passed": py_compile["success"] and unit_tests["success"],
            "gate_matrix_ok": (
                d_pass.accept
                and d_mom_only.reason == REJECT_ENTRY_SCORE_V2_BELOW
                and d_no_mom.reason == REJECT_MOMENTUM_LOW_REQUIRED
            ),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"v2={SCORE_POINTS_V2} min={ENTRY_SCORE_V2_GATE_MIN} tests_ok={report['verdict']['tests_passed']}")
    return 0 if report["verdict"]["tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
