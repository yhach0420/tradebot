#!/usr/bin/env python3
"""Phase313: Report after removing TV:mid from SCORE_POINTS_V2."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase313_remove_tv_token_report.json"
SRC = REPO / "kabu_native/src"


def _run_py_compile() -> dict[str, Any]:
    targets = [
        SRC / "small_paper/entry_expectancy_score_shadow.py",
        SRC / "research/exposure_gate.py",
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
        "stdout_tail": "\n".join(r.stdout.splitlines()[-10:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-8:]) if r.stderr else "",
    }


def main() -> int:
    sys.path.insert(0, str(SRC))
    from small_paper.entry_expectancy_score_shadow import (
        REQUIRED_V2_TOKENS,
        SCORE_GE5_THRESHOLD,
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
    fields = compute_entry_expectancy_score_fields(trade=max_trade)
    active = active_score_tokens_v2(max_trade)

    py_compile = _run_py_compile()
    unit_tests = _run_unit_tests()

    report = {
        "phase": 313,
        "title": "remove_tv_token",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "change_summary": {
            "removed_from_SCORE_POINTS_V2": ["TV:mid"],
            "SCORE_POINTS_V2": dict(SCORE_POINTS_V2),
            "REQUIRED_V2_TOKENS": sorted(REQUIRED_V2_TOKENS),
            "entry_score_min": SCORE_GE5_THRESHOLD,
            "max_score": sum(SCORE_POINTS_V2.values()),
        },
        "verification": {
            "tv_mid_in_v2": "TV:mid" in SCORE_POINTS_V2,
            "active_score_tokens_max": active,
            "tv_in_active_tokens": any(t.startswith("TV:") for t in active),
            "max_v2_score": fields["entry_expectancy_score_v2"],
            "score_ge5_flag": bool(fields["entry_expectancy_score_v2_ge5_flag"]),
            "momentum_low_required_pass": momentum_low_required_for_v2(max_trade),
        },
        "phase312_reference": {"TV_required": False},
        "checks": {"py_compile": py_compile, "unit_tests": unit_tests},
        "verdict": {
            "tv_removed": "TV:mid" not in SCORE_POINTS_V2,
            "active_tokens_clean": not any(t.startswith("TV:") for t in active),
            "max_score_is_5": sum(SCORE_POINTS_V2.values()) == 5,
            "tests_passed": py_compile["success"] and unit_tests["success"],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"v2={SCORE_POINTS_V2} max={fields['entry_expectancy_score_v2']} tests_ok={report['verdict']['tests_passed']}")
    return 0 if report["verdict"]["tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
