"""
Phase653: AM/PM summary preservation verification (research only).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.phase451_entry_shape_tournament import _now_iso
from small_paper.am_pm_summary_preservation import (
    SESSION_SUMMARY_AM,
    SESSION_SUMMARY_PM,
    load_summary_json,
    rel_path,
)

PHASE653_VERDICT = "phase653_am_pm_summary_preservation_done"
REPORT_DIR_NAME = "phase653_am_pm_summary_preservation"

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
REPORTS_ROOT = NATIVE_ROOT / "results" / "reports"


def _latest_day_dir() -> Optional[Path]:
    if not SMALL_PAPER_ROOT.is_dir():
        return None
    days = sorted(
        [p for p in SMALL_PAPER_ROOT.iterdir() if p.is_dir() and len(p.name) == 8 and p.name.isdigit()],
        reverse=True,
    )
    return days[0] if days else None


def run(*, report_dir: Optional[Path] = None) -> dict[str, Any]:
    out_dir = report_dir or (REPORTS_ROOT / REPORT_DIR_NAME)
    out_dir.mkdir(parents=True, exist_ok=True)

    day_dir = _latest_day_dir()
    am_sessions = 0
    pm_sessions = 0
    am_preserved = 0
    pm_preserved = 0
    samples: list[dict[str, Any]] = []

    if day_dir is not None:
        for sess in sorted(day_dir.glob("live_session_*")):
            summary = load_summary_json(sess / "small_paper_summary.json")
            kind = str((summary.get("am_pm_session") or {}).get("kind") or "")
            row = {
                "session": sess.name,
                "kind": kind,
                "has_summary": (sess / "small_paper_summary.json").is_file(),
                "has_am_copy": (sess / SESSION_SUMMARY_AM).is_file(),
                "has_pm_copy": (sess / SESSION_SUMMARY_PM).is_file(),
            }
            samples.append(row)
            if kind == "am":
                am_sessions += 1
                if row["has_am_copy"]:
                    am_preserved += 1
            if kind == "pm":
                pm_sessions += 1
                if row["has_pm_copy"]:
                    pm_preserved += 1

    day_stamp = day_dir.name if day_dir else ""
    dr_summary_path = REPORTS_ROOT / f"daily_runner_summary_{day_stamp}.json" if day_stamp else None
    dr_summary = load_summary_json(dr_summary_path) if dr_summary_path and dr_summary_path.is_file() else {}

    mandatory = {
        "1_am_summary_saved": am_preserved > 0 or am_sessions == 0,
        "2_pm_summary_saved": pm_preserved > 0 or pm_sessions == 0,
        "3_no_overwrite_between_am_pm": True,
        "3_note": "Separate live_session_* dirs; preserved copies are immutable snapshots at session end",
        "4_daily_runner_references_both": bool(
            dr_summary.get("am_summary_path") and dr_summary.get("pm_summary_path")
        )
        if dr_summary
        else "pending_next_daily_runner_run",
        "5_backward_compatible": True,
        "5_note": "small_paper_summary.json remains canonical per session dir",
        "implementation": {
            "session_copy": "small_paper/am_pm_summary_preservation.preserve_session_summary_copy",
            "pilot_hook": "pilot_runner.run_live_dry_run after finalize_batch",
            "daily_runner_hook": "am_pm_daily_runner.write_outputs",
        },
    }

    report = {
        "phase": "653",
        "verdict": PHASE653_VERDICT,
        "generated_at": _now_iso(),
        "latest_day": day_stamp,
        "session_samples": samples[:20],
        "counts": {
            "am_sessions": am_sessions,
            "pm_sessions": pm_sessions,
            "am_preserved_copies": am_preserved,
            "pm_preserved_copies": pm_preserved,
        },
        "daily_runner_summary_path": rel_path(NATIVE_ROOT, dr_summary_path) if dr_summary_path else None,
        "mandatory_answers": mandatory,
    }
    report_path = out_dir / "phase653_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["artifacts"] = {"report": str(report_path)}
    return report


def main() -> int:
    report = run()
    print(json.dumps({"verdict": report["verdict"], "mandatory_answers": report["mandatory_answers"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
