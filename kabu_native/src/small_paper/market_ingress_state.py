"""Market Ingress V2 state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

STARTING = "STARTING"
CONNECTING = "CONNECTING"
REGISTERING = "REGISTERING"
RUNNING = "RUNNING"
STALE_DETECTED = "STALE_DETECTED"
ENTRY_BLOCKED = "ENTRY_BLOCKED"
CLOSING_STALE_SOCKET = "CLOSING_STALE_SOCKET"
RECONNECTING = "RECONNECTING"
REREGISTERING = "REREGISTERING"
WAITING_FIRST_PUSH = "WAITING_FIRST_PUSH"
RECOVERED = "RECOVERED"
RECOVERY_FAILED = "RECOVERY_FAILED"
STORAGE_BLOCKED = "STORAGE_BLOCKED"
STOPPING = "STOPPING"
STOPPED = "STOPPED"

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STARTING: frozenset({CONNECTING, STOPPING, STOPPED}),
    CONNECTING: frozenset({REGISTERING, RECONNECTING, RECOVERY_FAILED, STOPPING, STOPPED}),
    REGISTERING: frozenset({WAITING_FIRST_PUSH, RUNNING, REREGISTERING, RECOVERY_FAILED, STOPPING}),
    WAITING_FIRST_PUSH: frozenset(
        {RUNNING, RECOVERED, STALE_DETECTED, RECOVERY_FAILED, REREGISTERING, STOPPING}
    ),
    RUNNING: frozenset(
        {
            STALE_DETECTED,
            ENTRY_BLOCKED,
            STORAGE_BLOCKED,
            REREGISTERING,
            STOPPING,
            STOPPED,
        }
    ),
    STALE_DETECTED: frozenset({ENTRY_BLOCKED, CLOSING_STALE_SOCKET, STOPPING}),
    ENTRY_BLOCKED: frozenset(
        {
            CLOSING_STALE_SOCKET,
            RECONNECTING,
            STORAGE_BLOCKED,
            RUNNING,
            RECOVERED,
            STOPPING,
        }
    ),
    CLOSING_STALE_SOCKET: frozenset({RECONNECTING, RECOVERY_FAILED, STOPPING}),
    RECONNECTING: frozenset({REREGISTERING, RECOVERY_FAILED, STOPPING}),
    REREGISTERING: frozenset({WAITING_FIRST_PUSH, RECOVERY_FAILED, STOPPING}),
    RECOVERED: frozenset({RUNNING, STALE_DETECTED, STOPPING}),
    RECOVERY_FAILED: frozenset({RECONNECTING, ENTRY_BLOCKED, STOPPING, STOPPED}),
    STORAGE_BLOCKED: frozenset({RUNNING, ENTRY_BLOCKED, STOPPING, STOPPED}),
    STOPPING: frozenset({STOPPED}),
    STOPPED: frozenset(),
}


@dataclass
class IngressStateMachine:
    state: str = STARTING
    connection_generation: int = 0
    registration_generation: int = 0
    recovery_attempt: int = 0
    recovery_count: int = 0
    recovery_success_count: int = 0
    entry_blocked: bool = False
    entry_block_reason: str = ""
    last_error: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, new_state: str, *, reason: str = "", force: bool = False) -> bool:
        allowed = ALLOWED_TRANSITIONS.get(self.state, frozenset())
        recovery_force = {
            STALE_DETECTED,
            CLOSING_STALE_SOCKET,
            RECONNECTING,
            REREGISTERING,
            WAITING_FIRST_PUSH,
            RECOVERED,
            RECOVERY_FAILED,
            ENTRY_BLOCKED,
            STORAGE_BLOCKED,
            STOPPING,
            STOPPED,
            RUNNING,
        }
        if (
            not force
            and new_state != self.state
            and new_state not in allowed
            and self.state != STOPPED
            and new_state not in recovery_force
        ):
            self.last_error = f"illegal_transition:{self.state}->{new_state}"
            return False
        prev = self.state
        self.state = new_state
        self.history.append({"from": prev, "to": new_state, "reason": reason})
        if len(self.history) > 200:
            self.history = self.history[-200:]
        return True

    def block_entry(self, reason: str) -> None:
        self.entry_blocked = True
        self.entry_block_reason = reason
        if self.state in (RUNNING, RECOVERED, STALE_DETECTED, STORAGE_BLOCKED):
            self.transition(ENTRY_BLOCKED, reason=reason)

    def unblock_entry(self) -> None:
        self.entry_blocked = False
        self.entry_block_reason = ""
        if self.state in (ENTRY_BLOCKED, RECOVERED):
            self.transition(RUNNING, reason="entry_unblocked")

    def bump_connection(self) -> int:
        self.connection_generation += 1
        return self.connection_generation

    def bump_registration(self) -> int:
        self.registration_generation += 1
        return self.registration_generation

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "connection_generation": self.connection_generation,
            "registration_generation": self.registration_generation,
            "recovery_attempt": self.recovery_attempt,
            "recovery_count": self.recovery_count,
            "recovery_success_count": self.recovery_success_count,
            "entry_blocked": self.entry_blocked,
            "entry_block_reason": self.entry_block_reason,
            "last_error": self.last_error,
        }
