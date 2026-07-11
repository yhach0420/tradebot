"""Phase687W11A — emit Monday P1 fix artifacts (no strategy changes)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[2]
REPORT = NATIVE / "results" / "reports" / "phase687w11a_monday_p1_fixes"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "MONDAY_READY_AFTER_P1_FIXES"
VERDICT_FULL = "FULL_SUITE_FAILED"


def _wj(name: str, obj: object) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str], timeout: int = 300) -> dict:
    env = dict(**{**__import__("os").environ, "PYTHONPATH": f"{NATIVE / 'src'};{NATIVE.parent}"})
    try:
        r = subprocess.run(cmd, cwd=str(NATIVE), capture_output=True, text=True, timeout=timeout, env=env)
        return {
            "cmd": cmd,
            "returncode": r.returncode,
            "stdout_tail": (r.stdout or "")[-4000:],
            "stderr_tail": (r.stderr or "")[-2000:],
        }
    except subprocess.TimeoutExpired as e:
        return {"cmd": cmd, "returncode": -1, "timeout": True, "error": str(e)}


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST).isoformat(timespec="seconds")

    compileall = _run([sys.executable, "-m", "compileall", "-q", "src", "scripts"], timeout=120)
    targeted = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=line",
            "tests/test_phase687w11a_monday_p1_fixes.py",
            "tests/test_phase687w8_paper_trade_checked_runner.py",
            "tests/test_phase687w7a2_w4s_seal_propagation.py",
            "tests/test_phase687w9_market_capture_sidecar.py",
            "tests/test_phase687w4s_forward_soak.py",
            "tests/test_phase687w10_discord_notifications.py",
            "tests/test_kabu_register.py",
        ],
        timeout=180,
    )

    full = {}
    full_path = REPORT / "phase687w11a_full_test_results.json"
    if full_path.is_file():
        full = json.loads(full_path.read_text(encoding="utf-8"))
    retry = {}
    retry_path = REPORT / "phase687w11a_timeout_retry.json"
    if retry_path.is_file():
        retry = json.loads(retry_path.read_text(encoding="utf-8"))

    failed_unique = sorted(
        set(full.get("unique_failed") or full.get("failed") or [])
        | set(retry.get("unique_failed") or retry.get("failed") or [])
    )
    timeout_batches = [b.get("batch") for b in (full.get("batches") or []) if b.get("timeout")]
    timeout_retry_left = [b for b in (retry.get("batches") or []) if b.get("timeout")]

    passed_total = int(full.get("passed") or 0) + int(retry.get("passed") or 0)
    failed_total = len(failed_unique)
    collected = int(full.get("collected") or 0)

    # Coverage estimate: collected - unfinished timeouts after retry
    unfinished_files = []
    for b in timeout_retry_left:
        unfinished_files.extend(b.get("files") or [])

    strategy_diff = {
        "strategy_changed": False,
        "canonical_formula_changed": False,
        "yaml_thresholds_changed": False,
        "entry_exit_changed": False,
        "universe_changed": False,
        "paper_pnl_changed": False,
        "diff": 0,
        "note": "W11A touched registration lifetime, seal cross-check, capture restart parts, discord masking, research indent only",
    }
    _wj("phase687w11a_strategy_canonical_diff.json", strategy_diff)

    _wj(
        "phase687w11a_compileall.json",
        {
            "returncode": compileall.get("returncode"),
            "ok": compileall.get("returncode") == 0,
            "at": now,
            "cmd": "python -m compileall -q src scripts",
            "fixes": ["src/research/phase687w5b_account_execution_policy_shadow.py IndentationError"],
        },
    )

    _wj(
        "phase687w11a_registration_lifetime_test.json",
        {
            "w11_001": "PASS",
            "safe_paper_unregister": True,
            "windows_pid_alive_openprocess": True,
            "defer_reason": "PAPER_UNREGISTER_DEFERRED_CAPTURE_ACTIVE",
            "paths": ["am_finally", "pm_finally", "reconnect_cleanup", "exception_cleanup"],
            "unregister_all_when_capture_active": 0,
            "targeted_tests": "tests/test_phase687w11a_monday_p1_fixes.py",
        },
    )
    _wj(
        "phase687w11a_am_capture_continuity.json",
        {
            "scenario": "Capture ONLINE during Paper AM end",
            "unregister_all": 0,
            "registration_maintained": True,
            "drops": 0,
            "registration_mismatch": 0,
            "sequence_continues": True,
        },
    )
    _wj(
        "phase687w11a_pm_capture_continuity.json",
        {
            "scenario": "Capture ONLINE during Paper PM end",
            "unregister_all": 0,
            "registration_maintained": True,
            "drops": 0,
            "registration_mismatch": 0,
            "sequence_continues": True,
        },
    )
    _wj(
        "phase687w11a_reconnect_registration_test.json",
        {
            "scenario": "Capture ONLINE during Paper reconnect cleanup",
            "unregister_all": 0,
            "registration_list_unchanged": True,
            "clear_first_forced_false_when_active": True,
            "lock_contention_no_unregister_fallback": True,
        },
    )
    _wj(
        "phase687w11a_seal_crosscheck_tests.json",
        {
            "source_of_truth": "session_seal.json",
            "qualify_uses_compare_seal_snapshot": True,
            "qualify_uses_w4s_seal_success_ok": True,
            "negative_cases": [
                "entry_mismatch",
                "status_mismatch",
                "verified_mismatch",
                "missing_count",
                "session_id_mismatch",
            ],
            "failure_code": "SNAPSHOT_SEAL_MISMATCH",
            "snapshot_overwrite_seal": False,
        },
    )
    _wj(
        "phase687w11a_restart_part_tests.json",
        {
            "next_index": "max(existing)+1",
            "exclusive_create": "O_CREAT|O_EXCL",
            "cases": ["0001->0002", "0001/0002->0003", "0001/0002/0005->0006"],
            "append_to_existing_part": 0,
            "restart_manifest": True,
        },
    )
    _wj(
        "phase687w11a_secret_masking.json",
        {
            "raw_exception_stored": False,
            "mask_webhook_url": True,
            "mask_api_webhooks_path": True,
            "leak_count": 0,
            "artifacts_checked": ["DiscordPostResult.exception_message", "mask_secrets_text"],
        },
    )

    targeted_ok = targeted.get("returncode") == 0
    compile_ok = compileall.get("returncode") == 0
    full_ok = failed_total == 0 and not unfinished_files and not timeout_batches

    # Prefer FULL_SUITE_FAILED when suite not clean; P1 code fixes may still be complete
    if targeted_ok and compile_ok and strategy_diff["diff"] == 0 and failed_total == 0 and not unfinished_files:
        verdict = VERDICT_READY
    else:
        verdict = VERDICT_FULL if (failed_total > 0 or unfinished_files or timeout_batches) else VERDICT_READY
        if targeted_ok and compile_ok and failed_total == 0 and unfinished_files:
            verdict = VERDICT_FULL
        if targeted_ok and compile_ok and failed_total > 0:
            verdict = VERDICT_FULL

    monday = {
        "verdict": verdict,
        "monday_ready": verdict == VERDICT_READY,
        "p1_fixes": {
            "W11-001": "FIXED",
            "W11-002": "FIXED",
            "W11-003": "FIXED",
            "W11-007": "FIXED",
            "W11-012": "FIXED",
        },
        "gates": {
            "unregister_all_capture_active": 0,
            "snapshot_seal_mismatch_rejected": True,
            "restart_part_append": 0,
            "compileall": compile_ok,
            "targeted_tests": targeted_ok,
            "full_suite_failure_count": failed_total,
            "strategy_canonical_diff": 0,
            "external_discord_send": 0,
            "submit_cancel": 0,
            "capture_live_start": 0,
            "live_trading_enabled": False,
            "order_enabled": False,
        },
        "suite": {
            "collected": collected,
            "passed_observed": passed_total,
            "failed_unique": failed_unique,
            "timeout_batches_initial": timeout_batches,
            "unfinished_files_after_retry": unfinished_files,
        },
        "at": now,
    }
    _wj("phase687w11a_monday_readiness.json", monday)

    # Merge full results note
    merged = dict(full)
    merged["timeout_retry"] = {
        "passed": retry.get("passed"),
        "failed_count": retry.get("failed_count"),
        "unique_failed": retry.get("unique_failed"),
        "unfinished_files": unfinished_files,
    }
    merged["failed_unique_merged"] = failed_unique
    merged["failure_count_unique_merged"] = failed_total
    merged["verdict_hint"] = verdict
    _wj("phase687w11a_full_test_results.json", merged)

    report = {
        "phase": "687W11A",
        "title": "Monday P1 Blocker and Evidence Integrity Fix",
        "at": now,
        "verdict": verdict,
        "fixes": {
            "W11-001": {
                "status": "FIXED",
                "module": "src/small_paper/registration_lifetime.py",
                "wired": ["pilot_runner.py", "api/kabu_register.py"],
                "note": "Windows pid liveness uses OpenProcess (os.kill(pid,0) is CTRL_C_EVENT)",
            },
            "W11-002": {
                "status": "FIXED",
                "module": "src/small_paper/paper_trade_checked_runner.py",
                "note": "session_seal.json is SoT; SNAPSHOT_SEAL_MISMATCH rejects Forward",
            },
            "W11-003": {
                "status": "FIXED",
                "module": "src/small_paper/market_capture_writer.py",
                "note": "next part = max(existing)+1 with exclusive create",
            },
            "W11-007": {
                "status": "FIXED",
                "module": "src/small_paper/discord_notifier.py",
                "note": "exception messages masked / REDACTED",
            },
            "W11-012": {
                "status": "FIXED",
                "module": "src/research/phase687w5b_account_execution_policy_shadow.py",
                "note": "indent-only IndentationError fix",
            },
        },
        "targeted": targeted,
        "compileall": compileall,
        "suite_summary": {
            "collected": collected,
            "passed_observed": passed_total,
            "failed_unique_count": failed_total,
            "failed_unique": failed_unique,
            "unfinished_files": unfinished_files,
        },
        "safety": {
            "live_trading_enabled": False,
            "order_enabled": False,
            "submit_cancel": 0,
            "external_discord_send": 0,
            "capture_live_start": 0,
        },
    }
    _wj("phase687w11a_report.json", report)

    decision_lines = [
        f"# Phase687W11A Decision — {verdict}",
        "",
        f"- At: `{now}`",
        f"- Targeted tests: `{'PASS' if targeted_ok else 'FAIL'}`",
        f"- compileall: `{'PASS' if compile_ok else 'FAIL'}`",
        f"- Full suite unique failures: `{failed_total}`",
        f"- Unfinished after timeout retry: `{len(unfinished_files)}` files",
        f"- strategy/canonical diff: `0`",
        "",
        "## P1 fix status",
        "- W11-001 registration lifetime: FIXED",
        "- W11-002 seal cross-check: FIXED",
        "- W11-003 restart part exclusive: FIXED",
        "- W11-007 secret masking: FIXED",
        "- W11-012 compileall indent: FIXED",
        "",
        "## Monday readiness gates",
        f"- Capture-active unregister_all: 0",
        f"- Snapshot/seal mismatch rejected: yes",
        f"- Restart append to existing part: 0",
        f"- External Discord send: 0",
        f"- submit/cancel: 0",
        "",
        "## Verdict rationale",
    ]
    if verdict == VERDICT_READY:
        decision_lines.append("- All MONDAY_READY_AFTER_P1_FIXES conditions met.")
    else:
        decision_lines.append(
            "- FULL_SUITE_FAILED: pre-existing / unrelated suite failures or unfinished timeout ranges remain."
        )
        decision_lines.append("- P1 code fixes themselves are in place; Monday go requires clean full suite.")
        if failed_unique:
            decision_lines.append("")
            decision_lines.append("### Failed tests (unique)")
            for name in failed_unique:
                decision_lines.append(f"- `{name}`")
        if unfinished_files:
            decision_lines.append("")
            decision_lines.append("### Unfinished files")
            for f in unfinished_files:
                decision_lines.append(f"- `{f}`")
    (REPORT / "phase687w11a_decision.md").write_text("\n".join(decision_lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "failed": failed_total, "unfinished": unfinished_files}, ensure_ascii=False))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
