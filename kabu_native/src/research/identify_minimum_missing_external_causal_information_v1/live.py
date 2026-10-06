"""Read-only 20260914 capture recheck. Do not restart a completed AM window. 0/0/0."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import _tail_jsonl_last
from research.causal_path_to_complete_strategy_v1.live_status import (
    _count_lines,
    _pid_alive,
    inspect_live_20260914,
)


def _latest_received(path_str: str | None) -> dict[str, Any]:
    from pathlib import Path

    path = Path(path_str or "")
    rec = _tail_jsonl_last(path) if path.is_file() else {}
    return {
        "received_at": rec.get("received_at"),
        "CurrentPriceTime": rec.get("CurrentPriceTime"),
        "CurrentPrice": rec.get("CurrentPrice"),
        "resolved_symbol": rec.get("resolved_symbol") or rec.get("Symbol"),
        "keys": sorted(rec.keys())[:24] if rec else [],
    }


def inspect_live_now() -> dict[str, Any]:
    base = inspect_live_20260914()
    fut = dict(base.get("futures") or {})
    nk = dict(fut.get("NK225mini") or {})
    tx = dict(fut.get("TOPIX") or {})
    nk_tail = _latest_received(nk.get("path"))
    tx_tail = _latest_received(tx.get("path"))
    br = dict(base.get("breadth") or {})
    from pathlib import Path

    raw = Path(str(br.get("raw_dir") or ""))
    exit_path = raw / "breadth_collector_exit.json"
    exit_body = {}
    if exit_path.is_file():
        import json

        try:
            exit_body = json.loads(exit_path.read_text(encoding="utf-8"))
        except Exception:
            exit_body = {}
    snap = dict(br.get("snapshot") or {})
    ranking_files = list(raw.glob("ranking_type*.jsonl")) if raw.is_dir() else []
    ranking_lines = {p.name: _count_lines(p) for p in ranking_files}
    fut_alive = bool(base.get("futures_capture_running"))
    br_alive = bool(base.get("breadth_capture_running"))
    fut_completed = (not fut_alive) and bool((fut.get("NK225mini") or {}).get("exists"))
    br_completed = (not br_alive) and bool(exit_body.get("clean_exit"))
    recovery = {
        "attempted": False,
        "reason": "completed_by_design_am_window" if (fut_completed or br_completed) else "not_needed_if_alive",
        "did_not_restart_healthy_or_completed": True,
        "did_not_sendorder": True,
    }
    if (not fut_alive) and (not br_alive) and not (fut_completed or br_completed):
        recovery = {
            "attempted": False,
            "reason": "unhealthy_but_outside_0755_1130_start_window_no_unsafe_restart",
            "did_not_restart_healthy_or_completed": True,
            "did_not_sendorder": True,
        }
    return {
        **base,
        "rechecked_now": True,
        "previous_report_was_around_1023_jst": True,
        "futures_pid": fut.get("file_pid") or (list(fut.get("pids") or [None])[0] if fut.get("pids") else None),
        "futures_alive": fut_alive,
        "futures_pid_alive": bool(fut.get("file_pid_alive")),
        "nk225mini_latest_timestamp": nk_tail.get("received_at"),
        "nk225mini_row_count": nk.get("received_count"),
        "nk225mini_tail": nk_tail,
        "topix_latest_timestamp": tx_tail.get("received_at"),
        "topix_row_count": tx.get("received_count"),
        "topix_tail": tx_tail,
        "breadth_pid": br.get("launcher_pid") or (list(br.get("pids") or [None])[0] if br.get("pids") else None),
        "breadth_alive": br_alive,
        "breadth_latest_snapshot": snap.get("latest_mtime") or snap.get("latest"),
        "breadth_snapshot_count": snap.get("n") or len(ranking_files),
        "breadth_ranking_lines": ranking_lines,
        "breadth_exit": exit_body,
        "breadth_cycles": exit_body.get("cycles"),
        "submit_cancel_live": "0/0/0",
        "recovery": recovery,
        "classification": "PROSPECTIVE_CAPTURE_20260914_AM_WINDOW_ENDED_BY_DESIGN"
        if (not fut_alive and not br_alive)
        else base.get("classification"),
        "fed_into_discovery_tuning": False,
        "used_to_redesign_hm1": False,
    }


def pid_alive(pid: int | None) -> bool:
    return _pid_alive(pid)
