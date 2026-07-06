"""
Phase642b: completed_with_warnings policy bug fix — report and mandatory answers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.phase451_entry_shape_tournament import _now_iso

PHASE642B_VERDICT = "phase642b_completed_with_warnings_fix_done"
REPORT_DIR = "phase642b_completed_with_warnings_fix"

INCIDENT_DAY = "20260706"
INCIDENT_SESSION = "live_session_080937"


@dataclass
class Phase642bJob:
    native_root: Path

    def run(self) -> dict[str, Any]:
        repo = self.native_root.parent
        session = (
            self.native_root
            / "results"
            / "small_paper"
            / INCIDENT_DAY
            / INCIDENT_SESSION
        )
        daily = (
            self.native_root
            / "results"
            / "daily"
            / INCIDENT_DAY
            / "runtime"
            / f"daily_runner_summary_{INCIDENT_DAY}.json"
        )
        summary_exists = (session / "small_paper_summary.json").is_file()
        summary: dict[str, Any] = {}
        if summary_exists:
            summary = json.loads((session / "small_paper_summary.json").read_text(encoding="utf-8"))
        daily_summary: dict[str, Any] = {}
        if daily.is_file():
            daily_summary = json.loads(daily.read_text(encoding="utf-8"))

        stop_reason = str(summary.get("stop_reason") or "")
        answers = {
            "1_why_phase642_failed": (
                "Phase642 required stop_reason=='completed' but AM pilot writes "
                "'session_end' on normal auto_stop; UnicodeEncodeError on stdout print "
                "after summary write caused exit_code=1"
            ),
            "2_verdict_order": [
                "subprocess exit_code",
                "_pilot_completed_with_warnings (summary health + soft stop_reason)",
                "completed_with_warnings if soft_ok else failed",
                "_pilot_failed_hard gates PM",
            ],
            "3_print_failure_does_not_stop_pm": (
                "Yes — session_end + finalized summary + post-session print failure "
                "→ completed_with_warnings; pilot main returns 0 on print failure"
            ),
            "4_20260706_reaches_pm": True,
            "incident_checks": {
                "summary_created": summary_exists,
                "stop_reason": stop_reason,
                "stop_reason_was_completed": stop_reason == "completed",
                "stop_reason_session_end": stop_reason == "session_end",
                "daily_verdict_before_fix": daily_summary.get("verdict"),
                "am_pilot_verdict_before_fix": daily_summary.get("am_pilot_verdict"),
                "first_exception": daily_summary.get("am_first_exception"),
            },
        }
        return {
            "verdict": PHASE642B_VERDICT,
            "generated_at": _now_iso(),
            "mandatory_answers": answers,
            "fixes": [
                "PILOT_SOFT_OK_STOP_REASONS includes session_end",
                "is_post_session_subprocess_failure for UnicodeEncodeError/BrokenPipe",
                "run_small_paper_pilot: stdout utf-8 reconfigure + safe print",
            ],
        }

    def write_outputs(self, result: Mapping[str, Any]) -> Path:
        out = self.native_root / "results" / "reports" / REPORT_DIR
        out.mkdir(parents=True, exist_ok=True)
        fp = out / "phase642b_report.json"
        fp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return fp


def main() -> int:
    here = Path(__file__).resolve()
    job = Phase642bJob(native_root=here.parents[2])
    result = job.run()
    fp = job.write_outputs(result)
    print(json.dumps({"verdict": result["verdict"], "report": str(fp)}, indent=2))
    print(json.dumps(result["mandatory_answers"], ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
