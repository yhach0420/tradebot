"""Read-only Runtime/Capture non-interference snapshot. No process control. No writes to live paths."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
TODAY = datetime.now(JST).strftime("%Y%m%d")
CAP_ROOT = NATIVE / "data" / "market_capture"
PAPER_ROOT = NATIVE / "results" / "small_paper"
RESEARCH_OUT = NATIVE / "results" / "research" / "am_c0_indicator_exit"
RESEARCH_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_c0_indicator_exit"

LIVE_HINTS = (
    "run_paper_trade",
    "pilot_runner",
    "v1r_paper",
    "v1r_paper_primary_launcher",
    "market_ingress_service",
    "market_capture",
    "capture_sidecar",
    "small_paper.pilot",
    "paper_trade",
    "ingress",
)
RESEARCH_HINTS = (
    "am_c0_indicator_exit",
    "research.am_c0_indicator_exit",
)
FORBIDDEN_WRITE_PREFIXES = (
    NATIVE / "data" / "market_capture",
    NATIVE / "results" / "paper_sessions",
    NATIVE / "results" / "small_paper",
    NATIVE / "data" / "tokens",
    NATIVE / "runtime",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tail_jsonl_last(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            pos = fh.tell()
            fh.seek(max(0, pos - 262144))
            chunk = fh.read().decode("utf-8", errors="replace")
        for line in reversed(chunk.splitlines()):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                return rec
    except Exception:
        return {}
    return {}


def _file_mtime(path: Optional[Path]) -> Optional[float]:
    if path is None or not path.exists():
        return None
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def _file_size(path: Optional[Path]) -> Optional[int]:
    if path is None or not path.exists():
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def active_capture_path(today: str = TODAY) -> Optional[Path]:
    root = CAP_ROOT / today
    if not root.is_dir():
        return None
    best: Optional[Path] = None
    best_n = -1
    try:
        for sess in root.iterdir():
            if not (sess.is_dir() and sess.name.startswith("session_ing_")):
                continue
            n = 0
            for p in sess.glob("push_part_*.jsonl"):
                try:
                    n += int(p.stat().st_size)
                except OSError:
                    continue
            if n > best_n:
                best, best_n = sess, n
    except OSError:
        return root if root.is_dir() else None
    if best is not None and best_n > 0:
        return best
    day_n = 0
    for p in root.glob("push_part_*.jsonl"):
        try:
            day_n += int(p.stat().st_size)
        except OSError:
            continue
    if day_n > 0:
        return root
    return root


def _latest_paper_session(today: str = TODAY) -> Optional[Path]:
    root = PAPER_ROOT / today
    if not root.is_dir():
        return None
    sess = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        if not (p.name.startswith("v1r_primary_") or p.name.startswith("live_session_")):
            continue
        if (p / "heartbeat.jsonl").is_file():
            sess.append(p)
    if not sess:
        return None
    prim = [p for p in sess if p.name.startswith("v1r_primary_")]
    use = prim or sess
    use.sort(key=lambda x: _file_mtime(x / "heartbeat.jsonl") or 0.0, reverse=True)
    return use[0]


def _cim_python_processes() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return []
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for rec in data:
        if not isinstance(rec, dict):
            continue
        pid = rec.get("ProcessId")
        cmd = str(rec.get("CommandLine") or "")
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            continue
        out.append({"pid": pid_i, "cmd": cmd})
    return out


def _classify_pid(cmd: str) -> str:
    low = cmd.lower()
    if any(h.lower() in low for h in RESEARCH_HINTS):
        return "RESEARCH"
    if "market_ingress_service" in low:
        return "CAPTURE"
    if "v1r_paper_primary_launcher" in low:
        return "RUNTIME"
    if any(h.lower() in low for h in LIVE_HINTS):
        return "LIVE"
    return "OTHER"


def snapshot(*, phase: str, today: str = TODAY) -> dict[str, Any]:
    cap = active_capture_path(today)
    paper = _latest_paper_session(today)
    hb_path = (paper / "heartbeat.jsonl") if paper is not None else None
    hb = _tail_jsonl_last(hb_path) if hb_path else {}
    ingress = _read_json(cap / "ingress_status.json") if cap is not None else {}
    last_push = None
    if cap is not None:
        parts = sorted(p for p in cap.glob("push_part_*.jsonl") if p.stat().st_size > 0) if cap.is_dir() else []
        if parts:
            last_push = parts[-1]
            last_ev = _tail_jsonl_last(last_push)
        else:
            last_ev = {}
    else:
        last_ev = {}
    procs = _cim_python_processes()
    for p in procs:
        p["kind"] = _classify_pid(str(p.get("cmd") or ""))
    live = [p for p in procs if p.get("kind") in {"LIVE", "RUNTIME", "CAPTURE"}]
    research = [p for p in procs if p.get("kind") == "RESEARCH"]
    runtime_pid = None
    capture_pid = None
    for p in procs:
        if p.get("kind") == "RUNTIME":
            runtime_pid = p.get("pid")
        elif p.get("kind") == "CAPTURE":
            capture_pid = p.get("pid")
    if runtime_pid is None:
        runtime_pid = hb.get("primary_pid") or hb.get("runtime_pid") or hb.get("pid")
    if capture_pid is None:
        capture_pid = ingress.get("pid") or ingress.get("capture_pid") or ingress.get("process_id")
    last_event = (
        last_ev.get("received_at")
        or last_ev.get("event_time")
        or last_ev.get("persisted_at")
        or last_ev.get("sequence")
    )
    return {
        "phase": phase,
        "today": today,
        "self_pid": os.getpid(),
        "RUNTIME_PID": runtime_pid,
        "CAPTURE_PID": capture_pid,
        "RUNTIME_HEARTBEAT": hb.get("ts") or hb.get("t") or hb.get("emitted_at") or hb.get("timestamp"),
        "RUNTIME_HB_SEQ": hb.get("hb_seq"),
        "RUNTIME_HEARTBEAT_MTIME": _file_mtime(hb_path),
        "CAPTURE_LAST_EVENT": last_event,
        "CAPTURE_LAST_SEQ": last_ev.get("sequence"),
        "CAPTURE_LAST_PART_MTIME": _file_mtime(last_push) if cap is not None else None,
        "CAPTURE_LAST_PART_SIZE": _file_size(last_push) if cap is not None else None,
        "ACTIVE_CAPTURE_PATH": str(cap) if cap is not None else "",
        "ACTIVE_PAPER_SESSION": str(paper) if paper is not None else "",
        "HEARTBEAT_PATH": str(hb_path) if hb_path is not None else "",
        "LIVE_PYTHON_N": len(live),
        "LIVE_PIDS": [p.get("pid") for p in live],
        "RESEARCH_PIDS": [p.get("pid") for p in research],
        "ingress_keys": sorted(ingress.keys())[:20] if ingress else [],
    }


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [RESEARCH_OUT.resolve(), RESEARCH_CACHE.resolve()]
    forbidden = []
    if active_capture:
        forbidden.append(Path(active_capture).resolve())
    if paper_session:
        forbidden.append(Path(paper_session).resolve())
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for w in writes:
        ws = str(w)
        for f in forbidden:
            fs = str(f)
            if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
                n += 1
    return n


def input_active_file_n(input_paths: list[str], active_capture: str, paper_session: str, today: str = TODAY) -> int:
    n = 0
    act = Path(active_capture).resolve() if active_capture else None
    paper = Path(paper_session).resolve() if paper_session else None
    today_cap = (CAP_ROOT / today).resolve()
    for raw in input_paths:
        if not raw:
            continue
        try:
            p = Path(raw).resolve()
        except Exception:
            continue
        ps = str(p)
        if act is not None and (p == act or ps.startswith(str(act) + os.sep)):
            n += 1
            continue
        if paper is not None and (p == paper or ps.startswith(str(paper) + os.sep)):
            n += 1
            continue
        if today in ps and (p == today_cap or ps.startswith(str(today_cap) + os.sep)):
            n += 1
    return n


def advanced(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    hb_b = before.get("RUNTIME_HEARTBEAT_MTIME")
    hb_a = after.get("RUNTIME_HEARTBEAT_MTIME")
    cap_b = before.get("CAPTURE_LAST_PART_MTIME")
    cap_a = after.get("CAPTURE_LAST_PART_MTIME")
    seq_b = before.get("CAPTURE_LAST_SEQ")
    seq_a = after.get("CAPTURE_LAST_SEQ")
    size_b = before.get("CAPTURE_LAST_PART_SIZE")
    size_a = after.get("CAPTURE_LAST_PART_SIZE")
    hb_seq_b = before.get("RUNTIME_HB_SEQ")
    hb_seq_a = after.get("RUNTIME_HB_SEQ")
    hb_adv = False
    if hb_seq_b is not None and hb_seq_a is not None:
        try:
            hb_adv = int(hb_seq_a) >= int(hb_seq_b)
        except (TypeError, ValueError):
            hb_adv = False
    if not hb_adv:
        hb_adv = hb_b is not None and hb_a is not None and float(hb_a) >= float(hb_b)
    ts_b = before.get("RUNTIME_HEARTBEAT")
    ts_a = after.get("RUNTIME_HEARTBEAT")
    if not hb_adv and ts_b and ts_a:
        hb_adv = str(ts_a) >= str(ts_b)
    cap_adv = False
    if seq_b is not None and seq_a is not None:
        try:
            cap_adv = int(seq_a) >= int(seq_b)
        except (TypeError, ValueError):
            cap_adv = False
    if not cap_adv and cap_b is not None and cap_a is not None:
        cap_adv = float(cap_a) >= float(cap_b)
    if not cap_adv and size_b is not None and size_a is not None:
        cap_adv = int(size_a) >= int(size_b)
    same_rt = before.get("RUNTIME_PID") == after.get("RUNTIME_PID")
    same_cap = before.get("CAPTURE_PID") == after.get("CAPTURE_PID")
    rt_alive = after.get("RUNTIME_PID") is not None
    cap_alive = after.get("CAPTURE_PID") is not None or bool(after.get("ACTIVE_CAPTURE_PATH"))
    return {
        "RUNTIME_PID_UNCHANGED": bool(same_rt),
        "CAPTURE_PID_UNCHANGED": bool(same_cap),
        "RUNTIME_HEARTBEAT_ADVANCED": bool(hb_adv),
        "CAPTURE_ADVANCED": bool(cap_adv),
        "RUNTIME_STILL_ALIVE": bool(rt_alive),
        "CAPTURE_STILL_ALIVE": bool(cap_alive),
    }


def set_research_priority_below_normal() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        BELOW_NORMAL = 0x00004000
        k = ctypes.windll.kernel32
        return bool(k.SetPriorityClass(k.GetCurrentProcess(), BELOW_NORMAL))
    except Exception:
        return False
