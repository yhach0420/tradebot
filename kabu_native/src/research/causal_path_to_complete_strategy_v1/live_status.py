"""Read-only 20260914 prospective capture status. Do not stop. Do not feed into Discovery."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.am_c0_indicator_exit.isolation import _cim_python_processes, _file_mtime, _file_size
from research.causal_path_to_complete_strategy_v1.isolation import NATIVE
from research.market_breadth_leadership_acquisition_v1.isolation import OUT as BREADTH_OUT, RAW_ROOT
from research.new_causal_information_acquisition_v1.isolation import CONTEXT_ROOT
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE

JST = ZoneInfo("Asia/Tokyo")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if handle:
            kernel.CloseHandle(handle)
            return True
    except Exception:
        pass
    try:
        import os

        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        return None


def _latest_file(root: Path, pattern: str) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "path": str(root)}
    files = list(root.rglob(pattern)) if root.is_dir() else []
    files = [p for p in files if p.is_file()]
    if not files:
        return {"exists": True, "n": 0, "latest": None, "root": str(root)}
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return {
        "exists": True,
        "n": len(files),
        "latest": str(latest),
        "latest_mtime": datetime.fromtimestamp(latest.stat().st_mtime, tz=JST).isoformat(),
        "latest_size": int(latest.stat().st_size),
        "root": str(root),
    }


def _count_lines(path: Path, limit: int = 500000) -> int | None:
    if not path.is_file():
        return None
    n = 0
    try:
        with path.open("rb") as fh:
            for n, _ in enumerate(fh, start=1):
                if n >= limit:
                    break
    except Exception:
        return None
    return n


def inspect_live_20260914(*, procs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    day = str(TRADING_DATE)
    procs = procs if procs is not None else _cim_python_processes()
    fut_procs = [p for p in procs if "run_futures_market_context_capture" in str(p.get("cmd") or "")]
    br_procs = [p for p in procs if "run_market_breadth_leadership_capture" in str(p.get("cmd") or "") or "market_breadth_leadership" in str(p.get("cmd") or "").lower()]
    launcher_dir = BREADTH_OUT / f"live_{day}"
    launcher_pid = _read_pid(launcher_dir / "launcher_pid.txt")
    meta = {}
    mp = launcher_dir / "launcher_meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    ctx = CONTEXT_ROOT / day
    raw = RAW_ROOT / day
    nk = ctx / "futures" / "nk225mini.jsonl"
    tx = ctx / "futures" / "topix.jsonl"
    pid_path = ctx / "new_info.pid"
    fut_pid = _read_pid(pid_path)
    status = {}
    sp = ctx / "status.json" if (ctx / "status.json").is_file() else ctx / "live_manifest.json"
    if sp.is_file():
        try:
            status = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            status = {}
    breadth_snap = _latest_file(raw, "*.jsonl")
    if breadth_snap.get("n", 0) == 0:
        breadth_snap = _latest_file(raw, "*")
    return {
        "classification": "PROSPECTIVE_CAPTURE_20260914",
        "trading_date": day,
        "fed_into_discovery_tuning": False,
        "did_not_stop_captures": True,
        "futures": {
            "command": "python kabu_native\\scripts\\run_futures_market_context_capture.py --live --trading-date 20260914",
            "process_n": len(fut_procs),
            "pids": [p.get("pid") for p in fut_procs],
            "file_pid": fut_pid,
            "file_pid_alive": _pid_alive(fut_pid),
            "process_alive": bool(fut_procs) or _pid_alive(fut_pid),
            "output_dir": str(ctx),
            "output_exists": ctx.is_dir(),
            "NK225mini": {
                "path": str(nk),
                "exists": nk.is_file(),
                "mtime": _file_mtime(nk),
                "size": _file_size(nk),
                "received_count": _count_lines(nk) if nk.is_file() else 0,
            },
            "TOPIX": {
                "path": str(tx),
                "exists": tx.is_file(),
                "mtime": _file_mtime(tx),
                "size": _file_size(tx),
                "received_count": _count_lines(tx) if tx.is_file() else 0,
            },
            "status_keys": sorted(status.keys())[:20] if status else [],
        },
        "breadth": {
            "command": "run_market_breadth_leadership_capture.py --live --trading-date 20260914",
            "launcher_dir": str(launcher_dir),
            "launcher_pid": launcher_pid,
            "launcher_pid_alive": _pid_alive(launcher_pid),
            "process_n": len(br_procs),
            "pids": [p.get("pid") for p in br_procs],
            "process_alive": bool(br_procs) or _pid_alive(launcher_pid),
            "launcher_meta": meta,
            "raw_dir": str(raw),
            "raw_exists": raw.is_dir(),
            "snapshot": breadth_snap,
        },
        "futures_capture_running": bool(fut_procs) or _pid_alive(fut_pid),
        "breadth_capture_running": bool(br_procs) or _pid_alive(launcher_pid),
        "native_root": str(NATIVE),
    }
