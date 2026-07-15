"""Phase687W30: classify Paper session validity (strategy vs operational)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

VALID_SESSION = "VALID_SESSION"
INVALID_REGISTER_FAILED = "INVALID_REGISTER_FAILED"
INVALID_NO_PUSH = "INVALID_NO_PUSH"
INVALID_NO_GATE = "INVALID_NO_GATE"
INVALID_EARLY_ABORT = "INVALID_EARLY_ABORT"
INVALID_ARTIFACT_INCOMPLETE = "INVALID_ARTIFACT_INCOMPLETE"

_NORMAL_STOP = frozenset(
    {
        "session_end",
        "duration_elapsed",
        "max_polls",
        "operator_stop",
        "keyboard_interrupt",
        "",
    }
)


def classify_session_validity(
    summary: Mapping[str, Any] | None = None,
    *,
    stop_reason: Optional[str] = None,
    push_messages: Optional[int] = None,
    gate_evaluations: Optional[int] = None,
    heartbeat_count: Optional[int] = None,
    session_seal_status: Optional[str] = None,
    runtime_sec: Optional[float] = None,
) -> dict[str, Any]:
    """Return validity class for Discord / PnL exclusion / Recovery."""
    s = summary or {}
    reason = str(stop_reason if stop_reason is not None else s.get("stop_reason") or "")
    push_n = int(push_messages if push_messages is not None else s.get("push_messages") or 0)
    gate_n = int(gate_evaluations if gate_evaluations is not None else s.get("gate_evaluations") or 0)
    hb_n = int(heartbeat_count if heartbeat_count is not None else s.get("heartbeat_count") or 0)
    seal = str(
        session_seal_status
        if session_seal_status is not None
        else s.get("session_seal_status") or s.get("seal_status") or ""
    )
    rt = float(runtime_sec if runtime_sec is not None else s.get("runtime_sec") or 0.0)

    if reason == "register_failed":
        klass = INVALID_REGISTER_FAILED
    elif seal == "INCOMPLETE" and push_n == 0 and gate_n == 0 and rt < 5.0:
        klass = INVALID_EARLY_ABORT
    elif seal == "INCOMPLETE":
        klass = INVALID_ARTIFACT_INCOMPLETE
    elif push_n <= 0:
        klass = INVALID_NO_PUSH
    elif gate_n <= 0:
        klass = INVALID_NO_GATE
    elif reason and reason not in _NORMAL_STOP and reason not in ("session_end",):
        # Unknown abort with some data still marked early abort when short
        klass = INVALID_EARLY_ABORT if rt < 30.0 else INVALID_NO_GATE
    else:
        klass = VALID_SESSION

    include_in_strategy = klass == VALID_SESSION
    return {
        "session_validity": klass,
        "include_in_strategy_metrics": include_in_strategy,
        "include_in_cumulative_pnl": include_in_strategy,
        "include_in_forward_day_count": include_in_strategy,
        "include_in_live_readiness_streak": include_in_strategy,
        "stop_reason": reason,
        "push_messages": push_n,
        "gate_evaluations": gate_n,
        "heartbeat_count": hb_n,
        "session_seal_status": seal or None,
        "discord_banner": (
            None
            if include_in_strategy
            else {
                "title": "【INVALID PAPER SESSION】",
                "cause": reason or klass,
                "push": push_n,
                "gate_evaluations": gate_n,
                "note": "損益は戦略成績に含めない",
            }
        ),
    }


def format_invalid_session_discord_lines(validity: Mapping[str, Any]) -> list[str]:
    banner = validity.get("discord_banner")
    if not isinstance(banner, Mapping):
        return []
    return [
        str(banner.get("title") or "【INVALID PAPER SESSION】"),
        f"原因: {banner.get('cause')}",
        f"PUSH: {banner.get('push')}",
        f"評価: {banner.get('gate_evaluations')}",
        str(banner.get("note") or "損益は戦略成績に含めない"),
    ]


def format_paper_not_running_discord_lines(
    *,
    stop_point: str = "register",
    push: int = 0,
    gate: int = 0,
    capture_status: str = "待機中",
) -> list[str]:
    return [
        "【PAPER NOT RUNNING】",
        f"停止点: {stop_point}",
        f"PUSH: {push}",
        f"ENTRY評価: {gate}",
        "Paper損益: 無効",
        f"Capture: {capture_status}",
        "operator action: Kabu登録状態を確認",
    ]


def format_register_recovered_discord_lines(
    *,
    registered: int = 50,
    expected: int = 50,
    push_receiving: bool = False,
) -> list[str]:
    """Only call when register readback OK; push_receiving gates RECOVERED wording."""
    if not push_receiving:
        return [
            "【REGISTER RETRY OK — WAITING PUSH】",
            f"登録: {registered}/{expected}",
            "PUSH: not yet receiving",
            "Paper評価: waiting",
        ]
    return [
        "【REGISTER RECOVERED】",
        f"登録: {registered}/{expected}",
        "PUSH: receiving",
        "Paper評価: running",
    ]
