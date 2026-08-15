"""Spawn Independent Market Ingress (V2) as a supervised child process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from small_paper.ingress_run_identity import (
    CURRENT_INGRESS_NOT_READY,
    DEFAULT_HEARTBEAT_MAX_AGE_SEC,
    ENV_ACTIVATION_ID,
    ENV_ACTIVATION_SHA,
    ENV_BUS_IDENTITY,
    ENV_CERTIFICATION_RUN_ID,
    ENV_INGRESS_RUN_ID,
    ENV_LAUNCH_NONCE,
    ENV_STAGE_RUN_ID,
    MISSING_EXPECTED_LAUNCH_NONCE,
    STALE_INGRESS_STATUS_REJECTED,
    activation_identity,
    append_wait_audit,
    capture_process_start_identity,
    evaluate_current_run_online,
    generate_launch_nonce,
    load_ingress_status_json,
    make_bus_identity,
    make_ingress_run_id,
    stale_fingerprint,
)
from small_paper.local_market_bus import bus_host, bus_port as default_bus_port
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
        try:
            from small_paper.derived_artifact_contract import ENV_RUNTIME_RUN_ID, ensure_runtime_run_id

            ensure_runtime_run_id(environ=env)
            for key in (ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID, ENV_RUNTIME_RUN_ID):
                val = str(os.environ.get(key) or env.get(key) or "").strip()
                if val:
                    env[key] = val
            env.setdefault("TRADEBOT_TRADING_DATE", str(trading_date))
        except Exception:
            pass
        try:
            from small_paper.auth_lifecycle import ENV_AUTH_PHASE, PHASE_INGRESS_STARTING

            env[ENV_AUTH_PHASE] = PHASE_INGRESS_STARTING
        except Exception:
            pass
    launch_nonce = generate_launch_nonce()
    ingress_run_id = make_ingress_run_id(trading_date=str(trading_date), launch_nonce=launch_nonce)
    activation_id, activation_sha = activation_identity(environ=env)
    host = bus_host(environ=env)
    port = int(bus_port) if bus_port else int(default_bus_port(environ=env))
    bus_identity = make_bus_identity(
        host=str(host),
        port=int(port),
        trading_date=str(trading_date),
        launch_nonce=launch_nonce,
    )
    env[ENV_LAUNCH_NONCE] = launch_nonce
    env[ENV_INGRESS_RUN_ID] = ingress_run_id
    env[ENV_BUS_IDENTITY] = bus_identity
    env[ENV_ACTIVATION_ID] = activation_id
    env[ENV_ACTIVATION_SHA] = activation_sha
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
        "launch_nonce": launch_nonce,
        "ingress_run_id": ingress_run_id,
        "bus_identity": bus_identity,
        "activation_id": activation_id,
        "activation_sha": activation_sha,
        "process_start_identity": capture_process_start_identity(int(proc.pid)),
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
    expected_launch_nonce: str = "",
    expected_ingress_run_id: str = "",
    expected_activation_id: str = "",
    expected_activation_sha: str = "",
    expected_pid: int = 0,
    expected_process_start_identity: str = "",
    expected_bus_identity: str = "",
    heartbeat_max_age_sec: float = DEFAULT_HEARTBEAT_MAX_AGE_SEC,
    query_fn: Any = None,
) -> dict[str, Any]:
    """Accept only current-run identity. Stale files are rejected, not ONLINE.

    Does not delete ingress_status.json. Timeout → CURRENT_INGRESS_NOT_READY.
    """
    status_path = Path(native_root) / "data" / "market_capture" / trading_date / "ingress_status.json"
    audit_path = Path(native_root) / "data" / "market_capture" / trading_date / "ingress_wait_audit.jsonl"
    nonce = str(expected_launch_nonce or "").strip()
    if not nonce:
        return {
            "ok": False,
            "reason": MISSING_EXPECTED_LAUNCH_NONCE,
            "status": CURRENT_INGRESS_NOT_READY,
            "stale_status_rejected_count": 0,
        }
    expected = {
        "launch_nonce": nonce,
        "ingress_run_id": str(expected_ingress_run_id or "").strip(),
        "activation_id": str(expected_activation_id or "").strip(),
        "activation_sha": str(expected_activation_sha or "").strip(),
        "trading_date": str(trading_date),
        "pid": int(expected_pid or 0),
        "process_start_identity": str(expected_process_start_identity or "").strip(),
        "bus_identity": str(expected_bus_identity or "").strip(),
    }
    deadline = time.monotonic() + float(timeout_sec)
    last: dict[str, Any] = {}
    last_eval: dict[str, Any] = {}
    seen_stale: set[str] = set()
    stale_status_rejected_count = 0
    while time.monotonic() < deadline:
        payload, err = load_ingress_status_json(status_path)
        if payload is None:
            last = {"error": err}
            time.sleep(0.25)
            continue
        last = payload
        last_eval = evaluate_current_run_online(
            payload,
            expected=expected,
            heartbeat_max_age_sec=heartbeat_max_age_sec,
            query_fn=query_fn,
            require_registered_count=require_registered_count,
        )
        if last_eval.get("ok"):
            auth_wait: dict[str, Any] = {}
            try:
                from small_paper.auth_lifecycle import (
                    DECISION_PASS,
                    PHASE_POST_INGRESS_PRE_BOARD,
                    consumer_auth_outcome,
                )
                from small_paper.kabu_token_authority import (
                    cert_live_auth_required,
                    current_stage_token_identity,
                )

                want_stage = str(current_stage_token_identity().get("stage_run_id") or "").strip()
                require_identity = bool(want_stage) or cert_live_auth_required()
                if require_identity:
                    outcome = consumer_auth_outcome(
                        native_root=Path(native_root),
                        trading_date=str(trading_date),
                        caller="wait_ingress_online",
                        phase=PHASE_POST_INGRESS_PRE_BOARD,
                    )
                    auth_wait = dict(outcome.get("decision") or {})
                    if str(auth_wait.get("decision") or "") != DECISION_PASS:
                        last_eval = dict(last_eval)
                        last_eval["auth_decision"] = auth_wait
                        last_eval["ok"] = False
                        last_eval["reject_code"] = "current_stage_token_identity_not_proven"
                        time.sleep(0.25)
                        continue
            except Exception as auth_exc:
                last_eval = dict(last_eval)
                last_eval["ok"] = False
                last_eval["auth_wait_error"] = f"{type(auth_exc).__name__}: {auth_exc}"
                time.sleep(0.25)
                continue
            return {
                "ok": True,
                "status": str(payload.get("state") or ""),
                "pid": payload.get("pid"),
                "snapshot": payload,
                "stale_status_rejected_count": stale_status_rejected_count,
                "launch_nonce": payload.get("launch_nonce"),
                "ingress_run_id": payload.get("ingress_run_id"),
                "process_start_identity": payload.get("process_start_identity"),
                "auth_decision": auth_wait or {"decision": "PASS", "reason": "identity_not_required"},
            }
        fp = stale_fingerprint(payload, str(last_eval.get("reject_code") or ""))
        if fp not in seen_stale:
            seen_stale.add(fp)
            stale_status_rejected_count += 1
            append_wait_audit(
                audit_path,
                {
                    "at_unix": time.time(),
                    "event": STALE_INGRESS_STATUS_REJECTED,
                    "reject_code": last_eval.get("reject_code"),
                    "pid": payload.get("pid"),
                    "launch_nonce": payload.get("launch_nonce"),
                    "expected_launch_nonce": nonce,
                    "ingress_run_id": payload.get("ingress_run_id"),
                    "trading_date": trading_date,
                },
            )
        time.sleep(0.25)
    return {
        "ok": False,
        "reason": CURRENT_INGRESS_NOT_READY,
        "status": CURRENT_INGRESS_NOT_READY,
        "last": last,
        "last_eval": last_eval,
        "stale_status_rejected_count": stale_status_rejected_count,
    }
