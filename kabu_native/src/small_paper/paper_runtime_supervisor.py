"""Phase675 — Paper Runtime external supervisor (ops only).

Monitors Runtime PID / heartbeat age / PUSH age / session clock.
On Heartbeat stall + PID alive → EVENT_LOOP_STALL:
  evidence save, Discord Critical (best-effort), safe stop, orphan recovery hint.
Never enables live orders. Restart capped per session with cooldown persistence.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

from small_paper.ws_freeze_recovery import (
    DEFAULT_HB_STALL_SEC,
    DEFAULT_PUSH_STALL_SEC,
    DEFAULT_SUPERVISOR_COOLDOWN_SEC,
    DEFAULT_SUPERVISOR_MAX_RESTARTS_PER_SESSION,
    EVENT_LOOP_STALL,
    load_jsonl,
    load_supervisor_attempts,
    now_iso,
    record_supervisor_attempt,
    supervisor_may_restart,
)


def _native_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, 0, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _safe_kill(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        try:
            if sys.platform == "win32":
                import subprocess

                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
                return True
        except Exception:
            return False
    return False


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def read_last_heartbeat(session_dir: Path) -> Optional[dict[str, Any]]:
    rows = load_jsonl(session_dir / "heartbeat.jsonl")
    return rows[-1] if rows else None


def read_last_error(session_dir: Path) -> Optional[dict[str, Any]]:
    rows = load_jsonl(session_dir / "errors.jsonl")
    return rows[-1] if rows else None


def heartbeat_age_sec(hb: Mapping[str, Any], *, now: Optional[datetime] = None) -> Optional[float]:
    ts = str(hb.get("emitted_at") or hb.get("event_time") or "")
    dt = _parse_iso(ts)
    if dt is None:
        return None
    now = now or datetime.now(JST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return (now - dt).total_seconds()


def evaluate_stall(
    session_dir: Path,
    *,
    runtime_pid: int,
    hb_stall_sec: float = DEFAULT_HB_STALL_SEC,
    push_stall_sec: float = DEFAULT_PUSH_STALL_SEC,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(JST)
    hb = read_last_heartbeat(session_dir)
    alive = _pid_alive(runtime_pid)
    hb_age = heartbeat_age_sec(hb, now=now) if hb else None
    last_push_at = (hb or {}).get("last_push_at")
    last_push_age = (hb or {}).get("last_push_age_sec")
    if last_push_age is None and last_push_at:
        pdt = _parse_iso(str(last_push_at))
        if pdt is not None:
            if pdt.tzinfo is None:
                pdt = pdt.replace(tzinfo=JST)
            last_push_age = (now - pdt).total_seconds()

    stall = bool(
        alive
        and hb is not None
        and hb_age is not None
        and hb_age >= hb_stall_sec
    )
    push_stalled = bool(
        last_push_age is not None and float(last_push_age) >= push_stall_sec
    )
    return {
        "event": EVENT_LOOP_STALL if stall else "OK",
        "stall": stall,
        "runtime_pid": runtime_pid,
        "pid_alive": alive,
        "heartbeat_age_sec": hb_age,
        "last_push_age_sec": last_push_age,
        "push_stalled": push_stalled,
        "last_heartbeat": hb,
        "evaluated_at": now.isoformat(timespec="seconds"),
        "session_dir": str(session_dir),
    }


def save_stall_evidence(session_dir: Path, snap: Mapping[str, Any]) -> Path:
    out = Path(session_dir) / "stall_evidence"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    path = out / f"event_loop_stall_{stamp}.json"
    path.write_text(json.dumps(dict(snap), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def notify_discord_critical(message: str, *, extra: Optional[Mapping[str, Any]] = None) -> bool:
    """Best-effort Critical notify; never raises; never blocks finalize."""
    try:
        from small_paper.discord_notifier import DiscordNotifier, DiscordNotifyConfig

        n = DiscordNotifier(DiscordNotifyConfig())
        if not getattr(n, "active", False):
            return False
        if hasattr(n, "notify_error"):
            n.notify_error(
                operation=EVENT_LOOP_STALL,
                message=message,
                extra=dict(extra or {}),
            )
            return True
    except Exception:
        return False
    return False


def handle_event_loop_stall(
    session_dir: Path,
    *,
    runtime_pid: int,
    allow_restart: bool = False,
    hb_stall_sec: float = DEFAULT_HB_STALL_SEC,
    push_stall_sec: float = DEFAULT_PUSH_STALL_SEC,
    max_restarts: int = DEFAULT_SUPERVISOR_MAX_RESTARTS_PER_SESSION,
    cooldown_sec: float = DEFAULT_SUPERVISOR_COOLDOWN_SEC,
) -> dict[str, Any]:
    snap = evaluate_stall(
        session_dir,
        runtime_pid=runtime_pid,
        hb_stall_sec=hb_stall_sec,
        push_stall_sec=push_stall_sec,
    )
    if not snap.get("stall"):
        return {"ok": True, "action": "none", "snap": snap}

    evidence = save_stall_evidence(session_dir, snap)
    notify_discord_critical(
        f"EVENT_LOOP_STALL pid={runtime_pid} hb_age={snap.get('heartbeat_age_sec')}",
        extra=snap,
    )
    killed = _safe_kill(int(runtime_pid))
    attempt = record_supervisor_attempt(
        session_dir,
        action="safe_stop",
        detail={"killed": killed, "evidence": str(evidence), "snap": snap},
        max_attempts=max_restarts,
    )
    may, reason = supervisor_may_restart(
        session_dir, max_attempts=max_restarts, cooldown_sec=cooldown_sec
    )
    result = {
        "ok": True,
        "action": EVENT_LOOP_STALL,
        "killed": killed,
        "evidence": str(evidence),
        "attempt": attempt,
        "restart_allowed": bool(allow_restart and may),
        "restart_block_reason": "" if may else reason,
        "orphan_recovery_required": True,
        "snap": snap,
        "at": now_iso(),
    }
    (Path(session_dir) / "runtime_supervisor_last_action.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def watch_once(
    session_dir: Path,
    *,
    runtime_pid: int,
    hb_stall_sec: float = DEFAULT_HB_STALL_SEC,
    push_stall_sec: float = DEFAULT_PUSH_STALL_SEC,
) -> dict[str, Any]:
    snap = evaluate_stall(
        session_dir,
        runtime_pid=runtime_pid,
        hb_stall_sec=hb_stall_sec,
        push_stall_sec=push_stall_sec,
    )
    if snap.get("stall"):
        return handle_event_loop_stall(
            session_dir,
            runtime_pid=runtime_pid,
            hb_stall_sec=hb_stall_sec,
            push_stall_sec=push_stall_sec,
        )
    return {"ok": True, "action": "ok", "snap": snap}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Paper runtime EVENT_LOOP_STALL supervisor")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--runtime-pid", type=int, required=True)
    ap.add_argument("--hb-stall-sec", type=float, default=DEFAULT_HB_STALL_SEC)
    ap.add_argument("--push-stall-sec", type=float, default=DEFAULT_PUSH_STALL_SEC)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval-sec", type=float, default=30.0)
    args = ap.parse_args(argv)
    session_dir = Path(args.session_dir)
    if args.loop:
        while True:
            out = watch_once(
                session_dir,
                runtime_pid=args.runtime_pid,
                hb_stall_sec=args.hb_stall_sec,
                push_stall_sec=args.push_stall_sec,
            )
            print(json.dumps(out, ensure_ascii=False))
            if out.get("action") == EVENT_LOOP_STALL:
                return 2
            time.sleep(max(5.0, float(args.interval_sec)))
    out = watch_once(
        session_dir,
        runtime_pid=args.runtime_pid,
        hb_stall_sec=args.hb_stall_sec,
        push_stall_sec=args.push_stall_sec,
    )
    print(json.dumps(out, ensure_ascii=False))
    return 2 if out.get("action") == EVENT_LOOP_STALL else 0


if __name__ == "__main__":
    raise SystemExit(main())
