"""Phase687W11A/W31 — Paper vs Capture kabu registration ownership.

SINGLE_INGRESS_LOCAL_FANOUT:
  Paper owns Kabu Station registration (WebSocket ingress).
  Capture is a localhost fanout consumer — never registration owner.

Do not treat CAPTURE_READY_FOR_FANOUT / RECEIVING / WRITING via paper_fanout
as registration owners. Status strings alone are insufficient.
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

from small_paper.market_capture_topology import TOPOLOGY_PASSIVE_DUAL, TOPOLOGY_SINGLE_INGRESS

JST = ZoneInfo("Asia/Tokyo")
log = logging.getLogger("kabu_native.registration_lifetime")

HEARTBEAT_FRESH_SEC = 90.0
LIVE_PROVENANCE = "LIVE_KABU_PUSH_CAPTURE"
AUDIT_REASON = "PAPER_UNREGISTER_DEFERRED_CAPTURE_ACTIVE"

# Topologies where Capture consumes Paper fanout and must NOT own Station register.
_FANOUT_CONSUMER_TOPOLOGIES = frozenset(
    {
        TOPOLOGY_SINGLE_INGRESS,
        "SINGLE_INGRESS_LOCAL_FANOUT",
        "PAPER_FANOUT",
        "SINGLE_INGRESS",
    }
)
_FANOUT_INGRESS = frozenset({"paper_fanout", "local_fanout", "PAPER_FANOUT", "LOCAL_FANOUT"})


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
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _topology_of(*sources: Mapping[str, Any]) -> str:
    for src in sources:
        if not src:
            continue
        for key in ("topology", "capture_topology", "push_topology"):
            val = str(src.get(key) or "").strip()
            if val:
                return val.upper()
    return ""


def _ingress_of(*sources: Mapping[str, Any]) -> str:
    for src in sources:
        if not src:
            continue
        for key in ("ingress", "push_ingress", "capture_ingress"):
            val = str(src.get(key) or "").strip()
            if val:
                return val
    return ""


def capture_is_fanout_consumer(
    *,
    topology: str = "",
    ingress: str = "",
    cap_status: str = "",
) -> bool:
    """True when Capture is localhost fanout consumer (Paper owns Station register)."""
    topo = str(topology or "").upper()
    ing = str(ingress or "").strip()
    if topo in {t.upper() for t in _FANOUT_CONSUMER_TOPOLOGIES}:
        return True
    if ing in _FANOUT_INGRESS or "fanout" in ing.lower():
        return True
    # READY_FOR_FANOUT without PASSIVE_DUAL direct ingress ⇒ consumer posture
    st = str(cap_status or "")
    if "READY_FOR_FANOUT" in st and TOPOLOGY_PASSIVE_DUAL.upper() not in topo:
        return True
    return False


def is_live_capture_registration_owner_active(
    native_root: Path,
    *,
    trading_date: Optional[str] = None,
    now: Optional[datetime] = None,
    heartbeat_fresh_sec: float = HEARTBEAT_FRESH_SEC,
) -> CaptureActiveDecision:
    """Return whether Capture currently owns Kabu Station registration.

    Phase687W31: topology + ingress decide ownership. Status alone is not enough.
    """
    from small_paper.market_capture_registration import trading_date_jst
    from small_paper.market_capture_sidecar import (
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

    from small_paper.market_capture_registration import read_registration_manifest

    reg = read_registration_manifest(Path(native_root))
    symbols = list(reg.get("registered_symbols") or man.get("registered_symbols") or [])
    n = len(symbols)

    pid = 0
    try:
        if pid_path.is_file():
            pid = int((pid_path.read_text(encoding="utf-8") or "0").strip() or 0)
    except Exception:
        pid = int(man.get("pid") or status.get("pid") or hb.get("pid") or 0)
    if not pid:
        pid = int(man.get("pid") or status.get("pid") or hb.get("pid") or 0)

    cap_status = str(status.get("capture_status") or hb.get("status") or "")
    topology = _topology_of(status, man, hb, reg)
    ingress = _ingress_of(status, man, hb, reg)
    base_details = {
        "symbol_count": n,
        "day_dir": str(day_dir),
        "topology": topology or None,
        "ingress": ingress or None,
        "capture_status": cap_status or None,
    }

    # Phase687W31 primary rule: fanout consumer never owns Station register.
    if capture_is_fanout_consumer(topology=topology, ingress=ingress, cap_status=cap_status):
        return CaptureActiveDecision(
            False,
            "paper_owns_register_fanout_consumer",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            pid=pid or None,
            capture_status=cap_status,
            details=base_details,
        )

    # Explicit non-owner statuses (still require topology check above first).
    if "READY_FOR_FANOUT" in cap_status or "PLANNED_FOLLOWER" in cap_status:
        return CaptureActiveDecision(
            False,
            f"capture_not_registration_owner:{cap_status or 'empty'}",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            pid=pid or None,
            capture_status=cap_status,
            details=base_details,
        )

    applied = status.get("applied")
    if applied is None:
        applied = man.get("applied")
    if applied is None:
        applied = reg.get("applied")
    verified = status.get("registration_verified")
    if verified is None:
        verified = man.get("registration_verified")
    if verified is None:
        verified = reg.get("registration_verified")

    # Legacy PASSIVE_DUAL direct socket: owner only if Capture actually applied register.
    topo_u = topology.upper()
    is_passive_dual = TOPOLOGY_PASSIVE_DUAL.upper() in topo_u or topo_u == "PASSIVE_DUAL"
    if not is_passive_dual:
        # Unknown / missing topology with live Capture → Paper remains register SoT
        # unless Capture explicitly applied a direct-socket registration.
        if applied is not True:
            return CaptureActiveDecision(
                False,
                "capture_not_direct_ingress_owner",
                trading_date=day,
                capture_session_id=str(man.get("capture_session_id") or ""),
                pid=pid or None,
                capture_status=cap_status,
                details={**base_details, "applied": applied, "registration_verified": verified},
            )

    if applied is not True:
        return CaptureActiveDecision(
            False,
            "capture_registration_not_applied",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            pid=pid or None,
            capture_status=cap_status,
            details={**base_details, "applied": applied, "registration_verified": verified},
        )

    if not _pid_alive(pid):
        return CaptureActiveDecision(
            False,
            "pid_not_alive",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            pid=pid or None,
            capture_status=cap_status,
            details=base_details,
        )

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
            details={**base_details, "heartbeat_age_sec": age, "fresh_sec": heartbeat_fresh_sec},
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
                details={**base_details, "scheduled_end_at": man.get("scheduled_end_at")},
            )

    if n < 1 or n > 50:
        return CaptureActiveDecision(
            False,
            "expected_symbols_out_of_range",
            trading_date=day,
            capture_session_id=str(man.get("capture_session_id") or ""),
            details={**base_details, "symbol_count": n},
        )

    return CaptureActiveDecision(
        True,
        "capture_passive_dual_direct_owner",
        trading_date=day,
        capture_session_id=str(man.get("capture_session_id") or ""),
        registration_generation=str(man.get("registration_generation") or reg.get("generation_id") or ""),
        pid=pid,
        capture_status=cap_status,
        details={**base_details, "applied": applied, "registration_verified": verified},
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
