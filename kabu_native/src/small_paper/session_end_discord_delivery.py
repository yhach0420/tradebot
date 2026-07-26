"""AM/PM/Daily session-end Discord delivery with HTTP flush (Paper observe-only).

enqueue ≠ sent. Only HTTP 2xx after worker.stop(flush) may mark discord="sent".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from notify.discord_notification_router import get_router
from small_paper.discord_notifier import notify_discord_session_end

# Bounded flush for session-end subprocess — must not reintroduce Paper hang risk.
DEFAULT_FLUSH_SEC = 25.0


def resolve_session_id(
    summary: Mapping[str, Any],
    *,
    output_dir: Optional[Path] = None,
    session_id: str = "",
) -> str:
    """Never return empty — empty dedupe keys are forbidden."""
    sid = str(session_id or summary.get("session_id") or "").strip()
    if sid:
        return sid
    if output_dir is not None:
        name = Path(output_dir).name.strip()
        if name:
            return name
    day = str(
        summary.get("trading_date") or summary.get("day_stamp") or summary.get("output_date") or ""
    ).replace("-", "")[:8]
    kind = ""
    am_pm = summary.get("am_pm_session")
    if isinstance(am_pm, Mapping):
        kind = str(am_pm.get("kind") or "").strip().lower()
    stop = str(summary.get("stop_reason") or "")
    if not kind:
        if stop == "morning_session_close":
            kind = "am"
        elif stop == "afternoon_session_close":
            kind = "pm"
        else:
            kind = "session"
    if day:
        return f"{day}_{kind}"
    return f"unknown_{kind or 'session'}"


def expected_session_end_dedupe_keys(
    summary: Mapping[str, Any],
    *,
    output_dir: Optional[Path] = None,
    session_id: str = "",
) -> dict[str, str]:
    """Paper summary + shadow stable keys expected from notify_discord_session_end."""
    from small_paper.shadow_summary_runtime_hook import SHADOW_NAME_COMPOSITE, session_kind_am_pm

    day = str(
        summary.get("trading_date") or summary.get("day_stamp") or summary.get("output_date") or ""
    ).replace("-", "")[:8]
    if not day:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        day = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d")
    stop = str(summary.get("stop_reason") or "")
    kind = session_kind_am_pm(summary)
    if not kind:
        if stop == "morning_session_close":
            kind = "am"
        elif stop == "afternoon_session_close":
            kind = "pm"
    if kind == "am":
        paper_key = f"am_summary|{day}"
    elif kind == "pm":
        paper_key = f"pm_summary|{day}"
    else:
        paper_key = f"daily_summary|{day}"
    sid = resolve_session_id(summary, output_dir=output_dir, session_id=session_id)
    out: dict[str, str] = {"paper": paper_key}
    if kind in ("am", "pm"):
        out["shadow"] = f"{day}|{sid}|{kind.upper()}|{SHADOW_NAME_COMPOSITE}"
    return out


def _latest_status_by_dedupe(
    native_root: Path,
    keys: Sequence[str],
    *,
    trading_date: str = "",
) -> dict[str, dict[str, Any]]:
    """Scan notification_events.jsonl for latest status per dedupe_key."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    day = str(trading_date or "").replace("-", "")[:8]
    if not day:
        day = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d")
    path = Path(native_root) / "results" / "notifications" / day / "notification_events.jsonl"
    wanted = {str(k) for k in keys if k}
    latest: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return latest
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                dk = str(row.get("dedupe_key") or "")
                if dk in wanted:
                    latest[dk] = row
    except Exception:
        return latest
    return latest


def deliver_session_end_discord(
    *,
    discord: Any,
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    native_root: Path,
    output_dir: Path,
    flush_sec: float = DEFAULT_FLUSH_SEC,
    session_id: str = "",
) -> dict[str, Any]:
    """Shared AM/PM/Daily path: notify → flush → HTTP-confirmed statuses only."""
    native_root = Path(native_root)
    output_dir = Path(output_dir)
    sid = resolve_session_id(summary, output_dir=output_dir, session_id=session_id)
    # Ensure downstream shadow hook sees a non-empty session_id without mutating caller
    # if they already have one; use a shallow copy for the notify call.
    summary_for_notify = dict(summary)
    if not str(summary_for_notify.get("session_id") or "").strip():
        summary_for_notify["session_id"] = sid

    expected = expected_session_end_dedupe_keys(
        summary_for_notify, output_dir=output_dir, session_id=sid
    )

    notify_discord_session_end(
        discord,
        events=events,
        summary=summary_for_notify,
        native_root=native_root,
        output_dir=output_dir,
    )

    router = get_router(native_root)
    flush = router.worker.stop(flush_sec=float(flush_sec))
    day = str(
        summary_for_notify.get("trading_date")
        or summary_for_notify.get("day_stamp")
        or ""
    ).replace("-", "")[:8]
    latest = _latest_status_by_dedupe(
        native_root, list(expected.values()), trading_date=day
    )

    per_key: dict[str, dict[str, Any]] = {}
    all_sent = True
    any_timeout = bool(flush.get("timed_out"))
    any_failed = False
    for label, key in expected.items():
        row = latest.get(key) or {}
        st = str(row.get("status") or "MISSING")
        http = row.get("http_status")
        ok = st == "SENT" and int(http or 0) >= 200 and int(http or 0) < 300
        if not ok:
            all_sent = False
        if st in ("FAILED", "TIMEOUT", "KILLED"):
            any_failed = True
        if st == "TIMEOUT":
            any_timeout = True
        per_key[label] = {
            "dedupe_key": key,
            "status": st,
            "http_status": http,
            "notification_id": row.get("notification_id"),
            "ok": ok,
        }

    if all_sent and expected:
        discord_status = "sent"
        ok = True
    elif any_timeout:
        discord_status = "timeout"
        ok = False
    elif any_failed:
        discord_status = "failed"
        ok = False
    elif any(v.get("status") == "QUEUED" for v in per_key.values()):
        discord_status = "queued"
        ok = False
    else:
        discord_status = "failed"
        ok = False

    return {
        "ok": ok,
        "discord": discord_status,
        "session_id": sid,
        "expected_keys": expected,
        "per_key": per_key,
        "flush": flush,
        "queue_remaining": int(flush.get("remaining") or 0),
        "worker_alive": bool(flush.get("worker_alive")),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
    }
