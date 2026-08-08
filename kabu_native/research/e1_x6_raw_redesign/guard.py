"""Paper-first operation guard for the raw-redesign research process.

The research process must yield to Paper Trade unconditionally:
- pause (checkpoint + normal exit) whenever Paper/pilot runner appears to run,
  a fresh Paper heartbeat exists, it is a JST weekday 08:30-15:45, or the Paper
  state cannot be determined safely;
- never stop/suspend/kill/restart any Paper process;
- run at BelowNormal priority, 1 worker, all math libs capped to 1 thread.

FORBIDDEN IMPORTS: broker API / order submit / cancel / Discord modules are
never imported by this package (enforced by assert_no_forbidden_imports).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

THREAD_ENV_CAPS = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}

# Substrings that identify Paper / pilot / capture processes in command lines.
PAPER_CMDLINE_MARKERS = (
    "paper_trade_checked_runner",
    "run_paper_trade",
    "pilot_runner",
    "small_paper.paper",
    "market_capture_sidecar",
    "paper_trade_healthcheck",
)

# Module name substrings that must never be imported by research code.
FORBIDDEN_MODULE_SUBSTRINGS = (
    "kabusapi",
    "broker",
    "order_submit",
    "order_cancel",
    "discord",
    "websocket",
    "requests",
)

HEARTBEAT_FRESH_SEC = 600.0
MIN_FREE_GB = 20.0


def apply_thread_caps() -> None:
    for k, v in THREAD_ENV_CAPS.items():
        os.environ[k] = v


def set_below_normal_priority() -> bool:
    try:
        import psutil

        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        return True
    except Exception:
        return False


def _now_jst() -> datetime:
    return datetime.now(JST)


def in_trading_window(now: Optional[datetime] = None) -> bool:
    """JST weekday 08:30-15:45 (inclusive edges) => research must pause."""
    t = now or _now_jst()
    if t.weekday() >= 5:
        return False
    hm = t.hour * 60 + t.minute
    return (8 * 60 + 30) <= hm <= (15 * 60 + 45)


def paper_processes() -> list[dict[str, Any]]:
    """Processes whose command line matches Paper markers. [] if none.

    Raises RuntimeError when the process table cannot be read (unknown state
    => caller must pause, per the safety contract).
    """
    import psutil

    me = os.getpid()
    found: list[dict[str, Any]] = []
    try:
        for pr in psutil.process_iter(["pid", "name", "cmdline"]):
            if pr.info["pid"] == me:
                continue
            cmd = " ".join(pr.info.get("cmdline") or [])
            low = cmd.lower()
            if any(m in low for m in PAPER_CMDLINE_MARKERS):
                found.append({"pid": pr.info["pid"], "name": pr.info["name"], "cmdline": cmd[:400]})
    except Exception as e:  # unknown state must pause, not proceed
        raise RuntimeError(f"PAPER_STATE_UNKNOWN: process scan failed: {e}") from e
    return found


def fresh_paper_heartbeat(native_root: Path, now: Optional[datetime] = None) -> Optional[str]:
    """Path of a Paper heartbeat file updated within HEARTBEAT_FRESH_SEC, else None."""
    t = (now or _now_jst()).timestamp()
    sp = native_root / "results" / "small_paper"
    if not sp.is_dir():
        return None
    day = (now or _now_jst()).strftime("%Y%m%d")
    for daydir in (sp / day,):
        if not daydir.is_dir():
            continue
        for hb in daydir.glob("live_session_*/heartbeat.jsonl"):
            try:
                if t - hb.stat().st_mtime <= HEARTBEAT_FRESH_SEC:
                    return str(hb)
            except OSError:
                continue
    return None


def disk_write_risk(store_root: Path) -> Optional[str]:
    """Reason string when actual free-space shortage / write failure risk exists.

    Note: >75% used alone does NOT block (per plan); only low absolute free
    space or a failed write probe blocks.
    """
    import shutil as _sh

    try:
        free_gb = _sh.disk_usage(str(store_root)).free / (1024**3)
    except OSError as e:
        return f"disk_usage_failed:{e}"
    if free_gb < MIN_FREE_GB:
        return f"free_space_low:{free_gb:.1f}GB<{MIN_FREE_GB}GB"
    probe = store_root / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return f"write_probe_failed:{e}"
    return None


def paper_guard_check(native_root: Path, store_root: Path) -> dict[str, Any]:
    """One guard evaluation. ok=False => research must checkpoint and exit.

    Never touches Paper processes in any way.
    """
    out: dict[str, Any] = {
        "checked_at_jst": _now_jst().isoformat(),
        "ok": True,
        "reasons": [],
        "paper_processes": [],
        "heartbeat": None,
    }
    if in_trading_window():
        out["ok"] = False
        out["reasons"].append("JST_WEEKDAY_TRADING_WINDOW_0830_1545")
    try:
        procs = paper_processes()
    except RuntimeError as e:
        out["ok"] = False
        out["reasons"].append(str(e))
        procs = []
    if procs:
        out["ok"] = False
        out["reasons"].append("PAPER_RUNNER_PROCESS_RUNNING")
        out["paper_processes"] = procs
    hb = fresh_paper_heartbeat(native_root)
    if hb:
        out["ok"] = False
        out["reasons"].append("PAPER_HEARTBEAT_FRESH")
        out["heartbeat"] = hb
    risk = disk_write_risk(store_root)
    if risk:
        out["ok"] = False
        out["reasons"].append(f"DISK_WRITE_RISK:{risk}")
    return out


def assert_no_forbidden_imports() -> list[str]:
    """Return loaded module names matching forbidden substrings (must be [])."""
    bad: list[str] = []
    for name in list(sys.modules):
        low = name.lower()
        if any(s in low for s in FORBIDDEN_MODULE_SUBSTRINGS):
            bad.append(name)
    return sorted(bad)
