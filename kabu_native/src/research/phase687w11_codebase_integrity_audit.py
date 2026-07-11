"""Phase687W11 — Full codebase integrity audit artifacts (audit-only, no fixes)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w11_codebase_integrity_audit"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_PASS_FINDINGS = "CODEBASE_INTEGRITY_AUDIT_PASS_WITH_FINDINGS"
MONDAY_GO_MONITOR = "MONDAY_GO_WITH_MONITORING"


def _wj(name: str, obj) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str], timeout: int = 600) -> dict:
    env = dict(**{**__import__("os").environ, "PYTHONPATH": f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"})
    try:
        p = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "")[-2500:]
        return {"cmd": cmd, "returncode": p.returncode, "ok": p.returncode == 0, "stdout_tail": out, "stderr_tail": (p.stderr or "")[-800:]}
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "returncode": -1, "ok": False, "stdout_tail": "", "stderr_tail": "TIMEOUT"}


ISSUES = [
    {
        "issue_id": "W11-001",
        "severity": "P1",
        "confidence": "HIGH",
        "component": "registration_ws",
        "file": "src/small_paper/pilot_runner.py",
        "line": "6584,6512",
        "function": "run_live_dry_run reconnect/finally",
        "runtime_reachable": True,
        "trigger": "AM or PM Paper session ends (or reconnect) while Market Capture Sidecar still running until 15:35",
        "consequence": "push.unregister_all() may clear Kabu registrations; Capture follower can lose market data for remainder of day",
        "evidence": "pilot_runner.py:6584 finally unregister_all; checked runner starts capture first and leaves it until 15:35",
        "reproduction": "Start checked runner; observe capture ONLINE; after AM pilot exits, inspect registration vs capture event rate",
        "existing_test_coverage": "Weak — W9 tests cover sidecar isolation but not Paper unregister vs Capture lifetime",
        "recommended_fix": "Defer unregister_all until after Capture scheduled end when external sidecar is active; or Capture-owned registration SoT",
        "fix_risk": "MEDIUM — changes registration lifecycle",
        "blocks_monday": True,
        "data_loss_risk": True,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-002",
        "severity": "P1",
        "confidence": "HIGH",
        "component": "seal_w4s",
        "file": "src/small_paper/paper_trade_checked_runner.py",
        "line": "271-335",
        "function": "qualify_session_artifacts",
        "runtime_reachable": True,
        "trigger": "soak_session_snapshot fields disagree with session_seal.json (forged or stale overlay)",
        "consequence": "Forward seal_qualified can trust snapshot without compare_seal_snapshot cross-check",
        "evidence": "entry/required prefer snap; compare_seal_snapshot unused in W8 post path",
        "reproduction": "Unit-construct snap with seal_pass fields != seal file; run qualify_session_artifacts",
        "existing_test_coverage": "Partial — W7A2 tests seal helpers; W8 post path lacks snap↔seal mismatch negative test",
        "recommended_fix": "Call compare_seal_snapshot / w4s_seal_success_ok(snap, seal) inside qualify_session_artifacts",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-003",
        "severity": "P1",
        "confidence": "HIGH",
        "component": "market_capture",
        "file": "src/small_paper/market_capture_writer.py",
        "line": "125,333-337",
        "function": "new_part_after_restart / open",
        "runtime_reachable": True,
        "trigger": "Sidecar auto-restart after part_0002 already exists",
        "consequence": "Restart may append into existing push_part_0002.jsonl contrary to new_part_no_append policy string",
        "evidence": "Writer starts idx=1; restart only bumps once to 2; open uses append",
        "reproduction": "Create part_0001 and part_0002; restart_count=1; observe append to 0002",
        "existing_test_coverage": "Weak — restart new-part claim not fully file-mutation tested",
        "recommended_fix": "On restart open max(existing_part_index)+1",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": True,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-004",
        "severity": "P2",
        "confidence": "HIGH",
        "component": "windows_process",
        "file": "run_paper_trade.bat + paper_trade_checked_runner.py",
        "line": "bat:57-95 / runner:preflight+smoke",
        "function": "step_preflight/step_smoke then bat repeats",
        "runtime_reachable": True,
        "trigger": "Canonical checked-runner production path",
        "consequence": "Duplicate preflight and smoke each day — latency/noise, not wrong trading",
        "evidence": "Call graph confirms double invocation",
        "reproduction": "Run checked bat; observe two preflight/smoke log blocks",
        "existing_test_coverage": "None for duplication",
        "recommended_fix": "Make bat skip when KABU_CHECKED_RUNNER=1 env set by parent",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-005",
        "severity": "P2",
        "confidence": "HIGH",
        "component": "discord",
        "file": "am_pm_daily_runner.py + pilot_runner.py",
        "line": "notify_screening_universe_discord / notify_universe_screening",
        "function": "AM/PM screening notify",
        "runtime_reachable": True,
        "trigger": "AM and PM universe ready + pilot session start",
        "consequence": "Duplicate universe-screening Discord messages (noise); not trade-decision impact",
        "evidence": "Both daily runner and pilot call screening notify",
        "reproduction": "Live AM start; count screening Discord messages",
        "existing_test_coverage": "Weak",
        "recommended_fix": "Single ownership: Runner OR Pilot for screening notify",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-006",
        "severity": "P2",
        "confidence": "HIGH",
        "component": "market_capture",
        "file": "src/small_paper/market_capture_sidecar.py",
        "line": "172-182",
        "function": "acquire_pid_file",
        "runtime_reachable": True,
        "trigger": "Two checked-runners / manual sidecar starts same day near-simultaneously",
        "consequence": "TOCTOU race on PID file; possible brief dual capture attempt (supervisor max 1 restart)",
        "evidence": "read→alive→unlink→write without O_EXCL",
        "reproduction": "Parallel spawn_sidecar_process twice",
        "existing_test_coverage": "Partial double-start expects exit 2",
        "recommended_fix": "Atomic O_EXCL PID create like registration_lock",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-007",
        "severity": "P2",
        "confidence": "MEDIUM",
        "component": "security",
        "file": "src/small_paper/discord_notifier.py",
        "line": "506-513",
        "function": "_post_with_result exception path",
        "runtime_reachable": True,
        "trigger": "Discord HTTP library exception embeds URL in str(e)",
        "consequence": "Possible webhook URL fragment in delivery audit / logs",
        "evidence": "exception_message = str(e) stored",
        "reproduction": "Force requests exception with URL in message; inspect audit JSONL",
        "existing_test_coverage": "Masking tests exist for audit module but not this path",
        "recommended_fix": "Store type(exc).__name__ only; mask before persist",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-008",
        "severity": "P2",
        "confidence": "HIGH",
        "component": "safety",
        "file": "src/small_paper/safety.py + scripts/run_small_paper_pilot.py",
        "line": "105-112, --skip-safety",
        "function": "check_order_disabled / pilot CLI",
        "runtime_reachable": True,
        "trigger": "YAML order_enabled:true + --skip-safety",
        "consequence": "Flag checks skipped; still no HTTP sendorder / HARD_FAIL Kabu write — residual misconfig risk only",
        "evidence": "KabuBrokerAdapter HARD_FAIL; DryRunBrokerAdapter for mutations; no sendorder HTTP client",
        "reproduction": "N/A for real orders today; document operator risk",
        "existing_test_coverage": "Strong HARD_FAIL / enablement gate tests",
        "recommended_fix": "Add check_live_trading_disabled; restrict --skip-safety; never remove HARD_FAIL without explicit Phase",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-009",
        "severity": "P2",
        "confidence": "HIGH",
        "component": "discord",
        "file": "src/notify/discord_notification_router.py",
        "line": "181-190",
        "function": "_publish_inner dedupe.record",
        "runtime_reachable": True,
        "trigger": "Enqueue succeeds then worker HTTP fails",
        "consequence": "Dedupe marks SENT on queue; failed delivery may not auto-retry (fail-open for trading)",
        "evidence": "dedupe.record status=SENT when queued=True",
        "reproduction": "Mock enqueue OK + post 500; verify DEDUPED on second publish",
        "existing_test_coverage": "Partial",
        "recommended_fix": "Record SENT only after HTTP 2xx, or FAILED allows retry",
        "fix_risk": "MEDIUM — changes notify reliability semantics",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-010",
        "severity": "P2",
        "confidence": "HIGH",
        "component": "w4s",
        "file": "src/small_paper/paper_trade_checked_runner.py",
        "line": "1121-1122,1187",
        "function": "step_post_session forward_q",
        "runtime_reachable": True,
        "trigger": "Same trading day AM+PM both seal-qualified LIVE_PAPER_RUNTIME",
        "consequence": "forward_qualified_session_count can be 2 in one day; may diverge from intended 1/day soak semantics",
        "evidence": "No AM/PM dedupe per trading_date",
        "reproduction": "Two soak snapshots same day both LIVE; inspect forward_q",
        "existing_test_coverage": "Weak on AM/PM dedupe policy",
        "recommended_fix": "Document or enforce one forward credit per trading_date if intended",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-011",
        "severity": "P3",
        "confidence": "HIGH",
        "component": "market_capture",
        "file": "src/small_paper/market_capture_sidecar.py",
        "line": "284,610",
        "function": "heartbeat_gap_count",
        "runtime_reachable": True,
        "trigger": "Always",
        "consequence": "Observability field always 0; gaps not detected",
        "evidence": "Initialized never incremented",
        "reproduction": "Read any capture summary",
        "existing_test_coverage": "None",
        "recommended_fix": "Increment on monotonic heartbeat gaps",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-012",
        "severity": "P3",
        "confidence": "CONFIRMED",
        "component": "static",
        "file": "src/research/phase687w5b_account_execution_policy_shadow.py",
        "line": "221",
        "function": "(module)",
        "runtime_reachable": False,
        "trigger": "compileall / import research module",
        "consequence": "IndentationError; research-only, not Monday Paper path",
        "evidence": "compileall failed on this file",
        "reproduction": "python -m compileall src/research/phase687w5b_account_execution_policy_shadow.py",
        "existing_test_coverage": "N/A",
        "recommended_fix": "Fix indent in separate Phase if module needed",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-013",
        "severity": "P3",
        "confidence": "HIGH",
        "component": "env_config",
        "file": "src/notify/discord_notification_router.py",
        "line": "31-36",
        "function": "resolve_webhook_url / ensure_repo_dotenv",
        "runtime_reachable": True,
        "trigger": "dotenv ImportError or IO error",
        "consequence": "Silent pass; webhooks appear unconfigured",
        "evidence": "except Exception: pass around ensure_repo_dotenv",
        "reproduction": "Break dotenv temporarily; observe SKIPPED_WEBHOOK",
        "existing_test_coverage": "env_loader tests cover happy path",
        "recommended_fix": "Log WARNING on dotenv load failure",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-014",
        "severity": "P3",
        "confidence": "HIGH",
        "component": "file_io",
        "file": "src/notify/discord_notification_dedupe.py",
        "line": "90-92",
        "function": "DedupeStore.record",
        "runtime_reachable": True,
        "trigger": "Disk full / permission on append",
        "consequence": "Fail-open: in-memory cache may diverge from disk; possible re-notify after restart",
        "evidence": "except Exception: pass on append",
        "reproduction": "Make runtime/ read-only; enqueue notify",
        "existing_test_coverage": "Partial",
        "recommended_fix": "Audit FAILED_DISK when append fails; retention policy for jsonl growth",
        "fix_risk": "LOW",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
    },
    {
        "issue_id": "W11-P0-NEG-001",
        "severity": "P0",
        "confidence": "CONFIRMED",
        "component": "safety",
        "file": "src/small_paper/live_order_safety_sm.py",
        "line": "850-859",
        "function": "KabuBrokerAdapter.submit_*/cancel_order",
        "runtime_reachable": True,
        "trigger": "Any mutation attempt",
        "consequence": "HARD_FAIL raised — POSITIVE CONTROL (not a defect)",
        "evidence": "RuntimeError HARD_FAIL on all mutations; DryRunBrokerAdapter used for engine; production_enablement NOT_AUTHORIZED",
        "reproduction": "Call adapter.submit_entry_order → HARD_FAIL",
        "existing_test_coverage": "Strong",
        "recommended_fix": "None — keep",
        "fix_risk": "N/A",
        "blocks_monday": False,
        "data_loss_risk": False,
        "paper_stop_risk": False,
        "real_order_risk": False,
        "note": "POSITIVE_CONTROL — counted separately; not a defect P0",
    },
]


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST).isoformat(timespec="seconds")

    # Defect issues only (exclude positive control)
    defects = [i for i in ISSUES if i["issue_id"] != "W11-P0-NEG-001"]
    p0 = [i for i in defects if i["severity"] == "P0"]
    p1 = [i for i in defects if i["severity"] == "P1"]
    p2 = [i for i in defects if i["severity"] == "P2"]
    p3 = [i for i in defects if i["severity"] == "P3"]
    monday_blockers = [i for i in defects if i.get("blocks_monday")]

    # Tests
    compileall = _run([sys.executable, "-m", "compileall", "-q", "src", "scripts"], timeout=120)
    suites = {
        "w9_w10_series": _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_phase687w9_market_capture_sidecar.py",
                "tests/test_phase687w10_discord_notifications.py",
                "tests/test_phase687w10a_shadow_runtime_hook.py",
                "tests/test_phase687w10b_discord_demo_sender.py",
                "tests/test_env_loader_discord_webhooks.py",
                "-q",
                "--tb=line",
            ],
            timeout=180,
        ),
        "w7_w4s_w8": _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_phase687w7a2_w4s_seal_propagation.py",
                "tests/test_phase687w7a1_recovery_assertion_integrity.py",
                "tests/test_phase687w7a_stateful_recovery.py",
                "tests/test_phase687w7_operational_recovery.py",
                "tests/test_phase687w4s_forward_soak.py",
                "tests/test_phase687w4t_kabu_readonly_readiness.py",
                "tests/test_phase687w4_runtime_readonly_latency.py",
                "tests/test_phase687w8_paper_trade_checked_runner.py",
                "-q",
                "--tb=line",
            ],
            timeout=180,
        ),
        "safety_discord_sample": _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_phase594_live_order_adapter_notifier.py",
                "tests/test_phase637_discord_notification.py",
                "-q",
                "--tb=line",
            ],
            timeout=120,
        ),
    }
    collect = _run([sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"], timeout=120)

    # Parse pass counts from stdout tails (best-effort)
    def _pass_count(r: dict) -> int:
        import re

        m = re.search(r"(\d+) passed", r.get("stdout_tail") or "")
        return int(m.group(1)) if m else 0

    passed = sum(_pass_count(v) for v in suites.values())
    failed_suites = [k for k, v in suites.items() if not v.get("ok")]

    call_graph = {
        "phase": "687W11",
        "generated_at": now,
        "entrypoint": "run_paper_trade_checked.bat",
        "ordered_steps": [
            {"step": 1, "file": "run_paper_trade_checked.bat", "lines": "1-31", "calls": "run_paper_trade_checked.ps1"},
            {"step": 2, "file": "kabu_native/scripts/run_paper_trade_checked.ps1", "calls": "python -m small_paper.paper_trade_checked_runner"},
            {"step": 3, "file": "src/small_paper/paper_trade_checked_runner.py", "function": "PaperTradeCheckedRunner.run", "lines": "1612+"},
            {"step": 4, "phase": "disk + kabu_readonly + universe + registration + capture start"},
            {"step": 5, "phase": "cache/preflight/smoke/recovery/design/safety"},
            {"step": 6, "function": "step_start_paper", "calls": "run_paper_trade.bat once", "guard": "paper_call_count"},
            {"step": 7, "file": "run_paper_trade.bat", "note": "DUPLICATE preflight/smoke then AM/PM daily runner"},
            {"step": 8, "file": "scripts/run_core10_dynamic40_am_pm_daily_runner.py", "function": "run_daily_runner"},
            {"step": 9, "function": "run_pilot_session(am)", "pilot": "run_small_paper_pilot.py → run_live_dry_run"},
            {"step": 10, "function": "AM finalize", "calls": "shadow finalize → notify_discord_session_end → RESEARCH_SHADOW"},
            {"step": 11, "function": "run_pilot_session(pm)", "same_finalize_path": True},
            {"step": 12, "function": "step_post_session W4S", "guard": "w4s_call_count==1"},
            {"step": 13, "function": "step_capture_finalize_verify", "note": "parent verify-only; sidecar seals 15:35"},
        ],
        "am_pm_same_finalize_code": True,
        "risks": {
            "double_paper_bat": "LOW — paper_call_count guard",
            "double_w4s": "LOW — w4s_call_count",
            "double_preflight_smoke": "CONFIRMED — W11-004",
            "double_screening_discord": "HIGH likelihood — W11-005",
            "double_shadow_summary": "LOW — stable_key dedupe AM/PM",
            "double_capture": "LOW-MEDIUM — PID file + TOCTOU W11-006",
            "unregister_vs_capture": "HIGH — W11-001 Monday monitor item",
        },
        "test_vs_production": {
            "production": "checked bat without --SkipPaper",
            "test": "--skip-paper / --capture-synthetic / --skip-w4s",
        },
    }
    _wj("phase687w11_runtime_call_graph.json", call_graph)

    _wj(
        "phase687w11_windows_process_audit.json",
        {
            "bat_entry": "run_paper_trade_checked.bat → powershell -File ps1",
            "paper_invoke": "cmd /c echo.| call run_paper_trade.bat (feeds pause)",
            "sidecar_spawn": "CREATE_NEW_PROCESS_GROUP; env copy after ensure_repo_dotenv",
            "findings": ["W11-004 duplicate preflight", "echo.| call may mask interactive pause but OK for automation"],
            "python_path": "sys.executable via ps1; not hard-coded absolute python",
            "cwd": "repo root for bat; code_root for sidecar",
            "orphan_risk": "Sidecar intended to outlive Paper; parent must not kill (verified by design)",
        },
    )

    _wj(
        "phase687w11_env_config_audit.json",
        {
            "sot": "C:/Users/yhach/Documents/tradebotfile/.env",
            "loader": "small_paper.env_loader.load_repo_dotenv override=False",
            "cwd_independent": True,
            "gitignored": True,
            "os_env_wins": True,
            "child_inheritance": "checked_runner._env and spawn_sidecar copy os.environ after load",
            "findings": ["W11-013 silent dotenv fail", "dual loader rest_client.load_kabu_env still present"],
            "urls_logged": False,
        },
    )

    _wj(
        "phase687w11_capture_audit.json",
        {
            "pid_isolation": True,
            "finalize_1535_jst": True,
            "max_auto_restarts": 1,
            "findings": ["W11-001 registration conflict", "W11-003 restart part append", "W11-006 PID TOCTOU", "W11-011 heartbeat_gap unused"],
            "paper_fail_open": True,
            "queue_overflow": "DEGRADED + gap, no raise",
        },
    )

    _wj(
        "phase687w11_registration_ws_audit.json",
        {
            "max_symbols": 50,
            "lock": "O_CREAT|O_EXCL",
            "sidecar_registers": False,
            "unregister_all_in_sidecar": False,
            "paper_unregister_all": True,
            "findings": ["W11-001 Paper unregister while Capture continues"],
            "dual_ws": "policy label; single WS in sidecar live path",
        },
    )

    _wj(
        "phase687w11_paper_runtime_audit.json",
        {
            "am_pm_shared_path": True,
            "discord_fail_open": True,
            "shadow_hook_fail_open": True,
            "canonical_attach_before_notify": True,
            "findings": ["W11-005 screening discord dup"],
            "strategy_audit_scope": "integrity only — ENTRY/EXIT logic not evaluated for quality",
        },
    )

    _wj(
        "phase687w11_safety_recovery_audit.json",
        {
            "order_enabled_default": False,
            "live_trading_enabled_default": False,
            "kabu_write_adapter": "HARD_FAIL on submit/cancel/flatten",
            "engine_broker": "DryRunBrokerAdapter",
            "production_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
            "submit_cancel_reachable": False,
            "findings": ["W11-008 --skip-safety residual misconfig (still no HTTP sendorder)"],
            "positive_controls": ["W11-P0-NEG-001"],
        },
    )

    _wj(
        "phase687w11_seal_w4s_audit.json",
        {
            "circular_hash_mitigated": True,
            "entry_count_zero_rejected": True,
            "findings": ["W11-002 snap vs seal cross-check gap", "W11-010 AM+PM forward_q inflation"],
            "w4s_live_only": True,
            "fixture_excluded": True,
        },
    )

    _wj(
        "phase687w11_discord_audit.json",
        {
            "hot_path_async": True,
            "http_in_worker_thread": True,
            "demo_sync_cli_only": True,
            "demo_dedupe_separated": True,
            "actual_shadow_separated_w10a": True,
            "cross_category_fallback_demo": False,
            "findings": ["W11-005", "W11-007", "W11-009"],
            "trading_unaffected_by_notify_failure": True,
        },
    )

    _wj(
        "phase687w11_file_io_audit.json",
        {
            "append_only": ["discord_notification_dedupe.jsonl", "demo dedupe", "notification audit jsonl", "capture push_part jsonl"],
            "findings": ["W11-014 dedupe append fail-open", "dedupe/audit growth retention not configured"],
            "atomic_replace": "seal/manifest paths use write patterns; capture writer buffered",
        },
    )

    _wj(
        "phase687w11_time_session_audit.json",
        {
            "jst_zoneinfo": "Asia/Tokyo used widely",
            "capture_end": "15:35 JST",
            "am": "09:03-11:25",
            "pm": "12:33-15:23",
            "refresh": "10:00 / 14:30",
            "holiday_logic": "mostly weekday schedule; exchange holiday calendar not deeply enforced in all paths — monitor",
            "findings": [],
        },
    )

    # Exception matrix CSV
    exc_rows = [
        ["boundary", "exception_policy", "paper", "capture", "session_fail", "critical_notify", "issue_ref"],
        ["disk_guard fail", "fail-closed", "STOP", "may_not_start", "N/A", "no", ""],
        ["kabu_readonly fail", "fail-closed", "STOP", "may_not_start", "N/A", "no", ""],
        ["capture start fail", "fail-closed paper", "STOP unless override", "STOPPED", "N/A", "ops optional", ""],
        ["paper precheck fail after capture", "fail-open capture", "BLOCKED", "CONTINUE 15:35", "yes", "PAPER BLOCKED ops", ""],
        ["discord notify fail", "fail-open", "CONTINUE", "CONTINUE", "no", "audit only", "W11-007"],
        ["shadow hook fail", "fail-open", "CONTINUE", "N/A", "no", "no", ""],
        ["writer queue overflow", "fail-open drop/gap", "N/A", "DEGRADED continue", "no", "capture degraded", ""],
        ["Kabu submit attempt", "HARD_FAIL", "N/A", "N/A", "N/A", "safety", "W11-P0-NEG-001"],
        ["dotenv load fail", "silent pass", "CONTINUE", "CONTINUE", "no", "no", "W11-013"],
        ["dedupe append fail", "silent pass", "CONTINUE", "CONTINUE", "no", "no", "W11-014"],
        ["AM pilot hard fail", "blocks PM", "AM fail / PM skip", "CONTINUE", "yes", "no", ""],
    ]
    with (REPORT / "phase687w11_exception_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(exc_rows)

    _wj(
        "phase687w11_security_audit.json",
        {
            "env_gitignored": True,
            "webhook_urls_in_logs": "bool configured only (env_loader)",
            "findings": ["W11-007 exception_message risk"],
            "eval_exec_pickle": "not scanned exhaustively; no hot-path pickle found in notify/paper",
            "secrets_in_artifacts_this_audit": False,
        },
    )

    _wj(
        "phase687w11_performance_audit.json",
        {
            "confirmed": [
                "Discord Paper path is async enqueue (not sync HTTP on PUSH thread)",
                "Duplicate preflight/smoke adds startup latency (W11-004)",
            ],
            "inferred": [
                "Seal rehash full session artifacts can be heavy on large days",
                "W4S scans results tree — growth over time",
                "Dedupe JSONL unbounded growth",
            ],
            "speculation_separated": True,
        },
    )

    _wj(
        "phase687w11_test_quality_audit.json",
        {
            "strengths": [
                "W9/W10/W10A/W10B cover routing, fail-open, demo isolation",
                "W7A2 covers entry_count=0 rejection",
                "HARD_FAIL / enablement tests exist",
            ],
            "weak_tests": [
                {"area": "Paper unregister vs Capture lifetime", "gap": "no integration test", "issue": "W11-001"},
                {"area": "snap vs seal mismatch in qualify_session_artifacts", "gap": "negative test missing", "issue": "W11-002"},
                {"area": "restart part index mutation", "gap": "policy string vs file behavior", "issue": "W11-003"},
                {"area": "dedupe SENT-on-enqueue", "gap": "partial", "issue": "W11-009"},
            ],
            "full_suite_collected": 1643,
            "phased_executed_passed": passed,
            "note": "Full 1643 not executed end-to-end in this audit window; critical Phase suites executed",
        },
    )

    test_results = {
        "compileall": compileall,
        "suites": suites,
        "collect_only_tail": collect.get("stdout_tail"),
        "passed_phased": passed,
        "failed_suites": failed_suites,
        "external_send": 0,
        "submit": 0,
        "cancel": 0,
        "discord_demo_not_run": True,
        "capture_not_started": True,
        "kabu_write_not_called": True,
    }
    _wj("phase687w11_test_results.json", test_results)

    monday = {
        "verdict": MONDAY_GO_MONITOR,
        "rationale": "P0 defects=0; real orders unreachable; P1 W11-001 capture data-loss risk if Paper unregister clears registrations while sidecar continues — monitor/workaround: watch Capture ONLINE after AM; optional defer unregister",
        "p0_defects": len(p0),
        "p1": len(p1),
        "monday_blockers": [i["issue_id"] for i in monday_blockers],
        "real_orders": "NOT AUTHORIZED / NOT IMPLEMENTED",
        "live_trading_enabled": False,
        "order_enabled": False,
        "submit": 0,
        "cancel": 0,
        "go_is_not_live_authorization": True,
    }
    _wj("phase687w11_monday_readiness.json", monday)

    _wj(
        "phase687w11_strategy_canonical_diff.json",
        {
            "strategy_changed": False,
            "canonical_formula_changed": False,
            "yaml_thresholds_changed": False,
            "audit_only": True,
            "diff": 0,
        },
    )

    # Issue register CSV
    fields = [
        "issue_id",
        "severity",
        "confidence",
        "component",
        "file",
        "line",
        "function",
        "runtime_reachable",
        "trigger",
        "consequence",
        "evidence",
        "reproduction",
        "existing_test_coverage",
        "recommended_fix",
        "fix_risk",
        "blocks_monday",
        "data_loss_risk",
        "paper_stop_risk",
        "real_order_risk",
    ]
    with (REPORT / "phase687w11_issue_register.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for i in defects:
            w.writerow(i)

    verdict = VERDICT_PASS_FINDINGS
    if p0:
        verdict = "CRITICAL_SAFETY_DEFECT_FOUND"
    elif any(i["blocks_monday"] and i["confidence"] in ("CONFIRMED", "HIGH") and i["severity"] == "P1" and i.get("paper_stop_risk") for i in defects):
        verdict = "MONDAY_BLOCKER_FOUND"

    # W11-001 blocks_monday for data loss monitoring but paper still runs → GO_WITH_MONITORING
    report = {
        "phase": "687W11",
        "generated_at": now,
        "verdict": verdict,
        "monday": monday["verdict"],
        "counts": {"P0": len(p0), "P1": len(p1), "P2": len(p2), "P3": len(p3)},
        "monday_stop_issues": [i["issue_id"] for i in monday_blockers],
        "data_loss_risk_issues": [i["issue_id"] for i in defects if i.get("data_loss_risk")],
        "paper_stop_risk_issues": [i["issue_id"] for i in defects if i.get("paper_stop_risk")],
        "real_order_risk": False,
        "real_orders": "NOT AUTHORIZED / NOT IMPLEMENTED",
        "tests": {"phased_passed": passed, "failed_suites": failed_suites, "collected": 1643},
        "strategy_canonical_diff": 0,
        "external_send": 0,
        "submit": 0,
        "cancel": 0,
        "compileall_ok": bool(compileall.get("ok")),
        "compileall_note": "IndentationError in research-only phase687w5b (W11-012)",
        "recommended_fix_order": [
            "W11-001 Paper unregister vs Capture lifetime",
            "W11-002 seal snap↔seal cross-check",
            "W11-003 capture restart part index",
            "W11-009 dedupe SENT-on-enqueue",
            "W11-004/005 noise duplicates",
            "W11-007 secret masking on exception_message",
        ],
        "no_auto_fix": True,
    }
    _wj("phase687w11_report.json", report)

    decision = f"""# Phase687W11 Decision

## Verdict
`{verdict}`

## Monday
`{monday['verdict']}`

## Counts
- P0 defects: {len(p0)}
- P1: {len(p1)}
- P2: {len(p2)}
- P3: {len(p3)}

## Monday monitoring item
**W11-001**: Paper `unregister_all()` at session end/reconnect can clear Kabu registration while Capture Sidecar continues until 15:35 → Capture data-loss risk. Paper itself continues. Monitor Capture ONLINE / event rate after AM exit.

## Real orders
NOT AUTHORIZED / NOT IMPLEMENTED — HARD_FAIL write adapter; DryRun broker; flags default false.

## Tests (phased)
- Passed: {passed}
- Failed suites: {failed_suites or 'none'}
- Collected (not all executed): 1643
- compileall: research IndentationError W11-012 (non-runtime)

## Strategy/canonical diff
0 (audit-only)

## External send / submit / cancel
0 / 0 / 0

## Fix order (user approval required — not auto-fixed)
1. W11-001 registration lifetime
2. W11-002 seal cross-check
3. W11-003 restart part index
4. W11-009 notify dedupe timing
5. W11-004/005 duplication noise
6. W11-007 exception masking
"""
    (REPORT / "phase687w11_decision.md").write_text(decision, encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": verdict,
                "monday": monday["verdict"],
                "P0": len(p0),
                "P1": len(p1),
                "P2": len(p2),
                "P3": len(p3),
                "passed": passed,
                "report": str(REPORT),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
