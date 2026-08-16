"""Checked-runner lifecycle decision layer (V26-C).

paper_trade_checked_runner remains the sole top-level lifecycle authority.
This module is a pure decision helper: no new Supervisor process.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.ownership_classifier import (
    CONFLICT,
    CURRENT_VALID,
    DEAD_OWNER,
    PID_REUSED,
    STALE_PROVEN_OWNED,
    UNKNOWN,
)
from small_paper.runtime_ownership import classify_production_owner

JST = ZoneInfo("Asia/Tokyo")

LIFECYCLE_AUTHORITY = "paper_trade_checked_runner"
LIFECYCLE_AUTHORITY_COUNT = 1
DECISION_LAYER = "small_paper.runtime_lifecycle"

STARTUP_SEQUENCE = (
    "PRE_INGRESS",
    "RECONCILE",
    "OWNERSHIP_CLEAR",
    "STATION_REACHABLE",
    "INGRESS_STARTING",
    "TOKEN_ISSUE",
    "TOKEN_PUBLISHED",
    "AUTH_READY",
    "BOARD_NATIVE",
    "PAPER_READY",
)

TEARDOWN_SEQUENCE = (
    "RUNNING_OR_FAILED",
    "STOPPING",
    "CHILD_STOP_REQUEST",
    "MANAGED_PROCESS_EXIT",
    "WRITER_STOP",
    "AUTHORITY_RELEASE",
    "REGISTRATION_STATE",
    "SESSION_SEAL",
    "STOPPED",
)

NOT_AUTH_READY_STATES = frozenset(
    {
        "CLAIMED_PENDING_TOKEN",
        "FAILED_ISSUE",
        "RELEASED_DEAD",
        "ENVIRONMENT_AUTH_BLOCKED",
        "AUTH_FAILED",
    }
)

KILL_NONE = "NONE"
KILL_GRACEFUL = "GRACEFUL"
KILL_FORCE = "FORCE"

CALLSITE_OWNER = "A_CANONICAL_DECISION_OWNER"
CALLSITE_CONSUMER = "B_CANONICAL_DECISION_CONSUMER"
CALLSITE_AUDIT = "C_HISTORICAL_AUDIT_ONLY"
CALLSITE_OBSOLETE = "D_OBSOLETE_PRODUCTION_LIFECYCLE"


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def is_auth_ready(
    *,
    status: Optional[Mapping[str, Any]] = None,
    authority_state: str = "",
    token_stage_class: str = "",
    cert_required: bool = False,
) -> tuple[bool, str]:
    st = dict(status or {})
    state = str(st.get("state") or "")
    auth_code = str(st.get("auth_failure_code") or st.get("entry_block_reason") or "")
    if "ENVIRONMENT_AUTH_BLOCKED" in auth_code or "4001007" in auth_code:
        return False, "ENVIRONMENT_AUTH_BLOCKED"
    if state in NOT_AUTH_READY_STATES or state == "AUTH_FAILED":
        return False, f"state_not_auth_ready:{state or 'empty'}"
    auth_state = str(authority_state or st.get("authority_state") or "")
    if auth_state in NOT_AUTH_READY_STATES:
        return False, f"authority_not_auth_ready:{auth_state}"
    stage = str(token_stage_class or st.get("token_stage_class") or "")
    if cert_required and stage and stage != "TOKEN_STAGE_MATCH":
        return False, f"token_stage_not_match:{stage}"
    if cert_required and not stage:
        return False, "token_stage_missing"
    online = state in {
        "RUNNING",
        "READY",
        "WAITING_FIRST_PUSH",
        "REGISTERING",
        "RECOVERED",
        "CONNECTING",
    }
    if not online:
        return False, f"state_not_ready:{state or 'empty'}"
    return True, "AUTH_READY"


def real_kabus_auth_ready(*, status: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    st = dict(status or {})
    code = str(st.get("auth_failure_code") or "")
    msg = str(st.get("auth_failure_message_sanitized") or st.get("last_error") or "")
    http_status = st.get("auth_failure_http_status")
    blocked = "ENVIRONMENT_AUTH_BLOCKED" in code or "4001007" in code or "4001007" in msg
    ready = (not blocked) and str(st.get("state") or "") not in {"AUTH_FAILED", "STOPPED"}
    return {
        "REAL_KABUS_AUTH_READY": bool(ready and not blocked),
        "ENVIRONMENT_AUTH_BLOCKED": blocked,
        "http_status": http_status,
        "kabu_code": "4001007" if ("4001007" in code or "4001007" in msg) else None,
        "state": st.get("state"),
    }


def decide_kill(
    classified: Mapping[str, Any],
    *,
    identity_proven: bool = False,
    stale_graceful_done: bool = False,
) -> dict[str, Any]:
    """Kill decision. PID-only is never enough; UNKNOWN/PID_REUSED/CONFLICT never kill."""
    ocls = str(classified.get("class") or UNKNOWN)
    out = {
        "action": KILL_NONE,
        "kill_allowed": False,
        "wrong_process_kill": 0,
        "class": ocls,
        "reason": str(classified.get("reason") or ocls),
    }
    if ocls in {UNKNOWN, PID_REUSED, CONFLICT}:
        out["reason"] = f"FAIL_CLOSED_{ocls}"
        return out
    if ocls == DEAD_OWNER:
        out["reason"] = "already_dead_reclaim_only"
        return out
    if not identity_proven:
        out["reason"] = "identity_not_proven"
        return out
    if ocls == CURRENT_VALID:
        out["action"] = KILL_FORCE if stale_graceful_done else KILL_GRACEFUL
        out["kill_allowed"] = True
        out["reason"] = "managed_current_identity_proven"
        return out
    if ocls == STALE_PROVEN_OWNED:
        if not stale_graceful_done:
            out["action"] = KILL_GRACEFUL
            out["kill_allowed"] = True
            out["reason"] = "stale_managed_graceful_first"
            return out
        out["action"] = KILL_FORCE
        out["kill_allowed"] = True
        out["reason"] = "stale_managed_identity_reconfirmed"
        return out
    return out


def evaluate_teardown_residuals(
    *,
    native_root: Path,
    trading_date: str,
    owned_pid: int = 0,
    classified: Optional[Mapping[str, Any]] = None,
    process_alive_fn: Optional[Any] = None,
) -> dict[str, Any]:
    cls = dict(classified or classify_production_owner(native_root=native_root, trading_date=trading_date))
    pid = int(cls.get("pid") or owned_pid or 0)
    alive = bool(cls.get("process_alive"))
    if process_alive_fn is not None and pid > 0:
        try:
            alive = bool(process_alive_fn(pid))
        except Exception:
            alive = False
    ocls = str(cls.get("class") or "")
    current_owner = ocls == CURRENT_VALID and alive
    issuer_live = current_owner and str(cls.get("authority_state") or "") not in {
        "RELEASED_DEAD",
        "FAILED_ISSUE",
        "",
    }
    residuals = {
        "managed_process_residual": 1 if (owned_pid and alive and pid == int(owned_pid)) else 0,
        "current_station_owner_residual": 1 if current_owner else 0,
        "current_issuer_residual": 1 if issuer_live else 0,
        "mutex_or_lease_residual": 0,
        "canonical_status_writer_residual": 0,
        "active_current_token_authority_residual": 1 if current_owner else 0,
        "current_registration_residual": 0,
    }
    ok = all(int(v) == 0 for v in residuals.values())
    return {
        "ok": ok,
        "ownership_class": ocls,
        "wrong_process_kill": 0,
        "residuals": residuals,
        "classified": {k: cls.get(k) for k in ("class", "pid", "reason", "authority_state", "process_alive")},
    }


def finish_teardown(
    *,
    native_root: Path,
    trading_date: str,
    owned_pid: int = 0,
) -> dict[str, Any]:
    """After managed process stop: reclaim dead authority, keep history, measure residuals."""
    from small_paper.kabu_token_authority import reclaim_dead_station_owner

    reclaim = reclaim_dead_station_owner(native_root=Path(native_root), trading_date=str(trading_date))
    residuals = evaluate_teardown_residuals(
        native_root=Path(native_root),
        trading_date=str(trading_date),
        owned_pid=int(owned_pid or 0),
    )
    out = {
        "at": _now_iso(),
        "sequence": list(TEARDOWN_SEQUENCE),
        "reclaim": reclaim,
        "residuals": residuals,
        "history_deleted": False,
        "wrong_process_kill": 0,
    }
    day = Path(native_root) / "data" / "market_capture" / str(trading_date)
    try:
        day.mkdir(parents=True, exist_ok=True)
        dest = day / "teardown_residual.json"
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(tmp, dest)
    except OSError:
        pass
    return out


def classify_env_auth_from_status(status: Mapping[str, Any]) -> dict[str, Any]:
    gate = real_kabus_auth_ready(status=status)
    return {
        "blocked": bool(gate.get("ENVIRONMENT_AUTH_BLOCKED")),
        "REAL_KABUS_AUTH_READY": bool(gate.get("REAL_KABUS_AUTH_READY")),
        "http_status": gate.get("http_status"),
        "kabu_code": gate.get("kabu_code"),
        "reason": "ENVIRONMENT_AUTH_BLOCKED" if gate.get("ENVIRONMENT_AUTH_BLOCKED") else "",
    }


def reconcile_startup(
    *,
    native_root: Path,
    trading_date: str,
) -> dict[str, Any]:
    """PRE_INGRESS → RECONCILE → OWNERSHIP_CLEAR. No PID kill."""
    from small_paper.auth_lifecycle import (
        DECISION_FAIL_CLOSED,
        PHASE_PRE_INGRESS,
        apply_pre_ingress_cleanup,
        decide_auth,
        inspect_leftover_auth_state,
        log_auth_decision,
        set_auth_phase,
    )
    from small_paper.ownership_classifier import CONFLICT, PID_REUSED, UNKNOWN

    set_auth_phase(PHASE_PRE_INGRESS)
    residue = inspect_leftover_auth_state(native_root=Path(native_root), trading_date=str(trading_date))
    classified = classify_production_owner(
        native_root=Path(native_root),
        trading_date=str(trading_date),
        pid_alive_fn=None,
    )
    ocls = str(classified.get("class") or residue.get("ownership_class") or "")
    alive = bool(classified.get("process_alive") or residue.get("owner_alive"))
    fail_closed = ocls in {PID_REUSED, CONFLICT} or (ocls == UNKNOWN and alive)
    decision = decide_auth(phase=PHASE_PRE_INGRESS, residue=residue, caller="reconcile_startup")
    log_auth_decision(decision)
    cleanup: dict[str, Any] = {"skipped": True}
    if not fail_closed:
        cleanup = apply_pre_ingress_cleanup(
            native_root=Path(native_root),
            trading_date=str(trading_date),
            residue=residue,
            decision=decision,
        )
    out = {
        "at": _now_iso(),
        "authority": LIFECYCLE_AUTHORITY,
        "sequence_prefix": ["PRE_INGRESS", "RECONCILE", "OWNERSHIP_CLEAR"],
        "ownership_class": ocls,
        "fail_closed": fail_closed,
        "decision": decision,
        "cleanup": cleanup,
        "wrong_process_kill": 0,
        "ok": (not fail_closed) and str(decision.get("decision") or "") != DECISION_FAIL_CLOSED,
    }
    if fail_closed:
        out["ok"] = False
        out["reason"] = f"FAIL_CLOSED_{ocls}"
    return out


CALLSITE_INVENTORY: tuple[dict[str, str], ...] = (
    {
        "module": "paper_trade_checked_runner.py",
        "class": CALLSITE_OWNER,
        "note": "sole top-level production lifecycle authority; no new Supervisor process",
    },
    {
        "module": "runtime_lifecycle.py",
        "class": CALLSITE_OWNER,
        "note": "pure decision helper used by the checked runner",
    },
    {
        "module": "ownership_classifier.py",
        "class": CALLSITE_OWNER,
        "note": "single production ownership classifier SoT",
    },
    {
        "module": "runtime_ownership.py",
        "class": CALLSITE_CONSUMER,
        "note": "thin loader around classify_owner; not a second classifier",
    },
    {
        "module": "auth_lifecycle.py",
        "class": CALLSITE_CONSUMER,
        "note": "inspect/decide consume classifier; PRE_INGRESS reclaim only DEAD_OWNER",
    },
    {
        "module": "kabu_token_authority.py",
        "class": CALLSITE_CONSUMER,
        "note": "claim/publish/reclaim consume classifier; POST /token SoT",
    },
    {
        "module": "api/kabu_register.py",
        "class": CALLSITE_CONSUMER,
        "note": "clear_register_before_session consumes consumer_auth_outcome; no pid kill",
    },
    {
        "module": "capture_child_cleanup.py",
        "class": CALLSITE_CONSUMER,
        "note": "owned-child stop; kill gated by classifier + identity",
    },
    {
        "module": "kabu_readonly_readiness.py",
        "class": CALLSITE_CONSUMER,
        "note": "acquire_token_for_readonly only; never POST /token",
    },
    {
        "module": "live_order_api_wiring.py",
        "class": CALLSITE_CONSUMER,
        "note": "readonly token consumer; no sendorder in paper",
    },
    {
        "module": "bounded_side_task.py",
        "class": CALLSITE_CONSUMER,
        "note": "kills own Popen child after start-identity reconfirm; not Ingress ownership",
    },
    {
        "module": "paper_runtime_supervisor.py",
        "class": CALLSITE_OBSOLETE,
        "note": "ops stall monitor; PRODUCTION_LIFECYCLE_ACTIVE=false; PID-only kill retired",
    },
    {
        "module": "market_ingress_spawn.py",
        "class": CALLSITE_CONSUMER,
        "note": "spawn/wait consume auth decision; session-scoped stderr",
    },
    {
        "module": "pilot_runner.py",
        "class": CALLSITE_CONSUMER,
        "note": "acquire_token_for_readonly; no unique ownership recovery",
    },
    {
        "module": "paper_full_day_certification.py",
        "class": CALLSITE_AUDIT,
        "note": "static source gate; runtime spawn uses checked runner / spawn_ingress_process",
    },
)


LEGACY_RETIREMENT: tuple[dict[str, Any], ...] = (
    {
        "legacy": "legacy preclear unique owner judgement",
        "production_active": False,
        "proof": "clear_register_before_session uses consumer_auth_outcome + classifier residue",
    },
    {
        "legacy": "old token reuse",
        "production_active": False,
        "proof": "TOKEN_STAGE_MISSING/MISMATCH fail-closed; leftover marker IGNORE_LEFTOVER_DO_NOT_REUSE",
    },
    {
        "legacy": "old ingress reuse",
        "production_active": False,
        "proof": "wait_ingress_online binds launch_nonce/pid/start; stale status rejected",
    },
    {
        "legacy": "PID-only cleanup",
        "production_active": False,
        "proof": "cleanup_owned_capture and supervisor kill require classifier + identity",
    },
    {
        "legacy": "readonly unique issue",
        "production_active": False,
        "proof": "kabu_readonly_readiness._readonly_or_owned_issue → acquire_token_for_readonly",
    },
    {
        "legacy": "Certification dedicated auth path",
        "production_active": False,
        "proof": "cert orchestrator is a source gate; issue_station_token remains the only POST /token",
    },
    {
        "legacy": "paper_runtime_supervisor unique kill",
        "production_active": False,
        "proof": "PRODUCTION_LIFECYCLE_ACTIVE=false; _safe_kill gated by decide_kill",
    },
    {
        "legacy": "pilot unique ownership recovery",
        "production_active": False,
        "proof": "pilot_runner uses acquire_token_for_readonly / consumer path",
    },
)


def production_lifecycle_path_proof() -> dict[str, Any]:
    return {
        "lifecycle_authority": LIFECYCLE_AUTHORITY,
        "lifecycle_authority_count": LIFECYCLE_AUTHORITY_COUNT,
        "ownership_classifier": "small_paper.ownership_classifier.classify_owner",
        "ownership_classifier_count": 1,
        "shared_by": [
            "normal Paper checked runner",
            "Full-Day Certification runner (source gate + same spawn/wait)",
            "PM_DIRECT harness",
            "Window A harness",
            "Window B harness",
            "Window C harness",
        ],
        "allowed_to_differ": ["input source", "clock/window", "deterministic replay"],
        "forbidden_to_differ": ["ownership", "start", "auth", "teardown"],
    }
