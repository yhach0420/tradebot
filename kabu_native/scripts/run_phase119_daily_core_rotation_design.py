#!/usr/bin/env python3
"""Phase 119: Core10 daily rotation design — Discord !core commands + freshness checks."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
DISCORD_BOT = ROOT / "discord_issue_bot" / "discord_issue_bot.py"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def determine_verdict(
    *,
    commands_ok: bool,
    limit_ok: bool,
    freshness_ok: bool,
    status: dict[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not commands_ok:
        return "core_commands_missing", ["!core list/add/remove/clear/replace not found in discord bot"]
    if not limit_ok:
        return "core_limit_not_enforced", ["Core10 add/replace limit guard missing"]
    if not freshness_ok:
        return "core_freshness_check_missing", ["core_last_updated_date / stale fields missing"]
    if not status.get("readable_exists"):
        notes.append("watchlist.json not found yet — !core replace to create")
    if status.get("core_stale_warning"):
        notes.append(f"caution: {status.get('core_stale_warning')}")
    notes.append("Core10 daily rotation ready for shadow pipeline (stale = caution only, not blocked)")
    return "daily_core_rotation_ready", notes


def main() -> int:
    _bootstrap()
    from universe.core_watchlist import (
        CORE_LIMIT,
        can_add_to_core,
        can_replace_core,
        core_status_report,
        discord_core_commands_present,
        discord_enforcement_ok,
        format_core_list_reply,
        load_core_state,
    )

    parser = argparse.ArgumentParser(description="Phase 119 daily Core10 rotation design")
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--trade-date", default=None)
    args = parser.parse_args()

    from universe.day_stamp import normalize_day_stamp

    day_stamp = (
        normalize_day_stamp(args.day_stamp)
        if args.day_stamp
        else datetime.now(JST).strftime("%Y%m%d")
    )
    if args.trade_date:
        trade_d = date.fromisoformat(args.trade_date)
    else:
        trade_d = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))

    status = core_status_report(ROOT, trade_date=trade_d)
    state = load_core_state(ROOT)

    commands_ok = discord_core_commands_present(ROOT)
    limit_ok = discord_enforcement_ok(ROOT)
    freshness_ok = all(
        k in status
        for k in (
            "core_last_updated_date",
            "core_is_today",
            "core_stale_warning",
            "morning_check_caution",
        )
    )

    add_block = can_add_to_core([f"{1000+i}.T" for i in range(CORE_LIMIT)], "9999.T")
    replace_block = can_replace_core(",".join(f"{1000+i}.T" for i in range(11)))

    design: dict[str, Any] = {
        "phase": 119,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "trade_date": trade_d.isoformat(),
        "verdict_options": {
            "A": "daily_core_rotation_ready",
            "B": "core_commands_missing",
            "C": "core_limit_not_enforced",
            "D": "core_freshness_check_missing",
        },
        "command_design": {
            "primary": "!core",
            "alias": "!watch (deprecated alias of !core)",
            "commands": {
                "list": "!core list",
                "add": "!core add 7203.T",
                "remove": "!core remove 7203.T",
                "clear": "!core clear",
                "replace": "!core replace 7203.T,9984.T,3905.T,...",
            },
            "rationale": (
                "Core10 is a daily situation-reflection slot, not a fixed watchlist. "
                "!core replace is the primary morning workflow; !watch kept for backward compatibility."
            ),
        },
        "discord_reply_examples": {
            "list": format_core_list_reply(
                ["3905.T", "6613.T", "9984.T"],
                trade_date=trade_d,
                core_last_updated_date=trade_d.isoformat(),
            ),
            "replace": "Core10 updated (8/10).\nDynamic slots: 42.",
            "add_limit": (
                "Core watchlist limit reached (10/10).\n"
                "Remove or replace symbols before adding more."
            ),
        },
        "daily_rotation": {
            "core_last_updated_date_field": "watchlist.json core_last_updated_date",
            "stale_policy": "morning_check_caution_not_blocked",
            "morning_workflow": [
                "!core replace <today's symbols>",
                "python kabu_native/scripts/run_phase118_core10_dynamic40_pipeline.py --day-stamp YYYYMMDD",
            ],
        },
        "limit_tests": {
            "add_11th_simulated": {
                "reject": add_block[0] is False,
                "reason": add_block[1],
            },
            "replace_11_simulated": {
                "reject": replace_block[0] is False,
                "reason": replace_block[1],
            },
        },
        "checks": {
            "core_commands_present": commands_ok,
            "core_limit_enforced": limit_ok,
            "core_freshness_check_present": freshness_ok,
        },
        "core10_status": status,
        "watchlist_state": {
            "symbols": state.symbols,
            "core_last_updated_date": state.core_last_updated_date,
            "updated_at_jst": state.updated_at_jst,
            "version": state.raw_version,
        },
        "pipeline_integration": {
            "phase106_mode": "core10-dynamic40",
            "phase118_script": _rel(NATIVE / "scripts" / "run_phase118_core10_dynamic40_pipeline.py"),
            "freshness_fields_in_pipeline": [
                "core_count",
                "core_symbols",
                "core_last_updated_date",
                "core_is_today",
                "core_stale_warning",
            ],
        },
        "outputs": {
            "design_json": _rel(REPORTS / "phase119_daily_core_rotation_design.json"),
            "status_json": _rel(REPORTS / "phase119_core_watchlist_status.json"),
            "status_json_dated": _rel(REPORTS / f"phase119_core_watchlist_status_{day_stamp}.json"),
        },
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_symbol_hardcode",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }

    verdict, notes = determine_verdict(
        commands_ok=commands_ok,
        limit_ok=limit_ok,
        freshness_ok=freshness_ok,
        status=status,
    )
    design["verdict"] = verdict
    design["verdict_notes"] = notes

    out_design = REPORTS / "phase119_daily_core_rotation_design.json"
    out_status = REPORTS / "phase119_core_watchlist_status.json"
    out_status_dated = REPORTS / f"phase119_core_watchlist_status_{day_stamp}.json"
    out_design.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    status_payload = {
        "phase": 119,
        "generated_at": design["generated_at"],
        "trade_date": trade_d.isoformat(),
        "verdict": verdict,
        **status,
    }
    text = json.dumps(status_payload, ensure_ascii=False, indent=2)
    out_status.write_text(text, encoding="utf-8")
    out_status_dated.write_text(text, encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": verdict,
                "core_count": status.get("core_count"),
                "core_is_today": status.get("core_is_today"),
                "core_stale_warning": status.get("core_stale_warning") or None,
            },
            ensure_ascii=True,
        )
    )
    return 0 if verdict == "daily_core_rotation_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
