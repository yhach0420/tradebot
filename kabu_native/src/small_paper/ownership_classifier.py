"""Runtime token/owner identity classifier (V26-B).

Alive checks are never PID-only. PID reuse is fail-closed: no kill, no reuse.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Optional

CURRENT_VALID = "CURRENT_VALID"
STALE_PROVEN_OWNED = "STALE_PROVEN_OWNED"
DEAD_OWNER = "DEAD_OWNER"
PID_REUSED = "PID_REUSED"
UNKNOWN = "UNKNOWN"
CONFLICT = "CONFLICT"

OWNER_INGRESS = "MARKET_INGRESS_SERVICE"

AUTHORITY_CLAIMED_PENDING_TOKEN = "CLAIMED_PENDING_TOKEN"
AUTHORITY_ACTIVE_TOKEN_OWNER = "ACTIVE_TOKEN_OWNER"
AUTHORITY_RELEASED_DEAD = "RELEASED_DEAD"
AUTHORITY_FAILED_ISSUE = "FAILED_ISSUE"

_MANAGED_ROLES = frozenset(
    {
        OWNER_INGRESS,
        "MARKET_INGRESS_SERVICE",
        "MARKET_CAPTURE_SIDECAR",
        "MARKET_CAPTURE_SUPERVISOR",
    }
)


def recorded_process_start(doc: Mapping[str, Any] | None) -> str:
    if not isinstance(doc, Mapping):
        return ""
    for key in (
        "owner_process_start_identity",
        "owner_process_start",
        "process_start_identity",
    ):
        raw = str(doc.get(key) or "").strip()
        if raw:
            return raw
    return ""


def recorded_pid(owner: Mapping[str, Any] | None, bundle: Mapping[str, Any] | None = None) -> int:
    for doc in (owner, bundle):
        if not isinstance(doc, Mapping):
            continue
        for key in ("owner_pid", "pid"):
            try:
                n = int(doc.get(key) or 0)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                return n
    return 0


def managed_previous_ingress(owner: Mapping[str, Any] | None, bundle: Mapping[str, Any] | None = None) -> bool:
    for doc in (owner, bundle):
        if not isinstance(doc, Mapping):
            continue
        for key in ("component_role", "owner_role", "owner", "kabu_token_authority", "role"):
            if str(doc.get(key) or "").strip() in _MANAGED_ROLES:
                return True
        caller = str(doc.get("caller") or doc.get("last_issue_caller") or "").strip()
        if caller.startswith("ingress_"):
            return True
    return False


def _live_process_start(pid: int, fn: Optional[Callable[[int], str]]) -> str:
    if pid <= 0:
        return ""
    if fn is not None:
        try:
            return str(fn(pid) or "").strip()
        except Exception:
            return ""
    try:
        from small_paper.ingress_run_identity import capture_process_start_identity

        return str(capture_process_start_identity(pid) or "").strip()
    except Exception:
        return ""


def _pid_alive_default(pid: int) -> bool:
    from small_paper.kabu_token_authority import _pid_alive

    return bool(_pid_alive(int(pid)))


def _stage_match(bundle: Mapping[str, Any] | None, current: Mapping[str, Any] | None) -> bool:
    if not isinstance(bundle, Mapping) or not isinstance(current, Mapping):
        return False
    want = str(current.get("stage_run_id") or current.get("stage_id") or "").strip()
    got = str(bundle.get("stage_run_id") or bundle.get("stage_id") or "").strip()
    if not want or not got or want != got:
        return False
    want_run = str(current.get("ingress_run_id") or "").strip()
    got_run = str(bundle.get("ingress_run_id") or "").strip()
    if want_run and got_run and want_run != got_run:
        return False
    want_nonce = str(current.get("launch_nonce") or "").strip()
    got_nonce = str(bundle.get("launch_nonce") or "").strip()
    if want_nonce and got_nonce and want_nonce != got_nonce:
        return False
    return True


def classify_owner(
    *,
    owner: Optional[Mapping[str, Any]] = None,
    bundle: Optional[Mapping[str, Any]] = None,
    current: Optional[Mapping[str, Any]] = None,
    pid_alive_fn: Optional[Callable[[int], bool]] = None,
    live_process_start_fn: Optional[Callable[[int], str]] = None,
) -> dict[str, Any]:
    """Classify station/day owner. Never treats PID existence as CURRENT_VALID."""
    own = dict(owner or {})
    bun = dict(bundle or {})
    cur = dict(current or {})
    pid = recorded_pid(own, bun)
    alive_fn = pid_alive_fn or _pid_alive_default
    process_alive = bool(pid > 0 and alive_fn(pid))
    recorded_start = recorded_process_start(own) or recorded_process_start(bun)
    live_start = _live_process_start(pid, live_process_start_fn) if process_alive else ""
    managed = managed_previous_ingress(own, bun)
    authority_state = str(own.get("authority_state") or bun.get("authority_state") or "").strip()
    owner_pid = recorded_pid(own, None)
    bundle_pid = recorded_pid(bun, None)
    out: dict[str, Any] = {
        "class": UNKNOWN,
        "pid": pid,
        "process_alive": process_alive,
        "recorded_process_start_identity": recorded_start or None,
        "live_process_start_identity": live_start or None,
        "managed_previous_ingress": managed,
        "authority_state": authority_state or None,
        "kill_allowed": False,
        "reuse_allowed": False,
        "reclaim_allowed": False,
        "reason": "",
        "wrong_process_kill": 0,
    }
    if owner_pid and bundle_pid and owner_pid != bundle_pid and process_alive:
        pending = authority_state in {
            AUTHORITY_CLAIMED_PENDING_TOKEN,
            AUTHORITY_FAILED_ISSUE,
            AUTHORITY_RELEASED_DEAD,
        }
        other_alive = bool(alive_fn(bundle_pid))
        if other_alive and not pending:
            out["class"] = CONFLICT
            out["reason"] = "owner_bundle_pid_split_live"
            return out
    if pid <= 0:
        out["class"] = UNKNOWN
        out["reason"] = "empty_owner"
        return out
    if process_alive and recorded_start and live_start and recorded_start != live_start:
        out["class"] = PID_REUSED
        out["reason"] = "process_start_identity_mismatch"
        return out
    if process_alive and recorded_start and not live_start:
        out["class"] = UNKNOWN
        out["reason"] = "live_process_start_unproven"
        return out
    current_pid = 0
    try:
        current_pid = int(cur.get("pid") or 0)
    except (TypeError, ValueError):
        current_pid = 0
    identity_match = False
    if current_pid and pid == current_pid:
        identity_match = True
        want_run = str(cur.get("ingress_run_id") or "").strip()
        got_run = str(own.get("ingress_run_id") or bun.get("ingress_run_id") or "").strip()
        want_nonce = str(cur.get("launch_nonce") or "").strip()
        got_nonce = str(own.get("launch_nonce") or bun.get("launch_nonce") or "").strip()
        if want_run and got_run and want_run != got_run:
            identity_match = False
        if want_nonce and got_nonce and want_nonce != got_nonce:
            identity_match = False
        rec_cur = str(cur.get("process_start_identity") or cur.get("owner_process_start_identity") or "").strip()
        if rec_cur and recorded_start and rec_cur != recorded_start:
            identity_match = False
    if process_alive and recorded_start and live_start and recorded_start == live_start:
        if identity_match or _stage_match(bun, cur):
            out["class"] = CURRENT_VALID
            out["reason"] = "live_identity_match"
            return out
        if managed:
            out["class"] = STALE_PROVEN_OWNED
            out["reason"] = "live_managed_not_current"
            return out
        out["class"] = UNKNOWN
        out["reason"] = "live_unmanaged_identity"
        return out
    if not process_alive:
        if managed:
            out["class"] = DEAD_OWNER
            out["reason"] = "managed_pid_not_alive"
            out["reclaim_allowed"] = True
            return out
        out["class"] = DEAD_OWNER
        out["reason"] = "pid_not_alive_unmanaged"
        return out
    # PID exists, no recorded start identity: cannot prove reuse.
    if managed:
        if identity_match and _stage_match(bun, cur) and authority_state == AUTHORITY_ACTIVE_TOKEN_OWNER:
            out["class"] = CURRENT_VALID
            out["reason"] = "current_pid_stage_match_legacy"
            return out
        out["class"] = STALE_PROVEN_OWNED
        out["reason"] = "managed_live_without_process_start"
        return out
    out["class"] = UNKNOWN
    out["reason"] = "live_pid_without_identity"
    return out


def current_identity_from_env(*, pid: int = 0) -> dict[str, Any]:
    from small_paper.ingress_run_identity import ENV_INGRESS_RUN_ID, ENV_LAUNCH_NONCE, ENV_STAGE_RUN_ID

    return {
        "pid": int(pid or os.getpid()),
        "launch_nonce": str(os.environ.get(ENV_LAUNCH_NONCE) or "").strip(),
        "ingress_run_id": str(os.environ.get(ENV_INGRESS_RUN_ID) or "").strip(),
        "stage_run_id": str(os.environ.get(ENV_STAGE_RUN_ID) or "").strip(),
        "process_start_identity": "",
    }
