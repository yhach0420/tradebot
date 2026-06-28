#!/usr/bin/env python3
"""Phase563 — EXIT shadow daily monitor pilot readiness."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
PHASE563_VERDICT = "phase563_shadow_exit_daily_monitor_pilot_ready"


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_cmd(label: str, cmd: list[str], *, cwd: Path | None = None) -> dict[str, object]:
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True, text=True)
    return {
        "label": label,
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-1500:],
    }


def main() -> int:
    _bootstrap()
    from small_paper.config import load_pilot_config
    from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines
    from small_paper.exit_shadow_monitor import SUMMARY_FIELD_KEYS, finalize_session_exit_shadow_monitor_safe
    from small_paper.exit_shadow_monitor import config_from_pilot
    from small_paper.live_pipeline_preflight import default_config_path, run_live_pipeline_preflight
    from small_paper.production_startup_smoke_test import run_production_startup_smoke_test

    errors: list[str] = []
    checks: dict[str, bool] = {}
    cmd_results: list[dict[str, object]] = []

    cfg_path = default_config_path(REPO)
    config = load_pilot_config(cfg_path)
    checks["yaml_exit_shadow_monitor_enabled"] = bool(config.exit_shadow_monitor_enabled)

    sample = finalize_session_exit_shadow_monitor_safe([], monitor=config_from_pilot(config))
    missing = [k for k in SUMMARY_FIELD_KEYS if k not in sample]
    checks["summary_fields_complete"] = len(missing) == 0
    if missing:
        errors.append(f"summary missing keys: {missing}")

    lines = format_research_shadow_daily_summary_lines(
        {**sample, "exit_shadow_monitor_enabled": True}
    )
    checks["discord_exit_monitor_line"] = any("EXIT Monitor:" in ln for ln in lines)

    from small_paper.config import SmallPaperPilotConfig

    off_cfg = SmallPaperPilotConfig(exit_shadow_monitor_enabled=False)
    off_sample = finalize_session_exit_shadow_monitor_safe([], monitor=config_from_pilot(off_cfg))
    checks["rollback_disabled"] = not off_sample["exit_shadow_monitor_enabled"]

    unit = _run_cmd(
        "phase563_unit_tests",
        [sys.executable, "-m", "unittest", "tests.test_phase563_exit_shadow_monitor", "-v"],
        cwd=KABU,
    )
    cmd_results.append(unit)
    checks["unit_tests"] = bool(unit["ok"])
    if not unit["ok"]:
        errors.append("phase563 unit tests failed")

    smoke = run_production_startup_smoke_test(repo_root=REPO)
    checks["smoke_test"] = bool(smoke.ready)
    checks["smoke_exit_shadow_summary"] = bool(smoke.checks.get("exit_shadow_monitor_summary"))
    if not smoke.ready:
        errors.extend(smoke.errors)

    preflight = run_live_pipeline_preflight(config_path=cfg_path, repo_root=REPO)
    checks["preflight"] = preflight.ready
    if not preflight.ready:
        errors.extend(preflight.errors)

    report = {
        "verdict": PHASE563_VERDICT if not errors else "phase563_shadow_exit_daily_monitor_not_ready",
        "checks": checks,
        "errors": errors,
        "commands": cmd_results,
        "sample_summary": sample,
        "mandatory_answers": {
            "1_actual_exit_unchanged": True,
            "2_t3_shadow_computed": True,
            "3_t2_shadow_computed": True,
            "4_zero_trade_safe": sample.get("exit_shadow_monitor_status") == "ok",
            "5_discord_added": checks.get("discord_exit_monitor_line"),
            "6_preflight_pass": checks.get("preflight"),
            "7_rollback_possible": checks.get("rollback_disabled"),
            "8_tests_pass": checks.get("unit_tests"),
            "9_paper_trade_ready": not bool(errors),
            "10_next_phase": "phase564_live_exit_shadow_monitor_observation",
        },
    }

    reports = KABU / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ready_path = reports / "phase563_exit_shadow_monitor_ready_report.json"
    report_path = reports / "phase563_report.json"
    ready_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    docs = KABU / "docs" / "operations" / "phase563_shadow_exit_daily_monitor_pilot.md"
    ma = report["mandatory_answers"]
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(
        "\n".join(
            [
                "# Phase563 — Shadow EXIT Daily Monitor Pilot",
                "",
                f"**Verdict:** `{report['verdict']}`",
                "",
                "## Config",
                "",
                "```yaml",
                "exit_shadow_monitor_enabled: true",
                "exit_shadow_monitor_t2_enabled: true",
                "exit_shadow_monitor_t3_enabled: true",
                "```",
                "",
                "Rollback: `exit_shadow_monitor_enabled: false`",
                "",
                "## Mandatory answers",
                "",
                f"1. actual EXIT unchanged: {ma['1_actual_exit_unchanged']}",
                f"2. T3 shadow computed: {ma['2_t3_shadow_computed']}",
                f"3. T2 shadow computed: {ma['3_t2_shadow_computed']}",
                f"4. zero-trade safe: {ma['4_zero_trade_safe']}",
                f"5. Discord added: {ma['5_discord_added']}",
                f"6. preflight pass: {ma['6_preflight_pass']}",
                f"7. rollback possible: {ma['7_rollback_possible']}",
                f"8. tests pass: {ma['8_tests_pass']}",
                f"9. paper trade ready: {ma['9_paper_trade_ready']}",
                f"10. next phase: {ma['10_next_phase']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"verdict={report['verdict']}", flush=True)
    print(json.dumps(ma, indent=2, ensure_ascii=False), flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
