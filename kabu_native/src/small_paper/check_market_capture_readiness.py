"""Phase687W9 — Market capture readiness CLI.

python -m small_paper.check_market_capture_readiness
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.market_capture_registration import (
    KABU_PUSH_REGISTER_LIMIT,
    read_registration_manifest,
    resolve_universe_symbols,
    trading_date_jst,
)
from small_paper.market_capture_sidecar import (
    CAPTURE_ONLINE,
    HEARTBEAT_FILE,
    STATUS_FILE,
    capture_day_dir,
)
from small_paper.market_capture_topology import TOPOLOGY_PASSIVE_DUAL

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[2]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, 0, pid)
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


def _disk_free_gb(path: Path) -> float:
    try:
        import shutil

        u = shutil.disk_usage(str(path))
        return round(u.free / (1024**3), 3)
    except Exception:
        return -1.0


def check_market_capture_readiness(
    native_root: Path,
    *,
    trading_date: Optional[str] = None,
    kabu_readonly_status: str = "UNKNOWN",
) -> dict[str, Any]:
    day = trading_date or trading_date_jst()
    out = capture_day_dir(native_root, day)
    man = read_registration_manifest(native_root)
    resolved = resolve_universe_symbols(native_root, day, allow_empty=True)
    expected = list(man.get("registered_symbols") or resolved.get("symbols") or [])
    expected_count = len(expected)
    actual = list(man.get("actual_symbols") or expected)
    actual_count = len(actual)
    registration_match = sorted(expected) == sorted(actual) if expected else False
    if man.get("status") == "PLANNED_FOLLOWER":
        registration_match = True

    status_obj: dict[str, Any] = {}
    hb: dict[str, Any] = {}
    if (out / STATUS_FILE).is_file():
        try:
            status_obj = json.loads((out / STATUS_FILE).read_text(encoding="utf-8"))
        except Exception:
            status_obj = {}
    if (out / HEARTBEAT_FILE).is_file():
        try:
            hb = json.loads((out / HEARTBEAT_FILE).read_text(encoding="utf-8"))
        except Exception:
            hb = {}

    sidecar_pid = int(status_obj.get("pid") or hb.get("pid") or 0)
    heartbeat_age = None
    hb_path = out / HEARTBEAT_FILE
    if hb_path.is_file():
        heartbeat_age = round(time.time() - hb_path.stat().st_mtime, 3)

    ws_status = str(status_obj.get("websocket_status") or ("CONNECTED" if status_obj.get("capture_status") == CAPTURE_ONLINE else "UNKNOWN"))
    topology = str(status_obj.get("topology") or TOPOLOGY_PASSIVE_DUAL)
    event_count = int(status_obj.get("event_count") or 0)
    disconnect_count = int(status_obj.get("disconnect_count") or 0)
    dropped = int(status_obj.get("dropped_event_count") or 0)
    queue_status = "OK"
    if dropped:
        queue_status = "OVERFLOW_OR_DROP"

    free_gb = _disk_free_gb(out if out.exists() else native_root)
    output_writable = False
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)  # type: ignore[arg-type]
        output_writable = True
    except Exception:
        output_writable = False

    secrets_present = bool(status_obj.get("secrets_present"))
    blockers: list[str] = []

    if kabu_readonly_status not in ("ONLINE", "READY", "OK", "PASS"):
        # allow UNKNOWN when synthetic / offline tests pass kabu separately
        if kabu_readonly_status not in ("UNKNOWN", "SKIP"):
            blockers.append(f"kabu_readonly_status={kabu_readonly_status}")
    if not (1 <= expected_count <= KABU_PUSH_REGISTER_LIMIT):
        if expected_count == 0:
            blockers.append("expected_symbol_count_empty")
        else:
            blockers.append(f"expected_symbol_count={expected_count}")
    if not registration_match:
        blockers.append("registration_mismatch")
    if sidecar_pid <= 0 or not _pid_alive(sidecar_pid):
        blockers.append("sidecar_not_running")
    if heartbeat_age is None or heartbeat_age > 30:
        blockers.append("heartbeat_stale")
    if not output_writable:
        blockers.append("output_not_writable")
    if free_gb >= 0 and free_gb < 1.0:
        blockers.append("disk_critical")
    if dropped != 0:
        blockers.append("dropped_event_count_nonzero")
    if secrets_present:
        blockers.append("secrets_present")

    capture_ready = len(blockers) == 0
    report = {
        "kabu_readonly_status": kabu_readonly_status,
        "registration_status": man.get("status") or "UNKNOWN",
        "expected_symbol_count": expected_count,
        "actual_symbol_count": actual_count,
        "registration_match": registration_match,
        "websocket_status": ws_status,
        "topology": topology,
        "sidecar_pid": sidecar_pid,
        "heartbeat_age_sec": heartbeat_age,
        "output_writable": output_writable,
        "free_disk_gb": free_gb,
        "queue_status": queue_status,
        "capture_event_count": event_count,
        "disconnect_count": disconnect_count,
        "dropped_event_count": dropped,
        "capture_ready": capture_ready,
        "blockers": blockers,
        "trading_date": day,
        "output_path": str(out),
        "live_trading_enabled": False,
        "order_enabled": False,
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check market capture readiness (Phase687W9)")
    parser.add_argument("--native-root", type=str, default=str(NATIVE_ROOT))
    parser.add_argument("--trading-date", type=str, default="")
    parser.add_argument("--kabu-readonly-status", type=str, default="UNKNOWN")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = check_market_capture_readiness(
        Path(args.native_root),
        trading_date=args.trading_date or None,
        kabu_readonly_status=args.kabu_readonly_status,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    return 0 if report.get("capture_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
