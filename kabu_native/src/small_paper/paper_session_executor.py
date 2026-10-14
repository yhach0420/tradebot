"""Activation-metadata dispatch for the Paper session executor.

The resolver reads execution-family metadata. It does not branch on strategy names.
Registration stays here, outside the X1 position owner.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Optional

PASSIVE_FILL_ENTRY_V1 = "PASSIVE_FILL_ENTRY_V1"
X1_IMMEDIATE_ASK = "X1_IMMEDIATE_ASK"
V1R_EXECUTOR = "V1rPassiveSessionExecutor"
X1_EXECUTOR = "FixedSupportX1SessionExecutor"
C3_EXECUTOR = "C3PaperSessionExecutor"
CAUSAL_EXECUTOR = "PaperExecutionCausalBook"
X1_OWNER_CLASSES = {X1_EXECUTOR, C3_EXECUTOR, CAUSAL_EXECUTOR}
X1_OWNER_ENV = "TRADEBOT_X1_SESSION_OWNER"
BINDING_CONTRADICTION = "PAPER_EXECUTOR_BINDING_CONTRADICTION"


class PaperExecutorBindingContradiction(RuntimeError):
    """Startup named the X1 executor, but a live component booted V1R entry."""


class UnresolvedPaperExecutor(RuntimeError):
    """Activation has no bound session executor. Callers must not invent one."""


def execution_family_of(activation: Mapping[str, Any]) -> str:
    """Read the execution family from activation metadata only."""
    family = str(activation.get("execution_family") or "")
    if family:
        return family
    roles = activation.get("runtime_roles") or {}
    manifest = str(roles.get("entry_manifest") or "")
    if manifest == PASSIVE_FILL_ENTRY_V1:
        return PASSIVE_FILL_ENTRY_V1
    return ""


def x1_live_owner_armed() -> bool:
    return str(os.environ.get(X1_OWNER_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def arm_x1_live_owner() -> None:
    os.environ[X1_OWNER_ENV] = "1"


def guard_v1r_native_boot() -> None:
    """Fail closed after the X1 live owner is armed. Legacy V1R boot stays available otherwise."""
    if x1_live_owner_armed():
        raise PaperExecutorBindingContradiction(BINDING_CONTRADICTION)


def bind_selected_live_executor(
    activation: Optional[Mapping[str, Any]] = None,
    *,
    strategy_mode: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve the selected session owner. X1 arms the V1R-boot guard and keeps the object."""
    if activation is None:
        from small_paper.v1r_activation_binding import load_activation_manifest, load_active_selector

        activation = load_activation_manifest(selector=load_active_selector())
    exe = resolve_paper_session_executor(activation, strategy_mode=strategy_mode)
    family = str(getattr(exe, "execution_family", "") or "")
    cls = type(exe).__name__
    x1 = family == X1_IMMEDIATE_ASK and cls in X1_OWNER_CLASSES
    if x1:
        arm_x1_live_owner()
    return {
        "executor": exe,
        "x1": x1,
        "session_owner_class": cls,
        "admission_owner_class": cls,
        "portfolio_owner_class": getattr(exe, "session_executor", cls),
        "execution_family": family,
        "execution_mode": str(getattr(exe, "execution_mode", "") or ""),
        "session_executor": getattr(exe, "session_executor", ""),
        "boot_v1r_native_entry": 0 if x1 else None,
    }


def resolve_sealed_session_executor(
    activation: Mapping[str, Any],
    *,
    strategy_mode: Optional[str] = None,
) -> Any:
    """Immediate-fill owner. Replay uses this so the offline flag cannot select it."""
    return resolve_paper_session_executor(activation, strategy_mode=strategy_mode, sealed=True)


def resolve_paper_session_executor(
    activation: Mapping[str, Any],
    *,
    strategy_mode: Optional[str] = None,
    sealed: bool = False,
) -> Any:
    """Map activation metadata to the one session executor.

    PASSIVE_FILL_ENTRY_V1 → V1r passiveSessionExecutor
    X1_IMMEDIATE_ASK → C3, or the causal C3 book when the paper flag is on
    """
    family = execution_family_of(activation)
    named = str(activation.get("session_executor") or "")
    if family == X1_IMMEDIATE_ASK or named == X1_EXECUTOR:
        if family not in ("", X1_IMMEDIATE_ASK):
            raise UnresolvedPaperExecutor(family)
        if strategy_mode is None:
            from small_paper.c3_paper_session import read_paper_strategy_mode

            strategy_mode = read_paper_strategy_mode()
        if strategy_mode == "C3" and not sealed:
            from small_paper.paper_runner_settlement_v1 import causal_paper_enabled

            if causal_paper_enabled():
                from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook

                executor = PaperExecutionCausalBook()
            else:
                from small_paper.c3_paper_session import C3PaperSessionExecutor

                executor = C3PaperSessionExecutor()
        elif strategy_mode == "C3":
            from small_paper.c3_paper_session import C3PaperSessionExecutor

            executor = C3PaperSessionExecutor()
        elif strategy_mode == "FIXED_SUPPORT_X1":
            from small_paper.c3_paper_session import residual_restore_block

            blocked = residual_restore_block()
            if blocked:
                raise UnresolvedPaperExecutor(blocked)
            from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor

            executor = FixedSupportX1SessionExecutor()
        else:
            raise UnresolvedPaperExecutor("PAPER_STRATEGY_MODE_FAIL_CLOSED")
        executor.activation_id = str(activation.get("activation_id") or activation.get("manifest_id") or "")
        executor.activation_sha = str(activation.get("sha256") or "")
        executor.candidate_name = str(activation.get("candidate_name") or activation.get("primary_strategy") or "")
        if strategy_mode == "C3":
            executor.candidate_name = "V16_C3_BUY1_TIME_RESIDUAL"
        return executor
    if family == PASSIVE_FILL_ENTRY_V1 or named == V1R_EXECUTOR:
        if family not in ("", PASSIVE_FILL_ENTRY_V1):
            raise UnresolvedPaperExecutor(family)
        from small_paper.v1r_passive_session_executor import V1rPassiveSessionExecutor

        return V1rPassiveSessionExecutor()
    raise UnresolvedPaperExecutor(family or named or "missing")


Admission = Callable[[str, float], tuple[bool, str]]


def _admission_label(result: Mapping[str, Any]) -> tuple[bool, str]:
    passed = bool(result.get("ok") and result.get("kabu_probe_symbol_registered"))
    if passed:
        return True, "PASS"
    reason = str(result.get("reason") or "")
    if reason == "probe_symbol_not_in_actual_registered_set":
        return False, "NOT_REGISTERED"
    if reason == "actual_registered_exact50_required":
        return False, "EXACT50_FAIL_CLOSED"
    return False, reason or "FAIL_CLOSED"


def _bare_membership_symbol(symbol: str) -> str:
    text = str(symbol or "").strip()
    if text.endswith(".T"):
        text = text[:-2]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.strip()


def bind_outer_registration(
    executor: Any,
    *,
    native_root: Any,
    trading_date: str,
    universe: Optional[list[str]] = None,
) -> Admission:
    """EXACT50 actual_symbols is the day-fixed AM freeze, never a PM screening CSV.

    Screening `universe` may differ (PM rebuild). That diff is recorded and ignored
    for admission membership so a 9223-vs-4166 PM CSV cannot zero every X1 entry.
    """
    from small_paper.kabu_registration_authority import resolve_registered_probe_symbol
    from small_paper.v1r_native_entry_live import resolve_day_fixed_am_runtime_universe

    passed = [_bare_membership_symbol(s) for s in (universe or []) if _bare_membership_symbol(s)]
    resolved = resolve_day_fixed_am_runtime_universe(
        native_root=native_root, trading_date=trading_date
    )
    freeze = [_bare_membership_symbol(s) for s in (resolved.get("symbols") or []) if _bare_membership_symbol(s)]
    screening_diff = bool(passed) and bool(freeze) and set(passed) != set(freeze)
    if resolved.get("ok") and freeze:
        symbols = freeze
    elif passed:
        symbols = passed
        screening_diff = False
    else:
        def refuse(symbol: str, _t: float) -> tuple[bool, str]:
            return False, "EXACT50_FAIL_CLOSED"

        executor.admission = refuse
        executor.admission_membership = []
        executor.admission_membership_source = str(resolved.get("reason") or "EMPTY_UNIVERSE")
        executor.screening_session_diff = False
        return refuse

    executor.admission_membership = list(symbols)
    executor.admission_membership_source = str(
        resolved.get("authority") or resolved.get("source") or "DAY_FIXED_AM"
    )
    executor.screening_session_diff = bool(screening_diff)

    def admit(symbol: str, _t: float) -> tuple[bool, str]:
        result = resolve_registered_probe_symbol(
            native_root,
            str(trading_date),
            actual_symbols=list(symbols),
            proposed_symbol=symbol,
            push=None,
            write_audit=False,
        )
        return _admission_label(result)

    executor.admission = admit
    return admit
