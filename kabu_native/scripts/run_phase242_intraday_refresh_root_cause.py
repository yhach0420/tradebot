#!/usr/bin/env python3
"""
Phase242: intraday refresh failed root-cause identification (review only).

Goal:
Identify why intraday_refresh failed, without changing production/YAML/entry logic.

Checks (user requirements):
1) exception / error log right after refresh start
2) refresh universe csv exists
3) refresh universe csv row count
4) whether register_symbols() was called (inferred)
5) register_symbols() return value (inferred)
6) kabu API response (from errors.jsonl api_error entries)
7) candidates=0 situation (inferred via merge/register meta)
8) branch where register_count=0 happens

Sources:
- small_paper_summary.json (intraday_refresh_* counters and paths)
- errors.jsonl (error_type=intraday_refresh and api_error)
- refresh universe csv (intraday_refresh_csv)
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase242_intraday_refresh_root_cause.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_raw": line, "_error": "json_decode_error"})
    return out


def _csv_row_count(path: Path) -> Optional[int]:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except OSError:
        return None


def _discover_sessions(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [p.parent for p in sorted(base.rglob("small_paper_summary.json"))]


def _intraday_events(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in errors if str(e.get("error_type") or "") == "intraday_refresh"]


def _api_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in errors if str(e.get("error_type") or "") == "api_error"]


def _find_refresh_pair(events: list[dict[str, Any]], refresh_time: str) -> dict[str, Any]:
    started = [e for e in events if e.get("event") == "started" and str(e.get("refresh_time") or "") == refresh_time]
    failed = [e for e in events if e.get("event") == "failed" and str(e.get("refresh_time") or "") == refresh_time]
    completed = [e for e in events if e.get("event") == "completed" and str(e.get("refresh_time") or "") == refresh_time]
    return {
        "started": started[-1] if started else None,
        "failed": failed[-1] if failed else None,
        "completed": completed[-1] if completed else None,
    }


def _infer_register_called(failed_ev: Optional[dict[str, Any]], completed_ev: Optional[dict[str, Any]]) -> Optional[bool]:
    # If completed exists → definitely called and succeeded.
    if completed_ev:
        return True
    # Certain failure reasons happen before register call.
    if not failed_ev:
        return None
    reason = str(failed_ev.get("reason") or "")
    if reason in ("refresh_csv_missing", "open_symbols_exceed_cap", "register_count_zero"):
        return False
    if reason in ("register_exception",):
        return True  # entered try: register_symbols_cleared()
    # Unknown: could be before or during
    return None


def _infer_register_return(failed_ev: Optional[dict[str, Any]], completed_ev: Optional[dict[str, Any]]) -> Optional[int]:
    if completed_ev:
        try:
            return int(completed_ev.get("register_count") or completed_ev.get("after_symbol_count") or 0)
        except (TypeError, ValueError):
            return None
    if failed_ev and str(failed_ev.get("reason") or "") == "register_count_zero":
        return 0
    return None


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sessions = _discover_sessions(SMALL_PAPER)

    audits: list[dict[str, Any]] = []
    failed_sessions: list[dict[str, Any]] = []

    for sdir in sessions:
        summ = _read_json(sdir / "small_paper_summary.json")
        if not bool(summ.get("intraday_refresh_enabled")):
            continue

        refresh_time = str(summ.get("intraday_refresh_time") or "")
        errors = _read_jsonl(sdir / "errors.jsonl")
        intraday = _intraday_events(errors)
        api_errs = _api_errors(errors)

        pair = _find_refresh_pair(intraday, refresh_time)
        started, failed, completed = pair["started"], pair["failed"], pair["completed"]

        refresh_csv = Path(str(summ.get("intraday_refresh_csv") or ""))
        refresh_csv_exists = refresh_csv.is_file()
        refresh_csv_rows = _csv_row_count(refresh_csv) if refresh_csv_exists else None

        # Candidates=0 / register_count=0 branches: infer from failure reason and meta payload.
        fail_reason = str((failed or {}).get("reason") or "")
        merge_meta = (failed or completed or {}).get("merge")
        reg_meta = (failed or completed or {}).get("register")

        # API response / call traces: include api_error entries around refresh time.
        # Keep minimal: only intraday_refresh* related ops, plus push errors if any.
        api_related = [
            e
            for e in api_errs
            if "intraday_refresh" in str(e.get("operation") or "")
        ]

        audit = {
            "session_id": sdir.relative_to(SMALL_PAPER).as_posix(),
            "session_dir": str(sdir),
            "summary": {
                "generated_at": summ.get("generated_at"),
                "ended_at": summ.get("ended_at"),
                "stop_reason": summ.get("stop_reason"),
                "intraday_refresh_enabled": summ.get("intraday_refresh_enabled"),
                "intraday_refresh_csv": str(refresh_csv) if str(refresh_csv) else None,
                "intraday_refresh_time": refresh_time or None,
                "intraday_refresh_triggered_count": summ.get("intraday_refresh_triggered_count"),
                "intraday_refresh_completed_count": summ.get("intraday_refresh_completed_count"),
                "intraday_refresh_failed_count": summ.get("intraday_refresh_failed_count"),
                "intraday_refresh_last_time": summ.get("intraday_refresh_last_time"),
                "intraday_refresh_last_register_count": summ.get("intraday_refresh_last_register_count"),
            },
            "checks": {
                "1_refresh_start_exception_log": failed is not None,
                "1_refresh_start_exception_event": failed,
                "2_refresh_universe_csv_exists": refresh_csv_exists,
                "3_refresh_universe_csv_row_count": refresh_csv_rows,
                "4_register_symbols_called_inferred": _infer_register_called(failed, completed),
                "5_register_symbols_return_inferred": _infer_register_return(failed, completed),
                "6_kabu_api_response_related_errors": api_related[:10],
                "7_candidate_count_zero_inferred": fail_reason == "register_count_zero",
                "8_register_count_zero_branch": {
                    "hit": fail_reason == "register_count_zero",
                    "failure_reason": fail_reason or None,
                    "register_meta": reg_meta,
                    "merge_meta": merge_meta,
                },
            },
        }
        audits.append(audit)

        if int(summ.get("intraday_refresh_failed_count") or 0) > 0 or (failed is not None):
            failed_sessions.append(
                {
                    "session_id": audit["session_id"],
                    "refresh_time": refresh_time or None,
                    "failed_reason": fail_reason or None,
                    "refresh_csv_exists": refresh_csv_exists,
                    "refresh_csv_rows": refresh_csv_rows,
                    "register_called_inferred": audit["checks"]["4_register_symbols_called_inferred"],
                    "register_return_inferred": audit["checks"]["5_register_symbols_return_inferred"],
                    "intraday_event_started": started,
                    "intraday_event_failed": failed,
                    "intraday_event_completed": completed,
                }
            )

    report = {
        "phase": 242,
        "mode": "intraday_refresh_root_cause",
        "constraints": {
            "review_only": True,
            "production_logic_change_forbidden": True,
            "entry_change_forbidden": True,
            "yaml_change_forbidden": True,
            "fix_forbidden_until_root_cause": True,
        },
        "population": {
            "sessions_with_intraday_refresh_enabled": len(audits),
            "sessions_with_refresh_failed": len(failed_sessions),
        },
        "failed_sessions": failed_sessions,
        "audits": audits,
        "notes": [
            "intraday_refresh events are logged into errors.jsonl with error_type='intraday_refresh'.",
            "register_symbols() call is inferred from intraday failure reason; no code changes made.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} enabled={len(audits)} failed={len(failed_sessions)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

