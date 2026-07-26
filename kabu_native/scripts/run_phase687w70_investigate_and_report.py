#!/usr/bin/env python3
"""Phase687W70 — delete-path audit, loss timeline, retention guard report, push_jsonl findings."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))
REPORTS = NATIVE / "results" / "reports"


def write_pair(stem: str, payload: dict[str, Any], md_lines: list[str]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (REPORTS / f"{stem}.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def delete_path_audit() -> dict[str, Any]:
    rows = [
        {
            "file": "scripts/phase687w43b_fix3_disk_remediation_plan.py",
            "function": "paper_keep_days / Plan B",
            "introduced": "~2026-07 (phase687w43b)",
            "delete_target": "classifies old paper as archive_candidate (latest 20 days keep)",
            "executes_delete": False,
            "reachable_from_runner": False,
            "can_wipe_small_paper": False,
            "can_delete_past_dates": False,
            "note": "Plan only; docstring forbids delete/move/compress",
            "action": "LEFT_AS_PLAN_ONLY — no code path to rmtree small_paper",
        },
        {
            "file": "src/small_paper/pullback_volume_forward_logger.py",
            "function": "cleanup_logger_temp_files",
            "delete_target": ".write_probe / *.tmp / *.partial under logger out_dir",
            "executes_delete": True,
            "reachable_from_runner": True,
            "can_wipe_small_paper": False,
            "note": "temp probes only",
            "action": "SAFE",
        },
        {
            "file": "scripts/check_runtime_parity.py / check_phase636_shadow_parity.py",
            "function": "shutil.rmtree staging",
            "delete_target": "results/small_paper/_phase630* staging copies",
            "executes_delete": True,
            "reachable_from_runner": False,
            "can_wipe_small_paper": False,
            "note": "scratch under _phase* — allowed by retention forbid rules",
            "action": "SAFE_SCRATCH",
        },
        {
            "file": "PS history quarantine (manual)",
            "function": "Move-Item style",
            "delete_target": "20260714 -> _quarantine_w28r3 (not May/Jun window)",
            "executes_delete": True,
            "reachable_from_runner": False,
            "can_wipe_small_paper": False,
            "note": "Found in PSReadLine history; post-dates lost window",
            "action": "DOCUMENTED — not cause of 5/28-6/14 loss",
        },
        {
            "file": "src/small_paper/data_retention_guard.py",
            "function": "forbid_protected_delete / check_retention_integrity",
            "delete_target": "blocks deletes under small_paper/push_jsonl/archive",
            "executes_delete": False,
            "reachable_from_runner": True,
            "note": "NEW Phase687W70 — fail-closed preflight + archive copy",
            "action": "ENABLED",
        },
        {
            "file": "src/small_paper/paper_trade_checked_runner.py",
            "function": "step_preflight",
            "delete_target": "none — blocks start on retention failure / disk>=92%",
            "executes_delete": False,
            "reachable_from_runner": True,
            "action": "WIRED",
        },
        {
            "file": "src/small_paper/pilot_runner.py",
            "function": "run_live finalize",
            "delete_target": "none — archive_session_copy after finalize_batch",
            "executes_delete": False,
            "reachable_from_runner": True,
            "action": "WIRED",
        },
        {
            "file": "scripts/disk_cleanup_research_artifacts.py",
            "function": "execute_deletes",
            "delete_target": "_phase* / replay temp under small_paper + research reports",
            "executes_delete": True,
            "reachable_from_runner": False,
            "can_wipe_small_paper": False,
            "can_delete_past_dates": False,
            "note": "W70: forbid_protected_delete before rmtree; live YYYYMMDD sessions blocked",
            "action": "HARDENED",
        },
        {
            "file": "src/research/phase620_disk_cleanup.py",
            "function": "run_disk_cleanup",
            "delete_target": "_phase620* checkpoints / *_replay under small_paper",
            "executes_delete": True,
            "reachable_from_runner": False,
            "can_wipe_small_paper": False,
            "can_delete_past_dates": False,
            "note": "W70: forbid_protected_delete before delete",
            "action": "HARDENED",
        },
    ]
    current_auto_delete = [
        r for r in rows if r.get("executes_delete") and r.get("can_wipe_small_paper")
    ]
    return {
        "phase": "Phase687W70",
        "current_auto_delete_of_protected_trees": False,
        "dangerous_live_paths": current_auto_delete,
        "rows": rows,
        "disk_75_policy": "warn at >=75%; hard-block Paper start at >=92%; never auto-delete",
        "git_history_note": (
            "results/small_paper is gitignored; no commit deletes session blobs. "
            "Phase55 introduced small_paper output path (2026-05-18)."
        ),
    }


def loss_timeline() -> dict[str, Any]:
    # Evidence from known reports
    cases = []
    for day, sess, present_src, present_at, strength in [
        (
            "2026-05-28",
            "live_session_082247",
            "daily_runner_summary_20260528.json + phase148 (path only)",
            "2026-05-28T~15:23",
            2,
        ),
        (
            "2026-06-04",
            "live_session_080544",
            "phase300 archived_event_scan read events (507k lines aggregate incl this session)",
            "2026-06-09T20:43:48",
            5,
        ),
        (
            "2026-06-09",
            "live_session_080641",
            "phase265_structural_trades_backfill_by_session.csv generated=78 trades",
            "2026-06-14T17:29:10",
            5,
        ),
        (
            "2026-06-12",
            "live_session_080806",
            "phase265 generated=79 + daily_runner_summary_20260612.json",
            "2026-06-14T17:29:10",
            5,
        ),
    ]:
        cases.append(
            {
                "session_date": day,
                "session_path": f"kabu_native/results/small_paper/{day.replace('-','')}/{sess}",
                "last_known_present_at": present_at,
                "last_known_present_source": present_src,
                "last_known_present_evidence_strength": strength,
                "first_known_missing_at": "2026-06-15T08:02:51",
                "first_known_missing_source": (
                    "small_paper_safety_20260615.json + day dir 20260615 ctime; "
                    "no 20260528/20260604 paths; oldest on-disk day becomes 20260615"
                ),
                "estimated_loss_window_start": "2026-06-14T17:29:10",
                "estimated_loss_window_end": "2026-06-15T08:02:51",
                "confidence": "HIGH_WINDOW_MEDIUM_CAUSE",
            }
        )

    hypotheses = {
        "A_recreate_results": {
            "status": "UNLIKELY",
            "why": "reports/ and 0615+ sessions survived; not a full results wipe",
        },
        "B_branch_worktree_switch": {
            "status": "UNLIKELY_AS_SOLE_CAUSE",
            "why": "same absolute path used before/after; worktrees lack old sessions too",
        },
        "C_cwd_or_abspath_change": {
            "status": "UNLIKELY",
            "why": "daily_runner continued writing to same kabu_native/results/small_paper",
        },
        "D_targeted_cleanup_pre_0615": {
            "status": "PLAUSIBLE",
            "why": (
                "phase687w43b later classified 'keep latest 20 days' but that script never deleted; "
                "manual cleanup following similar intent remains possible"
            ),
        },
        "E_manual_delete_or_folder_tidy": {
            "status": "PLAUSIBLE_TOP",
            "why": (
                "PS history has no explicit Remove-Item of 20260528-20260614; "
                "but Explorer/other-shell deletion would not appear. "
                "0615 day ctime is fresh create at 08:02:50, consistent with continued ops after loss"
            ),
        },
        "F_0615_newly_created_not_rename": {
            "status": "CONFIRMED",
            "why": "20260615 directory CreationTime=2026-06-15 08:02:50 — new folder, not rename of old tree",
        },
        "G_research_phase_bulk_delete": {
            "status": "NO_CODE_EVIDENCE",
            "why": "phase265/300 read-only; no rmtree of session roots in those modules",
        },
    }

    return {
        "phase": "Phase687W70",
        "boundary_reason_summary": (
            "On-disk small_paper suddenly begins at 20260615 because pre-0615 session directories "
            "were removed overnight 2026-06-14 evening → 2026-06-15 morning; "
            "0615+ are newly created live days, not a migrated corpus."
        ),
        "estimated_loss_window": {
            "start": "2026-06-14T17:29:10+09:00",
            "end": "2026-06-15T08:02:51+09:00",
            "confidence": "HIGH",
        },
        "most_likely_cause": "E_manual_or_external_folder_deletion_OR_D_targeted_cleanup_outside_tracked_scripts",
        "evidence_type": "circumstantial_with_hard_presence_absence_bounds",
        "direct_delete_command_found": False,
        "hypotheses": hypotheses,
        "sessions": cases,
        "period_labels": {
            "paper_operation_period": "2026-05-28〜 (runner summaries exist)",
            "available_complete_event_period": "2026-06-15〜",
            "missing_complete_event_period": "2026-05-28〜2026-06-14",
            "requested_period_note": "Do not call 0615-0717 'full period' without missing_periods",
        },
    }


def push_jsonl_findings() -> dict[str, Any]:
    push = NATIVE / "data" / "push_jsonl"
    children = list(push.iterdir()) if push.is_dir() else []
    return {
        "path": str(push),
        "exists": push.is_dir(),
        "children": [p.name for p in children],
        "config_record_push_jsonl": True,
        "recorder": "storage.push_recorder.PushRecorder -> native_root/data/push_jsonl/YYYY-MM-DD/{symbol}.jsonl",
        "why_empty_now": [
            "Directory only has .gitkeep today",
            "PS history shows past use of data/push_jsonl/2026-05-19 and 2026-05-20 — files existed then",
            "Likely deleted/moved with same loss window or earlier disk cleanup; not disabled in YAML",
            "Board aggregates persist in small_paper_events (eobi) so research continued without raw push",
        ],
        "auto_delete_in_code": False,
        "future_policy": "retain push_jsonl; compress only after verify + user approval; no auto-delete",
    }


def retention_guard_report() -> dict[str, Any]:
    from small_paper.data_retention_guard import check_retention_integrity, baseline_path

    r = check_retention_integrity()
    return {
        "phase": "Phase687W70",
        "baseline_path": str(baseline_path()),
        "check": r.to_dict(),
        "pre_start": "paper_trade_checked_runner.step_preflight calls check_retention_integrity",
        "session_end_backup": "pilot_runner run_live -> archive_session_copy -> results/archive/small_paper/{day}/{session}/BACKUP_COMPLETE.json",
        "disk_75": "warn at >=75%; hard-block start at >=92%; never auto-delete",
        "env_disable": "PAPER_RETENTION_GUARD_DISABLE=1 (emergency only)",
    }


def main() -> int:
    backup = {}
    bp = REPORTS / "phase687w70_immediate_backup_report.json"
    if bp.is_file():
        backup = json.loads(bp.read_text(encoding="utf-8"))

    del_audit = delete_path_audit()
    write_pair(
        "phase687w70_delete_path_audit",
        del_audit,
        [
            "# Phase687W70 Delete Path Audit",
            "",
            f"- current_auto_delete_of_protected_trees: **{del_audit['current_auto_delete_of_protected_trees']}**",
            f"- disk_75_policy: {del_audit['disk_75_policy']}",
            "",
            "## Findings",
        ]
        + [f"- `{r['file']}` — {r.get('action')} — {r.get('note','')}" for r in del_audit["rows"]],
    )

    timeline = loss_timeline()
    write_pair(
        "phase687w70_loss_timeline",
        timeline,
        [
            "# Phase687W70 Loss Timeline",
            "",
            f"**Boundary:** {timeline['boundary_reason_summary']}",
            "",
            f"- estimated_loss_window: {timeline['estimated_loss_window']}",
            f"- most_likely_cause: {timeline['most_likely_cause']}",
            f"- evidence_type: {timeline['evidence_type']}",
            f"- direct_delete_command_found: {timeline['direct_delete_command_found']}",
            "",
            "## Period labels (mandatory wording)",
            json.dumps(timeline["period_labels"], ensure_ascii=False, indent=2),
            "",
            "## Hypotheses",
        ]
        + [f"- **{k}**: {v['status']} — {v['why']}" for k, v in timeline["hypotheses"].items()]
        + ["", "## Sessions"]
        + [
            f"- {s['session_date']} present@{s['last_known_present_at']} (str{s['last_known_present_evidence_strength']}) "
            f"→ missing by {s['first_known_missing_at']}"
            for s in timeline["sessions"]
        ],
    )

    guard = retention_guard_report()
    push = push_jsonl_findings()
    guard["push_jsonl"] = push
    write_pair(
        "phase687w70_retention_guard_report",
        guard,
        [
            "# Phase687W70 Retention Guard Report",
            "",
            f"- baseline: `{guard['baseline_path']}`",
            f"- check_ok: {guard['check'].get('ok')} code={guard['check'].get('code')}",
            f"- sessions baseline/current: {guard['check'].get('baseline_session_count')}/{guard['check'].get('current_session_count')}",
            f"- disk_usage_pct: {guard['check'].get('disk_usage_pct')}",
            "",
            "## Wiring",
            f"- pre_start: {guard['pre_start']}",
            f"- session_end_backup: {guard['session_end_backup']}",
            f"- disk_75: {guard['disk_75']}",
            "",
            "## push_jsonl",
            json.dumps(push, ensure_ascii=False, indent=2),
        ],
    )

    # W66 wording note
    note = REPORTS / "phase687w66_period_wording_note.md"
    note.write_text(
        "\n".join(
            [
                "# Phase687W66 period wording correction (Phase687W70)",
                "",
                "Do not describe W66 as covering the full Paper operation history.",
                "",
                "- requested_period: full Paper history (as intended by --all-period)",
                "- paper_operation_period: 2026-05-28〜 (runner evidence)",
                "- available_complete_event_period: 2026-06-15〜2026-07-17 (on-disk events)",
                "- missing_complete_event_period: 2026-05-28〜2026-06-14",
                "- W66 actually evaluated: available_complete_event_period only",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Final answers bundle
    snap = backup.get("snapshot_dir", "")
    answers = {
        "1_emergency_backup_complete": bool(backup.get("protection_complete")),
        "2_sessions_copied": backup.get("sessions_copied"),
        "3_files_and_bytes": {
            "files": backup.get("files_in_manifest"),
            "bytes": backup.get("total_bytes_copied"),
        },
        "4_sha_mismatches": backup.get("failed_verifications"),
        "5_current_deletable_code": False,
        "6_git_history_deletable_sessions": False,
        "7_why_0615_boundary": timeline["boundary_reason_summary"],
        "8_last_known_present": "2026-06-14T17:29:10 phase265 (strength 5)",
        "9_first_known_missing": "2026-06-15T08:02:51 small_paper_safety_20260615 + day ctime",
        "10_estimated_loss_window": timeline["estimated_loss_window"],
        "11_most_likely_cause": timeline["most_likely_cause"],
        "12_evidence_type": timeline["evidence_type"],
        "13_manual_delete_trace": "No Remove-Item of lost days in PS history; quarantine move exists only for 20260714",
        "14_runner_init_bug": False,
        "15_windows_external_factor": "Not confirmed (Storage Sense typically spares Documents); SSD TRIM relevant for undelete only",
        "16_auto_delete_disabled": True,
        "17_retention_guard": guard["pre_start"],
        "18_session_end_backup": guard["session_end_backup"],
        "19_push_jsonl_reason": push["why_empty_now"],
        "20_runtime_logic_unchanged": True,
        "21_artifacts": {
            "backup_report": str(REPORTS / "phase687w70_immediate_backup_report.md"),
            "delete_audit": str(REPORTS / "phase687w70_delete_path_audit.md"),
            "loss_timeline": str(REPORTS / "phase687w70_loss_timeline.md"),
            "retention_guard": str(REPORTS / "phase687w70_retention_guard_report.md"),
            "snapshot": snap,
            "baseline": str(NATIVE / "results" / "retention" / "small_paper_retention_baseline.json"),
            "w66_note": str(note),
        },
    }

    verdict = (
        "PAPER_DATA_PROTECTED_LOSS_WINDOW_PARTIALLY_IDENTIFIED"
        if backup.get("protection_complete")
        else "PAPER_DATA_PROTECTION_FAILED"
    )
    # Cause not fully identified with direct command → partially identified
    final = {
        "phase": "Phase687W70",
        "verdict": verdict,
        "answers": answers,
        "runtime_unchanged": True,
        "shadow_unchanged": True,
        "entry_exit_unchanged": True,
    }
    (REPORTS / "phase687w70_final_summary.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"verdict": verdict, "backup_ok": backup.get("protection_complete")}, ensure_ascii=False))
    return 0 if backup.get("protection_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
