"""Registration attempt generation identity (trading_date + desired universe SHA).

PUSH must not unconditionally retry PUT. A terminal failed batch may only be
retried when the desired universe SHA changes or an operator/reset generation
occurs.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

REGISTERED = "REGISTERED"
TERMINAL_PARTIAL = "TERMINAL_PARTIAL"
PARTIAL_UNCONFIRMED = "PARTIAL_UNCONFIRMED"
TEMPORARY_FAILED = "TEMPORARY_FAILED"
NONE = "NONE"

TERMINAL_KABU_CODES = frozenset({"4001019"})
ENV_INGRESS_WAIT_FOR_FREEZE = "TRADEBOT_INGRESS_WAIT_FOR_FREEZE"
_TRUE = {"1", "true", "yes", "on"}


def ingress_wait_for_freeze(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    import os

    env = environ if environ is not None else os.environ
    return str(env.get(ENV_INGRESS_WAIT_FOR_FREEZE, "") or "").strip().lower() in _TRUE


def desired_universe_sha(symbols: Sequence[Any]) -> str:
    from small_paper.day_fixed_am_registration import canonical_membership_sha

    return canonical_membership_sha(list(symbols or []))


def attempt_identity(trading_date: str, desired_sha: str) -> tuple[str, str]:
    return (str(trading_date or ""), str(desired_sha or ""))


def is_terminal_batch_reject(*, kabu_code: Any = "", error: Any = "") -> bool:
    code = str(kabu_code or "").strip()
    text = str(error or "")
    return code in TERMINAL_KABU_CODES or any(c in text for c in TERMINAL_KABU_CODES)


def should_attempt_register(
    *,
    trading_date: str,
    desired_sha: str,
    registration_generation: int = 0,
    last_attempt_date: str = "",
    last_attempt_sha: str = "",
    last_attempt_state: str = NONE,
    last_attempt_generation: int = 0,
    operator_reset: bool = False,
) -> dict[str, Any]:
    """Decide whether a PUT/register attempt is allowed for this generation."""
    ident = attempt_identity(trading_date, desired_sha)
    last = attempt_identity(last_attempt_date, last_attempt_sha)
    gen = int(registration_generation or 0)
    last_gen = int(last_attempt_generation or 0)
    state = str(last_attempt_state or NONE)
    if operator_reset or (ident == last and gen > last_gen):
        return {
            "allow": True,
            "reason": "operator_reset_generation",
            "attempt_identity": ident,
        }
    if ident != last:
        return {
            "allow": True,
            "reason": "new_desired_universe",
            "attempt_identity": ident,
        }
    if state == REGISTERED:
        return {
            "allow": False,
            "reason": "already_registered_same_universe",
            "attempt_identity": ident,
        }
    if state in {TERMINAL_PARTIAL, PARTIAL_UNCONFIRMED}:
        return {
            "allow": False,
            "reason": "terminal_same_universe_suppressed",
            "attempt_identity": ident,
        }
    if state == TEMPORARY_FAILED:
        return {
            "allow": True,
            "reason": "temporary_retry",
            "attempt_identity": ident,
        }
    return {
        "allow": True,
        "reason": "first_attempt",
        "attempt_identity": ident,
    }


def snapshot_attempt(
    *,
    trading_date: str,
    desired_sha: str,
    registration_generation: int,
    state: str,
) -> dict[str, Any]:
    return {
        "trading_date": str(trading_date or ""),
        "desired_universe_sha": str(desired_sha or ""),
        "registration_generation": int(registration_generation or 0),
        "state": str(state or NONE),
        "attempt_identity": list(attempt_identity(trading_date, desired_sha)),
    }
