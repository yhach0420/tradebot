#!/usr/bin/env python3
"""
Phase333: Summary 100-share yen PnL display verification.

Output: kabu_native/results/reports/phase333_summary_100share_yen_pnl_report.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

OUT = Path("kabu_native/results/reports/phase333_summary_100share_yen_pnl_report.json")
TEST_MODULE = "kabu_native/tests/test_phase333_summary_100share_yen_pnl.py"
JST = ZoneInfo("Asia/Tokyo")

MODULE_PATHS = (
    "kabu_native/src/replay/pnl_yen.py",
    "kabu_native/src/small_paper/discord_message_builder.py",
    "kabu_native/src/small_paper/discord_notifier.py",
    "kabu_native/src/small_paper/pilot_runner.py",
)


def _run(cmd: list[str], *, cwd: Path, pythonpath: Path | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if pythonpath is not None:
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(pythonpath) + (os.pathsep + prev if prev else "")
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "ok": p.returncode == 0,
        "stderr": p.stderr[-4000:],
        "stdout": p.stdout[-3000:],
    }


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo


def _py_compile(repo: Path) -> dict[str, Any]:
    results = []
    ok = True
    for rel in MODULE_PATHS:
        r = _run([sys.executable, "-m", "py_compile", str(repo / rel)], cwd=repo)
        results.append({"path": rel, **r})
        ok = ok and r["ok"]
    return {"ok": ok, "modules": results}


def _demo() -> dict[str, Any]:
    from small_paper.discord_message_builder import (
        aggregate_daily_metrics,
        build_daily_summary_detail,
        format_summary_yen_display_lines,
        summary_notification_labels,
    )

    events = [
        {
            "event_type": "observer_exit",
            "symbol": "6981.T",
            "entry_price": 1000.0,
            "exit_price": 983.0,
            "pnl_pct": -1.7,
        }
    ]
    metrics = aggregate_daily_metrics(
        events,
        {"peak_open_slots": 1, "am_pm_session": {"kind": "pm"}},
        max_concurrent_positions=3,
    )
    return {
        "summary_labels_pm": summary_notification_labels({"am_pm_session": {"kind": "pm"}}),
        "yen_display_lines": format_summary_yen_display_lines(metrics),
        "daily_summary_excerpt": build_daily_summary_detail(metrics).splitlines()[:20],
        "metrics_snapshot": {
            k: metrics.get(k)
            for k in (
                "total_pnl_pct",
                "total_pnl_yen_100",
                "avg_pnl_yen_100",
                "profit_factor_yen_100",
            )
        },
    }


def main() -> int:
    repo = _bootstrap()
    native_src = repo / "kabu_native" / "src"
    py_compile = _py_compile(repo)
    unit_test = _run(
        [sys.executable, "-m", "unittest", TEST_MODULE, "-v"],
        cwd=repo,
        pythonpath=native_src,
    )
    demo = _demo()
    pilot = (repo / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")

    all_ok = (
        py_compile["ok"]
        and unit_test["ok"]
        and "最終損益:" in "\n".join(demo["daily_summary_excerpt"])
        and demo["metrics_snapshot"]["total_pnl_yen_100"] is not None
        and "_observer_exit_pnl_summary_fields" in pilot
    )

    report = {
        "phase": 333,
        "title": "summary_100share_yen_pnl_report",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "scope": {
            "daily_summary": True,
            "am_summary": True,
            "pm_summary": True,
            "session_summary_json": True,
            "discord_summary_notify": True,
            "unchanged": ["entry_notify", "exit_notify", "exit_logic", "entry_logic"],
        },
        "display_format": {
            "final_pnl": "最終損益: {pct}% / {yen}(100株)",
            "avg_pnl": "平均損益: {yen}/取引(100株)",
            "pf": "PF: {profit_factor_yen_100}",
        },
        "verification": {
            "py_compile": py_compile,
            "unit_test": unit_test,
            "demo": demo,
        },
        "verdict": {
            "summary_yen_display_ok": all_ok,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"summary_yen_display_ok={all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
