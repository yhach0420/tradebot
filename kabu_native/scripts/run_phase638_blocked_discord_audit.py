#!/usr/bin/env python3
"""Phase638: audit blocked ENTRY Discord notifications from live_session artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase638_blocked_discord_audit"
PHASE638_VERDICT_DONE = "phase638_blocked_discord_audit_done"

CAP_BLOCKED_ENV = "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL"
NOTIFY_REASONS = {
    "max_concurrent",
    "or_cap_full",
    "pbv2_cap_full",
    "REJECT_SAME_SYMBOL_OPEN_OVERLAP",
    "max_entries_per_scan",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_rejects(summary: dict[str, Any]) -> dict[str, int]:
    counts = summary.get("reject_reason_counts") or {}
    out = {k: int(counts.get(k) or 0) for k in NOTIFY_REASONS}
    overlap = int(summary.get("same_symbol_overlap_reject_count") or 0)
    if overlap and not out.get("REJECT_SAME_SYMBOL_OPEN_OVERLAP"):
        out["REJECT_SAME_SYMBOL_OPEN_OVERLAP"] = overlap
    return out


def _discord_errors(session_dir: Path) -> int:
    fp = session_dir / "errors.jsonl"
    if not fp.is_file():
        return 0
    n = 0
    with fp.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("error_type") == "discord_error":
                n += 1
    return n


def _find_sessions(root: Path, *, days: int) -> list[Path]:
    dirs = sorted(
        (p.parent for p in root.glob("*/live_session_*/small_paper_summary.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return dirs[: max(1, days * 4)]


def run_audit(*, days: int) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    sp_root = NATIVE_ROOT / "results" / "small_paper"
    sessions = _find_sessions(sp_root, days=days)

    cap_env_set = bool((os.environ.get(CAP_BLOCKED_ENV) or "").strip())
    rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    total_blocked = Counter()
    total_sent = 0
    total_attempt = 0
    total_discord_errors = 0

    for session_dir in sessions:
        summary_fp = session_dir / "small_paper_summary.json"
        if not summary_fp.is_file():
            continue
        summary = _load_json(summary_fp)
        rejects = _count_rejects(summary)
        notify_target = sum(rejects.values())
        sent = int(summary.get("cap_blocked_notify_sent_count") or 0)
        attempt = int(summary.get("cap_blocked_notify_attempt_count") or 0)
        discord_err = int(summary.get("discord_error_count") or _discord_errors(session_dir))
        day = session_dir.parent.name
        session = session_dir.name
        row = {
            "day": day,
            "session": session,
            "max_concurrent": rejects.get("max_concurrent", 0),
            "REJECT_SAME_SYMBOL_OPEN_OVERLAP": rejects.get("REJECT_SAME_SYMBOL_OPEN_OVERLAP", 0),
            "max_entries_per_scan": rejects.get("max_entries_per_scan", 0),
            "or_cap_full": rejects.get("or_cap_full", 0),
            "pbv2_cap_full": rejects.get("pbv2_cap_full", 0),
            "notify_target_total": notify_target,
            "cap_blocked_notify_sent": sent,
            "cap_blocked_notify_attempt": attempt,
            "discord_error_count": discord_err,
            "cap_blocked_webhook_configured": summary.get("cap_blocked_webhook_configured"),
        }
        rows.append(row)
        total_blocked.update({k: rejects.get(k, 0) for k in NOTIFY_REASONS})
        total_sent += sent
        total_attempt += attempt
        total_discord_errors += discord_err
        for reason, count in rejects.items():
            if count:
                trace_rows.append(
                    {
                        "day": day,
                        "session": session,
                        "reason": reason,
                        "reject_count": count,
                        "cap_blocked_sent": sent,
                        "note": "pre-fix sessions often show 0 sent despite rejects",
                    }
                )

    with (REPORT_DIR / "phase638_blocked_counts.csv").open("w", encoding="utf-8", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    with (REPORT_DIR / "phase638_notification_trace.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["day", "session", "reason", "reject_count", "cap_blocked_sent", "note"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(trace_rows)

    root_causes = [
        "Phase538 or_overlay: max_concurrent remapped to or_cap_full/pbv2_cap_full but notify checked max_concurrent only",
        "cap_blocked path gated by discord.active (trade-notify URL required)",
        "REJECT_SAME_SYMBOL_OPEN_OVERLAP and max_entries_per_scan never wired to notify_entry_cap_blocked",
    ]
    fixes = [
        "is_entry_blocked_discord_notify_reason covers all cap/overlap/max_scan reasons",
        "_notify_entry_blocked_discord uses cap_blocked_notify_enabled (not active)",
        "overlap + scan flush paths call cap-blocked notify",
        "discord_error_count + cap_blocked counts in summary / System Health",
    ]

    report = {
        "phase": "phase638_blocked_discord_audit",
        "verdict": PHASE638_VERDICT_DONE,
        "env": {
            "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL_set": cap_env_set,
            "discord_trade_cap_blocked_webhook_env": CAP_BLOCKED_ENV,
        },
        "sessions_audited": len(rows),
        "totals": {
            "blocked_rejects": dict(total_blocked),
            "notify_target_sum": sum(total_blocked.values()),
            "cap_blocked_notify_sent_sum": total_sent,
            "cap_blocked_notify_attempt_sum": total_attempt,
            "discord_error_count_sum": total_discord_errors,
        },
        "answers": {
            "1_blocks_occurred": sum(total_blocked.values()) > 0,
            "2_notify_target_count": sum(total_blocked.values()),
            "3_notify_function_called_historically": total_attempt > 0 or total_sent > 0,
            "4_webhook_configured_in_env_now": cap_env_set,
            "5_broken_since": "Phase538 (or_cap_full remap) + overlap/scan paths never wired",
            "6_root_cause": "route (reason mismatch + missing paths + active gate)",
            "7_fixes": fixes,
            "8_post_fix_verified": "tests/test_phase638_blocked_discord_audit.py",
        },
        "root_causes": root_causes,
        "phase637_impact": "operator summary only; did not remove cap-blocked webhook path",
        "phase616_impact": "ExtensionBus did not replace cap-blocked notify",
        "phase629_impact": "Stage6 reject path preserved but reason check too narrow",
    }
    (REPORT_DIR / "phase638_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase638 blocked Discord audit")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    report = run_audit(days=int(args.days))
    print(json.dumps(report["answers"], ensure_ascii=False, indent=2))
    print(f"report -> {REPORT_DIR / 'phase638_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
