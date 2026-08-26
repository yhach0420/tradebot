"""Phase687W16 — Owned Capture Sidecar child-process cleanup.

Stops only processes spawned by the checked runner (ownership-checked).
Does not stop unrelated / pre-existing capture processes.
Honors paper-block / scheduled-end continue policy (no silent lifecycle change).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

OPERATOR_STOP = "operator_stop.flag"
DEFAULT_GRACEFUL_TIMEOUT_SEC = 12.0
DEFAULT_TERMINATE_TIMEOUT_SEC = 5.0

OWNED_MARKERS = (
    "small_paper.market_capture_sidecar",
    "small_paper.market_capture_supervisor",
    "small_paper.market_ingress_service",
)


@dataclass
class OwnedCaptureProcess:
    pid: int
    cmd: list[str] = field(default_factory=list)
    output_dir: str = ""
    native_root: str = ""
    trading_date: str = ""
    synthetic: bool = False
    supervised: bool = False
    spawned_at: str = ""
    spawn_mono: float = 0.0
    create_time: str = ""
    parent_pid_at_spawn: int = 0
    cmdline_fingerprint: str = ""
    process_start_identity: str = ""
    component_role: str = ""
    ingress_run_id: str = ""
    launch_nonce: str = ""


@dataclass
class CleanupResult:
    shutdown_reason: str
    capture_pid: Optional[int]
    graceful_stop_requested: bool = False
    graceful_stop_ok: bool = False
    terminate_used: bool = False
    kill_used: bool = False
    remaining_processes: list[int] = field(default_factory=list)
    cleanup_duration_sec: float = 0.0
    ownership_verified: bool = False
    skipped: bool = False
    skip_reason: str = ""
    already_dead: bool = False
    error: str = ""
    ownership_detail: dict[str, Any] = field(default_factory=dict)
    ownership_class: str = ""
    kill_decision: dict[str, Any] = field(default_factory=dict)
    wrong_process_kill: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def cmdline_fingerprint(cmd: Sequence[str]) -> str:
    return " ".join(str(x) for x in cmd)


def query_process(pid: int) -> dict[str, Any]:
    """Best-effort process metadata (Windows-first, no psutil required).

    Important: on Windows, os.kill(pid, 0) is NOT a reliable liveness check
    (Python may succeed after the process has already exited). Prefer CIM/tasklist.
    """
    out: dict[str, Any] = {
        "pid": int(pid),
        "exists": False,
        "cmdline": "",
        "create_time": "",
        "parent_pid": None,
        "name": "",
    }
    if pid <= 0:
        return out
    if sys.platform == "win32":
        try:
            ps = (
                f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" "
                f"-ErrorAction SilentlyContinue; "
                f"if($p){{ "
                f"[PSCustomObject]@{{ProcessId=$p.ProcessId;ParentProcessId=$p.ParentProcessId;"
                f"Name=$p.Name;CommandLine=$p.CommandLine;CreationDate=$p.CreationDate}} "
                f"| ConvertTo-Json -Compress }} else {{ Write-Output '__NONE__' }}"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            raw = (r.stdout or "").strip()
            if raw and raw != "__NONE__" and raw.lower() != "null":
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("ProcessId"):
                    out["exists"] = True
                    out["cmdline"] = str(data.get("CommandLine") or "")
                    out["create_time"] = str(data.get("CreationDate") or "")
                    out["parent_pid"] = data.get("ParentProcessId")
                    out["name"] = str(data.get("Name") or "")
                    return out
            # Explicit none from CIM → not running (do not fall back to os.kill on Windows)
            return out
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}:{exc}"
        # Fallback: tasklist (still avoid os.kill(0) on Windows)
        try:
            r2 = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            body = (r2.stdout or "").strip()
            # Japanese/English "no tasks" messages, or empty
            if body and str(int(pid)) in body and not body.lower().startswith("info:"):
                # CSV line like "python.exe","1234",...
                if f'"{int(pid)}"' in body or f",{int(pid)}," in body.replace(" ", ""):
                    out["exists"] = True
                    out["name"] = body.split(",")[0].strip('"') if "," in body else ""
        except Exception as exc2:
            out["error"] = f"{out.get('error','')};{type(exc2).__name__}:{exc2}"
        return out
    # POSIX
    try:
        os.kill(int(pid), 0)
        out["exists"] = True
    except OSError:
        out["exists"] = False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        out["cmdline"] = cmdline
        out["exists"] = True
    except OSError:
        pass
    return out


def verify_ownership(owned: OwnedCaptureProcess, live: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Confirm pid still belongs to our spawned capture (prevent PID reuse kills)."""
    info = dict(live) if live is not None else query_process(owned.pid)
    detail = {
        "pid": owned.pid,
        "exists": bool(info.get("exists")),
        "cmdline_match": False,
        "native_root_match": False,
        "marker_match": False,
        "create_time_ok": True,
        "owned": False,
        "reason": "",
        "live": {k: info.get(k) for k in ("cmdline", "create_time", "parent_pid", "name")},
    }
    if not info.get("exists"):
        detail["reason"] = "not_running"
        return detail
    cmdline = str(info.get("cmdline") or "")
    marker_ok = any(m in cmdline for m in OWNED_MARKERS) or any(
        m in owned.cmdline_fingerprint for m in OWNED_MARKERS
    )
    # If live cmdline empty (permissions), fall back to fingerprint recorded at spawn.
    if not cmdline.strip():
        marker_ok = any(m in owned.cmdline_fingerprint for m in OWNED_MARKERS)
        cmdline = owned.cmdline_fingerprint
    native = str(owned.native_root or "").replace("/", "\\").lower()
    native_ok = (not native) or (native in cmdline.lower().replace("/", "\\"))
    # create_time: if both present and wildly different epoch, reject (best-effort string compare)
    create_ok = True
    if owned.create_time and info.get("create_time"):
        # Allow equality or prefix match; reject if both non-empty and completely disjoint
        a = str(owned.create_time)
        b = str(info.get("create_time"))
        create_ok = a == b or a[:12] == b[:12]
    detail.update(
        {
            "cmdline_match": owned.cmdline_fingerprint[:80] in cmdline if owned.cmdline_fingerprint else marker_ok,
            "native_root_match": native_ok,
            "marker_match": marker_ok,
            "create_time_ok": create_ok,
        }
    )
    start_ok = True
    if owned.process_start_identity:
        live_start = ""
        try:
            from small_paper.ingress_run_identity import capture_process_start_identity

            live_start = str(capture_process_start_identity(int(owned.pid)) or "")
        except Exception:
            live_start = ""
        if live_start:
            start_ok = live_start == str(owned.process_start_identity)
        detail["process_start_identity_match"] = start_ok
        detail["live_process_start_identity"] = live_start
    owned_ok = bool(marker_ok and native_ok and create_ok and start_ok)
    detail["owned"] = owned_ok
    detail["reason"] = "owned" if owned_ok else "ownership_mismatch"
    return detail


def _owned_identity_doc(owned: OwnedCaptureProcess) -> dict[str, Any]:
    cmd = f"{cmdline_fingerprint(owned.cmd)} {owned.cmdline_fingerprint}"
    role = str(owned.component_role or "").strip()
    if not role:
        if "market_ingress_service" in cmd:
            role = "MARKET_INGRESS_SERVICE"
        elif "market_capture" in cmd:
            role = "MARKET_CAPTURE_SIDECAR"
        else:
            role = "MARKET_INGRESS_SERVICE"
    return {
        "pid": int(owned.pid),
        "owner_pid": int(owned.pid),
        "process_start_identity": str(owned.process_start_identity or ""),
        "component_role": role,
        "owner_role": role,
        "owner": role,
        "caller": "checked_runner_owned_child",
        "ingress_run_id": str(owned.ingress_run_id or ""),
        "launch_nonce": str(owned.launch_nonce or ""),
        "kabu_token_authority": role if role == "MARKET_INGRESS_SERVICE" else "",
    }


def classify_owned_process(owned: OwnedCaptureProcess) -> dict[str, Any]:
    from small_paper.ownership_classifier import classify_owner

    doc = _owned_identity_doc(owned)
    return classify_owner(
        owner=doc,
        bundle=doc,
        current=doc,
        pid_alive_fn=_pid_alive,
    )


def decide_owned_kill(
    owned: OwnedCaptureProcess,
    *,
    identity_proven: bool,
    stale_graceful_done: bool = False,
) -> dict[str, Any]:
    from small_paper.runtime_lifecycle import decide_kill

    classified = classify_owned_process(owned)
    decision = decide_kill(
        classified,
        identity_proven=identity_proven,
        stale_graceful_done=stale_graceful_done,
    )
    decision["classified"] = classified
    return decision


def should_stop_on_shutdown(
    *,
    reason: str,
    paper_blocked_capture_continues: bool = False,
    continuing_until_scheduled_end: bool = False,
    synthetic: bool = False,
    seal_pass: bool = False,
    skip_capture_wait: bool = False,
) -> tuple[bool, str]:
    """Policy: preserve live Paper-block / 15:35 continue; always stop tests / Ctrl+C / exceptions.

    Distinction (do not collapse):
    - Ctrl+C / KeyboardInterrupt → stop owned Capture
    - Paper BLOCK + live runner continue policy → Capture continues to 15:35
    - Paper BLOCK + synthetic/test harness → stop (pytest residual must be 0)
    - Checked Runner normal exit with continuing_until → Capture continues (live)
    - Capture finalize/seal complete or skip_capture_wait → stop/confirm stopped
    """
    r = str(reason or "")
    if r in ("keyboard_interrupt", "exception", "signal", "test_teardown", "force", "atexit_orphan"):
        return True, r
    # Test / synthetic harness always cleans up (even if paper_blocked flag is set).
    if synthetic or skip_capture_wait:
        return True, "synthetic_or_test_stop"
    if seal_pass:
        return True, "seal_complete_stop"
    if paper_blocked_capture_continues or r == "paper_blocked_capture_continues":
        return False, "paper_blocked_capture_continues"
    if continuing_until_scheduled_end:
        try:
            from small_paper.runtime_clock import certification_mode

            if certification_mode():
                return True, "certification_stage_owned_stop"
        except Exception:
            pass
        return False, "capture_continuing_until_scheduled_end"
    if r == "normal_exit":
        return True, "normal_exit"
    return True, r or "default_stop"


def request_graceful_stop(
    output_dir: str | Path,
    *,
    session_id: str = "",
    pid: int = 0,
    reason: str = "operator_stop",
) -> bool:
    out = Path(output_dir) if output_dir else None
    if out is None or not out.is_dir():
        return False
    try:
        lines = [
            "stop",
            f"requested_at={_now_iso()}",
            f"reason={reason}",
        ]
        if session_id:
            lines.append(f"session_id={session_id}")
        if int(pid or 0) > 0:
            lines.append(f"pid={int(pid)}")
        (out / OPERATOR_STOP).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def parse_operator_stop_flag(path: Path | str) -> dict[str, Any]:
    """Parse operator_stop.flag.

    - Bare ``stop`` without timestamp → mtime fallback (age-comparable).
    - ``requested_at=`` present but unparseable → malformed (fail-safe ignore).
    """
    p = Path(path)
    out: dict[str, Any] = {
        "exists": p.is_file(),
        "malformed": False,
        "requested_at": None,
        "requested_at_dt": None,
        "session_id": "",
        "pid": None,
        "reason": "",
        "raw": "",
        "mtime_fallback": False,
    }
    if not p.is_file():
        return out
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=JST)
    except OSError:
        out["malformed"] = True
        return out
    out["raw"] = raw
    has_requested_at_key = False
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.lower() == "stop":
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if k == "requested_at":
            has_requested_at_key = True
            out["requested_at"] = v
            try:
                out["requested_at_dt"] = datetime.fromisoformat(v)
            except ValueError:
                out["malformed"] = True
        elif k == "session_id":
            out["session_id"] = v
        elif k == "pid":
            try:
                out["pid"] = int(v)
            except ValueError:
                out["malformed"] = True
        elif k == "reason":
            out["reason"] = v
    if out["malformed"]:
        return out
    if out["requested_at_dt"] is None and not has_requested_at_key:
        stripped = raw.strip().lower()
        if stripped == "stop" or stripped.startswith("stop\n") or "stop" in stripped.splitlines()[0:1]:
            out["requested_at_dt"] = mtime
            out["requested_at"] = mtime.isoformat(timespec="seconds")
            out["mtime_fallback"] = True
        elif not stripped:
            out["malformed"] = True
        else:
            # Unknown content without stop keyword
            out["malformed"] = True
    if out["requested_at_dt"] is None:
        out["malformed"] = True
    return out


def classify_operator_stop_for_process(
    flag_path: Path | str,
    *,
    process_started_at: datetime,
    process_session_id: str = "",
) -> dict[str, Any]:
    """Only flags created at/after this process start are honored as stop requests."""
    parsed = parse_operator_stop_flag(flag_path)
    result: dict[str, Any] = {
        **{k: v for k, v in parsed.items() if k != "raw"},
        "action": "ignore",
        "classification": "absent",
    }
    if not parsed.get("exists"):
        return result
    if parsed.get("malformed") or parsed.get("requested_at_dt") is None:
        result["classification"] = "malformed"
        result["action"] = "ignore"
        return result
    req = parsed["requested_at_dt"]
    started = process_started_at
    if req.tzinfo is None and started.tzinfo is not None:
        req = req.replace(tzinfo=started.tzinfo)
    elif req.tzinfo is not None and started.tzinfo is None:
        started = started.replace(tzinfo=req.tzinfo)
    # Compare at second resolution: requested_at ISO often lacks microseconds;
    # same-second flags written after start must still count as active.
    if req.replace(microsecond=0) < started.replace(microsecond=0):
        result["classification"] = "stale"
        result["action"] = "ignore"
        return result
    sid = str(parsed.get("session_id") or "")
    if sid and process_session_id and sid != process_session_id:
        result["classification"] = "foreign_session"
        result["action"] = "ignore"
        return result
    result["classification"] = "active"
    result["action"] = "stop"
    return result


def prepare_day_dir_operator_stop_for_spawn(
    day_dir: Path | str,
    *,
    spawn_started_at: Optional[datetime] = None,
    owned_session_id: str = "",
) -> dict[str, Any]:
    """Archive stale/malformed stop flags before spawning a new Capture for this day dir."""
    day = Path(day_dir)
    day.mkdir(parents=True, exist_ok=True)
    flag = day / OPERATOR_STOP
    started = spawn_started_at or datetime.now(JST)
    out: dict[str, Any] = {
        "day_dir": str(day),
        "flag_path": str(flag),
        "action": "none",
        "classification": "absent",
        "archived_to": "",
        "spawn_started_at": started.isoformat(timespec="seconds"),
    }
    if not flag.is_file():
        return out
    parsed = parse_operator_stop_flag(flag)
    out["parsed"] = {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in parsed.items()
        if k != "raw"
    }
    foreign_pid = parsed.get("pid")
    if foreign_pid and int(foreign_pid) > 0:
        live = query_process(int(foreign_pid))
        if live.get("exists"):
            cmdline = str(live.get("cmdline") or "")
            if any(m in cmdline for m in OWNED_MARKERS) or not cmdline.strip():
                out["classification"] = "foreign_live"
                out["action"] = "leave"
                return out

    cls = classify_operator_stop_for_process(
        flag,
        process_started_at=started,
        process_session_id=owned_session_id,
    )
    out["classification"] = cls.get("classification")
    # Stale / malformed / foreign_session / orphan active → archive so new spawn continues
    if cls.get("action") == "ignore" or cls.get("classification") == "active":
        ts = started.strftime("%Y%m%d_%H%M%S")
        dest = day / f"operator_stop.flag.stale_{ts}"
        n = 0
        while dest.exists():
            n += 1
            dest = day / f"operator_stop.flag.stale_{ts}_{n}"
        try:
            flag.replace(dest)
            out["action"] = "archived" if cls.get("classification") != "active" else "archived_orphan_active"
            out["archived_to"] = str(dest)
            if cls.get("classification") == "active":
                out["classification"] = "orphan_active"
        except OSError as exc:
            out["action"] = "archive_failed"
            out["error"] = str(exc)
    return out


def _pid_alive(pid: int) -> bool:
    return bool(query_process(pid).get("exists"))


def _terminate_pid(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            # Soft attempt first (no /F); tree with /T so process-group children are included.
            r = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return r.returncode == 0 or not _pid_alive(pid)
        except Exception:
            return not _pid_alive(pid)
    try:
        os.kill(int(pid), 15)
        return True
    except OSError:
        return not _pid_alive(pid)


def _kill_pid(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return r.returncode == 0 or not _pid_alive(pid)
        except Exception:
            return not _pid_alive(pid)
    try:
        os.kill(int(pid), 9)
        return True
    except OSError:
        return not _pid_alive(pid)


def cleanup_owned_capture(
    owned: Optional[OwnedCaptureProcess],
    *,
    reason: str,
    paper_blocked_capture_continues: bool = False,
    continuing_until_scheduled_end: bool = False,
    seal_pass: bool = False,
    skip_capture_wait: bool = False,
    graceful_timeout_sec: float = DEFAULT_GRACEFUL_TIMEOUT_SEC,
    terminate_timeout_sec: float = DEFAULT_TERMINATE_TIMEOUT_SEC,
) -> CleanupResult:
    t0 = time.time()
    if owned is None or int(owned.pid or 0) <= 0:
        return CleanupResult(
            shutdown_reason=reason,
            capture_pid=None,
            skipped=True,
            skip_reason="no_owned_pid",
            cleanup_duration_sec=round(time.time() - t0, 3),
        )

    stop, policy = should_stop_on_shutdown(
        reason=reason,
        paper_blocked_capture_continues=paper_blocked_capture_continues,
        continuing_until_scheduled_end=continuing_until_scheduled_end,
        synthetic=bool(owned.synthetic),
        seal_pass=seal_pass,
        skip_capture_wait=skip_capture_wait,
    )
    if not stop:
        return CleanupResult(
            shutdown_reason=reason,
            capture_pid=owned.pid,
            skipped=True,
            skip_reason=policy,
            cleanup_duration_sec=round(time.time() - t0, 3),
        )

    live = query_process(owned.pid)
    ownership = verify_ownership(owned, live)
    result = CleanupResult(
        shutdown_reason=reason,
        capture_pid=owned.pid,
        ownership_verified=bool(ownership.get("owned")),
        ownership_detail=ownership,
    )
    if not live.get("exists"):
        result.already_dead = True
        result.graceful_stop_ok = True
        result.cleanup_duration_sec = round(time.time() - t0, 3)
        return result
    if not ownership.get("owned"):
        # Refuse to kill — PID reuse or foreign process
        result.skipped = True
        result.skip_reason = "ownership_mismatch"
        result.remaining_processes = [owned.pid]
        result.cleanup_duration_sec = round(time.time() - t0, 3)
        return result

    identity_proven = bool(ownership.get("owned"))
    kill_dec = decide_owned_kill(owned, identity_proven=identity_proven, stale_graceful_done=False)
    result.ownership_class = str(kill_dec.get("class") or "")
    result.kill_decision = {k: kill_dec.get(k) for k in ("action", "kill_allowed", "reason", "class")}
    result.wrong_process_kill = 0
    if not kill_dec.get("kill_allowed"):
        result.skipped = True
        result.skip_reason = str(kill_dec.get("reason") or "kill_not_allowed")
        result.remaining_processes = [owned.pid]
        result.cleanup_duration_sec = round(time.time() - t0, 3)
        return result

    # 1) graceful via operator_stop.flag
    result.graceful_stop_requested = request_graceful_stop(
        owned.output_dir,
        session_id=str(getattr(owned, "trading_date", "") or ""),
        pid=int(owned.pid or 0),
        reason=str(reason or "cleanup"),
    )
    deadline = time.time() + max(0.5, float(graceful_timeout_sec))
    while time.time() < deadline:
        if not _pid_alive(owned.pid):
            result.graceful_stop_ok = True
            result.cleanup_duration_sec = round(time.time() - t0, 3)
            return result
        time.sleep(0.2)

    # Re-verify identity before terminate/force.
    live2 = query_process(owned.pid)
    ownership2 = verify_ownership(owned, live2)
    kill_dec2 = decide_owned_kill(
        owned,
        identity_proven=bool(ownership2.get("owned")),
        stale_graceful_done=True,
    )
    result.kill_decision = {k: kill_dec2.get(k) for k in ("action", "kill_allowed", "reason", "class")}
    result.ownership_class = str(kill_dec2.get("class") or result.ownership_class)
    if not ownership2.get("owned") or not kill_dec2.get("kill_allowed"):
        result.skipped = True
        result.skip_reason = str(kill_dec2.get("reason") or "identity_changed_before_force")
        result.remaining_processes = [owned.pid]
        result.cleanup_duration_sec = round(time.time() - t0, 3)
        return result

    # 2) terminate
    result.terminate_used = True
    _terminate_pid(owned.pid)
    deadline = time.time() + max(0.5, float(terminate_timeout_sec))
    while time.time() < deadline:
        if not _pid_alive(owned.pid):
            result.cleanup_duration_sec = round(time.time() - t0, 3)
            return result
        time.sleep(0.2)

    live3 = query_process(owned.pid)
    ownership3 = verify_ownership(owned, live3)
    kill_dec3 = decide_owned_kill(
        owned,
        identity_proven=bool(ownership3.get("owned")),
        stale_graceful_done=True,
    )
    if not ownership3.get("owned") or not kill_dec3.get("kill_allowed"):
        result.skipped = True
        result.skip_reason = str(kill_dec3.get("reason") or "identity_changed_before_kill")
        result.remaining_processes = [owned.pid]
        result.cleanup_duration_sec = round(time.time() - t0, 3)
        return result

    # 3) kill
    result.kill_used = True
    _kill_pid(owned.pid)
    time.sleep(0.3)
    if _pid_alive(owned.pid):
        result.remaining_processes = [owned.pid]
        result.error = "orphan_remains_after_kill"
    result.cleanup_duration_sec = round(time.time() - t0, 3)
    return result


def record_owned_from_spawn(spawn: Mapping[str, Any], *, native_root: Path) -> OwnedCaptureProcess:
    pid = int(spawn.get("pid") or 0)
    cmd = list(spawn.get("cmd") or [])
    owned = OwnedCaptureProcess(
        pid=pid,
        cmd=cmd,
        output_dir=str(spawn.get("output") or spawn.get("session_dir") or ""),
        native_root=str(native_root),
        trading_date=str(spawn.get("trading_date") or ""),
        synthetic=bool(spawn.get("synthetic")),
        supervised=bool(spawn.get("supervised")),
        spawned_at=_now_iso(),
        spawn_mono=time.monotonic(),
        parent_pid_at_spawn=os.getpid(),
        cmdline_fingerprint=cmdline_fingerprint(cmd),
        process_start_identity=str(spawn.get("process_start_identity") or ""),
        component_role=str(spawn.get("component_role") or "MARKET_INGRESS_SERVICE"),
        ingress_run_id=str(spawn.get("ingress_run_id") or ""),
        launch_nonce=str(spawn.get("launch_nonce") or ""),
    )
    live = query_process(pid)
    if live.get("create_time"):
        owned.create_time = str(live.get("create_time"))
    if live.get("cmdline"):
        # Prefer live cmdline for later ownership checks
        owned.cmdline_fingerprint = str(live.get("cmdline"))
    if not owned.process_start_identity:
        try:
            from small_paper.ingress_run_identity import capture_process_start_identity

            owned.process_start_identity = str(capture_process_start_identity(int(pid)) or "")
        except Exception:
            owned.process_start_identity = ""
    return owned


def write_cleanup_artifact(native_root: Path, trading_date: str, result: CleanupResult | Mapping[str, Any]) -> Path:
    out_dir = Path(native_root) / "results" / "reports" / "phase687w16_automatic_child_cleanup"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict() if isinstance(result, CleanupResult) else dict(result)
    payload["written_at"] = _now_iso()
    path = out_dir / f"cleanup_{trading_date}_{int(time.time())}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    latest = out_dir / f"cleanup_latest_{trading_date}.json"
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
