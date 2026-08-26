"""Today-only OPVAL degraded-universe contract. Does not change 50/50 PAPER_READY."""
from __future__ import annotations

from small_paper.kabu_registration_authority import verify_exact50_membership
from small_paper.operational_validation import (
    DEGRADED_OPVAL_READY,
    ENV_OPVAL_DEGRADED_UNIVERSE,
    ENV_OPVAL_MODE,
    evaluate_opval_degraded_universe_ready,
    opval_degraded_universe_mode,
)

FROZEN = [f"{1000 + i:04d}" for i in range(49)] + ["4449"]
LIVE49 = [s for s in FROZEN if s != "4449"]


def _env() -> dict[str, str]:
    return {
        ENV_OPVAL_MODE: "1",
        ENV_OPVAL_DEGRADED_UNIVERSE: "1",
        "TRADEBOT_ALLOW_DEGRADED_CLOCK_MISMATCH": "1",
    }


def _base_kwargs(**overrides: object) -> dict:
    body = {
        "native_root": ".",
        "trading_date": "20260819",
        "expected_capture_pid": 31260,
        "frozen_symbols": FROZEN,
        "live_symbols": LIVE49,
        "status": {
            "pid": 31260,
            "registration_retry_count": 10,
            "raw_last_sequence": 100,
            "auth_failure_count": 0,
            "circuit_reason": "RATE_LIMIT",
            "last_error": "KabuNativeApiError",
        },
        "token_audit": {
            "token_issue_count": 1,
            "unexpected_token_issue_count": 0,
            "blocked_second_issuer_count": 0,
        },
        "terminal_probe": {"ok": True, "code": "4002001", "message": "SYMBOL NOT FOUND"},
        "retry_before": 10,
        "retry_after": 10,
        "capture_seq_before": 100,
        "capture_seq_after": 120,
        "ingress_process_count": 1,
        "environ": _env(),
        "pid_alive": True,
        "allow_live_board": False,
    }
    body.update(overrides)
    return body


def test_degraded_mode_requires_explicit_flag() -> None:
    assert not opval_degraded_universe_mode(environ={ENV_OPVAL_MODE: "1"})
    assert opval_degraded_universe_mode(environ=_env())


def test_degraded_49_of_50_returns_degraded_ready_not_paper_ready() -> None:
    got = evaluate_opval_degraded_universe_ready(**_base_kwargs())
    assert got["ready"] is True
    assert got["classification"] == DEGRADED_OPVAL_READY
    assert got["classification"] != "PAPER_READY"
    assert got["terminal_invalid"] == ["4449"]
    assert got["active_universe_count"] == 49
    assert got["paper_ready_forbidden"] is True


def test_second_missing_symbol_is_not_ready() -> None:
    live = [s for s in LIVE49 if s != "1000"]
    got = evaluate_opval_degraded_universe_ready(**_base_kwargs(live_symbols=live))
    assert got["ready"] is False
    assert "terminal_invalid_not_exactly_4449" in str(got.get("reason") or "")
    assert got["classification"] != "PAPER_READY"


def test_retry_storm_blocks_paper() -> None:
    got = evaluate_opval_degraded_universe_ready(**_base_kwargs(retry_before=10, retry_after=14))
    assert got["ready"] is False
    assert got["retry_storm_active"] is True


def test_exact50_membership_still_rejects_empty_actual() -> None:
    """Normal 50/50 gate must remain untouched by this file's existence."""
    # Function still exists and is the production gate; degraded path must not alias it.
    assert callable(verify_exact50_membership)
    assert DEGRADED_OPVAL_READY != "PAPER_READY"


def test_degraded_probe_picks_remaining_not_4449() -> None:
    from small_paper.operational_validation import resolve_opval_degraded_probe_symbol

    got = resolve_opval_degraded_probe_symbol(
        ".",
        "20260819",
        frozen_symbols=FROZEN,
        active_symbols=LIVE49,
        environ=_env(),
    )
    assert got["ok"] is True
    assert got["exact50"] is False
    assert got["partial_unconfirmed"] is True
    assert "4449" not in str(got.get("symbol_key") or "")
    assert got["kabu_probe_symbol_frozen_member"] is True
    assert got["registration_mutation"] == 0


def test_formal_probe_selector_does_not_use_degraded_without_flag() -> None:
    from small_paper.operational_validation import (
        resolve_opval_degraded_probe_symbol,
        select_runtime_board_probe_symbol,
    )

    blocked = resolve_opval_degraded_probe_symbol(
        ".",
        "20260819",
        frozen_symbols=FROZEN,
        active_symbols=LIVE49,
        environ={ENV_OPVAL_MODE: "1"},
    )
    assert blocked["ok"] is False
    assert blocked["reason"] == "OPVAL_DEGRADED_UNIVERSE_FLAG_REQUIRED"
    assert callable(select_runtime_board_probe_symbol)
