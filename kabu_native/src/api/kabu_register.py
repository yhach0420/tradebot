"""
Phase 155: kabu PUSH register capacity helpers (unregister/all + register retry).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence

from api.push_client import KabuNativePushClient
from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env

# Official kabu PUSH + board register shared limit (symbols).
KABU_PUSH_REGISTER_LIMIT = 50

REGISTER_LIMIT_ERROR_CODES = frozenset({4001018, 4002006})


def parse_kabu_error_code(exc: BaseException) -> Optional[int]:
    """Extract kabusapi JSON Code from KabuNativeApiError message."""
    msg = str(exc)
    for pattern in (
        r'"Code"\s*:\s*(\d+)',
        r"'Code'\s*:\s*(\d+)",
        r"Code[=:]\s*(\d+)",
    ):
        m = re.search(pattern, msg)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def is_register_limit_error(exc: BaseException) -> bool:
    code = parse_kabu_error_code(exc)
    if code in REGISTER_LIMIT_ERROR_CODES:
        return True
    lowered = str(exc).lower()
    return "4002006" in lowered or "レジスト数" in str(exc) or "register_limit" in lowered


def format_register_failure_message(exc: BaseException, *, symbol_count: int) -> str:
    code = parse_kabu_error_code(exc)
    if is_register_limit_error(exc):
        return (
            f"kabu register limit (Code {code or 4002006}): requested {symbol_count} symbols "
            f"(max {KABU_PUSH_REGISTER_LIMIT}). Cleared via unregister/all and retried once. "
            "If this persists, restart kabuステーション or reduce universe size."
        )
    return f"kabu register failed for {symbol_count} symbols: {exc}"


def unregister_all_safe(push: KabuNativePushClient) -> dict[str, Any]:
    try:
        resp = push.unregister_all()
        return {"ok": True, "response": resp}
    except Exception as e:
        return {"ok": False, "error": str(e), "error_type": type(e).__name__}


def register_symbols_cleared(
    push: KabuNativePushClient,
    symbols_spec: Sequence[tuple[str, int]],
    *,
    clear_first: bool = True,
    retry_on_limit: bool = True,
) -> dict[str, Any]:
    """
    unregister/all (optional) then PUT /register.
    On 4002006 / register limit: clear again and retry once.
    """
    n = len(symbols_spec)
    if n > KABU_PUSH_REGISTER_LIMIT:
        raise KabuNativeApiError(
            f"register symbol count {n} exceeds kabu limit {KABU_PUSH_REGISTER_LIMIT}"
        )

    steps: list[dict[str, Any]] = []

    def _clear(step_name: str) -> None:
        if not clear_first:
            return
        unr = unregister_all_safe(push)
        steps.append({"step": step_name, **unr})

    def _register(step_name: str) -> dict[str, Any]:
        resp = push.register(symbols_spec)
        steps.append({"step": step_name, "ok": True, "symbol_count": n, "response": resp})
        return resp

    _clear("unregister_all_before_register")
    try:
        out = _register("register")
        return {"ok": True, "symbol_count": n, "steps": steps, "response": out}
    except KabuNativeApiError as first_err:
        steps.append(
            {
                "step": "register",
                "ok": False,
                "symbol_count": n,
                "error": str(first_err),
                "kabu_code": parse_kabu_error_code(first_err),
            }
        )
        if not retry_on_limit or not is_register_limit_error(first_err):
            raise
        _clear("unregister_all_retry_after_limit")
        try:
            out = _register("register_retry")
            return {
                "ok": True,
                "symbol_count": n,
                "steps": steps,
                "response": out,
                "recovered_from_register_limit": True,
            }
        except KabuNativeApiError as second_err:
            steps.append(
                {
                    "step": "register_retry",
                    "ok": False,
                    "error": str(second_err),
                    "kabu_code": parse_kabu_error_code(second_err),
                }
            )
            raise KabuNativeApiError(
                f"register failed after unregister retry ({n} symbols): {second_err}"
            ) from second_err


def push_client_from_repo(repo_root) -> tuple[KabuNativePushClient, KabuNativeRestClient, str]:
    from pathlib import Path

    load_kabu_env(repo_root=Path(repo_root))
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    return KabuNativePushClient(rest, token), rest, token


def clear_register_before_session(repo_root) -> dict[str, Any]:
    """Preflight: clear stale kabu registrations (no symbol list required)."""
    from pathlib import Path

    try:
        push, _, _ = push_client_from_repo(Path(repo_root))
        unr = unregister_all_safe(push)
        return {
            "ok": bool(unr.get("ok")),
            "cleared": bool(unr.get("ok")),
            "unregister_all": unr,
            "register_limit": KABU_PUSH_REGISTER_LIMIT,
            "list_registered_symbols_api": False,
            "note": "kabu API has no GET registered-symbols; use unregister/all before each session.",
        }
    except Exception as e:
        return {
            "ok": False,
            "cleared": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "register_limit": KABU_PUSH_REGISTER_LIMIT,
        }


def assess_register_capacity(*, universe_symbol_count: int) -> dict[str, Any]:
    """Static capacity check (no kabu connection)."""
    n = int(universe_symbol_count)
    return {
        "universe_symbol_count": n,
        "kabu_register_limit": KABU_PUSH_REGISTER_LIMIT,
        "within_limit": n <= KABU_PUSH_REGISTER_LIMIT,
        "headroom": max(0, KABU_PUSH_REGISTER_LIMIT - n),
        "unregister_all_available": True,
        "per_symbol_unregister_available": False,
        "would_exceed_if_stale_registered": n > 0,
        "risk_note": (
            "If prior session left registrations without unregister/all, "
            "register of 50 new symbols can hit Code 4002006 until cleared."
        ),
    }
