#!/usr/bin/env python3
"""
Phase176: Root-cause report for intraday refresh stopping the session.

Writes:
 - kabu_native/results/reports/phase176_intraday_refresh_stop_root_cause.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPORT = Path("kabu_native/results/reports/phase176_intraday_refresh_stop_root_cause.json")
SESSIONS = [
    Path("kabu_native/results/small_paper/20260528/live_session_082247"),
    Path("kabu_native/results/small_paper/20260528/live_session_122515"),
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl_head(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_raw": line, "_error": "json_decode_error"})
    return out


def _filter_intraday_refresh(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in events:
        if str(e.get("error_type") or "") != "intraday_refresh":
            continue
        out.append(e)
    return out


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    session_rows: list[dict[str, Any]] = []
    for sdir in SESSIONS:
        summ = _read_json(sdir / "small_paper_summary.json")
        errs = _read_jsonl_head(sdir / "errors.jsonl", limit=500)
        refresh_events = _filter_intraday_refresh(errs)
        session_rows.append(
            {
                "session_dir": str(sdir).replace("\\", "/"),
                "generated_at": summ.get("generated_at"),
                "ended_at": summ.get("ended_at"),
                "stop_reason": summ.get("stop_reason"),
                "session_start": summ.get("session_start"),
                "session_end": summ.get("session_end"),
                "intraday_refresh_enabled": summ.get("intraday_refresh_enabled"),
                "intraday_refresh_time": summ.get("intraday_refresh_time"),
                "intraday_refresh_triggered_count": summ.get("intraday_refresh_triggered_count"),
                "intraday_refresh_completed_count": summ.get("intraday_refresh_completed_count"),
                "intraday_refresh_failed_count": summ.get("intraday_refresh_failed_count"),
                "intraday_refresh_last_register_count": summ.get("intraday_refresh_last_register_count"),
                "refresh_events": refresh_events,
            }
        )

    report = {
        "phase": 176,
        "problem_statement": "intraday_refresh triggers at 10:00/14:30 and stops runner/push loop",
        "observed": session_rows,
        "root_cause": {
            "direct": "pilot_runner._maybe_intraday_refresh called _request_stop('open_symbols_exceed_cap') on merge_universe_with_open_symbols error",
            "why_open_symbols_exceed_cap": "refresh merge attempts to carry open symbols into new universe; if open symbols count > cap(3), merge returns error open_symbols_exceed_cap",
            "effect": "_request_stop sets stop_requested => main loop terminates; summary stop_reason becomes open_symbols_exceed_cap; refresh counts show triggered=1 completed=0 failed=1 register_count=0",
        },
        "fix_plan": {
            "behavior": [
                "refresh failure is warning/degraded, not fatal",
                "keep previous subscription when refresh fails",
                "do not unregister/all when new register specs invalid or empty",
            ],
            "implemented_patch": [
                "Do not call _request_stop on refresh failure paths",
                "Mark intraday_refresh_done=True on failure to avoid repeated attempts",
                "Guard: if specs empty => do not call register_symbols_cleared",
                "Emit intraday_refresh failed events with action=continue_keep_previous_subscription, will_stop=false",
            ],
        },
        "files_touched": ["kabu_native/src/small_paper/pilot_runner.py"],
    }

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

