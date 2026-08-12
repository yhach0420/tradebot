"""V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION — concurrent runtime duplex detector.

Distinct from V1R_PBV2_PRIMARY_CONTAMINATION (PBv2 drives Primary ENTRY/occupancy).

This verdict covers process/runtime multiplicity on the same trading day:
  - Market Ingress >1 for same --trading-date  (CONFIRMED operational class)
  - PBv2 / pilot >1                             (process duplex)
  - Authority duplex note: V1R-labeled launcher still starts classic trailing-mfe
    pilot (single process; overlaps PRIMARY_CONTAMINATION / class D)

submit/cancel/live must remain 0/0/0.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.capture_child_cleanup import query_process

JST = ZoneInfo("Asia/Tokyo")
VERDICT = "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION"
PRIMARY_VERDICT = "V1R_PBV2_PRIMARY_CONTAMINATION"
INGRESS_MARKER = "market_ingress_service"
PILOT_MARKER = "run_small_paper_pilot.py"


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _scan_python_cmdlines() -> list[dict[str, Any]]:
    """Best-effort list of python processes with CommandLine (Windows CIM)."""
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
        "| Select-Object ProcessId,ParentProcessId,CommandLine "
        "| ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        raw = (r.stdout or "").strip()
        if not raw or raw.lower() == "null":
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except Exception:
        return []
    return []


def _match_trading_date(cmdline: str, trading_date: str) -> bool:
    cl = cmdline or ""
    td = str(trading_date)
    if not td:
        return True
    if td in cl:
        return True
    # pilot often uses --output-date YYYYMMDD
    if f"--output-date {td}" in cl or f"--trading-date {td}" in cl:
        return True
    return False


def list_live_ingress(
    *, trading_date: str, native_root: Optional[Path] = None
) -> list[dict[str, Any]]:
    """Live market_ingress_service processes for trading_date (optional native-root filter)."""
    out: list[dict[str, Any]] = []
    native = str(native_root).replace("/", "\\").lower() if native_root else ""
    for row in _scan_python_cmdlines():
        cl = str(row.get("CommandLine") or "")
        if INGRESS_MARKER not in cl and "small_paper.market_ingress_service" not in cl:
            continue
        if not _match_trading_date(cl, trading_date):
            continue
        if native and native not in cl.replace("/", "\\").lower():
            continue
        out.append(
            {
                "pid": int(row.get("ProcessId") or 0),
                "ppid": row.get("ParentProcessId"),
                "cmdline": cl,
                "kind": "ingress",
            }
        )
    return [x for x in out if x["pid"] > 0]


def list_live_pilots(*, trading_date: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _scan_python_cmdlines():
        cl = str(row.get("CommandLine") or "")
        if PILOT_MARKER not in cl and "small_paper.pilot_runner" not in cl:
            continue
        if trading_date and not _match_trading_date(cl, trading_date):
            # also accept trailing_mfe yaml without date token if date empty
            if trading_date:
                continue
        out.append(
            {
                "pid": int(row.get("ProcessId") or 0),
                "ppid": row.get("ParentProcessId"),
                "cmdline": cl,
                "kind": "pilot",
                "trailing_mfe": "trailing_mfe" in cl.lower(),
            }
        )
    return [x for x in out if x["pid"] > 0]


def ingress_meta_consistency(native_root: Path, trading_date: str) -> dict[str, Any]:
    """Compare ingress.pid / spawn / status — mismatch is a duplex smell."""
    day = Path(native_root) / "data" / "market_capture" / str(trading_date)
    pid_file = 0
    spawn_pid = 0
    status_pid = 0
    status_state = ""
    session_dirs: list[str] = []
    if (day / "ingress.pid").is_file():
        try:
            pid_file = int((day / "ingress.pid").read_text(encoding="utf-8").strip().split()[0])
        except Exception:
            pid_file = 0
    if (day / "ingress_spawn.json").is_file():
        try:
            spawn_pid = int(
                json.loads((day / "ingress_spawn.json").read_text(encoding="utf-8")).get("pid") or 0
            )
        except Exception:
            spawn_pid = 0
    if (day / "ingress_status.json").is_file():
        try:
            st = json.loads((day / "ingress_status.json").read_text(encoding="utf-8"))
            status_pid = int(st.get("pid") or 0)
            status_state = str(st.get("state") or "")
        except Exception:
            pass
    if day.is_dir():
        session_dirs = sorted(p.name for p in day.glob("session_ing_*") if p.is_dir())
    pids = {p for p in (pid_file, spawn_pid, status_pid) if p > 0}
    alive = {}
    for p in pids:
        q = query_process(p)
        alive[str(p)] = bool(q.get("exists"))
    return {
        "day_dir": str(day),
        "pid_file": pid_file,
        "spawn_pid": spawn_pid,
        "status_pid": status_pid,
        "status_state": status_state,
        "session_dirs": session_dirs,
        "session_dir_count": len(session_dirs),
        "pid_set_size": len(pids),
        "pid_file_status_mismatch": bool(pid_file and status_pid and pid_file != status_pid),
        "alive": alive,
    }


def audit_duplicate_runtime(
    *,
    native_root: Path,
    trading_date: str,
) -> dict[str, Any]:
    ingress = list_live_ingress(trading_date=trading_date, native_root=native_root)
    pilots = list_live_pilots(trading_date=trading_date)
    # If date filter emptied pilots (cmdline without date), fall back to all pilots
    if not pilots:
        pilots = list_live_pilots(trading_date="")
    meta = ingress_meta_consistency(native_root, trading_date)
    ingress_dup = len(ingress) > 1 or meta.get("session_dir_count", 0) > 1 or bool(
        meta.get("pid_file_status_mismatch")
    )
    pilot_dup = len(pilots) > 1
    contaminated = bool(ingress_dup or pilot_dup)
    return {
        "verdict": VERDICT if contaminated else "NO_DUPLICATE_RUNTIME",
        "contaminated": contaminated,
        "trading_date": str(trading_date),
        "at": _now_iso(),
        "submit_cancel_live": "0/0/0",
        "counts": {
            "ingress": len(ingress),
            "pilot": len(pilots),
            "ingress_session_dirs": int(meta.get("session_dir_count") or 0),
        },
        "ingress_live": ingress,
        "pilot_live": [
            {k: v for k, v in p.items() if k != "cmdline"}
            | {"cmdline_tail": str(p.get("cmdline") or "")[-160:]}
            for p in pilots
        ],
        "ingress_meta": meta,
        "classes": {
            "ingress_process_duplex": len(ingress) > 1,
            "ingress_session_dir_duplex": int(meta.get("session_dir_count") or 0) > 1,
            "ingress_pid_meta_mismatch": bool(meta.get("pid_file_status_mismatch")),
            "pilot_process_duplex": pilot_dup,
            "pbv2_pilot_single_authority_duplex_note": (
                "Single trailing-mfe pilot under V1R Primary label is PRIMARY_CONTAMINATION/D, "
                "not process duplex - see " + PRIMARY_VERDICT
            ),
        },
        "related_verdicts": {
            PRIMARY_VERDICT: "PBv2 drives Primary ENTRY/occupancy (path contamination)",
            VERDICT: "Concurrent duplicate runtimes / meta mismatch same trading day",
        },
        "fix_target": {
            "ingress": "Fail-closed spawn when live ingress already exists for trading_date",
            "pilot": "At most one pilot; PBv2 must not run as second Primary process",
            "not_enough": "Notification-only or occupancy divert alone does not clear ingress duplex",
        },
    }


def write_verdict_artifacts(audit: dict[str, Any], *, native_root: Path) -> dict[str, str]:
    day = str(audit.get("trading_date") or "")
    research = (
        Path(native_root)
        / "results"
        / "research"
        / f"v1r_pbv2_duplicate_runtime_contamination_{day}"
    )
    research.mkdir(parents=True, exist_ok=True)
    paper = Path(native_root) / "results" / "small_paper" / day
    paper.mkdir(parents=True, exist_ok=True)
    vpath = research / "verdict.json"
    ppath = paper / "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION.json"
    slim = {
        "trading_date": day,
        "verdict": audit.get("verdict"),
        "contaminated": audit.get("contaminated"),
        "counts": audit.get("counts"),
        "classes": audit.get("classes"),
        "submit_cancel_live": "0/0/0",
        "note": (
            "Ingress duplex and/or pilot duplex on same trading day. "
            "Distinct from PRIMARY_CONTAMINATION (PBv2 ENTRY authority)."
        ),
        "at": audit.get("at"),
    }
    vpath.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ppath.write_text(json.dumps(slim, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {"research_verdict": str(vpath), "paper_flag": str(ppath)}
