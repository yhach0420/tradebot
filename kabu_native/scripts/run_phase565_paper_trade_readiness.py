#!/usr/bin/env python3
"""Phase565: paper trade readiness after Phase563 EXIT shadow monitor."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
PHASE565_VERDICT = "phase565_paper_trade_readiness_after_exit_shadow_monitor_ok"

EXPECTED_STRUCTURAL_EXIT = "combined_structural_exit_v1_trailing_mfe_shadow"
BAT_PATH = REPO / "run_paper_trade.bat"


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_cmd(label: str, cmd: list[str], *, cwd: Path) -> dict[str, object]:
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    elapsed = round(time.monotonic() - t0, 2)
    return {
        "label": label,
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "elapsed_sec": elapsed,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "ok": proc.returncode == 0,
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase565 paper trade readiness")
    parser.add_argument("--skip-am-dry-run", action="store_true", help="Skip AM runner dry-run (~5min)")
    parser.add_argument("--smoke-max-sec", type=float, default=60.0)
    args = parser.parse_args()

    from small_paper.config import SmallPaperPilotConfig, load_pilot_config
    from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines
    from small_paper.exit_shadow_monitor import SUMMARY_FIELD_KEYS, finalize_session_exit_shadow_monitor_safe
    from small_paper.exit_shadow_monitor import config_from_pilot
    from small_paper.live_pipeline_preflight import default_config_path, run_live_pipeline_preflight
    from small_paper.production_startup_smoke_test import run_production_startup_smoke_test

    errors: list[str] = []
    checks: dict[str, bool] = {}
    cmd_results: list[dict[str, object]] = []
    fixes_needed: list[str] = []

    cfg_path = default_config_path(REPO)
    if not cfg_path.is_absolute():
        cfg_path = REPO / cfg_path
    config = load_pilot_config(cfg_path)

    # 1 actual EXIT unchanged (T0 structural policy)
    checks["1_actual_exit_unchanged"] = config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT
    if not checks["1_actual_exit_unchanged"]:
        errors.append(
            f"structural_exit_policy expected {EXPECTED_STRUCTURAL_EXIT}, "
            f"got {config.structural_exit_policy!r}"
        )

    # 2 exit_shadow_monitor enabled in production YAML
    checks["2_exit_shadow_monitor_enabled"] = bool(config.exit_shadow_monitor_enabled)
    checks["2_t2_enabled"] = bool(config.exit_shadow_monitor_t2_enabled)
    checks["2_t3_enabled"] = bool(config.exit_shadow_monitor_t3_enabled)
    if not config.exit_shadow_monitor_enabled:
        errors.append("exit_shadow_monitor_enabled is false in production YAML")

    monitor = config_from_pilot(config)
    sample = finalize_session_exit_shadow_monitor_safe([], monitor=monitor)

    # 3 T2/T3 shadow fields in summary
    shadow_keys = [
        "shadow_exit_t2_pnl",
        "shadow_exit_t3_pnl",
        "shadow_exit_t2_delta",
        "shadow_exit_t3_delta",
    ]
    missing_shadow = [k for k in shadow_keys if k not in sample]
    checks["3_t2_t3_shadow_summary"] = not missing_shadow
    if missing_shadow:
        errors.append(f"summary missing shadow keys: {missing_shadow}")

    # 4 zero-trade safe
    missing_all = [k for k in SUMMARY_FIELD_KEYS if k not in sample]
    checks["4_zero_trade_safe"] = (
        not missing_all and sample.get("exit_shadow_monitor_status") == "ok"
    )
    if missing_all:
        errors.append(f"zero-trade summary missing keys: {missing_all}")

    # 5 Discord EXIT Monitor line
    discord_lines = format_research_shadow_daily_summary_lines(
        {**sample, "exit_shadow_monitor_enabled": True}
    )
    checks["5_discord_exit_monitor"] = any("EXIT Monitor:" in ln for ln in discord_lines)
    if not checks["5_discord_exit_monitor"]:
        errors.append("Discord summary missing EXIT Monitor line")

    # 9 rollback
    off_cfg = SmallPaperPilotConfig(exit_shadow_monitor_enabled=False)
    off_sample = finalize_session_exit_shadow_monitor_safe([], monitor=config_from_pilot(off_cfg))
    checks["9_rollback"] = not off_sample["exit_shadow_monitor_enabled"]
    if not checks["9_rollback"]:
        errors.append("rollback exit_shadow_monitor_enabled=false did not disable monitor")

    # 8 run_paper_trade.bat path exists and references same preflight/smoke scripts
    bat_text = BAT_PATH.read_text(encoding="utf-8") if BAT_PATH.is_file() else ""
    checks["8_run_paper_trade_bat_exists"] = BAT_PATH.is_file()
    checks["8_bat_preflight_script"] = "check_live_pipeline_preflight.py" in bat_text
    checks["8_bat_smoke_script"] = "run_production_startup_smoke_test.py" in bat_text
    checks["8_bat_am_runner"] = "run_core10_dynamic40_am_pm_daily_runner.py" in bat_text
    if not checks["8_run_paper_trade_bat_exists"]:
        errors.append(f"run_paper_trade.bat not found at {BAT_PATH}")
    elif not all(
        checks[k]
        for k in (
            "8_bat_preflight_script",
            "8_bat_smoke_script",
            "8_bat_am_runner",
        )
    ):
        errors.append("run_paper_trade.bat missing expected script references")

    py = sys.executable

    phase563 = _run_cmd(
        "phase563_ready",
        [py, str(KABU / "scripts" / "run_phase563_exit_shadow_monitor_ready.py")],
        cwd=REPO,
    )
    cmd_results.append(phase563)
    checks["phase563_ready"] = bool(phase563["ok"])
    if not phase563["ok"]:
        errors.append("run_phase563_exit_shadow_monitor_ready failed")

    smoke_cmd = _run_cmd(
        "production_smoke_test",
        [
            py,
            str(KABU / "scripts" / "run_production_startup_smoke_test.py"),
            "--exit-policy-shadow",
            "trailing-mfe",
        ],
        cwd=REPO,
    )
    cmd_results.append(smoke_cmd)
    checks["8_smoke_cmd"] = bool(smoke_cmd["ok"])
    if not smoke_cmd["ok"]:
        errors.append("run_production_startup_smoke_test failed")

    preflight_cmd = _run_cmd(
        "live_pipeline_preflight",
        [py, str(KABU / "scripts" / "check_live_pipeline_preflight.py")],
        cwd=REPO,
    )
    cmd_results.append(preflight_cmd)
    checks["7_preflight_cmd"] = bool(preflight_cmd["ok"])
    if not preflight_cmd["ok"]:
        errors.append("check_live_pipeline_preflight failed")

    phase559 = _run_cmd(
        "phase559_readiness",
        [
            py,
            str(KABU / "scripts" / "run_phase559_paper_trade_readiness.py"),
            *(["--skip-am-dry-run"] if args.skip_am_dry_run else []),
        ],
        cwd=REPO,
    )
    cmd_results.append(phase559)
    checks["phase559_readiness"] = bool(phase559["ok"])
    if not phase559["ok"]:
        errors.append("run_phase559_paper_trade_readiness failed")

    smoke_report = run_production_startup_smoke_test(repo_root=REPO)
    preflight_report = run_live_pipeline_preflight(config_path=cfg_path, repo_root=REPO)
    checks["7_preflight"] = preflight_report.ready
    checks["8_smoke_test"] = smoke_report.ready
    checks["8_smoke_exit_shadow_summary"] = bool(
        smoke_report.checks.get("exit_shadow_monitor_summary")
    )
    if not preflight_report.ready:
        errors.extend([f"preflight: {e}" for e in preflight_report.errors])
    if not smoke_report.ready:
        errors.extend([f"smoke: {e}" for e in smoke_report.errors])
    if config.exit_shadow_monitor_enabled and not smoke_report.checks.get(
        "exit_shadow_monitor_summary"
    ):
        errors.append("smoke missing exit_shadow_monitor_summary check")

    if not args.skip_am_dry_run:
        am = _run_cmd(
            "am_runner_dry_run",
            [
                py,
                str(KABU / "scripts" / "run_core10_dynamic40_am_pm_daily_runner.py"),
                "--skip-kabu",
                "--skip-safety",
                "--dry-run-only",
                "--day-stamp",
                "20260521",
                "--universe-mode",
                "core10-dynamic40-price-risk-filter-shadow",
                "--enable-intraday-refresh",
                "--exit-policy-shadow",
                "trailing-mfe",
            ],
            cwd=REPO,
        )
        cmd_results.append(am)
        checks["9_am_runner_dry_run"] = bool(am["ok"])
        if not am["ok"]:
            errors.append("AM runner dry-run failed")
    else:
        phase559_report_path = KABU / "results" / "reports" / "phase559_report.json"
        am_from_phase559 = False
        if phase559_report_path.is_file():
            try:
                phase559_payload = json.loads(phase559_report_path.read_text(encoding="utf-8"))
                for row in phase559_payload.get("command_results", []):
                    if row.get("label") == "am_runner_dry_run" and row.get("ok"):
                        am_from_phase559 = True
                        cmd_results.append(
                            {
                                "label": "am_runner_dry_run",
                                "cmd": row.get("cmd", ""),
                                "exit_code": row.get("exit_code", 0),
                                "elapsed_sec": row.get("elapsed_sec", 0),
                                "ok": True,
                                "source": "phase559_report",
                            }
                        )
                        break
            except (json.JSONDecodeError, OSError):
                pass
        if am_from_phase559:
            checks["9_am_runner_dry_run"] = True
        else:
            startup = _run_cmd(
                "am_runner_startup_smoke",
                [
                    py,
                    str(KABU / "scripts" / "run_core10_dynamic40_am_pm_daily_runner.py"),
                    "--startup-smoke-test",
                    "--universe-mode",
                    "core10-dynamic40-price-risk-filter-shadow",
                    "--exit-policy-shadow",
                    "trailing-mfe",
                ],
                cwd=REPO,
            )
            cmd_results.append(startup)
            checks["9_am_runner_dry_run"] = bool(startup["ok"])
            if not startup["ok"]:
                errors.append("AM runner startup-smoke-test failed")

    ready = len(errors) == 0
    verdict = PHASE565_VERDICT if ready else "phase565_paper_trade_readiness_failed"

    mandatory_answers = {
        "1_run_paper_trade_bat_ok": ready,
        "2_actual_exit_unchanged": checks.get("1_actual_exit_unchanged", False),
        "3_t2_t3_shadow_enabled": (
            checks.get("2_t2_enabled", False) and checks.get("2_t3_enabled", False)
        ),
        "4_summary_discord_ok": (
            checks.get("3_t2_t3_shadow_summary", False)
            and checks.get("5_discord_exit_monitor", False)
        ),
        "5_zero_trade_ok": checks.get("4_zero_trade_safe", False),
        "6_rollback_possible": checks.get("9_rollback", False),
        "7_preflight_ok": checks.get("7_preflight", False),
        "8_smoke_test_ok": checks.get("8_smoke_test", False),
        "9_dry_run_ok": checks.get("9_am_runner_dry_run", False),
        "10_fixes_needed": fixes_needed if fixes_needed else None,
    }

    out = {
        "verdict": verdict,
        "ready": ready,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config_path": str(cfg_path),
        "structural_exit_policy": config.structural_exit_policy,
        "checks": checks,
        "mandatory_answers": mandatory_answers,
        "command_results": cmd_results,
        "smoke_report": smoke_report.to_dict(),
        "preflight_verdict": preflight_report.verdict,
        "sample_exit_shadow_summary": sample,
        "errors": errors,
        "run_paper_trade_bat": str(BAT_PATH),
        "fixes_needed": fixes_needed,
    }

    reports = KABU / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / "phase565_report.json"
    report_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    out["report_path"] = str(report_path)

    docs = KABU / "docs" / "operations" / "phase565_paper_trade_readiness_after_exit_shadow_monitor.md"
    ma = mandatory_answers
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(
        "\n".join(
            [
                "# Phase565 — Paper Trade Readiness after EXIT Shadow Monitor",
                "",
                f"**Verdict:** `{verdict}`",
                "",
                "## Mandatory answers",
                "",
                f"1. run_paper_trade.bat OK: {ma['1_run_paper_trade_bat_ok']}",
                f"2. actual EXIT unchanged: {ma['2_actual_exit_unchanged']}",
                f"3. T2/T3 shadow enabled: {ma['3_t2_t3_shadow_enabled']}",
                f"4. Summary/Discord OK: {ma['4_summary_discord_ok']}",
                f"5. zero-trade OK: {ma['5_zero_trade_ok']}",
                f"6. rollback possible: {ma['6_rollback_possible']}",
                f"7. preflight OK: {ma['7_preflight_ok']}",
                f"8. smoke test OK: {ma['8_smoke_test_ok']}",
                f"9. dry-run OK: {ma['9_dry_run_ok']}",
                f"10. fixes needed: {ma['10_fixes_needed']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"verdict={verdict}", flush=True)
    print(json.dumps(mandatory_answers, indent=2, ensure_ascii=False), flush=True)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
