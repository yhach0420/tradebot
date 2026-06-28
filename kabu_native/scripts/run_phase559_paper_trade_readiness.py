#!/usr/bin/env python3
"""Phase559: paper trade readiness after Phase557/558 stop_low_mfe guard adoption."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
PHASE559_VERDICT = "phase559_paper_trade_readiness_after_phase557_ok"

MONITOR_FIELDS = [
    "stop_low_mfe_guard_reject_count",
    "stop_low_mfe_guard_missing_count",
    "stop_low_mfe_guard_blocked_winner",
    "stop_low_mfe_guard_blocked_big_winner",
    "stop_low_mfe_guard_net_shadow",
    "cluster_guard_reject_count",
    "cluster_guard_exception_count",
    "or_entry_count",
]

DISCORD_SUMMARY_KEYS = [
    "stop_low_mfe_guard_enabled",
    "stop_low_mfe_guard_reject_count",
    "stop_low_mfe_guard_missing_count",
    "stop_low_mfe_guard_net_shadow",
]


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
    parser = argparse.ArgumentParser(description="Phase559 paper trade readiness")
    parser.add_argument("--skip-am-dry-run", action="store_true", help="Skip AM runner dry-run (~5min)")
    parser.add_argument("--smoke-max-sec", type=float, default=30.0)
    args = parser.parse_args()

    from small_paper.config import load_pilot_config
    from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines
    from small_paper.live_pipeline_preflight import default_config_path, run_live_pipeline_preflight
    from small_paper.or_overlay_entry import build_or_overlay_state
    from small_paper.production_startup_smoke_test import run_production_startup_smoke_test
    from small_paper.stop_low_mfe_guard import (
        build_stop_low_mfe_guard_state,
        config_from_pilot,
        volume_acceleration_5m,
    )
    from storage.intraday_recorder import PushMinuteBarBuilder

    errors: list[str] = []
    checks: dict[str, bool] = {}
    cmd_results: list[dict[str, object]] = []

    cfg_path = default_config_path(REPO)
    if not cfg_path.is_absolute():
        cfg_path = REPO / cfg_path
    config = load_pilot_config(cfg_path)

    # 1 production YAML
    slm_cfg = config_from_pilot(config)
    checks["1_yaml_stop_low_mfe_enabled"] = slm_cfg.enabled
    checks["1_yaml_threshold_0_009"] = abs(slm_cfg.threshold - 0.009) < 1e-9
    if not slm_cfg.enabled:
        errors.append("stop_low_mfe_guard_enabled is false in production YAML")

    from datetime import datetime
    from zoneinfo import ZoneInfo

    # 2 live feature computable
    builder = PushMinuteBarBuilder()
    jst = ZoneInfo("Asia/Tokyo")
    base_ts = datetime(2026, 6, 24, 10, 15, tzinfo=jst)
    for i in range(12):
        builder.ingest_push_payload(
            {"CurrentPrice": 1000 + i, "TradingVolume": 1000 * (i + 1)},
            recorded_at=base_ts.replace(minute=9 + i),
        )
    vols = builder.snapshot_minute_volumes()
    accel = volume_acceleration_5m(vols)
    checks["2_volume_acceleration_live_computable"] = accel is not None
    if accel is None:
        errors.append("volume_acceleration_5m not computable from PushMinuteBarBuilder")

    # 3 missing pass / 5 pbv2 only
    checks["3_missing_policy_pass"] = slm_cfg.missing_policy == "pass"
    checks["5_pbv2_only"] = slm_cfg.pbv2_only
    if slm_cfg.missing_policy != "pass":
        errors.append(f"missing_policy expected pass, got {slm_cfg.missing_policy}")

    # 4 OR exempt
    guard = build_stop_low_mfe_guard_state(config)
    if guard is None:
        errors.append("build_stop_low_mfe_guard_state returned None")
    else:
        or_blocked = guard.check(
            {"symbol": "5074.T", "entry_type": "OR_OVERLAY", "volume_acceleration_5m": 0.5}
        ).blocked
        checks["4_or_exempt"] = not or_blocked
        if or_blocked:
            errors.append("OR overlay incorrectly blocked by stop_low_mfe guard")

    # runtime guards + CAP
    checks["or_overlay_enabled"] = bool(getattr(config, "or_overlay_enabled", False))
    checks["cluster_guard_enabled"] = bool(getattr(config, "entry_cluster_guard_enabled", False))
    checks["reentry_rsi_enabled"] = bool(getattr(config, "reentry_rsi_guard_enabled", False))
    checks["entry_quality_enabled"] = bool(getattr(config, "entry_quality_guard_enabled", False))
    cap_pbv2 = int(getattr(config, "cap_pbv2", 0) or 0)
    cap_or = int(getattr(config, "cap_or", 0) or 0)
    cap_total = int(getattr(config, "max_concurrent_positions", 0) or 0)
    checks["cap_split_4_1_5"] = cap_pbv2 == 4 and cap_or == 1 and cap_total == 5
    if not checks["cap_split_4_1_5"]:
        errors.append(f"CAP expected 4/1/5, got pbv2={cap_pbv2} or={cap_or} total={cap_total}")

    gate = config.make_exposure_gate(repo_root=REPO)
    if getattr(gate, "stop_low_mfe_guard", None) is None:
        errors.append("ExposureGate.stop_low_mfe_guard is None at production repo_root")

    # 6 summary / discord
    if guard is not None:
        summary = guard.summary_fields()
        missing_summary = [k for k in MONITOR_FIELDS[:5] if k not in summary]
        checks["6_summary_fields"] = not missing_summary
        if missing_summary:
            errors.append(f"summary missing SLM keys: {missing_summary}")
        discord_lines = format_research_shadow_daily_summary_lines(
            {
                "stop_low_mfe_guard_enabled": True,
                "stop_low_mfe_guard_reject_count": 0,
                "stop_low_mfe_guard_missing_count": 0,
                "stop_low_mfe_guard_net_shadow": 0,
            }
        )
        discord_ok = any("StopLowMFEGuard:" in line for line in discord_lines)
        checks["6_discord_slm_line"] = discord_ok
        if not discord_ok:
            errors.append("Discord summary missing StopLowMFEGuard line")

    # 7 rollback
    rollback = load_pilot_config(cfg_path)
    rollback.stop_low_mfe_guard_enabled = False
    rb_gate = rollback.make_exposure_gate(repo_root=REPO)
    checks["7_rollback"] = getattr(rb_gate, "stop_low_mfe_guard", None) is None
    if not checks["7_rollback"]:
        errors.append("rollback stop_low_mfe_guard_enabled=false still attaches guard")

    # external commands (same as run_paper_trade.bat)
    py = sys.executable
    phase557 = _run_cmd(
        "phase557_ready",
        [py, str(KABU / "scripts" / "run_phase557_stop_low_mfe_guard_ready.py"), "--skip-unit-tests", "--skip-overlap"],
        cwd=REPO,
    )
    cmd_results.append(phase557)
    if not phase557["ok"]:
        errors.append("run_phase557_stop_low_mfe_guard_ready failed")

    smoke = _run_cmd(
        "production_smoke_test",
        [
            py,
            str(KABU / "scripts" / "run_production_startup_smoke_test.py"),
            "--exit-policy-shadow",
            "trailing-mfe",
        ],
        cwd=REPO,
    )
    cmd_results.append(smoke)
    checks["8_smoke_fast"] = bool(smoke["ok"]) and float(smoke["elapsed_sec"]) <= args.smoke_max_sec
    if not smoke["ok"]:
        errors.append("run_production_startup_smoke_test failed")
    elif float(smoke["elapsed_sec"]) > args.smoke_max_sec:
        errors.append(f"smoke test too slow: {smoke['elapsed_sec']}s > {args.smoke_max_sec}s")

    preflight = _run_cmd(
        "live_pipeline_preflight",
        [py, str(KABU / "scripts" / "check_live_pipeline_preflight.py")],
        cwd=REPO,
    )
    cmd_results.append(preflight)
    if not preflight["ok"]:
        errors.append("check_live_pipeline_preflight failed")

    # duplicate in-process for report payload
    smoke_report = run_production_startup_smoke_test(repo_root=REPO)
    preflight_report = run_live_pipeline_preflight(config_path=cfg_path, repo_root=REPO)
    if not smoke_report.ready:
        errors.extend([f"smoke: {e}" for e in smoke_report.errors])
    if not preflight_report.ready:
        errors.extend([f"preflight: {e}" for e in preflight_report.errors])

    or_state = build_or_overlay_state(config)
    checks["or_overlay_build"] = or_state is not None

    # 10 AM runner
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
        checks["10_am_runner_dry_run"] = bool(am["ok"])
        if not am["ok"]:
            errors.append("AM runner dry-run failed")
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
        checks["10_am_runner_startup_smoke"] = bool(startup["ok"])
        if not startup["ok"]:
            errors.append("AM runner startup-smoke-test failed")

    ready = len(errors) == 0
    verdict = PHASE559_VERDICT if ready else "phase559_paper_trade_readiness_failed"
    out = {
        "verdict": verdict,
        "ready": ready,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config_path": str(cfg_path),
        "checks": checks,
        "monitor_fields": MONITOR_FIELDS,
        "discord_summary_keys": DISCORD_SUMMARY_KEYS,
        "command_results": cmd_results,
        "smoke_report": smoke_report.to_dict(),
        "preflight_verdict": preflight_report.verdict,
        "errors": errors,
        "run_paper_trade_bat": str(REPO / "run_paper_trade.bat"),
    }

    reports = KABU / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / "phase559_report.json"
    report_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    out["report_path"] = str(report_path)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
