"""Paper Runtime auth lifecycle phases (V25).

Token consumer decisions are explicit per phase. Implicit defer/fail is forbidden.
PRE_INGRESS defer is not "continue Paper unauthenticated"; it only waits for
current-stage Ingress to issue. POST_INGRESS_PRE_BOARD and later fail-close.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ENV_AUTH_PHASE = "TRADEBOT_AUTH_LIFECYCLE_PHASE"

PHASE_PRE_INGRESS = "PRE_INGRESS"
PHASE_INGRESS_STARTING = "INGRESS_STARTING"
PHASE_POST_INGRESS_PRE_BOARD = "POST_INGRESS_PRE_BOARD"
PHASE_BOARD_ACTIVE = "BOARD_ACTIVE"
PHASE_AM_RUNTIME = "AM_RUNTIME"
PHASE_AM_TO_PM_TRANSITION = "AM_TO_PM_TRANSITION"
PHASE_PM_RUNTIME = "PM_RUNTIME"
PHASE_TEARDOWN = "TEARDOWN"

PHASES = (
    PHASE_PRE_INGRESS,
    PHASE_INGRESS_STARTING,
    PHASE_POST_INGRESS_PRE_BOARD,
    PHASE_BOARD_ACTIVE,
    PHASE_AM_RUNTIME,
    PHASE_AM_TO_PM_TRANSITION,
    PHASE_PM_RUNTIME,
    PHASE_TEARDOWN,
)

DECISION_DEFER = "DEFER"
DECISION_FAIL_CLOSED = "FAIL_CLOSED"
DECISION_CLEANUP = "CLEANUP"
DECISION_REISSUE = "REISSUE"
DECISION_PASS = "PASS"

REASON_ISSUER_NOT_STARTED = "CURRENT_STAGE_ISSUER_NOT_STARTED"
REASON_CURRENT_IDENTITY_NOT_PROVEN = "CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN"
REASON_STALE_STAGE = "STALE_STAGE_TOKEN_REJECTED"
REASON_OWNER_DEAD = "CURRENT_STAGE_OWNER_DEAD"
REASON_PID_MISMATCH = "ISSUER_OWNER_PID_MISMATCH"
REASON_GENERATION_MISMATCH = "TOKEN_GENERATION_MISMATCH"
REASON_DUPLICATE_ISSUER = "DUPLICATE_TOKEN_ISSUER"
REASON_MATCH = "TOKEN_STAGE_MATCH"
REASON_LEFTOVER_IGNORED = "PRE_INGRESS_LEFTOVER_NOT_REUSED"
REASON_TEARDOWN = "TEARDOWN_NO_CONSUMER_TOKEN"

FAIL_CLOSED_PHASES = frozenset(
    {
        PHASE_POST_INGRESS_PRE_BOARD,
        PHASE_BOARD_ACTIVE,
        PHASE_AM_RUNTIME,
        PHASE_AM_TO_PM_TRANSITION,
        PHASE_PM_RUNTIME,
    }
)

CALLER_DEFAULT_PHASE: dict[str, str] = {
    "kabu_readonly_readiness": PHASE_PRE_INGRESS,
    "push_client_from_repo": PHASE_PRE_INGRESS,
    "clear_register_before_session": PHASE_PRE_INGRESS,
    "checked_runner.preclear": PHASE_PRE_INGRESS,
    "live_order_api_wiring": PHASE_PRE_INGRESS,
    "legacy_register_preclear": PHASE_PRE_INGRESS,
    "preflight_before_am": PHASE_PRE_INGRESS,
    "before_pm_session": PHASE_PRE_INGRESS,
    "after_am_session": PHASE_AM_TO_PM_TRANSITION,
    "verify_kabu_connection": PHASE_POST_INGRESS_PRE_BOARD,
    "board": PHASE_POST_INGRESS_PRE_BOARD,
    "safety": PHASE_POST_INGRESS_PRE_BOARD,
    "window_b": PHASE_POST_INGRESS_PRE_BOARD,
    "window_b_safety": PHASE_POST_INGRESS_PRE_BOARD,
    "window_c_safety": PHASE_POST_INGRESS_PRE_BOARD,
    "ingress_replay_connect": PHASE_INGRESS_STARTING,
    "ingress_connect": PHASE_INGRESS_STARTING,
    "run_live_dry_run": PHASE_AM_RUNTIME,
    "run_poll_dry_run": PHASE_AM_RUNTIME,
    "market_capture_sidecar": PHASE_POST_INGRESS_PRE_BOARD,
    "wait_ingress_online": PHASE_POST_INGRESS_PRE_BOARD,
    "paper_safety": PHASE_BOARD_ACTIVE,
    "pilot_reconnect": PHASE_AM_RUNTIME,
    "pilot_reconnect_ingress_v2": PHASE_AM_RUNTIME,
}


def set_auth_phase(phase: str, *, environ: Optional[dict[str, str]] = None) -> str:
    p = str(phase or "").strip().upper()
    if p not in PHASES:
        raise ValueError(f"unknown auth lifecycle phase: {phase}")
    env = environ if environ is not None else os.environ
    env[ENV_AUTH_PHASE] = p
    if environ is None:
        os.environ[ENV_AUTH_PHASE] = p
    return p


def current_auth_phase(*, caller: str = "", environ: Optional[Mapping[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_AUTH_PHASE) or "").strip().upper()
    if raw in PHASES:
        return raw
    mapped = CALLER_DEFAULT_PHASE.get(str(caller or "").strip(), "")
    if mapped:
        return mapped
    return PHASE_POST_INGRESS_PRE_BOARD


def inspect_leftover_auth_state(
    *,
    native_root: Path,
    trading_date: str,
    want: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    from small_paper.kabu_token_authority import (
        TOKEN_STAGE_MATCH,
        TOKEN_STAGE_MISSING,
        TOKEN_STAGE_MISMATCH,
        TOKEN_STAGE_NOT_APPLICABLE,
        classify_token_stage,
        current_stage_token_identity,
        load_station_bundle,
        load_station_owner,
        station_authority_dir,
        _pid_alive,
    )

    native = Path(native_root)
    day = str(trading_date)
    ident = dict(want or current_stage_token_identity())
    bundle = load_station_bundle()
    owner = load_station_owner() or {}
    classified = classify_token_stage(bundle, want=ident)
    owner_pid = int(owner.get("pid") or bundle.get("pid") or bundle.get("owner_pid") or 0)
    owner_alive = _pid_alive(owner_pid) if owner_pid > 0 else False
    station = station_authority_dir()
    lock_path = station / "issue.lock"
    day_dir = native / "data" / "market_capture" / day
    pid_path = day_dir / "ingress.pid"
    status_path = day_dir / "ingress_status.json"
    ingress_pid = 0
    ingress_alive = False
    status: dict[str, Any] = {}
    if pid_path.is_file():
        try:
            ingress_pid = int((pid_path.read_text(encoding="utf-8") or "0").strip() or 0)
        except (TypeError, ValueError, OSError):
            ingress_pid = 0
    if ingress_pid > 0:
        ingress_alive = _pid_alive(ingress_pid)
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status = {"corrupt": True}
        if not ingress_pid:
            try:
                ingress_pid = int(status.get("pid") or 0)
            except (TypeError, ValueError):
                ingress_pid = 0
            if ingress_pid > 0:
                ingress_alive = _pid_alive(ingress_pid)
    token_file = native / "data" / "kabu_token" / day / ".kabu_session_token"
    if not token_file.is_file():
        token_file = station / "kabu_station_token_bundle.json"
    bundle_gen = int(bundle.get("generation") or bundle.get("token_generation") or 0)
    owner_gen = int(owner.get("token_generation") or owner.get("generation") or 0)
    gen = bundle_gen or owner_gen
    got_stage = str(bundle.get("stage_run_id") or "")
    stage_key = "stage_run_id" in bundle
    ownership: dict[str, Any] = {}
    try:
        from small_paper.ownership_classifier import classify_owner

        ownership = classify_owner(
            owner=owner,
            bundle=bundle,
            current=ident,
            pid_alive_fn=_pid_alive,
        )
    except Exception as exc:
        ownership = {"class": "UNKNOWN", "reason": f"classifier_error:{type(exc).__name__}"}
    return {
        "want_stage": str(ident.get("stage_run_id") or "") or None,
        "got_stage": got_stage or ("missing" if (ident.get("stage_run_id") and not stage_key) else None),
        "stage_class": classified.get("class"),
        "stage_code": classified.get("code"),
        "generation": gen,
        "owner_pid": owner_pid,
        "owner_alive": owner_alive,
        "ownership_class": ownership.get("class"),
        "ownership_reason": ownership.get("reason"),
        "process_alive": ownership.get("process_alive"),
        "process_start_identity": ownership.get("recorded_process_start_identity"),
        "ingress_pid": ingress_pid,
        "ingress_alive": ingress_alive,
        "owner_ingress_pid_match": (owner_pid == ingress_pid) if owner_pid and ingress_pid else None,
        "has_bundle": bool(bundle),
        "has_token_string": bool(str(bundle.get("token") or "").strip()),
        "bundle_corrupt": bool(status.get("corrupt")) if status.get("corrupt") else False,
        "lock_present": lock_path.is_file(),
        "pid_file_present": pid_path.is_file(),
        "status_state": str(status.get("state") or "") or None,
        "token_stage_class_status": str(status.get("token_stage_class") or "") or None,
        "generation_mismatch": bool(bundle_gen and owner_gen and bundle_gen != owner_gen),
        "unscoped": classified.get("class") == TOKEN_STAGE_MISSING,
        "previous_stage": classified.get("class") == TOKEN_STAGE_MISMATCH,
        "match": classified.get("class") == TOKEN_STAGE_MATCH,
        "not_applicable": classified.get("class") == TOKEN_STAGE_NOT_APPLICABLE,
        "classified": classified,
        "ident": ident,
        "native_root": str(native),
        "trading_date": day,
    }


def decide_auth(
    *,
    phase: str,
    residue: Mapping[str, Any],
    caller: str = "",
) -> dict[str, Any]:
    """Return explicit DEFER / FAIL_CLOSED / CLEANUP / REISSUE / PASS."""
    p = str(phase or "").strip().upper() or PHASE_POST_INGRESS_PRE_BOARD
    stage_class = str(residue.get("stage_class") or "")
    want_stage = str(residue.get("want_stage") or "").strip()
    got_stage = str(residue.get("got_stage") or "")
    owner_pid = int(residue.get("owner_pid") or 0)
    owner_alive = bool(residue.get("owner_alive"))
    ingress_pid = int(residue.get("ingress_pid") or 0)
    ingress_alive = bool(residue.get("ingress_alive"))
    gen = residue.get("generation")
    out: dict[str, Any] = {
        "phase": p,
        "caller": caller,
        "decision": DECISION_FAIL_CLOSED,
        "reason": REASON_CURRENT_IDENTITY_NOT_PROVEN,
        "expected_stage": want_stage or None,
        "got_stage": got_stage or "missing",
        "expected_generation": None,
        "got_generation": gen,
        "issuer_pid": owner_pid or None,
        "owner_pid": owner_pid or None,
        "current_ingress_pid": ingress_pid or None,
        "token_source": "station_bundle",
        "stage_class": stage_class,
    }
    if p not in PHASES:
        out["reason"] = "UNKNOWN_AUTH_PHASE"
        return out

    if p == PHASE_TEARDOWN:
        out["decision"] = DECISION_CLEANUP
        out["reason"] = REASON_TEARDOWN
        return out

    if owner_pid and ingress_pid and owner_pid != ingress_pid and owner_alive and ingress_alive:
        out["decision"] = DECISION_FAIL_CLOSED
        out["reason"] = REASON_DUPLICATE_ISSUER
        return out
    ocls = str(residue.get("ownership_class") or "")
    if ocls in {"PID_REUSED", "CONFLICT"} or (
        ocls == "UNKNOWN" and bool(residue.get("owner_alive") or residue.get("process_alive"))
    ):
        out["decision"] = DECISION_FAIL_CLOSED
        out["reason"] = ocls
        out["ownership_class"] = ocls
        return out
    if bool(residue.get("generation_mismatch")) and p in FAIL_CLOSED_PHASES:
        out["decision"] = DECISION_FAIL_CLOSED
        out["reason"] = REASON_GENERATION_MISMATCH
        return out
    if bool(residue.get("bundle_corrupt")) and p in FAIL_CLOSED_PHASES:
        out["decision"] = DECISION_FAIL_CLOSED
        out["reason"] = REASON_CURRENT_IDENTITY_NOT_PROVEN
        return out

    if p == PHASE_INGRESS_STARTING:
        if stage_class == "TOKEN_STAGE_MATCH" and owner_alive and owner_pid:
            out["decision"] = DECISION_PASS
            out["reason"] = REASON_MATCH
            return out
        out["decision"] = DECISION_REISSUE
        out["reason"] = "CURRENT_STAGE_INGRESS_MUST_ISSUE"
        return out

    no_token = not bool(residue.get("has_token_string"))
    leftover = bool(want_stage) and stage_class in {
        "TOKEN_STAGE_MISSING",
        "TOKEN_STAGE_MISMATCH",
        "",
    }
    if not want_stage:
        if residue.get("has_token_string") and owner_alive:
            out["decision"] = DECISION_PASS
            out["reason"] = "TOKEN_STAGE_NOT_APPLICABLE"
            return out
        if p == PHASE_PRE_INGRESS:
            out["decision"] = DECISION_DEFER
            out["reason"] = REASON_ISSUER_NOT_STARTED
            return out
        out["decision"] = DECISION_FAIL_CLOSED if p in FAIL_CLOSED_PHASES else DECISION_DEFER
        out["reason"] = REASON_ISSUER_NOT_STARTED
        return out
    if p == PHASE_PRE_INGRESS:
        if stage_class == "TOKEN_STAGE_MATCH" and owner_alive:
            out["decision"] = DECISION_PASS
            out["reason"] = REASON_MATCH
            return out
        material = bool(
            residue.get("has_bundle")
            or residue.get("has_token_string")
            or residue.get("pid_file_present")
        )
        if leftover and material:
            out["decision"] = DECISION_CLEANUP
        else:
            out["decision"] = DECISION_DEFER
        out["reason"] = REASON_ISSUER_NOT_STARTED
        return out

    if p in FAIL_CLOSED_PHASES:
        if owner_pid and ingress_pid and owner_pid != ingress_pid and owner_alive and ingress_alive:
            out["decision"] = DECISION_FAIL_CLOSED
            out["reason"] = REASON_PID_MISMATCH
            return out
        if stage_class == "TOKEN_STAGE_MATCH" and owner_alive:
            out["decision"] = DECISION_PASS
            out["reason"] = REASON_MATCH
            return out
        if stage_class == "TOKEN_STAGE_MISMATCH" or (want_stage and got_stage not in {"", "missing"} and got_stage != want_stage):
            out["decision"] = DECISION_FAIL_CLOSED
            out["reason"] = REASON_STALE_STAGE
            return out
        if stage_class == "TOKEN_STAGE_MISSING" or (want_stage and (not got_stage or got_stage == "missing")):
            out["decision"] = DECISION_FAIL_CLOSED
            out["reason"] = REASON_CURRENT_IDENTITY_NOT_PROVEN
            return out
        if stage_class == "TOKEN_STAGE_MATCH" and not owner_alive:
            out["decision"] = DECISION_FAIL_CLOSED
            out["reason"] = REASON_OWNER_DEAD
            return out
        if p == PHASE_AM_TO_PM_TRANSITION and stage_class == "TOKEN_STAGE_MATCH":
            out["decision"] = DECISION_PASS
            out["reason"] = REASON_MATCH
            return out
        out["decision"] = DECISION_FAIL_CLOSED
        out["reason"] = REASON_CURRENT_IDENTITY_NOT_PROVEN
        return out

    out["decision"] = DECISION_FAIL_CLOSED
    out["reason"] = REASON_CURRENT_IDENTITY_NOT_PROVEN
    return out


def log_auth_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(decision)
    body["at"] = datetime.now(JST).isoformat(timespec="milliseconds")
    line = (
        "AUTH_DECISION "
        f"phase={body.get('phase')} "
        f"decision={body.get('decision')} "
        f"reason={body.get('reason')} "
        f"expected_stage={body.get('expected_stage')} "
        f"got_stage={body.get('got_stage')} "
        f"generation={body.get('got_generation')} "
        f"issuer_pid={body.get('issuer_pid')} "
        f"owner_pid={body.get('owner_pid')} "
        f"current_ingress_pid={body.get('current_ingress_pid')} "
        f"caller={body.get('caller')}"
    )
    print(line, flush=True)
    return body


def apply_pre_ingress_cleanup(
    *,
    native_root: Path,
    trading_date: str,
    residue: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Mark leftover unscoped/previous token as not reusable. Never kill current-stage owner.

    Does not delete the Station bundle (current Ingress publish replaces it).
    Does not kill PIDs (PID reuse). Dead managed owners are authority-reclaimed only.
    """
    native = Path(native_root)
    day_dir = native / "data" / "market_capture" / str(trading_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    reclaim: dict[str, Any] = {
        "reclaimed": False,
        "killed_pid": None,
        "wrong_process_kill": 0,
        "bundle_deleted": False,
    }
    try:
        from small_paper.kabu_token_authority import reclaim_dead_station_owner

        reclaim = reclaim_dead_station_owner(native_root=native, trading_date=str(trading_date))
    except Exception as exc:
        reclaim = {
            "reclaimed": False,
            "killed_pid": None,
            "wrong_process_kill": 0,
            "bundle_deleted": False,
            "error": type(exc).__name__,
        }
    marker = {
        "at": datetime.now(JST).isoformat(timespec="milliseconds"),
        "action": "IGNORE_LEFTOVER_DO_NOT_REUSE",
        "killed_pid": None,
        "bundle_deleted": False,
        "current_owner_preserved": not bool(reclaim.get("reclaimed")),
        "dead_owner_reclaimed": bool(reclaim.get("reclaimed")),
        "wrong_process_kill": 0,
        "reclaim": reclaim,
        "residue": {
            k: residue.get(k)
            for k in (
                "want_stage",
                "got_stage",
                "generation",
                "owner_pid",
                "owner_alive",
                "ingress_pid",
                "stage_class",
            )
        },
        "decision": dict(decision),
    }
    path = day_dir / "pre_ingress_leftover_ignored.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {
        "ok": True,
        "marker": str(path),
        "killed_pid": None,
        "wrong_process_kill": 0,
        "reclaim": reclaim,
    }


def consumer_auth_outcome(
    *,
    native_root: Path,
    trading_date: str,
    caller: str,
    phase: Optional[str] = None,
) -> dict[str, Any]:
    p = str(phase or current_auth_phase(caller=caller))
    residue = inspect_leftover_auth_state(native_root=native_root, trading_date=trading_date)
    decision = decide_auth(phase=p, residue=residue, caller=caller)
    log_auth_decision(decision)
    if decision.get("decision") == DECISION_CLEANUP and p == PHASE_PRE_INGRESS:
        apply_pre_ingress_cleanup(
            native_root=native_root,
            trading_date=trading_date,
            residue=residue,
            decision=decision,
        )
    return {"phase": p, "residue": residue, "decision": decision}


def raise_for_consumer_decision(decision: Mapping[str, Any], *, caller: str) -> None:
    from small_paper.kabu_token_authority import (
        CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN,
        STALE_STAGE_TOKEN_REJECTED,
        CurrentStageTokenIdentityNotProven,
        StaleStageTokenRejected,
        TokenUnavailable,
    )

    code = str(decision.get("decision") or "")
    reason = str(decision.get("reason") or "")
    want = decision.get("expected_stage")
    got = decision.get("got_stage")
    if code in {DECISION_DEFER, DECISION_CLEANUP}:
        raise TokenUnavailable(
            f"AUTH_DEFERRED_UNTIL_INGRESS caller={caller} phase={decision.get('phase')} "
            f"reason={reason} want_stage={want} got_stage={got}"
        )
    if reason == REASON_STALE_STAGE:
        raise StaleStageTokenRejected(
            f"{STALE_STAGE_TOKEN_REJECTED} caller={caller} phase={decision.get('phase')} "
            f"want_stage={want} got_stage={got}"
        )
    if reason in {REASON_ISSUER_NOT_STARTED, REASON_TEARDOWN} and not want:
        raise TokenUnavailable(
            f"INGRESS_TOKEN_UNAVAILABLE caller={caller} phase={decision.get('phase')} "
            f"reason={reason}"
        )
    raise CurrentStageTokenIdentityNotProven(
        f"{CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN} caller={caller} phase={decision.get('phase')} "
        f"reason={reason} want_stage={want} got_stage={got}"
    )
