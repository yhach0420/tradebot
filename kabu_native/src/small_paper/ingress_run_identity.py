"""Current-run Ingress status identity (V21).

ONLINE is not "a status file exists with state=WAITING_FIRST_PUSH".
ONLINE requires the status bundle to match the parent spawn identity and a
live process with the recorded start identity and a fresh real-clock heartbeat.

time.monotonic() is process-local and cannot be compared across waiter/child.
Cross-process freshness uses time.time() (domain C processing clock), never
session_now() / RuntimeClock.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

STATUS_SCHEMA_VERSION = "INGRESS_STATUS_CURRENT_RUN_V1"
ROLE_MARKET_INGRESS_SERVICE = "MARKET_INGRESS_SERVICE"

ENV_LAUNCH_NONCE = "TRADEBOT_INGRESS_LAUNCH_NONCE"
ENV_INGRESS_RUN_ID = "TRADEBOT_INGRESS_RUN_ID"
ENV_BUS_IDENTITY = "TRADEBOT_INGRESS_BUS_IDENTITY"
ENV_ACTIVATION_ID = "TRADEBOT_INGRESS_ACTIVATION_ID"
ENV_ACTIVATION_SHA = "TRADEBOT_INGRESS_ACTIVATION_SHA"
ENV_CERTIFICATION_RUN_ID = "TRADEBOT_CERTIFICATION_RUN_ID"
ENV_STAGE_RUN_ID = "TRADEBOT_CERT_STAGE_RUN_ID"

STALE_INGRESS_STATUS_REJECTED = "STALE_INGRESS_STATUS_REJECTED"
CURRENT_INGRESS_NOT_READY = "CURRENT_INGRESS_NOT_READY"
MISSING_EXPECTED_LAUNCH_NONCE = "MISSING_EXPECTED_LAUNCH_NONCE"

ONLINE_STATES = frozenset(
    {
        "RUNNING",
        "READY",
        "WAITING_FIRST_PUSH",
        "REGISTERING",
        "RECOVERED",
        "CONNECTING",
    }
)

REQUIRED_STATUS_FIELDS = (
    "status_schema_version",
    "activation_id",
    "activation_sha",
    "ingress_run_id",
    "launch_nonce",
    "pid",
    "process_start_identity",
    "trading_date",
    "role",
    "bus_identity",
    "state",
    "status_written_unix",
)

DEFAULT_HEARTBEAT_MAX_AGE_SEC = 20.0

QueryFn = Callable[[int], Mapping[str, Any]]


def generate_launch_nonce() -> str:
    return secrets.token_hex(16)


def make_ingress_run_id(*, trading_date: str, launch_nonce: str) -> str:
    return f"ingrun_{trading_date}_{launch_nonce[:16]}"


def make_bus_identity(*, host: str, port: int, trading_date: str, launch_nonce: str) -> str:
    return f"tcp://{host}:{int(port)}|{trading_date}|{launch_nonce}"


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """temp → flush → fsync → os.replace. Partial files are never the live path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + f".{os.getpid()}.{secrets.token_hex(6)}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        raise


def process_start_identity_from_query(query: Mapping[str, Any]) -> str:
    raw = str(query.get("create_time") or query.get("CreationDate") or "").strip()
    return raw


def capture_process_start_identity(pid: int, *, query_fn: Optional[QueryFn] = None) -> str:
    qfn = query_fn or _default_query
    for _ in range(3):
        live = dict(qfn(int(pid)) or {})
        ident = process_start_identity_from_query(live)
        if ident and live.get("exists"):
            return ident
        time.sleep(0.05)
    live = dict(qfn(int(pid)) or {})
    return process_start_identity_from_query(live)


def _default_query(pid: int) -> dict[str, Any]:
    from small_paper.capture_child_cleanup import query_process

    return dict(query_process(int(pid)))


def activation_identity(*, environ: Optional[Mapping[str, str]] = None) -> tuple[str, str]:
    env = environ if environ is not None else os.environ
    aid = str(env.get(ENV_ACTIVATION_ID) or "").strip()
    ash = str(env.get(ENV_ACTIVATION_SHA) or "").strip()
    if aid and ash:
        return aid, ash
    try:
        from small_paper.v1r_activation_binding import load_active_selector

        sel = load_active_selector()
        return str(sel.get("activation_id") or "").strip(), str(sel.get("activation_sha") or "").strip()
    except Exception:
        return aid, ash


def execution_scope_from_env(*, environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    env = environ if environ is not None else os.environ
    aid, ash = activation_identity(environ=env)
    return {
        "certification_run_id": str(env.get(ENV_CERTIFICATION_RUN_ID) or "").strip(),
        "stage_run_id": str(env.get(ENV_STAGE_RUN_ID) or "").strip(),
        "activation_id": aid,
        "activation_sha": ash,
    }


def stamp_execution_scope(doc: dict[str, Any], *, environ: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    from small_paper.session_runtime_identity import stamp_session_identity

    return stamp_session_identity(doc, environ=environ)


def artifact_matches_scope(doc: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    if not isinstance(doc, Mapping):
        return False
    for key in ("certification_run_id", "stage_run_id", "activation_sha"):
        want = str(expected.get(key) or "").strip()
        if not want:
            return False
        if str(doc.get(key) or "").strip() != want:
            return False
    return True


def load_ingress_status_json(path: Path) -> tuple[Optional[dict[str, Any]], str]:
    """Return (payload, error). Partial / unreadable JSON is not ONLINE."""
    p = Path(path)
    if not p.is_file():
        return None, "status_file_missing"
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"status_unreadable:{type(exc).__name__}"
    if not raw.strip():
        return None, "status_empty"
    try:
        data = json.loads(raw)
    except Exception as exc:
        return None, f"status_partial_json:{type(exc).__name__}"
    if not isinstance(data, dict):
        return None, "status_not_object"
    return data, ""


def evaluate_current_run_online(
    status: Optional[Mapping[str, Any]],
    *,
    expected: Mapping[str, Any],
    now_unix: Optional[float] = None,
    heartbeat_max_age_sec: float = DEFAULT_HEARTBEAT_MAX_AGE_SEC,
    query_fn: Optional[QueryFn] = None,
    require_registered_count: int = 0,
) -> dict[str, Any]:
    """Fail-closed current-run ONLINE check. One mismatch → not ONLINE."""
    now = float(time.time() if now_unix is None else now_unix)
    out: dict[str, Any] = {
        "ok": False,
        "reason": STALE_INGRESS_STATUS_REJECTED,
        "reject_code": "",
        "heartbeat_unix_age": None,
        "pid_alive": False,
        "process_start_match": False,
    }
    nonce = str(expected.get("launch_nonce") or "").strip()
    if not nonce:
        out["reason"] = MISSING_EXPECTED_LAUNCH_NONCE
        out["reject_code"] = MISSING_EXPECTED_LAUNCH_NONCE
        return out
    if not isinstance(status, Mapping) or not status:
        out["reject_code"] = "partial_or_empty_status"
        return out
    for field in REQUIRED_STATUS_FIELDS:
        val = status.get(field)
        if val is None or (isinstance(val, str) and not str(val).strip()):
            out["reject_code"] = f"missing_field:{field}"
            return out
    if str(status.get("status_schema_version") or "") != STATUS_SCHEMA_VERSION:
        out["reject_code"] = "schema_mismatch"
        return out
    if str(status.get("role") or "") != ROLE_MARKET_INGRESS_SERVICE:
        out["reject_code"] = "role_mismatch"
        return out
    if str(status.get("trading_date") or "") != str(expected.get("trading_date") or ""):
        out["reject_code"] = "trading_date_mismatch"
        return out
    if str(status.get("activation_id") or "") != str(expected.get("activation_id") or ""):
        out["reject_code"] = "activation_id_mismatch"
        return out
    if str(status.get("activation_sha") or "") != str(expected.get("activation_sha") or ""):
        out["reject_code"] = "activation_sha_mismatch"
        return out
    if str(status.get("ingress_run_id") or "") != str(expected.get("ingress_run_id") or ""):
        out["reject_code"] = "ingress_run_id_mismatch"
        return out
    if str(status.get("launch_nonce") or "") != nonce:
        out["reject_code"] = "launch_nonce_mismatch"
        return out
    exp_bus = str(expected.get("bus_identity") or "").strip()
    if exp_bus and str(status.get("bus_identity") or "") != exp_bus:
        out["reject_code"] = "bus_identity_mismatch"
        return out
    state = str(status.get("state") or "")
    if state not in ONLINE_STATES:
        out["reject_code"] = f"state_not_ready:{state}"
        return out
    need = int(require_registered_count or 0)
    if need > 0 and int(status.get("registered_symbol_count") or 0) < need:
        out["reject_code"] = "registered_count_short"
        out["reason"] = STALE_INGRESS_STATUS_REJECTED
        return out
    try:
        written = float(status.get("status_written_unix"))
    except (TypeError, ValueError):
        out["reject_code"] = "status_written_unix_invalid"
        return out
    age = now - written
    out["heartbeat_unix_age"] = age
    if age > float(heartbeat_max_age_sec) or age < -5.0:
        out["reject_code"] = "heartbeat_stale"
        return out
    try:
        pid = int(status.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        out["reject_code"] = "pid_invalid"
        return out
    exp_pid = int(expected.get("pid") or 0)
    if exp_pid > 0 and pid != exp_pid:
        out["reject_code"] = "pid_mismatch"
        return out
    qfn = query_fn or _default_query
    live = dict(qfn(pid) or {})
    if not live.get("exists"):
        out["reject_code"] = "pid_dead"
        return out
    out["pid_alive"] = True
    recorded = str(status.get("process_start_identity") or "")
    live_ident = process_start_identity_from_query(live)
    if not recorded or not live_ident or recorded != live_ident:
        out["reject_code"] = "process_start_identity_mismatch"
        return out
    exp_ident = str(expected.get("process_start_identity") or "").strip()
    if exp_ident and exp_ident != live_ident:
        out["reject_code"] = "process_start_identity_mismatch"
        return out
    out["process_start_match"] = True
    out["ok"] = True
    out["reason"] = "CURRENT_RUN_ONLINE"
    out["reject_code"] = ""
    out["state"] = state
    out["pid"] = pid
    return out


def stale_fingerprint(status: Optional[Mapping[str, Any]], reject_code: str) -> str:
    body = {
        "reject_code": reject_code,
        "pid": None if not isinstance(status, Mapping) else status.get("pid"),
        "launch_nonce": None if not isinstance(status, Mapping) else status.get("launch_nonce"),
        "ingress_run_id": None if not isinstance(status, Mapping) else status.get("ingress_run_id"),
        "process_start_identity": None
        if not isinstance(status, Mapping)
        else status.get("process_start_identity"),
        "activation_sha": None if not isinstance(status, Mapping) else status.get("activation_sha"),
        "status_written_unix": None if not isinstance(status, Mapping) else status.get("status_written_unix"),
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def append_wait_audit(path: Path, row: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
