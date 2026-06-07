#!/usr/bin/env python3
"""Phase310: Report after removing Duration:high / Price:high from SCORE_POINTS_V2."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase310_remove_duration_price_tokens_report.json"
SRC = REPO / "kabu_native/src"


def _active_tokens(trade: dict[str, Any]) -> list[str]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _feature_token

    active: list[str] = []
    for token in SCORE_POINTS_V2:
        lbl = token.split(":", 1)[0]
        if _feature_token(lbl, trade) == token:
            active.append(token)
    return active


def _run_py_compile() -> dict[str, Any]:
    targets = [
        SRC / "small_paper/entry_expectancy_score_shadow.py",
        REPO / "kabu_native/tests/test_phase237_entry_expectancy_score_v2_shadow.py",
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
        "stdout_tail": "\n".join(r.stdout.splitlines()[-8:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-8:]) if r.stderr else "",
    }


def main() -> int:
    sys.path.insert(0, str(SRC))
    from small_paper.entry_expectancy_score_shadow import (
        SCORE_GE5_THRESHOLD,
        SCORE_POINTS,
        SCORE_POINTS_V2,
        compute_entry_expectancy_score_fields,
    )

    sample_full = {
        "trading_value": 3e10,
        "rolling_mae_pct": -0.0003,
        "entry_high_break_recent": False,
        "max_continuation_duration": 500.0,
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
        "current_price": 5000.0,
    }
    sample_max_v2 = {
        "trading_value": 3e10,
        "entry_high_break_recent": False,
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
    }
    fields_full = compute_entry_expectancy_score_fields(trade=sample_full)
    fields_max = compute_entry_expectancy_score_fields(trade=sample_max_v2)
    active_full = _active_tokens(sample_full)
    active_max = _active_tokens(sample_max_v2)

    py_compile = _run_py_compile()
    unit_tests = _run_unit_tests()

    report = {
        "phase": 310,
        "title": "remove_duration_price_tokens",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "change_summary": {
            "removed_from_SCORE_POINTS_V2": ["Duration:high", "Price:high"],
            "retained_in_SCORE_POINTS_V2": list(SCORE_POINTS_V2.keys()),
            "SCORE_POINTS_V2": dict(SCORE_POINTS_V2),
            "SCORE_POINTS_v1_unchanged": dict(SCORE_POINTS),
            "entry_score_min_unchanged": SCORE_GE5_THRESHOLD,
            "repointing_deferred": True,
        },
        "verification": {
            "duration_high_in_v2": "Duration:high" in SCORE_POINTS_V2,
            "price_high_in_v2": "Price:high" in SCORE_POINTS_V2,
            "active_score_tokens_sample_full": active_full,
            "active_score_tokens_max_v2_trade": active_max,
            "duration_price_in_active_tokens": any(
                t.startswith("Duration:") or t.startswith("Price:") for t in active_full + active_max
            ),
            "max_v2_score_observed": fields_max["entry_expectancy_score_v2"],
            "full_trade_v2_score": fields_full["entry_expectancy_score_v2"],
            "score_ge5_flag_full_trade": fields_full["entry_expectancy_score_v2_ge5_flag"],
        },
        "known_followup": {
            "score_ge5_rarity": (
                "Phase308 scenario B: only 2 pool candidates reached score>=5 in replay "
                "(1,562,172 decision pool). Re-pointing / min threshold change deferred to later phase."
            ),
            "phase308_B_trade_count": 2,
            "phase308_B_score5_pool_count": 2,
        },
        "checks": {
            "py_compile": py_compile,
            "unit_tests": unit_tests,
        },
        "verdict": {
            "tokens_removed_from_v2": (
                "Duration:high" not in SCORE_POINTS_V2 and "Price:high" not in SCORE_POINTS_V2
            ),
            "active_tokens_clean": not any(
                t.startswith("Duration:") or t.startswith("Price:") for t in active_full + active_max
            ),
            "tests_passed": unit_tests["success"] and py_compile["success"],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"v2={SCORE_POINTS_V2} tests_ok={report['verdict']['tests_passed']}")
    return 0 if report["verdict"]["tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
