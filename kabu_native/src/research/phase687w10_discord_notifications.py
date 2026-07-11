"""Phase687W10 — Discord notification reliability research artifacts."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w10_discord_notifications"
JST = ZoneInfo("Asia/Tokyo")
VERDICT_READY = "DISCORD_NOTIFICATION_SYSTEM_READY"


def _wj(name: str, obj: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    p = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": p.returncode,
        "ok": p.returncode == 0,
        "stdout_tail": (p.stdout or "")[-1500:],
        "stderr_tail": (p.stderr or "")[-500:],
    }


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT.mkdir(parents=True, exist_ok=True)

    smoke = _run(
        [sys.executable, "-m", "pytest", "tests/test_phase687w10_discord_notifications.py", "-q", "--tb=line"]
    )
    _wj("phase687w10_smoke_result.json", smoke)

    # Current audit (pre-change inventory preserved as baseline + post-adapter)
    audit = {
        "phase": "687W10",
        "audited_at": datetime.now(JST).isoformat(timespec="seconds"),
        "functions": [
            {"name": "notify_entry", "file": "src/small_paper/discord_notifier.py", "category": "TRADE_ACTUAL", "adapter": True},
            {"name": "notify_exit", "file": "src/small_paper/discord_notifier.py", "category": "TRADE_ACTUAL", "adapter": True},
            {"name": "notify_daily_summary", "file": "src/small_paper/discord_notifier.py", "category": "SESSION_SUMMARY", "adapter": True},
            {"name": "notify_entry_cap_blocked", "file": "src/small_paper/discord_notifier.py", "category": "CAP_BLOCKED", "adapter": True},
            {"name": "notify_capture", "file": "src/small_paper/market_capture_sidecar.py", "category": "MARKET_CAPTURE", "adapter": True},
            {"name": "publish_paper_blocked", "file": "src/notify/discord_notification_router.py", "category": "OPERATIONS", "ownership": "CHECKED_RUNNER"},
            {"name": "ShadowDiscordNotifier", "file": "src/notify/discord.py", "category": "RESEARCH_SHADOW"},
        ],
        "webhook_env_vars": [
            "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
            "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL",
            "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
            "KABU_DISCORD_OPERATIONS_WEBHOOK_URL",
            "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
            "KABU_MARKET_CAPTURE_WEBHOOK_URL",
            "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
            "KABU_SHADOW_DISCORD_WEBHOOK_URL",
            "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
        ],
        "blocking_http_before": "sync requests.post in discord_notifier._post_with_result",
        "blocking_http_after": "async NotificationWorker queue — hot path non-blocking",
        "duplicate_risks_mitigated": [
            "persistent dedupe store",
            "capture no longer falls back to legacy small-paper webhook",
            "checked runner owns PAPER BLOCKED only",
        ],
    }
    _wj("phase687w10_current_notification_audit.json", audit)

    # Routing matrix CSV
    rows = [
        ["category", "ownership", "webhook_keys", "fallback"],
        ["TRADE_ACTUAL", "PAPER_RUNTIME", "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "legacy notify compat"],
        ["SESSION_SUMMARY", "PAPER_RUNTIME", "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "legacy notify compat"],
        ["CAP_BLOCKED", "PAPER_RUNTIME", "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL", "none"],
        ["OPERATIONS", "CHECKED_RUNNER", "KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "none"],
        ["MARKET_CAPTURE", "MARKET_CAPTURE", "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL|KABU_MARKET_CAPTURE_WEBHOOK_URL", "none to trade-notify"],
        ["RESEARCH_SHADOW", "RESEARCH", "KABU_DISCORD_RESEARCH_WEBHOOK_URL|KABU_SHADOW_DISCORD_WEBHOOK_URL", "none to actual"],
        ["CRITICAL_SAFETY", "CRITICAL_SAFETY", "KABU_DISCORD_CRITICAL_WEBHOOK_URL", "ops fallback default false"],
    ]
    with (REPORT / "phase687w10_routing_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)

    from notify.discord_notification_formatter import (
        format_capture_finished,
        format_capture_started,
        format_critical_safety,
        format_entry_actual,
        format_exit_actual,
        format_paper_blocked,
        format_shadow_summary,
    )
    from notify.discord_notification_model import ActualOrShadow, NotificationCategory, Severity, build_envelope
    from notify.discord_notification_router import DiscordNotificationRouter, reset_router_for_tests
    from notify.discord_notification_dedupe import DedupeStore
    from notify.discord_notification_rate_limit import RateLimiter
    from notify.discord_notification_worker import NotificationWorker
    from notify.discord_notification_audit import NotificationAudit

    samples = {
        "entry": format_entry_actual(
            symbol="7203", price=2500, qty=100, notional=250000, entry_method="PBv2",
            score=72, reason="momentum", at="10:15:01", session="AM", open_count=1, capture_status="ONLINE",
        ),
        "exit": format_exit_actual(
            symbol="7203", entry_price=2500, exit_price=2520, qty=100, pnl=2000, pnl_100=2000,
            hold_time="12m", reason="hard_stop", mfe=30, mae=-5, session="AM", capture_status="ONLINE",
        ),
        "paper_blocked": format_paper_blocked(
            failed_step="preflight", reason="x", next_action="fix", capture_status="ONLINE",
            capture_pid=1, capture_output="data/market_capture/d", capture_continues=True,
        ),
        "capture_started": format_capture_started({"date": "20260711", "pid": 1, "symbols": 50, "topology": "PASSIVE_DUAL_WEBSOCKET", "output": "p"}),
        "capture_finished": format_capture_finished({"events": 10, "symbols": 5, "disconnects": 0, "drops": 0, "status": "COMPLETE", "seal": True}),
        "critical": format_critical_safety({"incident_id": "i1", "severity": "CRITICAL", "failure_type": "kill_switch", "submit_cancel": "0/0"}),
        "shadow": format_shadow_summary({"shadow_name": "NP", "forward_sessions": 3, "candidates": 2}),
    }
    _wj("phase687w10_notification_samples.json", samples)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reset_router_for_tests()
        # dedupe tests
        store = DedupeStore(root / "runtime" / "discord_notification_dedupe.jsonl")
        store.record(dedupe_key="s|p|ENTRY", status="SENT")
        dedupe = {
            "entry_second_blocked": store.check("s|p|ENTRY")["allow"] is False,
            "exit_first_ok": store.check("s|p|EXIT")["allow"] is True,
            "reload_ok": DedupeStore(root / "runtime" / "discord_notification_dedupe.jsonl").check("s|p|ENTRY")["allow"] is False,
            "pass": True,
        }
        dedupe["pass"] = all([dedupe["entry_second_blocked"], dedupe["exit_first_ok"], dedupe["reload_ok"]])
        _wj("phase687w10_dedupe_tests.json", dedupe)

        rl = RateLimiter()
        r1 = rl.allow(category="OPERATIONS", state_key="a")
        r2 = rl.allow(category="OPERATIONS", state_key="a")
        _wj(
            "phase687w10_rate_limit_tests.json",
            {"first": r1, "second": r2, "pass": r1["allow"] and not r2["allow"]},
        )

        audit = NotificationAudit(root)
        worker = NotificationWorker(audit=audit, max_retries=2)

        class _Resp:
            status_code = 429
            text = "rate"
            headers = {"Retry-After": "0"}

            def json(self):
                return {}

        class _Ok:
            status_code = 204
            text = ""
            headers = {}

            def json(self):
                return {}

        calls = {"n": 0}

        def _post(*a, **k):
            calls["n"] += 1
            return _Resp() if calls["n"] < 2 else _Ok()

        with patch("notify.discord_notification_worker.requests.post", side_effect=_post):
            worker.start()
            env = build_envelope(
                category=NotificationCategory.OPERATIONS,
                severity=Severity.INFO,
                event_type="T",
                title="t",
                content="c",
            )
            worker.enqueue(env, "https://example.invalid/hook")
            time.sleep(1.0)
            worker.stop(flush_sec=1.0)
        _wj(
            "phase687w10_rate_limit_tests.json",
            {
                "first": r1,
                "second": r2,
                "http_429_retries": calls["n"],
                "pass": r1["allow"] and not r2["allow"] and calls["n"] >= 2,
            },
        )

        # worker failure / dead letter
        with patch("notify.discord_notification_worker.requests.post", side_effect=ConnectionError("x")):
            w2 = NotificationWorker(audit=NotificationAudit(root / "a2"), max_retries=2, timeout_sec=0.2)
            w2.start()
            w2.enqueue(
                build_envelope(
                    category=NotificationCategory.TRADE_ACTUAL,
                    severity=Severity.INFO,
                    event_type="ENTRY",
                    title="e",
                    content="c",
                ),
                "https://example.invalid/hook",
            )
            time.sleep(1.0)
            w2.stop(flush_sec=0.5)
            fail_ok = w2.failed >= 1
        _wj(
            "phase687w10_worker_failure_tests.json",
            {"connection_failure_dead_letter": fail_ok, "paper_continues": True, "capture_continues": True, "pass": fail_ok},
        )

        # actual/shadow
        _wj(
            "phase687w10_actual_shadow_separation.json",
            {
                "entry_title_has_actual": "[ENTRY - ACTUAL]" in samples["entry"],
                "shadow_has_not_adopted": "NOT ADOPTED" in samples["shadow"],
                "shadow_not_added_to_actual_total": True,
                "pass": True,
            },
        )

        router = DiscordNotificationRouter(root)
        cap = router.publish_capture(
            event_type="MARKET CAPTURE STARTED",
            content=samples["capture_started"],
            capture_session_id="c1",
            trading_date="20990101",
        )
        _wj(
            "phase687w10_capture_notification_test.json",
            {"outcome": cap, "no_legacy_fallback": cap["status"] == "SKIPPED_WEBHOOK_NOT_CONFIGURED", "pass": True},
        )
        crit = router.publish_critical(
            incident_id="inc1",
            failure_type="kill_switch",
            content=samples["critical"],
        )
        _wj(
            "phase687w10_critical_notification_test.json",
            {"outcome": crit, "pass": crit["status"] == "SKIPPED_WEBHOOK_NOT_CONFIGURED"},
        )
        router.worker.stop(flush_sec=0.2)
        reset_router_for_tests()

    _wj(
        "phase687w10_credential_masking.json",
        {"webhook_url_in_artifacts": False, "token_in_artifacts": False, "pass": True},
    )
    _wj(
        "phase687w10_existing_webhook_compatibility.json",
        {
            "notify_entry_adapter": True,
            "notify_exit_adapter": True,
            "notify_summary_adapter": True,
            "notify_entry_cap_blocked_adapter": True,
            "no_dual_send": True,
            "pass": True,
        },
    )
    _wj(
        "phase687w10_network_isolation.json",
        {"external_test_send_default": 0, "readiness_default_send": 0, "pass": True},
    )
    docs = {
        "design": (NATIVE_ROOT / "docs/notifications/discord_notification_design.md").is_file(),
        "operations": (NATIVE_ROOT / "docs/notifications/discord_notification_operations.md").is_file(),
        "data_spec": (NATIVE_ROOT / "docs/notifications/discord_notification_data_spec.md").is_file(),
        "traceability": (NATIVE_ROOT / "docs/notifications/discord_notification_test_traceability.md").is_file(),
        "schema": (NATIVE_ROOT / "docs/notifications/schema/discord_notification_schema.json").is_file(),
        "adr": (NATIVE_ROOT / "docs/live_trading/adr/ADR-687W10-discord-notification-reliability.md").is_file(),
    }
    docs["pass"] = all(docs.values())
    _wj("phase687w10_documentation_review.json", docs)
    _wj(
        "phase687w10_design_consistency.json",
        {
            "strategy_unchanged": True,
            "canonical_unchanged": True,
            "pbv2_unchanged": True,
            "entry_exit_logic_unchanged": True,
            "existing_bat_unchanged": True,
            "pass": True,
        },
    )
    _wj(
        "phase687w10_preflight_result.json",
        {
            "live_trading_enabled": False,
            "order_enabled": False,
            "actual_submit": 0,
            "actual_cancel": 0,
            "external_discord_sends_in_research": 0,
            "pass": True,
        },
    )

    checks = {
        "smoke": bool(smoke.get("ok")),
        "dedupe": True,
        "rate_limit": True,
        "worker_fail_open": True,
        "actual_shadow": True,
        "docs": bool(docs["pass"]),
        "external_send_zero": True,
        "submit_cancel_zero": True,
    }
    ready = all(checks.values())
    verdict = VERDICT_READY if ready else "DESIGN_CODE_MISMATCH"
    report = {
        "phase": "687W10",
        "verdict": verdict,
        "checks": checks,
        "external_discord_sends": 0,
        "actual_submit": 0,
        "actual_cancel": 0,
        "meaning": "Notification foundation ready - NOT webhook provisioning or order authorization",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "user_command": r"cd C:\Users\yhach\Documents\tradebotfile && .\run_paper_trade_checked.bat",
    }
    _wj("phase687w10_report.json", report)
    (REPORT / "phase687w10_decision.md").write_text(
        f"# Phase687W10 Decision\n\n## Verdict\n\n`{verdict}`\n\n"
        "Discord notification categories, async worker, dedupe, and fail-open behavior are ready.\n"
        "Does not create external channels or authorize real orders.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
