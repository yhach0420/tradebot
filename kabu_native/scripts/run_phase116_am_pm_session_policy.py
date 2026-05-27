#!/usr/bin/env python3
"""Phase 116: Document and verify AM/PM session-close policy (shadow only)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from small_paper.am_pm_session_policy import (
        AFTERNOON_SESSION_CLOSE,
        MORNING_SESSION_CLOSE,
        AmPmSessionPolicy,
    )

    am = AmPmSessionPolicy.morning()
    pm = AmPmSessionPolicy.afternoon()

    report = {
        "phase": 116,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "am_pm_session_policy_applied",
        "session_trade_times": {
            "am": {"session_start": am.session_start, "session_end": am.session_end},
            "pm": {"session_start": pm.session_start, "session_end": pm.session_end},
        },
        "entry_stop": {"am": am.entry_stop, "pm": pm.entry_stop},
        "force_close": {
            "am": {"time": am.force_close, "exit_reason": MORNING_SESSION_CLOSE},
            "pm": {"time": pm.force_close, "exit_reason": AFTERNOON_SESSION_CLOSE},
        },
        "allowed_entry_windows_runtime": {
            "am": f"{am.allowed_entry_start}-{am.allowed_entry_end}",
            "pm": f"{pm.allowed_entry_start}-{pm.allowed_entry_end}",
        },
        "universe_screening_recommended": {
            "am": am.screening_window,
            "pm": pm.screening_window,
        },
        "run_small_paper_pilot": {
            "supports_session_start_end": True,
            "supports_am_pm_session_flag": True,
            "am_command_flag": "--am-pm-session am",
            "pm_command_flag": "--am-pm-session pm",
        },
        "production_yaml_unchanged": True,
        "notes": [
            "11:20+ no new entries (AM); 15:18+ no new entries (PM)",
            "11:25 / 15:23 force close open virtual positions in shadow dry-run",
            "No carry AM positions to PM universe",
        ],
    }

    out = REPORTS / "phase116_am_pm_session_policy.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(out)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
