"""Paper Primary activation binding.

Identity, selection, hash pinning, safety contract, and factory routing.
Does not implement ENTRY, EXIT, or support. The factory imports the frozen
candidate session engine.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from small_paper.v1r_activation_binding import (
    OUT,
    V25_ACTIVATION_ID,
    audit_runtime_inventory_coverage,
    load_activation_manifest,
    load_active_selector,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
    verify_selector_binding,
)

ACTIVATION_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V1"
ACTIVATION_V2_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V2"
ACTIVATION_V3_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V3"
ACTIVATION_V4_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V4"
ACTIVATION_V5_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V5"
ACTIVATION_V6_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V6"
ACTIVATION_V7_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V7"
ACTIVATION_V8_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V8"
ACTIVATION_V9_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V9"
ACTIVATION_V10_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V10"
ACTIVATION_V11_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V11"
ACTIVATION_V12_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V12"
ACTIVATION_V13_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V13"
ACTIVATION_V14_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V14"
ACTIVATION_V15_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V15"
_X1_PARENT = {
    ACTIVATION_V2_ID: ACTIVATION_ID,
    ACTIVATION_V3_ID: ACTIVATION_V2_ID,
    ACTIVATION_V4_ID: ACTIVATION_V3_ID,
    ACTIVATION_V5_ID: ACTIVATION_V4_ID,
    ACTIVATION_V6_ID: ACTIVATION_V5_ID,
    ACTIVATION_V7_ID: ACTIVATION_V6_ID,
    ACTIVATION_V8_ID: ACTIVATION_V7_ID,
    ACTIVATION_V9_ID: ACTIVATION_V8_ID,
    ACTIVATION_V10_ID: ACTIVATION_V9_ID,
    ACTIVATION_V11_ID: ACTIVATION_V10_ID,
    ACTIVATION_V12_ID: ACTIVATION_V11_ID,
    ACTIVATION_V13_ID: ACTIVATION_V12_ID,
    ACTIVATION_V14_ID: ACTIVATION_V13_ID,
    ACTIVATION_V15_ID: ACTIVATION_V14_ID,
}
X1_EXECUTION_FAMILY = "X1_IMMEDIATE_ASK"
X1_SESSION_EXECUTOR = "FixedSupportX1SessionExecutor"
X1_SESSION_LOOP_OWNER = "paper_session_executor"
CANDIDATE_ID = "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1"
ENTRY_SHA = "c93c69f0edf84f5ec03b145ee17e25ef5bc2c4ecaa08cfb5e1c6fca7ec052ba9"
EXIT_SHA = "8f8ddb3d47190b96df84eb83d9e5f694bfd034ebc650a8a86a85822b5bcb8175"
COMPLETE_STRATEGY_SHA = "3002fdada09206568dd6de5e43f6e2c3317a55e40df2d66cdefef317a7c0fd0a"
CANDIDATE_MANIFEST_SHA = "aa5b27b40d2876a809fbbd9a0d9594bbb4d70481b5b24a05d89b395db3fa19a3"
PREVIOUS_PAPER_ACTIVATION_ID = V25_ACTIVATION_ID
PREVIOUS_SELECTOR_PATH = OUT / "history" / "active_v1r_activation_V25_pointer.json"
SESSION_LOOP_OWNER = "candidate_factory"
SESSION_NOT_STARTED = "PAPER_SESSION_NOT_STARTED"
ASSERTION_FAIL = "PAPER_PRIMARY_ASSERTION_FAILED"
FACTORY_MODULE = "research.event_time_impulse_fixed_entry_support_candidate_v1.simulate"


@dataclass
class PaperPrimaryAssertion:
    ok: bool
    reason: str = ""
    checks: dict[str, bool] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    startup_block: str = ""
    ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_previous_paper_activation() -> dict[str, Any]:
    """Load the immutable V25 artifact by path. Does not consult the active selector."""
    path = OUT / f"{PREVIOUS_PAPER_ACTIVATION_ID}.json"
    body = load_activation_manifest(path=path)
    ok, got, calc = verify_manifest_self_sha(body)
    if not ok:
        raise RuntimeError(f"previous activation self-sha mismatch got={got} calc={calc}")
    return body


def format_startup_contract(identity: Mapping[str, Any], *, ready: bool, reason: str = "") -> str:
    """Startup lines are the selected activation fields, not a second hardcoded identity."""
    submit = identity.get("submit", "")
    cancel = identity.get("cancel", "")
    live = identity.get("live", "")
    scl = identity.get("submit_cancel_live") or f"{submit}/{cancel}/{live}"
    lines = [
        "[PAPER PRIMARY STARTUP]",
        f"activation_id={identity.get('activation_id', '')}",
        f"activation_sha={identity.get('activation_sha', '')}",
        f"candidate_name={identity.get('candidate_name', '')}",
        f"ENTRY_SHA={identity.get('entry_sha', '')}",
        f"EXIT_SHA={identity.get('exit_sha', '')}",
        f"COMPLETE_STRATEGY_SHA={identity.get('complete_strategy_sha', '')}",
        f"manifest_SHA={identity.get('manifest_sha', '')}",
        f"primary_role={identity.get('primary_role', '')}",
        f"paper_only={str(identity.get('paper_only', '')).lower()}",
        f"submit/cancel/live={scl}",
        f"READY={'YES' if ready else 'NO'}",
    ]
    if identity.get("execution_family"):
        lines.append(f"execution_family={identity.get('execution_family')}")
    if identity.get("session_executor"):
        lines.append(f"session_executor={identity.get('session_executor')}")
    if reason:
        lines.append(f"reason={reason}")
    return "\n".join(lines)


def _identity_from_manifest(selector: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "activation_id": str(manifest.get("activation_id") or manifest.get("manifest_id") or ""),
        "activation_sha": str(manifest.get("sha256") or ""),
        "candidate_name": str(manifest.get("candidate_name") or manifest.get("primary_strategy") or ""),
        "primary_strategy": str(manifest.get("primary_strategy") or ""),
        "primary_role": str(manifest.get("primary_role") or ""),
        "entry_sha": str(manifest.get("entry_sha") or ""),
        "exit_sha": str(manifest.get("exit_sha") or ""),
        "complete_strategy_sha": str(manifest.get("complete_strategy_sha") or ""),
        "manifest_sha": str(manifest.get("candidate_manifest_sha") or ""),
        "paper_only": manifest.get("paper_only"),
        "live": manifest.get("live"),
        "submit": manifest.get("submit"),
        "cancel": manifest.get("cancel"),
        "submit_cancel_live": str(manifest.get("submit_cancel_live") or ""),
        "session_loop_owner": str(manifest.get("session_loop_owner") or ""),
        "execution_family": str(manifest.get("execution_family") or ""),
        "session_executor": str(manifest.get("session_executor") or ""),
        "parent_activation_id": str(manifest.get("parent_activation_id") or ""),
        "selector_activation_id": str(selector.get("activation_id") or ""),
        "selector_activation_sha": str(selector.get("activation_sha") or ""),
    }


def _fail(
    reason: str,
    checks: dict[str, bool],
    identity: dict[str, Any],
) -> PaperPrimaryAssertion:
    block = format_startup_contract(identity, ready=False, reason=reason)
    return PaperPrimaryAssertion(
        ok=False, reason=reason, checks=checks, identity=identity, startup_block=block, ready=False
    )


def _assert_fixed_entry_support(
    selector: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> PaperPrimaryAssertion:
    checks: dict[str, bool] = {}
    identity = _identity_from_manifest(selector, manifest)

    if not identity["activation_id"]:
        return _fail(f"{ASSERTION_FAIL}:activation_missing", checks, identity)

    bind = verify_selector_binding(selector, manifest)
    checks["selector_activation_id"] = bind["activation_id_match"]
    checks["selector_activation_sha"] = bind["activation_sha_match"]
    self_ok, _got, _calc = verify_manifest_self_sha(manifest)
    checks["manifest_self_sha"] = self_ok
    if not all((checks["selector_activation_id"], checks["selector_activation_sha"], self_ok)):
        return _fail(f"{ASSERTION_FAIL}:activation_sha_mismatch", checks, identity)

    checks["candidate_name"] = identity["candidate_name"] == CANDIDATE_ID == identity["primary_strategy"]
    if not checks["candidate_name"]:
        return _fail(f"{ASSERTION_FAIL}:candidate_name_mismatch", checks, identity)

    checks["entry_sha"] = identity["entry_sha"] == ENTRY_SHA
    checks["exit_sha"] = identity["exit_sha"] == EXIT_SHA
    checks["complete_strategy_sha"] = identity["complete_strategy_sha"] == COMPLETE_STRATEGY_SHA
    checks["manifest_sha"] = identity["manifest_sha"] == CANDIDATE_MANIFEST_SHA
    for key, label in (
        ("entry_sha", "entry_sha_mismatch"),
        ("exit_sha", "exit_sha_mismatch"),
        ("complete_strategy_sha", "complete_strategy_sha_mismatch"),
        ("manifest_sha", "manifest_sha_mismatch"),
    ):
        if not checks[key]:
            return _fail(f"{ASSERTION_FAIL}:{label}", checks, identity)

    checks["primary_role"] = identity["primary_role"] == "PAPER_PRIMARY"
    checks["paper_only"] = identity["paper_only"] is True
    checks["live"] = identity["live"] is False
    checks["submit"] = identity["submit"] == 0
    checks["cancel"] = identity["cancel"] == 0
    checks["submit_cancel_live"] = identity["submit_cancel_live"] == "0/0/0"
    if not checks["paper_only"]:
        return _fail(f"{ASSERTION_FAIL}:paper_only", checks, identity)
    if not checks["live"]:
        return _fail(f"{ASSERTION_FAIL}:live", checks, identity)
    if not (checks["submit"] and checks["cancel"] and checks["submit_cancel_live"] and checks["primary_role"]):
        return _fail(f"{ASSERTION_FAIL}:submit_cancel_live", checks, identity)

    inv = verify_runtime_inventory(manifest)
    gen = verify_generator_inventory_coverage(manifest)
    cov = audit_runtime_inventory_coverage()
    checks["runtime_inventory"] = bool(inv.get("ok")) and bool(gen.get("ok")) and bool(cov.get("ok"))
    if not checks["runtime_inventory"]:
        return _fail(f"{ASSERTION_FAIL}:runtime_inventory", checks, identity)

    from research.event_time_impulse_fixed_entry_support_candidate_v1.identity import bind as bind_candidate

    bound = bind_candidate()
    checks["implementation_entry_sha"] = bound["entry_sha256"] == ENTRY_SHA
    checks["implementation_exit_sha"] = bound["exit_sha256"] == EXIT_SHA
    checks["implementation_complete_sha"] = bound["complete_strategy_sha256"] == COMPLETE_STRATEGY_SHA
    checks["implementation_manifest_sha"] = bound["manifest_sha256"] == CANDIDATE_MANIFEST_SHA
    checks["implementation_candidate"] = bound["manifest"]["candidate_id"] == CANDIDATE_ID
    if not all(
        (
            checks["implementation_entry_sha"],
            checks["implementation_exit_sha"],
            checks["implementation_complete_sha"],
            checks["implementation_manifest_sha"],
            checks["implementation_candidate"],
        )
    ):
        return _fail(f"{ASSERTION_FAIL}:implementation_hash_mismatch", checks, identity)

    identity["session_loop_owner"] = str(manifest.get("session_loop_owner") or SESSION_LOOP_OWNER)
    block = format_startup_contract(identity, ready=True)
    return PaperPrimaryAssertion(
        ok=True, reason="", checks=checks, identity=identity, startup_block=block, ready=True
    )


def assert_selected_paper_primary(
    *,
    path: Optional[Any] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> PaperPrimaryAssertion:
    """Resolve the active selector and require the fixed-support activation.

    The preserved V25 artifact can be loaded by path. Selecting it does not make
    it the current Paper Primary.
    """
    try:
        selector = load_active_selector(path=path, environ=environ)
        manifest = load_activation_manifest(selector=selector)
    except Exception as exc:
        reason = f"{ASSERTION_FAIL}:activation_missing:{type(exc).__name__}:{exc}"
        return _fail(reason, {"selector_load": False}, {})

    aid = str(selector.get("activation_id") or "")
    if aid in (ACTIVATION_ID, ACTIVATION_V2_ID, ACTIVATION_V3_ID, ACTIVATION_V4_ID, ACTIVATION_V5_ID, ACTIVATION_V6_ID, ACTIVATION_V7_ID, ACTIVATION_V8_ID, ACTIVATION_V9_ID, ACTIVATION_V10_ID, ACTIVATION_V11_ID, ACTIVATION_V12_ID, ACTIVATION_V13_ID, ACTIVATION_V14_ID, ACTIVATION_V15_ID):
        result = _assert_fixed_entry_support(selector, manifest)
        if not result.ok:
            return result
        if aid in _X1_PARENT:
            checks = dict(result.checks)
            identity = dict(result.identity)
            parent = _X1_PARENT[aid]
            checks["parent_activation"] = identity.get("parent_activation_id") == parent
            checks["execution_family"] = identity.get("execution_family") == X1_EXECUTION_FAMILY
            checks["session_executor"] = identity.get("session_executor") == X1_SESSION_EXECUTOR
            checks["session_loop_owner"] = identity.get("session_loop_owner") == X1_SESSION_LOOP_OWNER
            if not all(
                (
                    checks["parent_activation"],
                    checks["execution_family"],
                    checks["session_executor"],
                    checks["session_loop_owner"],
                )
            ):
                return _fail(f"{ASSERTION_FAIL}:x1_executor_binding", checks, identity)
            result.checks = checks
        return result
    if aid == PREVIOUS_PAPER_ACTIVATION_ID:
        identity = _identity_from_manifest(selector, manifest)
        roles = manifest.get("runtime_roles") or {}
        identity["primary_strategy"] = str(roles.get("strategy") or identity["primary_strategy"])
        identity["candidate_name"] = identity["primary_strategy"]
        identity["primary_role"] = "PREVIOUS_PAPER_ACTIVATION"
        identity["session_loop_owner"] = "previous_v1r_gate"
        return _fail(
            f"{ASSERTION_FAIL}:previous_activation_not_current_primary",
            {"previous_activation_resolved": True, "current_primary": False},
            identity,
        )
    return _fail(
        f"{ASSERTION_FAIL}:candidate_name_mismatch",
        {"candidate_name": False},
        _identity_from_manifest(selector, manifest),
    )


def resolve_paper_primary(
    *,
    path: Optional[Any] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Construct the parity-proven candidate engine. Does not start a session."""
    assertion = assert_selected_paper_primary(path=path, environ=environ)
    if not assertion.ok:
        raise RuntimeError(assertion.reason or ASSERTION_FAIL)
    owner = assertion.identity.get("session_loop_owner")
    if owner == X1_SESSION_LOOP_OWNER:
        from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor

        return {
            "candidate_name": assertion.identity["candidate_name"],
            "primary_role": assertion.identity["primary_role"],
            "session_executor": FixedSupportX1SessionExecutor,
            "execution_family": X1_EXECUTION_FAMILY,
            "simulate_session": None,
            "identity": assertion.identity,
            "paper_executed": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
    if owner != SESSION_LOOP_OWNER:
        raise RuntimeError(f"{ASSERTION_FAIL}:factory_requires_candidate_activation")
    from research.event_time_impulse_fixed_entry_support_candidate_v1.simulate import simulate_session

    if simulate_session.__module__ != FACTORY_MODULE:
        raise RuntimeError(f"{ASSERTION_FAIL}:factory_module_mismatch")
    return {
        "candidate_name": assertion.identity["candidate_name"],
        "primary_role": assertion.identity["primary_role"],
        "simulate_session": simulate_session,
        "identity": assertion.identity,
        "paper_executed": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
