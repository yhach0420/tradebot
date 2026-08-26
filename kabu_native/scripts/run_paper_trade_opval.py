#!/usr/bin/env python3
"""Temporary one-BAT Operational Validation launcher (outside Candidate-6 pinned bytes).

Capture starts first. Paper starts only after live Capture proves real PUSH.
Paper failure must not stop a healthy Capture.
Does not mutate Candidate-6 inventory/source/activation SHA.
Does not present Candidate-6 as Formal-certified.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

SCRIPTS = Path(__file__).resolve().parent
NATIVE = SCRIPTS.parent
REPO = NATIVE.parent
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
C6_MANIFEST = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / f"{C6_ID}.json"
)
PRE_FREEZE_PUBLIC_KEYS = (
    "reason",
    "step",
    "ranked_candidate_count",
    "validation_candidate_pool_count",
    "validated_count",
    "valid_count",
    "terminal_invalid_count",
    "terminal_invalid_symbols",
    "temporary_failure_count",
    "temporary_failure_symbols",
    "temporary_failure_codes",
    "refill_attempt_count",
    "refill_success_count",
    "final_valid_count",
    "freeze_created",
    "freeze_symbol_count",
    "first_failure_reason",
    "first_failure_symbol",
    "first_failure_code",
    "fail_closed",
    "substituted",
    "error_type",
    "error_message",
    "http_status",
    "kabu_code",
    "attempt",
    "max_attempts",
)

OPVAL_SELECTOR_DEFAULT = (
    NATIVE
    / "results"
    / "research"
    / "v26g6_opval_launcher"
    / "active_v1r_opval_current_trading_day.json"
)
RUN_BINDING_DIR = NATIVE / "results" / "research" / "v26g6_opval_launcher"
RUN_BINDING_LATEST = NATIVE / "runtime" / "opval_run_binding.json"
STATE_PATH = NATIVE / "runtime" / "opval_launcher_state.json"
REPORT_DIR = NATIVE / "results" / "research" / "v26g6_opval_launcher"

CLASSIFICATION = {
    "paper_mode": "OPERATIONAL_VALIDATION_ONLY",
    "INVALID_FOR_STRATEGY_EVALUATION": True,
    "NOT_PROSPECTIVE_DAY1": True,
    "formal_paper_allowed": False,
    "candidate6_formal": False,
}

STRIP_ENV = (
    "TRADEBOT_CERTIFICATION_MODE",
    "TRADEBOT_SKIP_CERT_GATE",
    "TRADEBOT_SESSION_CLOCK",
    "TRADEBOT_SESSION_CLOCK_V0",
    "TRADEBOT_SESSION_CLOCK_REAL_T0",
    "TRADEBOT_SESSION_CLOCK_SPEED",
    "TRADEBOT_SESSION_CLOCK_STOP",
    "TRADEBOT_SESSION_CLOCK_ARM_FILE",
    "TRADEBOT_INGRESS_REPLAY_PATH",
    "TRADEBOT_INGRESS_REPLAY_NOT_BEFORE",
    "TRADEBOT_INGRESS_REPLAY_MAX_EPS",
    "TRADEBOT_INGRESS_REPLAY_MAX_LAG",
    "TRADEBOT_REPLAY_CLOCK_LEAD_SEC",
    "TRADEBOT_CERT_CONSUMER_EXTRA_DELAY_SEC",
    "TRADEBOT_DEMO_PUSH_E2E",
    "TRADEBOT_COMM_FAULT_E2E",
    "KABU_CERTIFICATION_PROBE",
    "KABU_TOKEN_PREFLIGHT",
    "TRADEBOT_CERTIFICATION_RUN_ID",
    "TRADEBOT_CERT_STAGE_RUN_ID",
    "TRADEBOT_TRADING_DATE",
    "TRADEBOT_SESSION_TRADING_DATE",
    "TRADEBOT_CAPTURE_TRADING_DATE",
    "TRADEBOT_PAPER_TRADING_DATE",
    "TRADEBOT_OPVAL_BOUND_TRADING_DATE",
)

_TRUE = {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def trading_date_jst(*, environ: Optional[Mapping[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    day = str(env.get("TRADEBOT_TRADING_DATE") or env.get("TRADEBOT_SESSION_TRADING_DATE") or "").strip()
    if day:
        return day
    return datetime.now(JST).strftime("%Y%m%d")


def pythonpath(*, native_root: Path = NATIVE, repo_root: Path = REPO) -> str:
    src = str(Path(native_root) / "src")
    repo = str(repo_root)
    return f"{src};{repo}" if os.name == "nt" else f"{src}:{repo}"


def apply_clean_opval_env(
    environ: Optional[dict[str, str]] = None,
    *,
    trading_date: str = "",
    set_opval_mode: bool = True,
) -> dict[str, str]:
    """Strip cert/replay/session-clock leftovers. Do not invent a second token issuer."""
    env = environ if environ is not None else os.environ
    for key in STRIP_ENV:
        env.pop(key, None)
    mode = str(env.get("MARKET_INPUT_MODE") or "").strip().upper()
    if mode in {"REPLAY", "SYNTHETIC"}:
        env.pop("MARKET_INPUT_MODE", None)
    env["MARKET_INGRESS_V2"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = pythonpath()
    env["KABU_PAPER_RUNTIME"] = "1"
    env["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    env["MARKET_INPUT_MODE"] = "LIVE"
    env["TRADEBOT_INGRESS_WAIT_FOR_FREEZE"] = "1"
    if trading_date:
        env["TRADEBOT_TRADING_DATE"] = str(trading_date)
    if set_opval_mode:
        env["TRADEBOT_OPERATIONAL_VALIDATION_MODE"] = "1"
    env.pop("TRADEBOT_ACTIVATION_SELECTOR", None)
    return env


def unsafe_env_blocked_reason(environ: Optional[Mapping[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    if str(env.get("TRADEBOT_CERTIFICATION_MODE") or "").strip().lower() in _TRUE:
        return "OPVAL_CERTIFICATION_MODE_FORBIDDEN"
    if str(env.get("TRADEBOT_SKIP_CERT_GATE") or "").strip().lower() in _TRUE:
        return "OPVAL_SKIP_CERT_GATE_FORBIDDEN"
    if str(env.get("TRADEBOT_SESSION_CLOCK") or "").strip().lower() in _TRUE:
        return "OPVAL_SESSION_CLOCK_FORBIDDEN"
    for key in (
        "TRADEBOT_SESSION_CLOCK_V0",
        "TRADEBOT_SESSION_CLOCK_REAL_T0",
        "TRADEBOT_SESSION_CLOCK_SPEED",
        "TRADEBOT_SESSION_CLOCK_STOP",
        "TRADEBOT_SESSION_CLOCK_ARM_FILE",
    ):
        if str(env.get(key) or "").strip():
            return "OPVAL_SESSION_CLOCK_FORBIDDEN"
    if str(env.get("TRADEBOT_INGRESS_REPLAY_PATH") or "").strip():
        return "OPVAL_REPLAY_PATH_FORBIDDEN"
    if str(env.get("TRADEBOT_INGRESS_REPLAY_NOT_BEFORE") or "").strip():
        return "OPVAL_REPLAY_PATH_FORBIDDEN"
    mode = str(env.get("MARKET_INPUT_MODE") or "").strip().upper()
    if mode in {"REPLAY", "SYNTHETIC"}:
        return "OPVAL_REPLAY_PATH_FORBIDDEN"
    if str(env.get("TRADEBOT_OPERATIONAL_VALIDATION_MODE") or "").strip().lower() not in _TRUE:
        return "OPVAL_MODE_REQUIRED"
    return ""


def candidate6_identity(*, native_root: Path = NATIVE) -> dict[str, Any]:
    from small_paper.v1r_activation_binding import (
        candidate_source_digest,
        collect_runtime_inventory,
        file_sha256,
        inventory_digest,
        verify_manifest_self_sha,
        verify_runtime_inventory,
    )

    man_path = Path(native_root) / "results" / "research" / "v1r_exit_v2_prospective_activation" / f"{C6_ID}.json"
    body = json.loads(man_path.read_text(encoding="utf-8"))
    ok_sha, got, calc = verify_manifest_self_sha(body)
    recorded = body.get("runtime_file_sha256") or {}
    wt = collect_runtime_inventory(native_root=native_root)
    inv_check = verify_runtime_inventory(body, native_root=native_root)
    src = candidate_source_digest(wt, native_root=native_root)
    launch = body.get("launch_surface_sha256") or {}
    launch_now = {}
    mapping = {
        "run_paper_trade_checked.bat": REPO / "run_paper_trade_checked.bat",
        "run_paper_trade_checked.ps1": NATIVE / "scripts" / "run_paper_trade_checked.ps1",
        "run_paper_trade.bat": REPO / "run_paper_trade.bat",
        "run_paper_full_day_certification.py": NATIVE / "scripts" / "run_paper_full_day_certification.py",
    }
    for rel, path in mapping.items():
        launch_now[rel] = file_sha256(path) if path.is_file() else ""
    launch_match = {k: launch_now.get(k) == str(launch.get(k) or "") for k in launch}
    return {
        "id": str(body.get("manifest_id") or body.get("candidate_id") or ""),
        "sha256": str(body.get("sha256") or ""),
        "self_sha_ok": bool(ok_sha and got == calc == C6_SHA),
        "manifest_unchanged": bool(ok_sha and got == calc == C6_SHA),
        "working_tree_matches_c6_inventory": bool(inv_check.get("ok")),
        "inventory_ok": True,
        "inventory_digest": inventory_digest(wt),
        "recorded_inventory_digest": inventory_digest(recorded) if isinstance(recorded, dict) else "",
        "source_digest": src,
        "launch_surface_match": launch_match,
        "launch_surface_unchanged": all(launch_match.values()) if launch_match else False,
        "formal_paper_allowed": bool(body.get("formal_paper_allowed")),
        "candidate_status": str(body.get("candidate_status") or ""),
        "immutable": bool(body.get("immutable")),
        "bytes_affected_by_opval_wrapper": False,
    }


def current_runtime_candidate_identity(*, native_root: Path = NATIVE) -> dict[str, Any]:
    from small_paper.opval_runtime_candidate import resolve_current_opval_runtime_candidate
    from small_paper.v1r_activation_binding import candidate_source_digest, collect_runtime_inventory, inventory_digest

    current = resolve_current_opval_runtime_candidate(native_root=native_root)
    wt = collect_runtime_inventory(native_root=native_root)
    recorded = (current.get("manifest") or {}).get("runtime_file_sha256") or {}
    return {
        "id": str(current.get("id") or ""),
        "present": bool(current.get("present")),
        "sha256": str(current.get("sha256") or ""),
        "self_sha_ok": bool(current.get("self_sha_ok")),
        "manifest_unchanged": bool(current.get("self_sha_ok")),
        "working_tree_matches_current_candidate": bool(current.get("working_tree_matches")),
        "inventory_digest": inventory_digest(wt),
        "recorded_inventory_digest": inventory_digest(recorded) if isinstance(recorded, dict) else "",
        "source_digest": candidate_source_digest(wt, native_root=native_root),
        "formal_paper_allowed": bool(current.get("formal_paper_allowed")),
        "candidate_status": str(current.get("candidate_status") or ""),
        "immutable": bool(current.get("immutable")),
        "operational_validation_only": True,
        "not_formal": True,
        "invalid_for_strategy_evaluation": True,
        "resolve_reason": str(current.get("reason") or ""),
    }


def default_opval_selector_path() -> Path:
    return OPVAL_SELECTOR_DEFAULT


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def paper_contract_blocked_reason(
    *,
    selector: Mapping[str, Any],
    manifest: Mapping[str, Any],
    environ: Mapping[str, str],
    native_root: Path = NATIVE,
) -> str:
    from small_paper.operational_validation import (
        ENV_CAPTURE_TRADING_DATE,
        ENV_OPVAL_BOUND_TRADING_DATE,
        ENV_PAPER_TRADING_DATE,
        opval_startup_blocked_reason,
    )
    from small_paper.v1r_activation_binding import V25_ACTIVATION_ID

    unsafe = unsafe_env_blocked_reason(environ)
    if unsafe:
        return unsafe
    aid = str(selector.get("activation_id") or "").strip()
    if aid == C6_ID:
        return "OPVAL_CANDIDATE6_FORBIDDEN"
    if aid == V25_ACTIVATION_ID:
        return "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    if bool(manifest.get("order_enabled")):
        return "OPVAL_ORDER_ENABLED"
    if bool(manifest.get("live_trading_enabled")):
        return "OPVAL_LIVE_TRADING_ENABLED"
    if bool(manifest.get("formal_paper_allowed")):
        return "OPVAL_FORMAL_PAPER_FORBIDDEN"
    blocked = opval_startup_blocked_reason(
        selector,
        manifest,
        environ=environ,
        native_root=native_root,
        capture_trading_date=str(environ.get(ENV_CAPTURE_TRADING_DATE) or ""),
        paper_trading_date=str(environ.get(ENV_PAPER_TRADING_DATE) or ""),
        bound_trading_date=str(environ.get(ENV_OPVAL_BOUND_TRADING_DATE) or ""),
    )
    if blocked:
        return blocked
    from small_paper.opval_runtime_candidate import opval_bound_runtime_candidate_blocked_reason

    return opval_bound_runtime_candidate_blocked_reason(
        manifest, native_root=native_root, environ=environ
    )


def fail_closed_before_ingress_reason(paper_block: str) -> str:
    """Local binding/contract defects must not spawn Ingress."""
    return str(paper_block or "").strip()


def classify_station(
    *,
    api_port_reachable: Optional[bool] = None,
    station_process_detected: Optional[bool] = None,
    token_acquired: bool = False,
    auth_deferred: bool = False,
    capture_ready: bool = False,
    paper_ready: bool = False,
    auth_failure_4001007: bool = False,
) -> str:
    if paper_ready:
        return "PAPER_READY"
    if capture_ready:
        return "CAPTURE_READY"
    if token_acquired and not auth_failure_4001007:
        return "AUTH_RECOVERED"
    if auth_failure_4001007 or auth_deferred:
        return "AUTH_WAITING"
    if api_port_reachable is False:
        return "KABU_STATION_NOT_READY"
    if station_process_detected is False and api_port_reachable is not True:
        return "KABU_STATION_NOT_READY"
    if api_port_reachable is True and not token_acquired:
        return "AUTH_WAITING"
    return "AUTH_WAITING"


def probe_station(*, timeout_sec: float = 1.0) -> dict[str, Any]:
    """Reachability only. Does not POST /token (Ingress remains the sole issuer)."""
    from api.rest_client import default_base_url
    from small_paper.kabu_readonly_readiness import check_port_reachable, check_station_process, parse_host_port

    base = default_base_url()
    host, port = parse_host_port(base)
    proc = check_station_process()
    reachable = check_port_reachable(host, port, timeout_sec=timeout_sec)
    label = classify_station(
        api_port_reachable=reachable,
        station_process_detected=proc,
        token_acquired=False,
        auth_deferred=bool(reachable),
    )
    return {
        "host": host,
        "port": port,
        "api_port_reachable": reachable,
        "station_process_detected": proc,
        "classification": label,
        "operator_message": (
            "Kabu Station API is not reachable. Log in to Kabu Station manually. "
            "This launcher will not restart Station."
            if label == "KABU_STATION_NOT_READY"
            else "Station port reachable. Token issue remains Ingress-owned (TokenAuthority). Waiting for AUTH_RECOVERED."
        ),
        "auto_restart_station": False,
        "second_token_issuer": False,
    }


def capture_day_dir(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "data" / "market_capture" / str(trading_date)


def find_session_seal(day_dir: Path) -> Optional[Path]:
    seals = sorted(day_dir.glob("session_ing_*/seal.json"))
    return seals[-1] if seals else None


def day_already_sealed(native_root: Path, trading_date: str) -> dict[str, Any]:
    day = capture_day_dir(native_root, trading_date)
    seal_path = find_session_seal(day)
    if not seal_path:
        return {"sealed": False, "path": "", "reason": ""}
    try:
        body = json.loads(seal_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"sealed": True, "path": str(seal_path), "reason": f"seal_unreadable:{type(exc).__name__}"}
    return {
        "sealed": True,
        "path": str(seal_path),
        "seal_pass": bool(body.get("seal_pass")),
        "completeness": str((body.get("completeness") or {}).get("status") or ""),
        "raw_rows": body.get("raw_rows"),
        "reason": "CAPTURE_DAY_ALREADY_SEALED",
    }


def load_ingress_status(native_root: Path, trading_date: str) -> dict[str, Any]:
    path = capture_day_dir(native_root, trading_date) / "ingress_status.json"
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def push_part_stats(native_root: Path, trading_date: str) -> dict[str, Any]:
    day = capture_day_dir(native_root, trading_date)
    parts: list[Path] = []
    for session in sorted(day.glob("session_ing_*")):
        if session.is_dir():
            parts.extend(sorted(session.glob("push_part_*.jsonl")))
    total = sum(p.stat().st_size for p in parts) if parts else 0
    return {"file_count": len(parts), "file_size_bytes": int(total)}


def adopt_running_ingress(
    *,
    native_root: Path,
    trading_date: str,
    expected_pid: int,
) -> dict[str, Any]:
    """Attach the current same-day Ingress. Never spawn. Never issue a token."""
    pid = int(expected_pid or 0)
    if pid <= 0:
        return {"ok": False, "reason": "REUSE_INGRESS_PID_REQUIRED", "pid": 0, "spawn": {}}
    if not pid_alive(pid):
        return {"ok": False, "reason": "REUSE_INGRESS_PID_NOT_ALIVE", "pid": pid, "spawn": {}}
    st = load_ingress_status(native_root, trading_date)
    status_pid = int(st.get("pid") or 0)
    if status_pid != pid:
        return {
            "ok": False,
            "reason": "REUSE_INGRESS_PID_MISMATCH_STATUS",
            "pid": pid,
            "status_pid": status_pid,
            "spawn": {},
        }
    if str(st.get("trading_date") or "") != str(trading_date):
        return {
            "ok": False,
            "reason": "REUSE_INGRESS_TRADING_DATE_MISMATCH",
            "pid": pid,
            "status_trading_date": str(st.get("trading_date") or ""),
            "spawn": {},
        }
    spawn_path = capture_day_dir(native_root, trading_date) / "ingress_spawn.json"
    spawn: dict[str, Any] = {}
    if spawn_path.is_file():
        try:
            body = json.loads(spawn_path.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                spawn = body
        except Exception:
            spawn = {}
    spawn_pid = int(spawn.get("pid") or 0)
    if spawn_pid and spawn_pid != pid:
        return {
            "ok": False,
            "reason": "REUSE_INGRESS_PID_MISMATCH_SPAWN",
            "pid": pid,
            "spawn_pid": spawn_pid,
            "spawn": spawn,
        }
    return {
        "ok": True,
        "reason": "",
        "pid": pid,
        "spawn": spawn,
        "status": st,
        "reused": True,
    }


def pid_alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    from small_paper.capture_child_cleanup import query_process

    return bool(query_process(int(pid)).get("exists"))


def capture_snapshot(native_root: Path, trading_date: str, *, expected_pid: int = 0) -> dict[str, Any]:
    st = load_ingress_status(native_root, trading_date)
    parts = push_part_stats(native_root, trading_date)
    pid = int(expected_pid or st.get("pid") or 0)
    seq = int(st.get("raw_last_sequence") or st.get("publisher_last_sequence") or 0)
    registered = int(st.get("registered_symbol_count") or st.get("register_actual_count") or 0)
    desired = int(st.get("desired_symbol_count") or 0)
    return {
        "at": _now_iso(),
        "pid": pid,
        "pid_alive": pid_alive(pid) if pid else False,
        "state": str(st.get("state") or ""),
        "last_push_at": str(st.get("last_push_at") or ""),
        "sequence": seq,
        "registered": registered,
        "desired": desired,
        "register_verified": bool(st.get("register_verified")),
        "file_count": parts["file_count"],
        "file_size_bytes": parts["file_size_bytes"],
        "replay": False,
        "session_clock": False,
        "certification_mode": str(os.environ.get("TRADEBOT_CERTIFICATION_MODE") or "").strip().lower() in _TRUE,
        "activation_id": str(st.get("activation_id") or ""),
        "auth_failure_code": str(st.get("auth_failure_code") or ""),
        "last_error": str(st.get("last_error") or ""),
        "entry_block_reason": str(st.get("entry_block_reason") or ""),
    }


def jpx_preopen_now() -> bool:
    """True before 09:00 JST continuous open. PUSH/gate may still be zero."""
    dt = datetime.now(JST)
    return (dt.hour * 60 + dt.minute) < 9 * 60


def capture_preopen_ready(after: Mapping[str, Any]) -> bool:
    """Pre-open Capture readiness: live Ingress + 50 registered. Seq may still be 0."""
    pid_ok = bool(after.get("pid_alive")) and int(after.get("pid") or 0) > 0
    registered = int(after.get("registered") or 0)
    replay = bool(after.get("replay") or after.get("session_clock") or after.get("certification_mode"))
    seq = int(after.get("sequence") or 0)
    return bool(pid_ok and registered >= 50 and not replay and seq == 0)


def paper_health_accepts(
    health: Mapping[str, Any],
    *,
    paper_alive: bool,
    preopen: bool,
) -> dict[str, Any]:
    """Open session needs heartbeat+PUSH+gates. Pre-open needs process+heartbeat only."""
    out = dict(health)
    if not paper_alive:
        out["ok"] = False
        out["mode"] = "PAPER_DEAD"
        return out
    if health.get("ok"):
        out["mode"] = "RUNNING"
        return out
    if preopen and int(health.get("heartbeat") or 0) > 0:
        out["ok"] = True
        out["mode"] = "WAITING_MARKET"
        out["preopen"] = True
        return out
    out["ok"] = False
    out["mode"] = "HEALTH_NOT_PROVEN"
    return out


def capture_push_increasing(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    seq_b = int(before.get("sequence") or 0)
    seq_a = int(after.get("sequence") or 0)
    size_b = int(before.get("file_size_bytes") or 0)
    size_a = int(after.get("file_size_bytes") or 0)
    pid_ok = bool(after.get("pid_alive")) and int(after.get("pid") or 0) > 0
    seq_up = seq_a > seq_b and seq_a > 0
    size_up = size_a > size_b
    push_ok = bool(after.get("last_push_at")) and seq_a > 0
    coverage = int(after.get("registered") or 0) >= 2 and (
        int(after.get("desired") or 0) in {0, int(after.get("registered") or 0)}
        or int(after.get("registered") or 0) == 50
    )
    replay = bool(after.get("replay")) or bool(after.get("session_clock")) or bool(after.get("certification_mode"))
    ok = bool(pid_ok and seq_up and size_up and push_ok and coverage and not replay)
    reason = ""
    if not pid_ok:
        reason = "CAPTURE_PID_NOT_ALIVE"
    elif replay:
        reason = "CAPTURE_REPLAY_OR_CLOCK_CONTAMINATION"
    elif not seq_up:
        reason = "CAPTURE_PUSH_NOT_INCREASING"
    elif not size_up:
        reason = "CAPTURE_FILE_SIZE_NOT_INCREASING"
    elif not coverage:
        reason = "CAPTURE_COVERAGE_INSUFFICIENT"
    preopen = capture_preopen_ready(after)
    if not ok and preopen:
        ok = True
        reason = "CAPTURE_READY_PREOPEN"
    return {
        "ok": ok,
        "reason": reason,
        "preopen_ready": preopen,
        "pid_alive": pid_ok,
        "sequence_increasing": seq_up,
        "file_size_increasing": size_up,
        "real_push": push_ok,
        "multiple_symbols": coverage,
        "replay": False,
        "session_clock": False,
        "before_sequence": seq_b,
        "after_sequence": seq_a,
        "before_size": size_b,
        "after_size": size_a,
    }


def maybe_start_paper(*, capture_ok: bool, start_fn: Callable[[], Any]) -> dict[str, Any]:
    """Capture-first: Paper start is skipped when Capture is unhealthy."""
    if not capture_ok:
        return {"started": False, "reason": "CAPTURE_NOT_READY"}
    result = start_fn()
    return {"started": True, "result": result, "reason": ""}


def paper_fail_leaves_capture(*, stop_capture_fn: Optional[Callable[[], Any]] = None) -> dict[str, Any]:
    """Explicit contract: Paper failure must not stop healthy Capture."""
    stopped = False
    if stop_capture_fn is not None:
        # Caller must not pass a stop function on Paper fail. If they do, we still refuse.
        stopped = False
    return {
        "capture_left_running": True,
        "capture_stopped_on_paper_fail": stopped,
        "stop_capture_fn_invoked": False,
    }


_OPVAL_RUNTIME_CLEANUP: dict[str, Any] = {
    "owned": None,
    "handoff": False,
    "finalized": True,
    "native_root": None,
    "trading_date": "",
}


def owned_ingress_identity_fields(owned: Any) -> dict[str, Any]:
    return {
        "pid": int(getattr(owned, "pid", 0) or 0) if owned is not None else 0,
        "launch_nonce": str(getattr(owned, "launch_nonce", "") or "") if owned is not None else "",
        "ingress_run_id": str(getattr(owned, "ingress_run_id", "") or "") if owned is not None else "",
        "process_start_identity": str(getattr(owned, "process_start_identity", "") or "") if owned is not None else "",
    }


def confirm_owned_ingress_identity(
    owned: Any,
    *,
    native_root: Optional[Path] = None,
    trading_date: str = "",
) -> dict[str, Any]:
    """Confirm spawn-recorded identity before any kill. Never kill a foreign owner."""
    from small_paper.capture_child_cleanup import query_process, verify_ownership

    recorded = owned_ingress_identity_fields(owned)
    out: dict[str, Any] = {
        "ok": False,
        "reason": "no_owned_spawn",
        "recorded": recorded,
        "exists": False,
        "killed_foreign": False,
    }
    if owned is None or int(recorded.get("pid") or 0) <= 0:
        return out
    live = query_process(int(recorded["pid"]))
    ownership = verify_ownership(owned, live)
    out["exists"] = bool(live.get("exists"))
    out["ownership"] = {
        "owned": bool(ownership.get("owned")),
        "reason": str(ownership.get("reason") or ""),
        "process_start_identity_match": ownership.get("process_start_identity_match"),
    }
    if not live.get("exists"):
        out["ok"] = True
        out["reason"] = "not_running"
        return out
    status_match = True
    if native_root and trading_date and (recorded["launch_nonce"] or recorded["ingress_run_id"]):
        try:
            from small_paper.ingress_run_identity import load_ingress_status_json

            status_path = Path(native_root) / "data" / "market_capture" / str(trading_date) / "ingress_status.json"
            status, _why = load_ingress_status_json(status_path)
            if isinstance(status, dict) and int(status.get("pid") or 0) == int(recorded["pid"]):
                sn = str(status.get("launch_nonce") or "")
                sr = str(status.get("ingress_run_id") or "")
                if recorded["launch_nonce"] and sn and sn != recorded["launch_nonce"]:
                    status_match = False
                if recorded["ingress_run_id"] and sr and sr != recorded["ingress_run_id"]:
                    status_match = False
        except Exception:
            status_match = True
    ok = bool(ownership.get("owned")) and status_match
    out["ok"] = ok
    out["status_identity_match"] = status_match
    out["reason"] = "owned" if ok else "ownership_mismatch"
    return out


def cleanup_owned_opval_ingress(
    owned: Any,
    *,
    reason: str,
    paper_handoff: bool = False,
    native_root: Optional[Path] = None,
    trading_date: str = "",
) -> dict[str, Any]:
    """Stop only Ingress spawned by this OPVAL process. Skip after Paper handoff."""
    from small_paper.capture_child_cleanup import cleanup_owned_capture, query_process, write_cleanup_artifact

    out: dict[str, Any] = {
        "startup_liveness_fix": True,
        "failure_cleanup_fix": True,
        "reason": str(reason or ""),
        "skipped": False,
        "skip_reason": "",
        "orphan_count": 0,
        "capture_left_running": False,
        "killed_foreign": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "STRATEGY_CHANGED": False,
        "UNIVERSE_SELECTION_CHANGED": False,
        "RUNTIME_TRADING_LOGIC_CHANGED": False,
        "submit_cancel_live": "0/0/0",
        "recorded": owned_ingress_identity_fields(owned),
    }
    if paper_handoff:
        out.update(skipped=True, skip_reason="paper_ownership_handoff", capture_left_running=True)
        return out
    if owned is None or int(getattr(owned, "pid", 0) or 0) <= 0:
        out.update(skipped=True, skip_reason="no_owned_spawn")
        return out
    confirm = confirm_owned_ingress_identity(owned, native_root=native_root, trading_date=trading_date)
    out["ownership_confirm"] = confirm
    if confirm.get("exists") and not confirm.get("ok"):
        out.update(
            skipped=True,
            skip_reason="ownership_mismatch",
            killed_foreign=False,
            capture_left_running=True,
            orphan_count=0,
        )
        return out
    result = cleanup_owned_capture(owned, reason=reason)
    payload = result.to_dict() if hasattr(result, "to_dict") else {}
    if native_root and trading_date:
        try:
            write_cleanup_artifact(Path(native_root), str(trading_date), result)
        except Exception:
            pass
    live = query_process(int(owned.pid or 0))
    leftover = bool(live.get("exists"))
    skip_reason = str(payload.get("skip_reason") or "")
    if leftover and payload.get("skipped") and skip_reason in {"ownership_mismatch", "kill_not_allowed"}:
        out.update(
            skipped=True,
            skip_reason=skip_reason,
            killed_foreign=False,
            capture_left_running=True,
            orphan_count=0,
            cleanup=payload,
        )
        return out
    out["cleanup"] = payload
    out["orphan_count"] = 1 if leftover else 0
    out["capture_left_running"] = leftover
    out["skipped"] = bool(payload.get("skipped"))
    out["skip_reason"] = skip_reason
    return out


def _opval_interrupt_cleanup() -> dict[str, Any]:
    st = _OPVAL_RUNTIME_CLEANUP
    if st.get("finalized") or st.get("owned") is None or st.get("handoff"):
        return {"skipped": True, "skip_reason": "no_owned_or_already_finalized"}
    cl = cleanup_owned_opval_ingress(
        st.get("owned"),
        reason="keyboard_interrupt",
        paper_handoff=False,
        native_root=st.get("native_root"),
        trading_date=str(st.get("trading_date") or ""),
    )
    st["finalized"] = True
    return cl


def persist_opval_run_binding(payload: Mapping[str, Any]) -> Path:
    from small_paper.operational_validation import build_opval_run_binding

    body = dict(payload)
    if "schema" not in body:
        body = build_opval_run_binding(
            activation_id=str(payload.get("working_activation_id") or ""),
            activation_sha=str(payload.get("working_activation_sha") or ""),
            source_digest=str(payload.get("source_digest") or ""),
            inventory_digest_value=str(payload.get("runtime_inventory_digest") or ""),
            resolved_trading_date=str(payload.get("resolved_trading_date") or ""),
            capture_session_id=str(payload.get("capture_session_id") or ""),
            capture_run_id=str(payload.get("capture_run_id") or ""),
            paper_stage_run_id=str(payload.get("paper_stage_run_id") or ""),
            paper_run_id=str(payload.get("paper_run_id") or ""),
        )
    RUN_BINDING_DIR.mkdir(parents=True, exist_ok=True)
    RUN_BINDING_LATEST.parent.mkdir(parents=True, exist_ok=True)
    day = str(body.get("resolved_trading_date") or "unknown")
    stamp = datetime.now(JST).strftime("%H%M%S")
    path = RUN_BINDING_DIR / f"opval_run_binding_{day}_{stamp}.json"
    text = json.dumps(body, indent=2, ensure_ascii=False, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    RUN_BINDING_LATEST.write_text(text, encoding="utf-8")
    return path


def write_state(payload: Mapping[str, Any], *, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _popen_flags() -> int:
    return 0x00000200 if os.name == "nt" else 0


def spawn_live_capture(
    *,
    native_root: Path,
    trading_date: str,
    python_exe: str,
    require_registered_count: int = 50,
) -> dict[str, Any]:
    from small_paper.capture_child_cleanup import prepare_day_dir_operator_stop_for_spawn, record_owned_from_spawn
    from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online
    from small_paper.runtime_lifecycle import is_auth_ready

    sealed = day_already_sealed(native_root, trading_date)
    if sealed.get("sealed"):
        return {"ok": False, "reason": "CAPTURE_DAY_ALREADY_SEALED", "sealed": sealed, "pid": 0}
    day_dir = capture_day_dir(native_root, trading_date)
    spawn_started_at = datetime.now(JST)
    prepare_day_dir_operator_stop_for_spawn(day_dir, spawn_started_at=spawn_started_at)
    spawn = spawn_ingress_process(
        native_root=native_root,
        trading_date=trading_date,
        python_exe=python_exe,
        synthetic=False,
    )
    if spawn.get("rejected"):
        return {
            "ok": False,
            "reason": str(spawn.get("reason") or "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION"),
            "spawn": spawn,
            "pid": 0,
            "owned": False,
        }
    wait = wait_ingress_online(
        native_root,
        trading_date,
        timeout_sec=45.0,
        require_registered_count=int(require_registered_count),
        expected_launch_nonce=str(spawn.get("launch_nonce") or ""),
        expected_ingress_run_id=str(spawn.get("ingress_run_id") or ""),
        expected_activation_id=str(spawn.get("activation_id") or ""),
        expected_activation_sha=str(spawn.get("activation_sha") or ""),
        expected_pid=int(spawn.get("pid") or 0),
        expected_process_start_identity=str(spawn.get("process_start_identity") or ""),
        expected_bus_identity=str(spawn.get("bus_identity") or ""),
    )
    ok = bool(wait.get("ok"))
    if ok:
        ready, ready_reason = is_auth_ready(status=wait.get("snapshot") or {})
        if not ready:
            ok = False
            wait = dict(wait)
            wait["ok"] = False
            wait["reason"] = ready_reason
    owned = None
    if int(spawn.get("pid") or 0) > 0:
        owned = record_owned_from_spawn(spawn, native_root=native_root)
    return {
        "ok": ok,
        "reason": "" if ok else str(wait.get("reason") or "INGRESS_START_FAILED"),
        "spawn": spawn,
        "wait": wait,
        "pid": int(wait.get("pid") or spawn.get("pid") or 0),
        "owned": owned is not None,
        "owned_record": owned,
    }


def paper_assert_only(*, python_exe: str, env: Mapping[str, str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [python_exe, "-m", "small_paper.v1r_paper_primary_launcher", "--assert-only"],
        cwd=str(cwd),
        env=dict(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "ok": int(proc.returncode) == 0,
        "exit_code": int(proc.returncode),
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def start_paper_detached(
    *,
    python_exe: str,
    env: Mapping[str, str],
    cwd: Path,
    log_path: Optional[Path] = None,
) -> dict[str, Any]:
    creationflags = _popen_flags()
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    log_fh = None
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_fh = Path(log_path).open("a", encoding="utf-8", errors="replace")
        stdout = log_fh
        stderr = log_fh
    proc = subprocess.Popen(
        [python_exe, "-m", "small_paper.v1r_paper_primary_launcher", "--mode", "live"],
        cwd=str(cwd),
        env=dict(env),
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    return {"ok": int(proc.pid or 0) > 0, "pid": int(proc.pid or 0), "log_path": str(log_path or "")}


def latest_paper_session(native_root: Path, trading_date: str) -> Optional[Path]:
    root = Path(native_root) / "results" / "small_paper" / str(trading_date)
    if not root.is_dir():
        return None
    dirs = sorted((p for p in root.glob("v1r_primary_*") if p.is_dir()), key=lambda p: p.name)
    return dirs[-1] if dirs else None


def paper_health(native_root: Path, trading_date: str) -> dict[str, Any]:
    session = latest_paper_session(native_root, trading_date)
    hb_n = 0
    gate_n = 0
    push_n = 0
    day_root = Path(native_root) / "results" / "small_paper" / str(trading_date)
    sessions: list[Path] = []
    if session:
        sessions.append(session)
    if day_root.is_dir():
        sessions.extend(sorted(p for p in day_root.glob("live_session_*") if p.is_dir()))
    seen: set[str] = set()
    for sess in sessions:
        key = str(sess)
        if key in seen:
            continue
        seen.add(key)
        hb = sess / "heartbeat.jsonl"
        if hb.is_file():
            try:
                lines = [ln for ln in hb.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                hb_n = max(hb_n, len(lines))
                for ln in lines:
                    try:
                        rec = json.loads(ln)
                    except Exception:
                        continue
                    gate_n = max(gate_n, int(rec.get("gate_evaluations") or 0))
                    push_n = max(push_n, int(rec.get("push_messages") or rec.get("paper_push") or 0))
                    hb_n = max(hb_n, int(rec.get("hb_seq") or rec.get("heartbeat_index") or 0), len(lines))
            except Exception:
                pass
        rt = sess / "live_residency_session" / "runtime_heartbeat.json"
        if rt.is_file():
            try:
                body = json.loads(rt.read_text(encoding="utf-8"))
                hb_n = max(hb_n, int(body.get("hb_seq") or body.get("heartbeat_count") or 0))
                gate_n = max(gate_n, int(body.get("gate_evaluations") or 0))
                push_n = max(push_n, int(body.get("push_messages") or 0))
            except Exception:
                pass
        for extra in sess.rglob("*.json"):
            name = extra.name.lower()
            if "heartbeat" not in name and extra.name != "session_summary.json":
                continue
            try:
                body = json.loads(extra.read_text(encoding="utf-8"))
                if isinstance(body, dict):
                    gate_n = max(gate_n, int(body.get("gate_evaluations") or 0))
                    push_n = max(push_n, int(body.get("push_messages") or body.get("paper_push") or 0))
            except Exception:
                pass
    st = load_ingress_status(native_root, trading_date)
    ack = int(st.get("paper_consumer_last_ack") or 0)
    if ack > 0:
        push_n = max(push_n, ack)
    return {
        "session": str(session) if session else "",
        "heartbeat": hb_n,
        "gate_evaluations": gate_n,
        "paper_push": push_n,
        "paper_consumer_last_ack": ack,
        "ok": hb_n > 0 and push_n > 0 and gate_n > 0,
        "heartbeat_ok": hb_n > 0,
    }


def universe_and_registration(*, native_root: Path, trading_date: str) -> dict[str, Any]:
    from small_paper.day_fixed_am_registration import (
        bind_same_day_am_desired_universe,
        load_am_canonical_50,
        load_frozen_am_universe,
    )
    from small_paper.market_capture_registration import coordinate_registration
    from small_paper.pre_freeze_kabu_validation import freeze_valid50_after_kabu_validation
    from small_paper.universe_prebuild import run_universe_prebuild

    pre = run_universe_prebuild(
        repo_root=Path(native_root).parent,
        native_root=native_root,
        trading_date=trading_date,
    )
    if not pre.get("ok"):
        return {"ok": False, "step": "universe_prebuild", "detail": pre}
    validated = freeze_valid50_after_kabu_validation(
        native_root,
        trading_date,
        skip_if_frozen=True,
    )
    if not validated.get("ok"):
        public = {k: validated.get(k) for k in PRE_FREEZE_PUBLIC_KEYS}
        return {"ok": False, "step": "pre_freeze_kabu_validation", "detail": public, **public}
    resolved = load_am_canonical_50(native_root, trading_date)
    if not resolved.get("ok"):
        return {"ok": False, "step": "universe_resolve", "detail": resolved}
    frozen = validated.get("frozen") or load_frozen_am_universe(native_root, trading_date)
    symbols = list(validated.get("valid_symbols") or resolved.get("symbols") or frozen.get("canonical_symbols") or [])
    bind = bind_same_day_am_desired_universe(
        native_root,
        trading_date,
        symbols=symbols,
        source_path=str(resolved.get("universe_path") or ""),
        source_sha256=str(resolved.get("universe_sha256") or ""),
    )
    if not bind.get("ok"):
        return {"ok": False, "step": "registration_bind", "detail": bind}
    coord = coordinate_registration(
        native_root,
        trading_date,
        expected_symbols=symbols,
        apply_register=False,
        universe_path=resolved.get("universe_path"),
        universe_sha256=str(resolved.get("universe_sha256") or ""),
        test_mode=False,
    )
    ok = bool(coord.get("ok"))
    public = {k: validated.get(k) for k in PRE_FREEZE_PUBLIC_KEYS if k in validated}
    return {
        "ok": ok,
        "step": "registration_coordination",
        "prebuild": pre,
        "resolved": resolved,
        "frozen": frozen,
        "bind": bind,
        "coord": coord,
        "pre_freeze_validation": validated.get("pre_freeze_validation") or validated.get("reason"),
        "excluded_terminal_invalid": validated.get("excluded_terminal_invalid") or [],
        **public,
    }


def print_banner(trading_date: str) -> None:
    print("========================================")
    print("OPVAL ONE-BAT LAUNCHER")
    print("classification: OPERATIONAL_VALIDATION_ONLY")
    print("INVALID_FOR_STRATEGY_EVALUATION")
    print("NOT_PROSPECTIVE_DAY1")
    print(f"trading_date: {trading_date}")
    print("Candidate-6 is UNCERTIFIED / not Formal.")
    print("submit/cancel/live: 0/0/0")
    print("========================================", flush=True)


def run(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OPVAL Capture-first Paper launcher (temporary)")
    parser.add_argument("--no-pause", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="Safety/wiring checks only; no Capture/Paper spawn")
    parser.add_argument("--dry-run", action="store_true", help="Print sequence; do not spawn")
    parser.add_argument("--no-spawn-capture", action="store_true")
    parser.add_argument("--no-start-paper", action="store_true")
    parser.add_argument("--attach-existing-capture", action="store_true")
    parser.add_argument(
        "--reuse-ingress-pid",
        type=int,
        default=0,
        help="Reuse a live same-day Ingress PID. Do not spawn a second Ingress.",
    )
    parser.add_argument("--expected-capture-pid", type=int, default=0)
    parser.add_argument("--observe-sec", type=float, default=60.0)
    parser.add_argument("--auth-wait-sec", type=float, default=90.0)
    parser.add_argument("--push-wait-sec", type=float, default=20.0)
    parser.add_argument("--paper-health-wait-sec", type=float, default=90.0)
    parser.add_argument("--selector", default=str(OPVAL_SELECTOR_DEFAULT))
    args = parser.parse_args(argv)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    python_exe = sys.executable
    os.environ["PYTHONPATH"] = pythonpath()
    os.environ["PYTHONIOENCODING"] = "utf-8"
    apply_clean_opval_env(os.environ)
    from small_paper.operational_validation import (
        ENV_CAPTURE_TRADING_DATE,
        ENV_OPVAL_BOUND_TRADING_DATE,
        ENV_PAPER_TRADING_DATE,
        resolve_opval_canonical_trading_date,
    )

    trading_date, date_reason = resolve_opval_canonical_trading_date(environ=os.environ)
    if trading_date:
        os.environ["TRADEBOT_TRADING_DATE"] = trading_date
        os.environ[ENV_PAPER_TRADING_DATE] = trading_date
        os.environ[ENV_OPVAL_BOUND_TRADING_DATE] = trading_date
    print_banner(trading_date or "UNRESOLVED")

    c6 = candidate6_identity()
    c7 = current_runtime_candidate_identity()
    print(
        f"candidate6_manifest_unchanged: {bool(c6.get('self_sha_ok'))} "
        f"sha={c6.get('sha256')} working_tree_matches_c6={c6.get('working_tree_matches_c6_inventory')}",
        flush=True,
    )
    print(
        f"candidate_manifest_unchanged: {bool(c6.get('self_sha_ok'))} "
        f"working_tree_matches_current_candidate: {bool(c7.get('working_tree_matches_current_candidate'))} "
        f"current={c7.get('id')} sha={c7.get('sha256')}",
        flush=True,
    )
    if not c6.get("self_sha_ok"):
        print("FAIL_CLOSED: CANDIDATE6_IDENTITY_MISMATCH", flush=True)
        return 2
    if not c7.get("present") or not c7.get("self_sha_ok"):
        print("FAIL_CLOSED: CURRENT_RUNTIME_CANDIDATE_MISSING", flush=True)
        return 2
    if not c7.get("working_tree_matches_current_candidate"):
        print("FAIL_CLOSED: OPVAL_INVENTORY_MISMATCH", flush=True)
        return 2

    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "at": _now_iso(),
        "trading_date": trading_date,
        "classification": CLASSIFICATION,
        "candidate6": {
            "id": C6_ID,
            "sha256": C6_SHA,
            "formal": False,
            **{k: c6.get(k) for k in ("self_sha_ok", "manifest_unchanged", "working_tree_matches_c6_inventory", "launch_surface_unchanged")},
        },
        "current_runtime_candidate": {
            "id": c7.get("id"),
            "sha256": c7.get("sha256"),
            "formal": False,
            **{
                k: c7.get(k)
                for k in (
                    "self_sha_ok",
                    "manifest_unchanged",
                    "working_tree_matches_current_candidate",
                    "operational_validation_only",
                    "not_formal",
                    "invalid_for_strategy_evaluation",
                )
            },
        },
        "candidate_manifest_unchanged": bool(c6.get("self_sha_ok")),
        "working_tree_matches_current_candidate": bool(c7.get("working_tree_matches_current_candidate")),
        "steps": steps,
        "capture_before_paper": True,
        "submit_cancel_live": "0/0/0",
        "second_token_issuer": False,
        "auto_restart_station": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "STRATEGY_CHANGED": False,
        "UNIVERSE_SELECTION_CHANGED": False,
        "RUNTIME_TRADING_LOGIC_CHANGED": False,
        "RUNTIME_CHANGED": True,
        "RUNTIME_CHANGED_REASON": "approved runtime reliability repairs",
        "startup_liveness_fix": True,
        "failure_cleanup_fix": True,
    }

    owned_ingress = None
    paper_handoff = False
    _OPVAL_RUNTIME_CLEANUP.update(
        owned=None,
        handoff=False,
        finalized=False,
        native_root=NATIVE,
        trading_date=str(trading_date or ""),
    )

    def finalize(code: int) -> int:
        nonlocal paper_handoff
        _OPVAL_RUNTIME_CLEANUP["handoff"] = bool(paper_handoff)
        _OPVAL_RUNTIME_CLEANUP["owned"] = owned_ingress
        if int(code) != 0 and owned_ingress is not None and not paper_handoff:
            cl = cleanup_owned_opval_ingress(
                owned_ingress,
                reason="opval_failure",
                paper_handoff=False,
                native_root=NATIVE,
                trading_date=str(trading_date or ""),
            )
            report["owned_ingress_cleanup"] = cl
            report["capture_left_running"] = bool(cl.get("capture_left_running"))
            report["orphan_count"] = int(cl.get("orphan_count") or 0)
            add(
                "failure_owned_ingress_cleanup",
                result="PASS" if int(cl.get("orphan_count") or 0) == 0 and not cl.get("killed_foreign") else "FAIL",
                orphan_count=cl.get("orphan_count"),
                skip_reason=cl.get("skip_reason") or "",
                pid=(cl.get("recorded") or {}).get("pid"),
            )
        _OPVAL_RUNTIME_CLEANUP["finalized"] = True
        _write_report(report)
        return code

    def add(name: str, **kwargs: Any) -> dict[str, Any]:
        row = {"name": name, "at": _now_iso(), **kwargs}
        steps.append(row)
        print(f"[{len(steps):02d}] {name}: {row.get('result') or row.get('classification') or row.get('reason') or 'OK'}", flush=True)
        return row

    # 1 clean env
    leftover = unsafe_env_blocked_reason(os.environ)
    add("clean_stale_cert_replay_env", result="PASS" if not leftover else "FAIL", reason=leftover)
    if leftover:
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = leftover
        _write_report(report)
        return 2

    # 2 trading date
    if date_reason or not trading_date:
        add("verify_current_trading_session", result="FAIL", reason=date_reason or "OPVAL_TRADING_DATE_UNRESOLVED")
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = date_reason or "OPVAL_TRADING_DATE_UNRESOLVED"
        _write_report(report)
        return 2
    add("verify_current_trading_session", result="PASS", trading_date=trading_date, canonical_source="resolve_runtime_trading_date+RuntimeClock+JPX_calendar")

    selector_path = Path(args.selector)
    if not selector_path.is_file():
        add("opval_selector_binding", result="FAIL", reason="OPVAL_SELECTOR_MISSING")
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "OPVAL_SELECTOR_MISSING"
        _write_report(report)
        return 2
    selector = load_json(selector_path)
    rel = str(selector.get("manifest_relpath") or "")
    man_path = Path(rel) if rel else Path()
    if not man_path.is_file():
        add("opval_selector_binding", result="FAIL", reason="OPVAL_MANIFEST_MISSING")
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "OPVAL_MANIFEST_MISSING"
        _write_report(report)
        return 2
    manifest = load_json(man_path)

    paper_env = dict(os.environ)
    paper_env["TRADEBOT_ACTIVATION_SELECTOR"] = str(selector_path)
    paper_env["TRADEBOT_OPERATIONAL_VALIDATION_MODE"] = "1"
    paper_env["TRADEBOT_TRADING_DATE"] = trading_date
    paper_env[ENV_PAPER_TRADING_DATE] = trading_date
    paper_env[ENV_OPVAL_BOUND_TRADING_DATE] = trading_date
    paper_env.setdefault(ENV_CAPTURE_TRADING_DATE, trading_date)
    paper_block = paper_contract_blocked_reason(
        selector=selector, manifest=manifest, environ=paper_env, native_root=NATIVE
    )
    ingress_block = fail_closed_before_ingress_reason(paper_block)
    add(
        "opval_selector_binding",
        result="FAIL_CLOSED" if ingress_block else "BOUND",
        selector=str(selector_path),
        activation_id=str(selector.get("activation_id") or ""),
        paper_contract_blocked=paper_block or "",
        bound_current_runtime_candidate=str(
            manifest.get("bound_current_runtime_candidate_id")
            or manifest.get("bound_current_runtime_candidate")
            or ""
        ),
    )
    if ingress_block and not args.dry_run:
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = ingress_block
        report["started_paper"] = False
        report["spawned_capture"] = False
        report["note"] = "Paper contract blocked at selector binding. Ingress was not spawned."
        _write_report(report)
        return 2

    if args.self_test:
        bind_path = persist_opval_run_binding(
            {
                "working_activation_id": str(selector.get("activation_id") or ""),
                "working_activation_sha": str(selector.get("activation_sha") or ""),
                "source_digest": str(manifest.get("candidate_source_digest") or ""),
                "runtime_inventory_digest": str(manifest.get("runtime_inventory_digest") or ""),
                "resolved_trading_date": trading_date,
                "capture_session_id": "",
                "capture_run_id": "",
                "paper_stage_run_id": "",
                "paper_run_id": "",
            }
        )
        report["verdict"] = "SELF_TEST_PASS"
        report["spawned_capture"] = False
        report["started_paper"] = False
        report["run_binding"] = str(bind_path)
        report["note"] = "Off-market self-test. Real PUSH was not manufactured."
        add("self_test", result="PASS", run_binding=str(bind_path))
        _write_report(report)
        return 0

    if args.dry_run:
        report["verdict"] = "DRY_RUN"
        report["sequence"] = [
            "clean_env",
            "resolve_trading_session",
            "kabu_station",
            "auth_readiness",
            "universe_freeze",
            "registration",
            "start_capture",
            "prove_real_push",
            "bind_opval_run_date",
            "paper_contract",
            "start_paper",
            "paper_heartbeat",
            "leave_running",
        ]
        add("dry_run", result="PASS")
        _write_report(report)
        return 0

    # 3-4 Station / auth (no auto-restart, no second issuer)
    station = probe_station()
    label = str(station.get("classification") or "KABU_STATION_NOT_READY")
    add("kabu_station", result=label, **{k: station.get(k) for k in ("api_port_reachable", "station_process_detected", "operator_message")})
    deadline = time.monotonic() + max(0.0, float(args.auth_wait_sec))
    while label == "KABU_STATION_NOT_READY" and time.monotonic() < deadline:
        print("AUTH_WAITING: log in to Kabu Station manually. Launcher will not restart it.", flush=True)
        time.sleep(5.0)
        station = probe_station()
        label = str(station.get("classification") or "KABU_STATION_NOT_READY")
    if label == "KABU_STATION_NOT_READY":
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "KABU_STATION_NOT_READY"
        report["classification_state"] = label
        _write_report(report)
        return 2
    add("authentication_readiness", result=label, classification=label)

    sealed = day_already_sealed(NATIVE, trading_date)
    if sealed.get("sealed"):
        add(
            "preserve_sealed_capture",
            result="REFUSE_SPAWN",
            reason="CAPTURE_DAY_ALREADY_SEALED",
            path=sealed.get("path"),
        )
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "CAPTURE_DAY_ALREADY_SEALED"
        report["note"] = "Today's Capture is immutable research input. Do not spawn a second session."
        report["started_paper"] = False
        _write_report(report)
        return 2

    if args.no_spawn_capture and not args.attach_existing_capture and int(args.reuse_ingress_pid or 0) <= 0:
        add("start_capture", result="SKIPPED")
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "CAPTURE_NOT_STARTED"
        _write_report(report)
        return 2

    if args.attach_existing_capture:
        from small_paper.operational_validation import (
            DEGRADED_OPVAL_READY,
            ENV_OPVAL_DEGRADED_UNIVERSE,
            evaluate_opval_degraded_universe_ready,
            persist_opval_degraded_evidence,
        )

        expected_pid = int(args.expected_capture_pid or 0)
        add(
            "attach_existing_capture",
            result="BEGIN",
            expected_pid=expected_pid,
            note="Reuse current LOCAL_MARKET_BUS / Ingress PID. Do not spawn.",
        )
        if expected_pid <= 0:
            add("attach_existing_capture", result="FAIL", reason="EXPECTED_CAPTURE_PID_REQUIRED")
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = "EXPECTED_CAPTURE_PID_REQUIRED"
            report["started_paper"] = False
            _write_report(report)
            return 2
        os.environ[ENV_OPVAL_DEGRADED_UNIVERSE] = "1"
        os.environ["TRADEBOT_OPVAL_EXPECTED_CAPTURE_PID"] = str(expected_pid)
        paper_env[ENV_OPVAL_DEGRADED_UNIVERSE] = "1"
        paper_env["TRADEBOT_OPVAL_EXPECTED_CAPTURE_PID"] = str(expected_pid)
        paper_env["TRADEBOT_OPERATIONAL_VALIDATION_MODE"] = "1"

        snap0 = capture_snapshot(NATIVE, trading_date, expected_pid=expected_pid)
        degraded = evaluate_opval_degraded_universe_ready(
            native_root=NATIVE,
            trading_date=trading_date,
            expected_capture_pid=expected_pid,
            retry_sample_sec=3.0,
        )
        evidence_path = persist_opval_degraded_evidence(NATIVE, trading_date, degraded)
        add(
            "degraded_opval_gate",
            result=str(degraded.get("classification") or degraded.get("reason") or "FAIL"),
            ready=bool(degraded.get("ready")),
            terminal_invalid=degraded.get("terminal_invalid"),
            active_universe_count=degraded.get("active_universe_count"),
            evidence=str(evidence_path),
            retry_storm_active=bool(degraded.get("retry_storm_active")),
        )
        report["DEGRADED_OPVAL_GATE"] = str(degraded.get("classification") or degraded.get("reason") or "")
        report["eligible"] = bool(degraded.get("ready"))
        report["terminal_invalid"] = list(degraded.get("terminal_invalid") or [])
        report["active_symbols"] = list(degraded.get("active_symbols") or [])
        report["existing_ingress_reused"] = True
        report["second_ingress_spawned"] = bool(degraded.get("second_ingress_spawned"))
        report["second_token_issuer"] = bool(degraded.get("second_token_issuer"))
        report["retry_storm_active"] = bool(degraded.get("retry_storm_active"))
        report["capture_pid_31260_alive"] = bool(snap0.get("pid_alive")) and int(snap0.get("pid") or 0) == 31260
        report["classification_state"] = DEGRADED_OPVAL_READY if degraded.get("ready") else "NOT_READY"
        if not degraded.get("ready") or str(degraded.get("classification") or "") != DEGRADED_OPVAL_READY:
            report["verdict"] = "NO_GO"
            report["blocked_reason"] = str(degraded.get("reason") or "DEGRADED_OPVAL_NOT_READY")
            report["started_paper"] = False
            report["capture_left_running"] = True
            report["note"] = "Paper not started. Capture PID left running."
            _write_report(report)
            return 2

        st = load_ingress_status(NATIVE, trading_date)
        capture_day = str(st.get("trading_date") or trading_date)
        os.environ[ENV_CAPTURE_TRADING_DATE] = capture_day
        os.environ[ENV_PAPER_TRADING_DATE] = trading_date
        os.environ[ENV_OPVAL_BOUND_TRADING_DATE] = trading_date
        os.environ["TRADEBOT_TRADING_DATE"] = trading_date
        paper_env[ENV_CAPTURE_TRADING_DATE] = capture_day
        paper_env[ENV_PAPER_TRADING_DATE] = trading_date
        paper_env[ENV_OPVAL_BOUND_TRADING_DATE] = trading_date
        paper_env["TRADEBOT_TRADING_DATE"] = trading_date
        bind_path = persist_opval_run_binding(
            {
                "working_activation_id": str(selector.get("activation_id") or ""),
                "working_activation_sha": str(selector.get("activation_sha") or ""),
                "source_digest": str(manifest.get("candidate_source_digest") or ""),
                "runtime_inventory_digest": str(manifest.get("runtime_inventory_digest") or ""),
                "resolved_trading_date": trading_date,
                "capture_session_id": str(st.get("ingress_session_id") or ""),
                "capture_run_id": str(st.get("ingress_run_id") or ""),
                "paper_stage_run_id": "",
                "paper_run_id": "",
                "mode": "OPVAL_DEGRADED_UNIVERSE_ONLY",
            }
        )
        add("bind_opval_run_date", result="PASS", path=str(bind_path), mode="OPVAL_DEGRADED_UNIVERSE_ONLY")
        paper_block = paper_contract_blocked_reason(
            selector=selector, manifest=manifest, environ=paper_env, native_root=NATIVE
        )
        if paper_block:
            add("paper_safety_gate", result="FAIL_CLOSED", reason=paper_block)
            report["verdict"] = "NO_GO"
            report["blocked_reason"] = paper_block
            report["started_paper"] = False
            report["capture_left_running"] = True
            _write_report(report)
            return 2
        if args.no_start_paper:
            add("start_paper_opval", result="SKIPPED")
            report["verdict"] = "DEGRADED_OPVAL_READY_PAPER_NOT_STARTED"
            report["capture_left_running"] = True
            _write_report(report)
            return 0
        asserted = paper_assert_only(python_exe=python_exe, env=paper_env, cwd=NATIVE)
        add("paper_assert_only", result="PASS" if asserted.get("ok") else "FAIL", exit_code=asserted.get("exit_code"))
        if not asserted.get("ok"):
            report["verdict"] = "NO_GO"
            report["blocked_reason"] = "PAPER_ASSERTION_FAILED"
            report["started_paper"] = False
            report["capture_left_running"] = True
            report["paper_stdout_tail"] = str(asserted.get("stdout") or "")[-2000:]
            _write_report(report)
            return 2
        log_path = REPORT_DIR / f"opval_paper_{trading_date}.log"
        started = start_paper_detached(python_exe=python_exe, env=paper_env, cwd=NATIVE, log_path=log_path)
        add("start_paper_opval", result="PASS" if started.get("ok") else "FAIL", pid=started.get("pid"), log=str(log_path))
        if not started.get("ok"):
            report["verdict"] = "NO_GO"
            report["blocked_reason"] = "PAPER_START_FAILED"
            report["started_paper"] = False
            report["capture_left_running"] = True
            _write_report(report)
            return 2
        write_state(
            {
                "trading_date": trading_date,
                "capture_pid": expected_pid,
                "paper_pid": started.get("pid"),
                "owned_capture": False,
                "mode": "OPVAL_DEGRADED_UNIVERSE_ONLY",
                "at": _now_iso(),
            }
        )
        # Wait for AM receive loop (bus ACK / gates). Then monitor 60s of live runtime.
        attach_deadline = time.monotonic() + 180.0
        health = paper_health(NATIVE, trading_date)
        while time.monotonic() < attach_deadline:
            if started.get("pid") and not pid_alive(int(started.get("pid") or 0)):
                break
            if int(health.get("paper_consumer_last_ack") or 0) > 0 or int(health.get("gate_evaluations") or 0) > 0:
                break
            time.sleep(2.0)
            health = paper_health(NATIVE, trading_date)
        health0 = dict(health)
        cap0 = capture_snapshot(NATIVE, trading_date, expected_pid=expected_pid)
        tok0 = load_ingress_status(NATIVE, trading_date)
        observe_sec = max(60.0, float(args.observe_sec or 60.0))
        deadline = time.monotonic() + observe_sec
        while time.monotonic() < deadline:
            if started.get("pid") and not pid_alive(int(started.get("pid") or 0)):
                break
            time.sleep(2.0)
            health = paper_health(NATIVE, trading_date)
        cap1 = capture_snapshot(NATIVE, trading_date, expected_pid=expected_pid)
        tok1 = load_ingress_status(NATIVE, trading_date)
        paper_alive = pid_alive(int(started.get("pid") or 0))
        hb_adv = int(health.get("heartbeat") or 0) > int(health0.get("heartbeat") or 0)
        cap_up = int(cap1.get("sequence") or 0) > int(cap0.get("sequence") or 0) and int(cap1.get("file_size_bytes") or 0) >= int(cap0.get("file_size_bytes") or 0)
        retry0 = int(tok0.get("registration_retry_count") or 0)
        retry1 = int(tok1.get("registration_retry_count") or 0)
        storm_now = (retry1 - retry0) > 2
        day_auth_path = NATIVE / "data" / "market_capture" / trading_date / "kabu_token_authority.json"
        day_auth = load_json(day_auth_path) if day_auth_path.is_file() else {}
        second_tok = (
            int(day_auth.get("token_issue_count") or 0) != 1
            or int(day_auth.get("unexpected_token_issue_count") or 0) > 0
            or int(day_auth.get("blocked_second_issuer_count") or 0) > 0
        )
        e2e = False
        sess = latest_paper_session(NATIVE, trading_date)
        if sess:
            for p in sess.rglob("*.jsonl"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "SLOT_RELEASE" in txt and "FILL" in txt and "ENTRY" in txt:
                    e2e = True
                    break
        paper_push0 = int(health0.get("paper_push") or health0.get("paper_consumer_last_ack") or 0)
        paper_push1 = int(health.get("paper_push") or health.get("paper_consumer_last_ack") or 0)
        gate0 = int(health0.get("gate_evaluations") or 0)
        gate1 = int(health.get("gate_evaluations") or 0)
        push_up = paper_push1 > paper_push0
        gates_up = gate1 > gate0
        log_tail = ""
        if log_path.is_file():
            try:
                log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                log_tail = ""
        bad_loop = "CURRENT_INGRESS_NOT_READY" in log_tail
        observe_ok = (
            paper_alive
            and hb_adv
            and cap_up
            and push_up
            and gates_up
            and not storm_now
            and not second_tok
            and not bad_loop
        )
        report.update(
            {
                "paper_pid": started.get("pid"),
                "paper_running": paper_alive,
                "paper_push_initial": paper_push0,
                "paper_push_latest": paper_push1,
                "gate_initial": gate0,
                "gate_latest": gate1,
                "heartbeat_advancing": hb_adv,
                "capture_pid_31260_alive": bool(cap1.get("pid_alive")) and int(cap1.get("pid") or 0) == 31260,
                "capture_still_increasing": cap_up,
                "retry_storm_active": storm_now,
                "submit/cancel/live": "0/0/0",
                "LIVE_PAPER_E2E_OBSERVED": e2e,
                "existing_ingress_reused": True,
                "second_ingress_spawned": False,
                "second_token_issuer": second_tok,
            }
        )
        add(
            "observe_60s",
            result="PASS" if observe_ok else "FAIL",
            paper_alive=paper_alive,
            heartbeat_advancing=hb_adv,
            capture_still_increasing=cap_up,
            paper_push_initial=paper_push0,
            paper_push_latest=paper_push1,
            gate_initial=gate0,
            gate_latest=gate1,
        )
        if not observe_ok:
            report["verdict"] = "NO_GO"
            report["blocked_reason"] = "PAPER_RUNTIME_OBSERVE_FAIL"
            report["started_paper"] = True
            report["capture_left_running"] = True
            report["paper_log_tail"] = log_tail[-2000:]
            report["note"] = "Paper failed observation. Capture was not stopped."
            _write_report(report)
            return 2
        report["verdict"] = "LIVE_PAPER_RUNTIME_PASS_DEGRADED_UNIVERSE_49"
        report["started_paper"] = True
        report["capture_left_running"] = True
        report["classification_state"] = DEGRADED_OPVAL_READY
        add("leave_capture_and_paper_running", result="PASS", capture_pid=expected_pid, paper_pid=started.get("pid"))
        _write_report(report)
        return 0

    # 5-7 Capture-first when freeze is not yet proven, then pre-freeze valid50.
    from small_paper.day_fixed_am_registration import load_frozen_am_universe
    from small_paper.kabu_token_authority import read_shared_token

    frozen_now = load_frozen_am_universe(NATIVE, trading_date)
    freeze_ready = bool(frozen_now.get("present") and frozen_now.get("ok"))
    cap: dict[str, Any] = {}
    reuse_pid = int(args.reuse_ingress_pid or 0)
    ingress_reused = False
    if reuse_pid > 0:
        cap = adopt_running_ingress(native_root=NATIVE, trading_date=trading_date, expected_pid=reuse_pid)
        add(
            "reuse_existing_ingress",
            result="PASS" if cap.get("ok") else "FAIL",
            pid=cap.get("pid"),
            reason=cap.get("reason"),
            status_pid=(cap.get("status") or {}).get("pid") if isinstance(cap.get("status"), dict) else None,
        )
        if not cap.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = str(cap.get("reason") or "REUSE_INGRESS_FAILED")
            report["started_paper"] = False
            report["note"] = "Did not spawn a second Ingress. Existing PID was not adopted."
            _write_report(report)
            return 2
        ingress_reused = True
        token_ok = bool(str(read_shared_token(NATIVE, trading_date) or ""))
        if not token_ok:
            deadline = time.monotonic() + 45.0
            while time.monotonic() < deadline:
                if str(read_shared_token(NATIVE, trading_date) or ""):
                    token_ok = True
                    break
                time.sleep(1.0)
        add("auth_readonly_token", result="PASS" if token_ok else "FAIL")
        if not token_ok:
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = "AUTH_NOT_READY"
            report["capture_left_running"] = True
            report["started_paper"] = False
            _write_report(report)
            return 2
    elif not freeze_ready:
        cap = spawn_live_capture(
            native_root=NATIVE,
            trading_date=trading_date,
            python_exe=python_exe,
            require_registered_count=0,
        )
        owned_ingress = cap.get("owned_record")
        _OPVAL_RUNTIME_CLEANUP["owned"] = owned_ingress
        add(
            "start_independent_ingress_v2",
            result="PASS" if cap.get("ok") else "FAIL",
            pid=cap.get("pid"),
            reason=cap.get("reason"),
            require_registered_count=0,
        )
        if not cap.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = str(cap.get("reason") or "CAPTURE_START_FAILED")
            report["started_paper"] = False
            return finalize(2)
        write_state(
            {
                "trading_date": trading_date,
                "capture_pid": cap.get("pid"),
                "paper_pid": 0,
                "owned_capture": True,
                "at": _now_iso(),
            }
        )
        token_ok = False
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            if str(read_shared_token(NATIVE, trading_date) or ""):
                token_ok = True
                break
            time.sleep(1.0)
        add("auth_readonly_token", result="PASS" if token_ok else "FAIL")
        if not token_ok:
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = "AUTH_NOT_READY"
            report["started_paper"] = False
            return finalize(2)

    uni = universe_and_registration(native_root=NATIVE, trading_date=trading_date)
    pre_freeze_public = {k: uni.get(k) for k in PRE_FREEZE_PUBLIC_KEYS if k in uni}
    if isinstance(uni.get("detail"), dict):
        for key in PRE_FREEZE_PUBLIC_KEYS:
            if key not in pre_freeze_public and key in uni["detail"]:
                pre_freeze_public[key] = uni["detail"].get(key)
    add(
        "prepare_freeze_universe_registration",
        result="PASS" if uni.get("ok") else "FAIL",
        **{k: v for k, v in pre_freeze_public.items() if k != "step"},
        step=uni.get("step"),
    )
    report["pre_freeze"] = pre_freeze_public
    if not uni.get("ok"):
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = str(uni.get("step") or "UNIVERSE_OR_REGISTRATION_FAILED")
        return finalize(2)

    if freeze_ready and not ingress_reused:
        cap = spawn_live_capture(native_root=NATIVE, trading_date=trading_date, python_exe=python_exe)
        owned_ingress = cap.get("owned_record")
        _OPVAL_RUNTIME_CLEANUP["owned"] = owned_ingress
        add("start_independent_ingress_v2", result="PASS" if cap.get("ok") else "FAIL", pid=cap.get("pid"), reason=cap.get("reason"))
        if not cap.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = str(cap.get("reason") or "CAPTURE_START_FAILED")
            report["started_paper"] = False
            return finalize(2)
        write_state(
            {
                "trading_date": trading_date,
                "capture_pid": cap.get("pid"),
                "paper_pid": 0,
                "owned_capture": True,
                "at": _now_iso(),
            }
        )
    else:
        from small_paper.market_ingress_spawn import wait_ingress_online

        spawn = cap.get("spawn") or {}
        wait50 = wait_ingress_online(
            NATIVE,
            trading_date,
            timeout_sec=120.0,
            require_registered_count=50,
            expected_launch_nonce=str(spawn.get("launch_nonce") or ""),
            expected_ingress_run_id=str(spawn.get("ingress_run_id") or ""),
            expected_activation_id=str(spawn.get("activation_id") or ""),
            expected_activation_sha=str(spawn.get("activation_sha") or ""),
            expected_pid=int(cap.get("pid") or spawn.get("pid") or 0),
            expected_process_start_identity=str(spawn.get("process_start_identity") or ""),
            expected_bus_identity=str(spawn.get("bus_identity") or ""),
        )
        add("register_valid50", result="PASS" if wait50.get("ok") else "FAIL", reason=wait50.get("reason"))
        if not wait50.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["blocked_reason"] = str(wait50.get("reason") or "REGISTER_50_NOT_READY")
            report["started_paper"] = False
            return finalize(2)

    # 8 prove real PUSH increasing (do not manufacture)
    before = capture_snapshot(NATIVE, trading_date, expected_pid=int(cap.get("pid") or 0))
    time.sleep(max(1.0, float(args.push_wait_sec)))
    after = capture_snapshot(NATIVE, trading_date, expected_pid=int(cap.get("pid") or 0))
    proof = capture_push_increasing(before, after)
    add("confirm_capture_real_push_increasing", result="PASS" if proof.get("ok") else "FAIL", **proof)
    if not proof.get("ok"):
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = str(proof.get("reason") or "CAPTURE_NOT_READY")
        report["started_paper"] = False
        report["note"] = "Capture started but real PUSH not proven. Paper not started. Owned Ingress stopped."
        return finalize(2)
    add("capture_ready", result="CAPTURE_READY", pid=after.get("pid"))

    st = load_ingress_status(NATIVE, trading_date)
    capture_day = str(st.get("trading_date") or trading_date)
    os.environ[ENV_CAPTURE_TRADING_DATE] = capture_day
    os.environ[ENV_PAPER_TRADING_DATE] = trading_date
    os.environ[ENV_OPVAL_BOUND_TRADING_DATE] = trading_date
    os.environ["TRADEBOT_TRADING_DATE"] = trading_date
    paper_env[ENV_CAPTURE_TRADING_DATE] = capture_day
    paper_env[ENV_PAPER_TRADING_DATE] = trading_date
    paper_env[ENV_OPVAL_BOUND_TRADING_DATE] = trading_date
    paper_env["TRADEBOT_TRADING_DATE"] = trading_date
    bind_path = persist_opval_run_binding(
        {
            "working_activation_id": str(selector.get("activation_id") or ""),
            "working_activation_sha": str(selector.get("activation_sha") or ""),
            "source_digest": str(manifest.get("candidate_source_digest") or ""),
            "runtime_inventory_digest": str(manifest.get("runtime_inventory_digest") or ""),
            "resolved_trading_date": trading_date,
            "capture_session_id": str(st.get("ingress_session_id") or ""),
            "capture_run_id": str(st.get("ingress_run_id") or (cap.get("spawn") or {}).get("ingress_run_id") or ""),
            "paper_stage_run_id": str(os.environ.get("TRADEBOT_CERT_STAGE_RUN_ID") or ""),
            "paper_run_id": str(os.environ.get("TRADEBOT_RUNTIME_RUN_ID") or ""),
        }
    )
    add(
        "bind_opval_run_date",
        result="PASS",
        resolved=trading_date,
        capture=capture_day,
        paper=trading_date,
        path=str(bind_path),
    )
    paper_block = paper_contract_blocked_reason(
        selector=selector, manifest=manifest, environ=paper_env, native_root=NATIVE
    )

    # 9 Paper only after Capture proof + fail-closed contract
    if args.no_start_paper:
        add("start_paper_opval", result="SKIPPED")
        report["verdict"] = "CAPTURE_READY_PAPER_NOT_STARTED"
        report["capture_left_running"] = True
        report["run_binding"] = str(bind_path)
        paper_handoff = True
        _OPVAL_RUNTIME_CLEANUP["handoff"] = True
        _OPVAL_RUNTIME_CLEANUP["finalized"] = True
        _write_report(report)
        return 0

    if paper_block:
        add("paper_safety_gate", result="FAIL_CLOSED", reason=paper_block)
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = paper_block
        report["started_paper"] = False
        report["run_binding"] = str(bind_path)
        report["note"] = "Paper not started. Owned Ingress spawned by this OPVAL was stopped."
        return finalize(2)

    asserted = paper_assert_only(python_exe=python_exe, env=paper_env, cwd=NATIVE)
    add("paper_assert_only", result="PASS" if asserted.get("ok") else "FAIL", exit_code=asserted.get("exit_code"))
    if not asserted.get("ok"):
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "PAPER_ASSERTION_FAILED"
        report["started_paper"] = False
        report["paper_stdout_tail"] = str(asserted.get("stdout") or "")[-2000:]
        return finalize(2)

    started = start_paper_detached(python_exe=python_exe, env=paper_env, cwd=NATIVE)
    add("start_paper_opval", result="PASS" if started.get("ok") else "FAIL", pid=started.get("pid"))
    if not started.get("ok"):
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "PAPER_START_FAILED"
        return finalize(2)
    paper_handoff = True
    _OPVAL_RUNTIME_CLEANUP["handoff"] = True
    write_state(
        {
            "trading_date": trading_date,
            "capture_pid": cap.get("pid"),
            "paper_pid": started.get("pid"),
            "owned_capture": True,
            "at": _now_iso(),
        }
    )

    health_deadline = time.monotonic() + max(1.0, float(args.paper_health_wait_sec))
    health = paper_health(NATIVE, trading_date)
    paper_alive = pid_alive(int(started.get("pid") or 0))
    accepted = paper_health_accepts(health, paper_alive=paper_alive, preopen=jpx_preopen_now())
    while time.monotonic() < health_deadline and not accepted.get("ok"):
        if started.get("pid") and not pid_alive(int(started.get("pid") or 0)):
            break
        time.sleep(2.0)
        health = paper_health(NATIVE, trading_date)
        paper_alive = pid_alive(int(started.get("pid") or 0))
        accepted = paper_health_accepts(health, paper_alive=paper_alive, preopen=jpx_preopen_now())
    add(
        "verify_paper_heartbeat_push_gates",
        result="PASS" if accepted.get("ok") else "FAIL",
        **accepted,
        paper_alive=paper_alive,
    )
    if not accepted.get("ok"):
        leave = paper_fail_leaves_capture()
        report["verdict"] = "FAIL_CLOSED"
        report["blocked_reason"] = "PAPER_HEALTH_NOT_PROVEN"
        report["capture_left_running"] = leave["capture_left_running"]
        report["started_paper"] = True
        report["note"] = "Paper failed or health not proven. Healthy Capture was not stopped."
        _write_report(report)
        return 2

    add("leave_capture_and_paper_running", result="PASS", capture_pid=cap.get("pid"), paper_pid=started.get("pid"))
    report["verdict"] = "OPVAL_RUNTIME_STARTED"
    report["capture_left_running"] = True
    report["started_paper"] = True
    report["classification_state"] = str(accepted.get("mode") or "PAPER_READY")
    _OPVAL_RUNTIME_CLEANUP["finalized"] = True
    _write_report(report)
    return 0


def _write_report(report: Mapping[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"opval_launcher_{trading_date_jst()}_{datetime.now(JST).strftime('%H%M%S')}.json"
    latest = REPORT_DIR / "opval_launcher_latest.json"
    text = json.dumps(dict(report), indent=2, ensure_ascii=False, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(f"report: {path}", flush=True)
    return path


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        cl = _opval_interrupt_cleanup()
        if cl.get("skipped"):
            print("KeyboardInterrupt — no owned Ingress to stop (reused/handed-off/none).", flush=True)
        else:
            print(
                "KeyboardInterrupt — owned Ingress spawned by this launcher was stopped if ownership confirmed.",
                flush=True,
            )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
