"""Phase687W11A — Paper must not unregister_all while Capture Sidecar owns the day.

Capture Sidecar continues until 15:35 JST. Paper AM/PM exit/reconnect must not
clear the shared Kabu PUSH registration list while live capture is active.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
log = logging.getLogger("kabu_native.registration_lifetime")

HEARTBEAT_FRESH_SEC = 90.0
LIVE_PROVENANCE = "LIVE_KABU_PUSH_CAPTURE"
AUDIT_REASON = "PAPER_UNREGISTER_DEFERRED_CAPTURE_ACTIVE"


@dataclass
class CaptureActiveDecision:
    active: bool
    reason: str
    trading_date: str = ""
    capture_session_id: str = ""
    registration_generation: str = ""
    pid: Optional[int] = None
    capture_status: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason": self.reason,
            "trading_date": self.trading_date,
            "capture_session_id": self.capture_session_id,
            "registration_generation": self.registration_generation,
            "pid": self.pid,
            "capture_status": self.capture_status,
            "details": dict(self.details),
        }


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* appears alive.

    On Windows, ``os.kill(pid, 0)`` is *not* a liveness probe — signal 0 is
    ``CTRL_C_EVENT`` and will interrupt the current process group. Use
    ``OpenProcess`` instead (same pattern as market_capture_sidecar).
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # support ...+09:00 and naive
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def is_live_capture_registration_owner_active(
    native_root: Path,
    *,
    trading_date: Optional[str] = None,
    now: Optional[datetime] = None,
    heartbeat_fresh_sec: float = HEARTBEAT_FRESH_SEC,
) -> CaptureActiveDecision:
    """Return whether live Capture Sidecar currently owns registration for the day."""
    from small_paper.market_capture_registration import trading_date_jst
    from small_paper.market_capture_sidecar import (
        CAPTURE_DEGRADED,
        CAPTURE_ONLINE,
        CAPTURE_REGISTRATION_MISMATCH,
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )

    day = trading_date or trading_date_jst(now)
    now_jst = now or datetime.now(JST)
    if now_jst.tzinfo is None:
        now_jst = now_jst.replace(tzinfo=JST)
    else:
        now_jst = now_jst.astimezone(JST)

    day_dir = capture_day_dir(Path(native_root), day)
    man = _read_json(day_dir / MANIFEST_FILE)
    status = _read_json(day_dir / STATUS_FILE)
    hb = _read_json(day_dir / HEARTBEAT_FILE)
    pid_path = day_dir / PID_FILE_NAME

    if not man:
        return CaptureActiveDecision(False, "capture_manifest_missing", trading_date=day)

    man_day = str(man.get("trading_date") or "")
    if man_day and man_day != day:
        return CaptureActiveDecision(
            False,
            "trading_date_mismatch",
            trading_date=day,
            details={"manifest_trading_date": man_day},
        )

    if bool(man.get("fixture")) or bool(man.get("synthetic")) or bool(man.get("test_mode")):
        return CaptureActiveDecision(
            False,
            "fixture_or_synthetic",
            trading_date=day,
            details={
                "fixture": man.get("fixture"),
                "synthetic": man.get("synthetic"),
                "test_mode": man.get("test_mode"),
                "provenance": man.get("provenance"),
            },
        )
    if str(man.get("provenance") or "") != LIVE_PROVENANCE:
        return CaptureActiveDecision(
            False,
            "provenance_not_live",
            trading_date=day,
            details={"provenance": man.get("provenance")},
        )

    # registration manifest expected symbols
    from small_paper.market_capture_registration import read_registration_manifest

    reg = read_registration_manifest(Path(native_root))
    symbols = list(reg.get("registered_symbols") or man.get("registered_symbols") or [])
    n = len(symbols)
    if n < 1 or n > 50:
        return CaptureActiveDecision(
            False,
            "expected_symbols_out_of_range",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            details={"symbol_count": n},
        )
    if not reg and not (day_dir / "registration_manifest.json").is_file():
        # allow capture-day copy
        if not symbols:
            return CaptureActiveDecision(False, "registration_manifest_missing", trading_date=day)

    pid = 0
    try:
        if pid_path.is_file():
            pid = int((pid_path.read_text(encoding="utf-8") or "0").strip() or 0)
    except Exception:
        pid = int(man.get("pid") or status.get("pid") or hb.get("pid") or 0)
    if not pid:
        pid = int(man.get("pid") or status.get("pid") or hb.get("pid") or 0)
    if not _pid_alive(pid):
        return CaptureActiveDecision(
            False,
            "pid_not_alive",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            pid=pid or None,
        )

    # heartbeat freshness (mtime or payload at)
    hb_path = day_dir / HEARTBEAT_FILE
    age = None
    if hb_path.is_file():
        try:
            age = time.time() - hb_path.stat().st_mtime
        except Exception:
            age = None
    if age is None or age > float(heartbeat_fresh_sec):
        return CaptureActiveDecision(
            False,
            "stale_heartbeat",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            pid=pid,
            details={"heartbeat_age_sec": age, "fresh_sec": heartbeat_fresh_sec},
        )

    sched = _parse_iso(str(man.get("scheduled_end_at") or ""))
    if sched is not None:
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=JST)
        else:
            sched = sched.astimezone(JST)
        if now_jst > sched:
            return CaptureActiveDecision(
                False,
                "past_scheduled_end",
                trading_date=day,
                capture_session_id=str(man.get("capture_session_id") or ""),
                pid=pid,
                details={"scheduled_end_at": man.get("scheduled_end_at")},
            )

    cap_status = str(status.get("capture_status") or hb.get("status") or "")
    ok_statuses = {CAPTURE_ONLINE, CAPTURE_DEGRADED, CAPTURE_REGISTRATION_MISMATCH, "ONLINE", "DEGRADED"}
    # Also accept empty status if heartbeat fresh + pid alive (startup race)
    if cap_status and cap_status not in ok_statuses and not cap_status.startswith("CAPTURE_ONLINE"):
        # FINISHED / COMPLETE → inactive
        if "COMPLETE" in cap_status or "FINISHED" in cap_status or "NO_MARKET" in cap_status:
            return CaptureActiveDecision(
                False,
                f"capture_status={cap_status}",
                trading_date=day,
                capture_session_id=str(man.get("capture_session_id") or ""),
                pid=pid,
                capture_status=cap_status,
            )

    return CaptureActiveDecision(
        True,
        "capture_active",
        trading_date=day,
        capture_session_id=str(man.get("capture_session_id") or ""),
        registration_generation=str(man.get("registration_generation") or reg.get("generation_id") or ""),
        pid=pid,
        capture_status=cap_status or CAPTURE_ONLINE,
        details={"symbol_count": n, "day_dir": str(day_dir)},
    )


def should_defer_paper_unregister(
    native_root: Path,
    *,
    trading_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> CaptureActiveDecision:
    return is_live_capture_registration_owner_active(
        native_root, trading_date=trading_date, now=now
    )


def _audit_defer(
    native_root: Path,
    decision: CaptureActiveDecision,
    *,
    paper_session_id: str = "",
    am_pm: str = "",
    path_label: str = "",
) -> Path:
    day = decision.trading_date or datetime.now(JST).strftime("%Y%m%d")
    out = Path(native_root) / "results" / "notifications" / day
    out.mkdir(parents=True, exist_ok=True)
    path = out / "paper_unregister_deferred.jsonl"
    row = {
        "reason": AUDIT_REASON,
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "capture_session_id": decision.capture_session_id,
        "registration_generation": decision.registration_generation,
        "paper_session_id": paper_session_id,
        "am_pm": am_pm,
        "path_label": path_label,
        "decision": decision.to_dict(),
    }
    try:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return path


def safe_paper_unregister(
    push: Any,
    *,
    native_root: Path,
    trading_date: Optional[str] = None,
    paper_session_id: str = "",
    am_pm: str = "",
    path_label: str = "",
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Call unregister_all only when Capture is NOT the active registration owner.
    Never raises into Paper finalize.
    """
    try:
        decision = should_defer_paper_unregister(native_root, trading_date=trading_date, now=now)
        if decision.active:
            _audit_defer(
                native_root,
                decision,
                paper_session_id=paper_session_id,
                am_pm=am_pm,
                path_label=path_label,
            )
            log.info(
                "unregister_all deferred: capture active session=%s reason=%s",
                decision.capture_session_id,
                decision.reason,
            )
            return {
                "ok": True,
                "deferred": True,
                "unregister_all_called": False,
                "reason": AUDIT_REASON,
                "decision": decision.to_dict(),
            }
        if push is None:
            return {"ok": False, "deferred": False, "unregister_all_called": False, "error": "no_push"}
        push.unregister_all()
        return {
            "ok": True,
            "deferred": False,
            "unregister_all_called": True,
            "reason": decision.reason,
            "decision": decision.to_dict(),
        }
    except Exception as exc:
        log.warning("safe_paper_unregister failed (fail-open): %s", type(exc).__name__)
        return {
            "ok": False,
            "deferred": False,
            "unregister_all_called": False,
            "error": type(exc).__name__,
        }


def clear_first_allowed_for_register(
    native_root: Path,
    *,
    trading_date: Optional[str] = None,
) -> bool:
    """register_symbols_cleared must not clear when Capture owns registration."""
    return not should_defer_paper_unregister(native_root, trading_date=trading_date).active
