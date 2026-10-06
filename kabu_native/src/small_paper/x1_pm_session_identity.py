"""PM summary source-of-truth identity across restart segments.

Does not rewrite this session's X1 book. Attaches sibling PM session ids so a
recovered-session Discord summary cannot be mistaken for the full afternoon.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _session_kind(summary: Mapping[str, Any], cfg: Mapping[str, Any]) -> str:
    for blob in (summary, cfg):
        am_pm = blob.get("am_pm_session") if isinstance(blob.get("am_pm_session"), Mapping) else {}
        kind = str(am_pm.get("kind") or blob.get("session_kind") or "").strip().lower()
        if kind in {"am", "pm"}:
            return kind
    return ""


def _x1_counts(summary: Mapping[str, Any]) -> dict[str, int]:
    book = summary.get("x1_executor") if isinstance(summary.get("x1_executor"), Mapping) else {}
    perf = summary.get("x1_performance") if isinstance(summary.get("x1_performance"), Mapping) else {}
    return {
        "entry": int(book.get("x1_entry_n") or perf.get("entry_n") or 0),
        "exit": int(book.get("x1_exit_n") or perf.get("exit_n") or 0),
        "open": int(book.get("x1_open_n") or perf.get("open_n") or 0),
        "latched": int(book.get("x1_full_latched_n") or 0),
        "admission": int(book.get("x1_admission_n") or 0),
        "push": int(summary.get("push_messages") or 0),
        "gate": int(summary.get("gate_evaluations") or 0),
    }


def list_same_day_pm_segments(session_dir: Path) -> list[dict[str, Any]]:
    parent = Path(session_dir).resolve().parent
    if not parent.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(parent.glob("live_session_*")):
        if not path.is_dir():
            continue
        summary = _load_json(path / "small_paper_summary.json")
        cfg = _load_json(path / "live_session_config.json")
        if not summary and not cfg:
            rows.append(
                {
                    "dir": path.name,
                    "session_id": path.name,
                    "kind": "",
                    "empty_dir": not any(path.iterdir()),
                    "entry": 0,
                    "exit": 0,
                    "open": 0,
                    "latched": 0,
                    "admission": 0,
                    "push": 0,
                    "gate": 0,
                    "ended_at": "",
                    "stop_reason": "",
                }
            )
            continue
        kind = _session_kind(summary, cfg)
        if kind and kind != "pm":
            continue
        counts = _x1_counts(summary)
        rows.append(
            {
                "dir": path.name,
                "session_id": str(summary.get("session_id") or path.name),
                "kind": kind or "pm",
                "empty_dir": False,
                "ended_at": str(summary.get("ended_at") or ""),
                "stop_reason": str(summary.get("stop_reason") or ""),
                **counts,
            }
        )
    return rows


def attach_pm_summary_identity(summary: dict[str, Any], session_dir: Optional[Path]) -> dict[str, Any]:
    """Stamp source session + sibling PM segments. Leaves x1_entry_n unchanged."""
    identity: dict[str, Any] = {
        "summary_source_session_id": str(summary.get("session_id") or ""),
        "full_pm_summary": False,
        "SUMMARY_SCOPE": "SEGMENT",
        "SOURCE_SESSIONS": [],
        "FULL_PM_VALID": False,
        "OPERATIONALLY_INVALID": True,
        "pm_session_segments": [],
        "pm_day_aggregate": {
            "entry": int(((summary.get("x1_executor") or {}) if isinstance(summary.get("x1_executor"), Mapping) else {}).get("x1_entry_n") or 0),
            "exit": int(((summary.get("x1_executor") or {}) if isinstance(summary.get("x1_executor"), Mapping) else {}).get("x1_exit_n") or 0),
            "open": int(((summary.get("x1_executor") or {}) if isinstance(summary.get("x1_executor"), Mapping) else {}).get("x1_open_n") or 0),
            "segment_n": 1,
        },
        "submit_cancel_live": "0/0/0",
    }
    if session_dir is None:
        summary.update(identity)
        return identity
    segments = [
        row
        for row in list_same_day_pm_segments(Path(session_dir))
        if not row.get("empty_dir")
    ]
    identity["pm_session_segments"] = segments
    current = Path(session_dir).name
    others = [row for row in segments if row.get("dir") != current]
    identity["full_pm_summary"] = len(others) == 0
    stop = str(summary.get("stop_reason") or "")
    identity["SUMMARY_SCOPE"] = "FULL_PM" if identity["full_pm_summary"] else "SEGMENT"
    identity["SOURCE_SESSIONS"] = [str(row.get("session_id") or row.get("dir") or "") for row in segments]
    identity["FULL_PM_VALID"] = bool(
        identity["full_pm_summary"] and stop == "afternoon_session_close"
    )
    identity["OPERATIONALLY_INVALID"] = bool(not identity["FULL_PM_VALID"])
    identity["pm_day_aggregate"] = {
        "entry": sum(int(row.get("entry") or 0) for row in segments),
        "exit": sum(int(row.get("exit") or 0) for row in segments),
        "open": sum(int(row.get("open") or 0) for row in segments),
        "latched": sum(int(row.get("latched") or 0) for row in segments),
        "admission": sum(int(row.get("admission") or 0) for row in segments),
        "segment_n": len(segments),
    }
    if not identity["summary_source_session_id"]:
        identity["summary_source_session_id"] = str(summary.get("session_id") or current)
    summary["summary_source_session_id"] = identity["summary_source_session_id"]
    summary["full_pm_summary"] = identity["full_pm_summary"]
    summary["pm_session_segments"] = identity["pm_session_segments"]
    summary["pm_day_aggregate"] = identity["pm_day_aggregate"]
    summary["SUMMARY_SCOPE"] = identity["SUMMARY_SCOPE"]
    summary["SOURCE_SESSIONS"] = identity["SOURCE_SESSIONS"]
    summary["FULL_PM_VALID"] = identity["FULL_PM_VALID"]
    summary["OPERATIONALLY_INVALID"] = identity["OPERATIONALLY_INVALID"]
    return identity
