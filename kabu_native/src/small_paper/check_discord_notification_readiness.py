"""Phase687W10 — Discord notification readiness CLI (default: no external send)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

NATIVE_ROOT = Path(__file__).resolve().parents[2]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Discord notification readiness (Phase687W10/W10B)")
    parser.add_argument("--native-root", type=str, default=str(NATIVE_ROOT))
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="Explicitly send ONE test notification (requires configured webhook)",
    )
    parser.add_argument(
        "--send-demo-all",
        action="store_true",
        help="Explicitly send all 17 DEMO notifications to configured category webhooks (Phase687W10B)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.send_test and args.send_demo_all:
        print("ERROR: --send-test and --send-demo-all are mutually exclusive", file=sys.stderr)
        return 2

    # Repo-root .env first (cwd-independent). OS env wins. Never print webhook URLs.
    from small_paper.env_loader import ensure_repo_dotenv, log_webhook_configured

    env_status = ensure_repo_dotenv()
    log_webhook_configured(env_status)

    from notify.discord_notification_model import (
        ActualOrShadow,
        NotificationCategory,
        Severity,
        build_envelope,
    )
    from notify.discord_notification_router import get_router

    root = Path(args.native_root)
    router = get_router(root)
    report = router.readiness()
    report["env"] = env_status.as_public_dict()
    report["external_test_send"] = 0
    report["external_demo_send"] = 0
    report["live_trading_enabled"] = False
    report["order_enabled"] = False
    report["submit"] = 0
    report["cancel"] = 0

    if args.send_demo_all:
        from notify.discord_demo_sender import format_console_result, run_discord_demo_all

        try:
            demo = run_discord_demo_all(root)
        except Exception as exc:
            print(f"ERROR: demo sender failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        report["external_demo_send"] = int(demo.counts()["sent"])
        report["demo"] = demo.to_dict()
        # Stop readiness worker (demo uses its own sync HTTP; do not leave orphan worker)
        try:
            router.worker.stop(flush_sec=0.5)
        except Exception:
            pass
        print(format_console_result(demo))
        print()
        print(json.dumps({"readiness": report.get("notification_ready"), "demo": demo.to_dict()}, ensure_ascii=False, indent=2))
        return int(demo.exit_code)

    if args.send_test:
        env = build_envelope(
            category=NotificationCategory.OPERATIONS,
            severity=Severity.INFO,
            event_type="READINESS_TEST",
            title="[DISCORD READINESS TEST]",
            content="TradeBot Discord readiness test (Phase687W10). Real orders disabled.",
            dedupe_key=f"ops|readiness_test|{report.get('checked_at', '')}",
            actual_or_shadow=ActualOrShadow.OPERATIONS,
            source_module="check_discord_notification_readiness",
            ownership="CHECKED_RUNNER",
        )
        outcome = router.publish(env)
        report["external_test_send"] = 1 if outcome.get("queued") or outcome.get("status") == "SENT" else 0
        report["test_outcome"] = outcome
        router.worker.stop(flush_sec=3.0)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("notification_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
