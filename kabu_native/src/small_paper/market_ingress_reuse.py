"""Explicit Capture / Market Ingress reuse (fail-closed; no implicit attach).

Used only when Checked Runner is invoked with --reuse-capture.
Never spawns a new ingress process.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.capture_child_cleanup import query_process
from small_paper.ingress_run_identity import evaluate_current_run_online
from small_paper.market_ingress_protocol import now_iso

ONLINE_STATES = frozenset(
    {"RUNNING", "WAITING_FIRST_PUSH", "REGISTERING", "RECOVERED", "CONNECTING"}
)
TOPOLOGY = "INDEPENDENT_MARKET_INGRESS"
EXPECTED_UNIVERSE_N = 50


def ingress_day_dir(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "data" / "market_capture" / str(trading_date)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _cmdline_ok(cmdline: str, *, native_root: Path, trading_date: str) -> tuple[bool, str]:
    cl = (cmdline or "").replace("/", "\\").lower()
    if "market_ingress_service" not in cl and "small_paper.market_ingress_service" not in cl:
        return False, "cmdline_missing_market_ingress_service"
    td = str(trading_date)
    if td not in (cmdline or "") and f"--trading-date {td}" not in (cmdline or ""):
        # allow compact forms
        if td not in cl:
            return False, "cmdline_trading_date_mismatch"
    native = str(native_root).replace("/", "\\").lower()
    if native and native not in cl:
        return False, "cmdline_native_root_mismatch"
    return True, "ok"


def validate_reusable_ingress(
    *,
    native_root: Path,
    trading_date: str,
    expected_symbol_count: int,
    expected_pid: Optional[int] = None,
) -> dict[str, Any]:
    """Fail-closed validation of an already-running Independent Market Ingress.

    Universe rule:
      - Runner expected_symbol_count must be 50.
      - If ingress desired/registered are already non-zero, both must equal 50 (50/50).
      - If both are still 0 (pre-Paper write), allow with runtime_register_pending=True.
    """
    root = Path(native_root)
    day = str(trading_date)
    out: dict[str, Any] = {
        "ok": False,
        "at": now_iso(),
        "reuse": True,
        "spawned": False,
        "topology": TOPOLOGY,
        "trading_date": day,
        "day_dir": str(ingress_day_dir(root, day)),
        "checks": {},
        "reason": "",
    }
    checks = out["checks"]

    if int(expected_symbol_count) != EXPECTED_UNIVERSE_N:
        out["reason"] = f"universe_expected_not_50:{expected_symbol_count}"
        checks["universe_expected"] = False
        return out
    checks["universe_expected"] = True

    day_dir = ingress_day_dir(root, day)
    if not day_dir.is_dir():
        out["reason"] = "day_dir_missing"
        checks["day_dir"] = False
        return out
    checks["day_dir"] = True

    status_path = day_dir / "ingress_status.json"
    pid_path = day_dir / "ingress.pid"
    spawn_path = day_dir / "ingress_spawn.json"
    if not status_path.is_file():
        out["reason"] = "ingress_status_missing"
        checks["status_file"] = False
        return out
    checks["status_file"] = True

    try:
        status = _read_json(status_path)
    except Exception as exc:
        out["reason"] = f"ingress_status_unreadable:{type(exc).__name__}"
        return out

    pid = int(status.get("pid") or 0)
    if expected_pid is not None and int(expected_pid) > 0 and pid != int(expected_pid):
        out["reason"] = f"status_pid_mismatch:status={pid} expected={expected_pid}"
        checks["pid_match_expected"] = False
        return out
    checks["pid_match_expected"] = True

    if pid_path.is_file():
        try:
            file_pid = int(pid_path.read_text(encoding="utf-8").strip().split()[0])
        except Exception:
            file_pid = 0
        if file_pid and file_pid != pid:
            out["reason"] = f"pid_file_mismatch:file={file_pid} status={pid}"
            checks["pid_file"] = False
            return out
    checks["pid_file"] = True

    spawn: dict[str, Any] = {}
    if spawn_path.is_file():
        try:
            spawn = _read_json(spawn_path)
            if str(spawn.get("trading_date") or "") not in ("", day):
                out["reason"] = f"spawn_trading_date_mismatch:{spawn.get('trading_date')}"
                checks["spawn_trading_date"] = False
                return out
            if int(spawn.get("pid") or 0) not in (0, pid):
                out["reason"] = f"spawn_pid_mismatch:{spawn.get('pid')}"
                checks["spawn_pid"] = False
                return out
        except Exception as exc:
            out["reason"] = f"spawn_meta_unreadable:{type(exc).__name__}"
            return out
    checks["spawn_meta"] = True

    spawn_nonce = str(spawn.get("launch_nonce") or "")
    if not spawn_nonce:
        out["reason"] = "current_run_identity:missing_spawn_launch_nonce"
        checks["current_run_identity"] = False
        return out
    identity = evaluate_current_run_online(
        status,
        expected={
            "launch_nonce": spawn_nonce,
            "ingress_run_id": str(spawn.get("ingress_run_id") or ""),
            "activation_id": str(spawn.get("activation_id") or ""),
            "activation_sha": str(spawn.get("activation_sha") or ""),
            "trading_date": day,
            "pid": pid,
            "process_start_identity": str(spawn.get("process_start_identity") or ""),
            "bus_identity": str(spawn.get("bus_identity") or ""),
        },
        query_fn=query_process,
    )
    if not identity.get("ok"):
        out["reason"] = f"current_run_identity:{identity.get('reject_code') or identity.get('reason')}"
        checks["current_run_identity"] = False
        return out
    checks["current_run_identity"] = True
    checks["pid_alive"] = True
    checks["state"] = True
    st = str(status.get("state") or "")
    live = query_process(pid)

    desired = int(status.get("desired_symbol_count") or 0)
    registered = int(status.get("registered_symbol_count") or 0)
    runtime_pending = desired == 0 and registered == 0
    block_reason = str(status.get("entry_block_reason") or "")
    raw_seq = int(status.get("raw_last_sequence") or 0)
    bus = status.get("bus") if isinstance(status.get("bus"), Mapping) else {}
    publish_ok = int((bus or {}).get("publish_ok") or 0)
    # REGISTER_FAILED with desired=50/registered=0: Capture is online and may already
    # be receiving PUSH from a prior Station registration while retrying PUT.
    # Allow reuse so Paper can attach as consumer; ENTRY stays blocked until Capture
    # verifies 50/50. Partial registered (e.g. 10/50) remains fail-closed.
    register_retry_pending = (
        desired == EXPECTED_UNIVERSE_N
        and registered == 0
        and block_reason in ("REGISTER_FAILED", "REGISTERING", "WAITING_FIRST_PUSH", "WAITING_DESIRED_REGISTER")
        and (raw_seq > 0 or publish_ok > 0 or st in ONLINE_STATES)
    )
    if not runtime_pending and not register_retry_pending:
        if desired != EXPECTED_UNIVERSE_N or registered != EXPECTED_UNIVERSE_N:
            out["reason"] = f"universe_not_50_50:desired={desired} registered={registered}"
            checks["universe_50_50"] = False
            return out
        checks["universe_50_50"] = True
    elif register_retry_pending:
        checks["universe_50_50"] = "register_retry_pending"
    else:
        checks["universe_50_50"] = "pending_paper_runtime_register"
    out["runtime_register_pending"] = bool(runtime_pending or register_retry_pending)
    out["register_retry_pending"] = bool(register_retry_pending)

    # Topology: V2 day-dir + ingress_status implies Independent Market Ingress
    checks["topology"] = True
    out.update(
        {
            "ok": True,
            "reason": "reuse_ok",
            "pid": pid,
            "status": st,
            "snapshot": status,
            "output": str(day_dir),
            "desired_symbol_count": desired,
            "registered_symbol_count": registered,
            "expected_symbol_count": int(expected_symbol_count),
            "live": {k: live.get(k) for k in ("exists", "cmdline", "name", "create_time")},
        }
    )
    return out


def attach_existing_ingress(
    *,
    native_root: Path,
    trading_date: str,
    expected_symbol_count: int,
    expected_pid: Optional[int] = None,
) -> dict[str, Any]:
    """Validate and return reuse attach meta. Does not spawn. Does not claim ownership."""
    result = validate_reusable_ingress(
        native_root=native_root,
        trading_date=trading_date,
        expected_symbol_count=expected_symbol_count,
        expected_pid=expected_pid,
    )
    result["spawned"] = False
    result["owned_by_runner"] = False
    result["at"] = now_iso()
    if result.get("ok"):
        # Persist attach marker (does not replace ingress.pid)
        day_dir = ingress_day_dir(Path(native_root), trading_date)
        marker = {
            "at": result["at"],
            "mode": "reuse_capture",
            "pid": result.get("pid"),
            "trading_date": trading_date,
            "topology": TOPOLOGY,
            "owned_by_runner": False,
            "runtime_register_pending": result.get("runtime_register_pending"),
        }
        try:
            (day_dir / "ingress_reuse_attach.json").write_text(
                json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return result
