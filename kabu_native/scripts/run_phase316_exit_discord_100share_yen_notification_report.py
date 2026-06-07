#!/usr/bin/env python3
"""Phase316: EXIT Discord notification 100-share yen display report."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase316_exit_discord_100share_yen_notification_report.json"
SRC = REPO / "kabu_native/src"


def _run_py_compile() -> dict[str, Any]:
    targets = [
        SRC / "replay/pnl_yen.py",
        SRC / "notify/discord.py",
        SRC / "small_paper/discord_message_builder.py",
        SRC / "small_paper/discord_notifier.py",
        REPO / "kabu_native/tests/test_phase316_exit_discord_100share_yen_notification.py",
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
    tests = ["kabu_native/tests/test_phase316_exit_discord_100share_yen_notification.py"]
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
        "stdout_tail": "\n".join(r.stdout.splitlines()[-14:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-8:]) if r.stderr else "",
    }


def main() -> int:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from replay.pnl_yen import format_exit_pnl_line
    from small_paper.discord_message_builder import build_entry_detail, build_exit_detail

    exit_with_yen = build_exit_detail(
        symbol="3905.T",
        entry_price=2857.0,
        exit_price=2869.0,
        pnl_pct=0.42,
        mfe_pct=0.8,
        mae_pct=-0.2,
        hold_minutes=12.0,
        exit_reason="trailing_mfe_exit",
        pnl_yen_100=1200.0,
    )
    exit_without_yen = build_exit_detail(
        symbol="9984.T",
        entry_price=0.0,
        exit_price=0.0,
        pnl_pct=-0.5,
        mfe_pct=None,
        mae_pct=None,
        hold_minutes=5.0,
        exit_reason="hard_stop",
    )
    entry_sample = build_entry_detail(
        symbol="3905.T",
        entry_price=4520.0,
        stop_price=4465.76,
        slot_usage="2/3",
        entry_score_v2=3,
        data={"entry_expectancy_score_v2": 3},
    )

    py_compile = _run_py_compile()
    unit_tests = _run_unit_tests()

    report = {
        "phase": 316,
        "title": "exit_discord_100share_yen_notification",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "change_summary": {
            "scope": "EXIT_notification_display_only",
            "entry_notification_changed": False,
            "daily_summary_changed": False,
            "entry_exit_logic_changed": False,
            "display_format": "損益: {pnl_pct}% / {pnl_yen_100}円(100株)",
            "example": format_exit_pnl_line(0.42, 1200.0),
        },
        "samples": {
            "exit_with_yen": exit_with_yen,
            "exit_without_yen": exit_without_yen,
            "entry_unchanged_has_no_yen": "円(100株)" not in entry_sample,
        },
        "checks": {"py_compile": py_compile, "unit_tests": unit_tests},
        "verdict": {
            "yen_in_exit_with_pnl_yen_100": "損益: +0.42% / +1,200円(100株)" in exit_with_yen,
            "exit_without_yen_still_has_pct": "損益: -0.50%" in exit_without_yen,
            "entry_unchanged": "円(100株)" not in entry_sample,
            "tests_passed": py_compile["success"] and unit_tests["success"],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"example={report['change_summary']['example']} tests_ok={report['verdict']['tests_passed']}")
    return 0 if report["verdict"]["tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
