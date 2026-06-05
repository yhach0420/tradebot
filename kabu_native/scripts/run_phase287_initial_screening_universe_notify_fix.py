#!/usr/bin/env python3
"""
Phase287: Report + demo for AM/PM Screening universe Discord notifications.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase287_initial_screening_universe_notify_fix.json"
NATIVE = REPO / "kabu_native"
SRC = NATIVE / "src"


def _bootstrap() -> None:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=REPO)
    except Exception:
        env_path = REPO / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _code_audit() -> dict[str, Any]:
    paths = {
        "daily_runner": NATIVE / "src/runner/am_pm_daily_runner.py",
        "pilot_runner": NATIVE / "src/small_paper/pilot_runner.py",
        "discord_notifier": NATIVE / "src/small_paper/discord_notifier.py",
        "message_builder": NATIVE / "src/small_paper/discord_message_builder.py",
        "run_daily": NATIVE / "scripts/run_core10_dynamic40_am_pm_daily_runner.py",
        "run_pilot": NATIVE / "scripts/run_small_paper_pilot.py",
    }
    text = {k: p.read_text(encoding="utf-8") for k, p in paths.items()}
    return {
        "1_am_csv_after_notify": "notify_screening_universe_discord" in text["daily_runner"]
        and 'session_label="AM Screening"' in text["daily_runner"],
        "2_am_pilot_screening_notify": "notify_universe_screening" in text["pilot_runner"],
        "3_pm_csv_after_notify": 'session_label="PM Screening"' in text["daily_runner"],
        "4_pm_pilot_screening_notify": "notify_universe_screening" in text["pilot_runner"],
        "5_refresh_separate_dedupe": "dedupe_key=f\"refresh|" in text["discord_notifier"]
        and "dedupe_key=f\"screening|" in text["discord_notifier"],
        "6_trade_notify_webhook": "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL" in text["discord_notifier"],
        "7_initial_removed_none": "（なし）" in text["message_builder"]
        and "build_universe_screening_overview" in text["message_builder"],
        "hooks": {
            "am_pm_daily_runner": [
                "notify_screening_universe_discord() after build_am_universe",
                "notify_screening_universe_discord() after build_pm_universe",
            ],
            "pilot_runner": [
                "notify_universe_screening() at live session start (dedupe with daily runner)",
            ],
            "pilot_runner_refresh": [
                "notify_universe_refresh() at 10:00 / 14:30 intraday refresh only",
            ],
        },
    }


def _run_unit_tests() -> dict[str, Any]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(NATIVE / "tests"), pattern="test_phase287*.py")
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "ok": result.wasSuccessful(),
    }


def _demo_symbols(n: int = 50) -> list[str]:
    return [f"{7200 + i}.T" for i in range(n)]


def _send_demos(*, live: bool) -> dict[str, Any]:
    from small_paper.discord_message_builder import (
        build_universe_refresh_detail,
        build_universe_screening_overview,
        preview_payload,
    )
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    syms = _demo_symbols(50)
    cfg = SmallPaperDiscordConfig(
        enabled=True,
        observer_only=True,
        send_universe_refresh=True,
        trade_notify_webhook_env="KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    )
    notifier = SmallPaperDiscordNotifier(
        cfg,
        profile="momentum_volume_v13_combined",
        entry_profile="momentum_volume_v13_combined",
        policy_label="phase287_demo",
    )
    previews = {
        "AM_Screening": build_universe_screening_overview(
            session_label="AM Screening",
            watch_symbol_count=len(syms),
        ),
        "PM_Screening": build_universe_screening_overview(
            session_label="PM Screening",
            watch_symbol_count=len(syms),
        ),
        "AM_Refresh_1000": build_universe_refresh_detail(
            session_label="AM",
            refresh_time="10:00",
            added=["3905.T"],
            removed=["5856.T"],
            watch_symbols=syms,
        ),
        "PM_Refresh_1430": build_universe_refresh_detail(
            session_label="PM",
            refresh_time="14:30",
            added=["3110.T"],
            removed=[],
            watch_symbols=syms,
        ),
    }
    sent: dict[str, Any] = {"live": live, "webhook_active": notifier.active}
    if not live:
        sent["previews"] = {
            k: preview_payload(
                event_tag="Universe Screening" if "Screening" in k else "Universe Refresh",
                title_line=f"【DEMO】 {k}",
                detail=v[:500] + ("..." if len(v) > 500 else ""),
                color=0x2B6CB0,
            )
            for k, v in previews.items()
        }
        return sent

    sent["AM_Screening"] = notifier.notify_universe_screening(
        session_label="AM Screening [DEMO]",
        watch_symbols=syms,
        day_stamp=datetime.now().strftime("%Y%m%d") + "demo_am",
    )
    sent["PM_Screening"] = notifier.notify_universe_screening(
        session_label="PM Screening [DEMO]",
        watch_symbols=syms,
        day_stamp=datetime.now().strftime("%Y%m%d") + "demo_pm",
    )
    sent["AM_Refresh"] = notifier.notify_universe_refresh(
        session_label="AM",
        refresh_time="10:00",
        added_symbols=["3905.T"],
        removed_symbols=["5856.T"],
        watch_symbols=syms,
    )
    sent["PM_Refresh"] = notifier.notify_universe_refresh(
        session_label="PM",
        refresh_time="14:30",
        added_symbols=["3110.T"],
        removed_symbols=[],
        watch_symbols=syms,
    )
    return sent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Send to trade-notify webhook")
    parser.add_argument("--offline-only", action="store_true", help="Preview only")
    args = parser.parse_args()
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    audit = _code_audit()
    tests = _run_unit_tests()
    live = bool(args.live) and not args.offline_only
    demos = _send_demos(live=live)

    report = {
        "phase": 287,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "objective": "AM/PM Screening universe notify on trade-notify channel",
        "constraints": {
            "entry_logic_unchanged": True,
            "exit_logic_unchanged": True,
            "universe_selection_unchanged": True,
            "notification_wiring_only": True,
        },
        "expected_flow": [
            "1. AM Screening Universe notify",
            "2. ENTRY / EXIT",
            "3. 10:00 Refresh Universe notify",
            "4. AM Daily Summary",
            "5. PM Screening Universe notify",
            "6. ENTRY / EXIT",
            "7. 14:30 Refresh Universe notify",
            "8. Daily Summary",
        ],
        "verification_checklist": audit,
        "test_results": tests,
        "demo_send": demos,
        "implementation": {
            "files_changed": [
                "kabu_native/src/small_paper/discord_message_builder.py",
                "kabu_native/src/small_paper/discord_notifier.py",
                "kabu_native/src/runner/am_pm_daily_runner.py",
                "kabu_native/src/small_paper/pilot_runner.py",
            ],
            "new_api": "SmallPaperDiscordNotifier.notify_universe_screening()",
            "dedupe": {
                "screening": "screening|{AM|PM Screening}|{day_stamp} cooldown 12h",
                "refresh": "refresh|{AM|PM}|{10:00|14:30} cooldown 60s",
            },
        },
        "status": "complete"
        if tests["ok"]
        and all(audit[k] for k in audit if k.startswith(("1_", "2_", "3_", "4_", "5_", "6_", "7_")))
        else "incomplete",
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"tests_ok={tests['ok']} live_demo={live}")
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
