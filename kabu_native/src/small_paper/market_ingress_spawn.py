"""Spawn Independent Market Ingress (V2) as a supervised child process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from small_paper.market_ingress_protocol import now_iso


def _live_ingress_pids(*, native_root: Path, trading_date: str) -> list[dict[str, Any]]:
    """Detect already-running ingress for this trading-date (fail-closed duplex guard)."""
    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress

        return list_live_ingress(trading_date=str(trading_date), native_root=Path(native_root))
    except Exception:
        return []


def spawn_ingress_process(
    *,
    native_root: Path,
    trading_date: str,
    python_exe: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    synthetic: bool = False,
    bus_port: Optional[int] = None,
    silence_stale_sec: Optional[float] = None,
    code_root: Optional[Path] = None,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    """Spawn ingress. Refuses if a live ingress already exists for trading_date.

    Returns meta with pid>0 on success. On duplex reject:
      {"ok": False, "rejected": True, "reason": "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION", "pid": 0, ...}
    """
    live = [] if allow_duplicate or synthetic else _live_ingress_pids(
        native_root=Path(native_root), trading_date=str(trading_date)
    )
    if live:
        return {
            "ok": False,
            "rejected": True,
            "reason": "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION",
            "detail": "ingress_already_running_for_trading_date",
            "pid": 0,
            "live_ingress": live,
            "trading_date": str(trading_date),
            "at": now_iso(),
            "spawned": False,
        }

    exe = python_exe or sys.executable
    root = Path(code_root) if code_root else Path(native_root)
    if not (root / "src" / "small_paper").is_dir():
        root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    src = str(root / "src")
    repo = str(root.parent)
    env["PYTHONPATH"] = f"{src};{repo}" if sys.platform == "win32" else f"{src}:{repo}"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MARKET_INGRESS_V2"] = "1"
    from small_paper.runtime_clock import apply_non_issuer_env, official_cert_child_env

    if synthetic:
        # TOKEN_CONSUMER_ONLY. Strip TRADEBOT_SESSION_CLOCK* / TRADEBOT_INGRESS_REPLAY*
        # and certification flags. KABU_AUTH_MODE=NONE — no POST /token.
        apply_non_issuer_env(env)
    else:
        # AUTHORIZED_ISSUER. Keep clock + replay. KABU_AUTH_MODE=LIVE.
        env = official_cert_child_env(env)
        env["PYTHONPATH"] = f"{src};{repo}" if sys.platform == "win32" else f"{src}:{repo}"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MARKET_INGRESS_V2"] = "1"
    cmd = [
        exe,
        "-m",
        "small_paper.market_ingress_service",
        "--native-root",
        str(native_root),
        "--trading-date",
        trading_date,
    ]
    if synthetic:
        cmd.append("--synthetic")
    if bus_port:
        cmd.extend(["--bus-port", str(int(bus_port))])
    if silence_stale_sec and float(silence_stale_sec) > 0:
        cmd.extend(["--silence-stale-sec", str(float(silence_stale_sec))])
    if symbols:
        cmd.extend(["--symbols", ",".join(symbols)])
    day = Path(native_root) / "data" / "market_capture" / trading_date
    day.mkdir(parents=True, exist_ok=True)
    stderr_path = day / "ingress_stderr.log"
    creationflags = 0x00000200 if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=stderr_path.open("w", encoding="utf-8"),
        creationflags=creationflags,
        start_new_session=(sys.platform != "win32"),
    )
    meta = {
        "ok": True,
        "rejected": False,
        "spawned": True,
        "at": now_iso(),
        "pid": proc.pid,
        "cmd": cmd,
        "trading_date": trading_date,
        "synthetic": synthetic,
    }
    (day / "ingress.pid").write_text(str(proc.pid), encoding="utf-8")
    (day / "ingress_spawn.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta

def wait_ingress_online(
    native_root: Path,
    trading_date: str,
    *,
    timeout_sec: float = 45.0,
    require_registered_count: int = 0,
) -> dict[str, Any]:
    status = Path(native_root) / "data" / "market_capture" / trading_date / "ingress_status.json"
    deadline = time.monotonic() + float(timeout_sec)
    last: dict[str, Any] = {}
    need = int(require_registered_count or 0)
    while time.monotonic() < deadline:
        if status.is_file():
            try:
                last = json.loads(status.read_text(encoding="utf-8"))
                st = str(last.get("state") or "")
                if st in (
                    "RUNNING",
                    "WAITING_FIRST_PUSH",
                    "REGISTERING",
                    "RECOVERED",
                    "CONNECTING",
                ):
                    if need > 0 and int(last.get("registered_symbol_count") or 0) < need:
                        time.sleep(0.25)
                        continue
                    return {"ok": True, "status": st, "pid": last.get("pid"), "snapshot": last}
            except Exception as exc:
                last = {"error": type(exc).__name__}
        time.sleep(0.25)
    return {"ok": False, "reason": "ingress_online_timeout", "last": last}
