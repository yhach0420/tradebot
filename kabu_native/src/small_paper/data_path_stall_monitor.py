"""Paper data-path stall monitor (ops only).

PAPER_DATA_PATH_STALLED fires only when all hold:
  1) market hours
  2) startup grace elapsed
  3) heartbeat age abnormal
  4) observation-window PUSH delta == 0
  5) observation-window gate_evaluations delta == 0

Heartbeat generation period is NOT changed here.
ENTRY/EXIT / CAP / OR / Shadow are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DataPathMonitorState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PUSH_ONLY = "PUSH_ONLY"
    STALLED = "STALLED"
    PROCESS_DEAD = "PROCESS_DEAD"
    OFF_HOURS = "OFF_HOURS"


@dataclass
class StallMonitorConfig:
    heartbeat_sec: float = 300.0
    startup_grace_sec: float = 60.0
    observe_window_sec: float = 60.0
    # After at least one heartbeat: age > this ⇒ abnormal (default 2 periods).
    heartbeat_abnormal_after_first_sec: Optional[float] = None
    # Before first heartbeat: overdue if elapsed >= this (default 1 period).
    heartbeat_overdue_before_first_sec: Optional[float] = None

    def abnormal_after_first(self) -> float:
        if self.heartbeat_abnormal_after_first_sec is not None:
            return float(self.heartbeat_abnormal_after_first_sec)
        return float(self.heartbeat_sec) * 2.0

    def overdue_before_first(self) -> float:
        if self.heartbeat_overdue_before_first_sec is not None:
            return float(self.heartbeat_overdue_before_first_sec)
        return float(self.heartbeat_sec)


@dataclass
class StallMonitorSnapshot:
    state: DataPathMonitorState
    notify_stalled: bool = False
    notify_recovered: bool = False
    notify_process_dead: bool = False
    push_only_warning: bool = False
    heartbeat_age_sec: float = 0.0
    push_delta: int = 0
    gate_delta: int = 0
    reason: str = ""
    process_alive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "notify_stalled": self.notify_stalled,
            "notify_recovered": self.notify_recovered,
            "notify_process_dead": self.notify_process_dead,
            "push_only_warning": self.push_only_warning,
            "heartbeat_age_sec": round(self.heartbeat_age_sec, 3),
            "push_delta": self.push_delta,
            "gate_delta": self.gate_delta,
            "reason": self.reason,
            "process_alive": self.process_alive,
        }


@dataclass
class DataPathStallMonitor:
    config: StallMonitorConfig = field(default_factory=StallMonitorConfig)
    _start_mono: Optional[float] = None
    _last_hb_mono: Optional[float] = None
    _hb_count: int = 0
    _window_push: int = 0
    _window_gate: int = 0
    _window_mono: Optional[float] = None
    _push_delta: int = 0
    _gate_delta: int = 0
    _state: DataPathMonitorState = DataPathMonitorState.STARTING
    _stall_notified: bool = False
    _process_dead_notified: bool = False
    _was_stalled: bool = False
    _push_only_since_mono: Optional[float] = None

    def reset(self, *, start_mono: float) -> None:
        self._start_mono = float(start_mono)
        self._last_hb_mono = None
        self._hb_count = 0
        self._window_push = 0
        self._window_gate = 0
        self._window_mono = float(start_mono)
        self._push_delta = 0
        self._gate_delta = 0
        self._state = DataPathMonitorState.STARTING
        self._stall_notified = False
        self._process_dead_notified = False
        self._was_stalled = False
        self._push_only_since_mono = None

    def note_heartbeat(self, *, mono: float, heartbeat_count: int) -> None:
        self._last_hb_mono = float(mono)
        self._hb_count = int(heartbeat_count)

    def _update_deltas(self, *, mono: float, push: int, gate: int) -> None:
        if self._window_mono is None:
            self._window_mono = float(mono)
            self._window_push = int(push)
            self._window_gate = int(gate)
            self._push_delta = 0
            self._gate_delta = 0
            return
        elapsed_win = float(mono) - float(self._window_mono)
        if elapsed_win >= float(self.config.observe_window_sec):
            self._push_delta = max(0, int(push) - int(self._window_push))
            self._gate_delta = max(0, int(gate) - int(self._window_gate))
            self._window_push = int(push)
            self._window_gate = int(gate)
            self._window_mono = float(mono)
        else:
            # Intra-window: use growth since last window baseline (not yet committed).
            self._push_delta = max(0, int(push) - int(self._window_push))
            self._gate_delta = max(0, int(gate) - int(self._window_gate))

    def _heartbeat_age(self, mono: float) -> float:
        start = float(self._start_mono if self._start_mono is not None else mono)
        if self._last_hb_mono is not None:
            return max(0.0, float(mono) - float(self._last_hb_mono))
        return max(0.0, float(mono) - start)

    def _heartbeat_abnormal(self, *, mono: float, age: float) -> bool:
        if self._hb_count > 0 or self._last_hb_mono is not None:
            return age >= self.config.abnormal_after_first()
        # No heartbeat yet: abnormal only once first HB is overdue.
        return age >= self.config.overdue_before_first()

    def evaluate(
        self,
        *,
        mono: float,
        push_messages: int,
        gate_evaluations: int,
        heartbeat_count: int,
        in_market_hours: bool,
        in_entry_hours: bool = True,
        process_alive: bool = True,
        force_window_roll: bool = False,
    ) -> StallMonitorSnapshot:
        if self._start_mono is None:
            self.reset(start_mono=mono)

        if heartbeat_count > self._hb_count:
            self.note_heartbeat(mono=mono, heartbeat_count=heartbeat_count)

        if force_window_roll and self._window_mono is not None:
            # Test helper: pretend observe window elapsed.
            self._window_mono = float(mono) - float(self.config.observe_window_sec)

        self._update_deltas(mono=mono, push=push_messages, gate=gate_evaluations)
        age = self._heartbeat_age(mono)
        push_d = int(self._push_delta)
        gate_d = int(self._gate_delta)
        start = float(self._start_mono) if self._start_mono is not None else float(mono)
        elapsed = float(mono) - start

        notify_stalled = False
        notify_recovered = False
        notify_process_dead = False
        push_only_warning = False
        reason = ""

        if not process_alive:
            self._state = DataPathMonitorState.PROCESS_DEAD
            if not self._process_dead_notified:
                notify_process_dead = True
                self._process_dead_notified = True
                self._stall_notified = True  # suppress duplicate stall spam
            reason = "paper_process_dead"
            return StallMonitorSnapshot(
                state=self._state,
                notify_stalled=False,
                notify_recovered=False,
                notify_process_dead=notify_process_dead,
                push_only_warning=False,
                heartbeat_age_sec=age,
                push_delta=push_d,
                gate_delta=gate_d,
                reason=reason,
                process_alive=False,
            )

        if not in_market_hours:
            # Recovery from stall only if we already stalled and data resumes off-hours — ignore.
            self._state = DataPathMonitorState.OFF_HOURS
            return StallMonitorSnapshot(
                state=self._state,
                heartbeat_age_sec=age,
                push_delta=push_d,
                gate_delta=gate_d,
                reason="off_hours",
                process_alive=True,
            )

        if elapsed < float(self.config.startup_grace_sec):
            growing = push_d > 0 or gate_d > 0 or push_messages > 0 or gate_evaluations > 0
            self._state = DataPathMonitorState.STARTING if growing or self._hb_count == 0 else DataPathMonitorState.STARTING
            return StallMonitorSnapshot(
                state=DataPathMonitorState.STARTING,
                heartbeat_age_sec=age,
                push_delta=push_d,
                gate_delta=gate_d,
                reason="startup_grace",
                process_alive=True,
            )

        hb_abn = self._heartbeat_abnormal(mono=mono, age=age)
        growing = push_d > 0 or gate_d > 0

        # Healthy paths even with overdue/missing heartbeat.
        if growing:
            if push_d > 0 and gate_d <= 0:
                self._state = DataPathMonitorState.PUSH_ONLY
                if self._push_only_since_mono is None:
                    self._push_only_since_mono = float(mono)
                sustained = (float(mono) - float(self._push_only_since_mono)) >= float(
                    self.config.observe_window_sec
                )
                push_only_warning = bool(in_entry_hours and sustained)
                reason = "push_growing_gate_flat"
            else:
                self._state = (
                    DataPathMonitorState.STARTING
                    if self._hb_count <= 0
                    else DataPathMonitorState.RUNNING
                )
                self._push_only_since_mono = None
                reason = "push_or_gate_growing"

            if self._was_stalled and (push_d > 0 or gate_d > 0):
                notify_recovered = True
                self._was_stalled = False
                self._stall_notified = False
            return StallMonitorSnapshot(
                state=self._state,
                notify_recovered=notify_recovered,
                push_only_warning=push_only_warning,
                heartbeat_age_sec=age,
                push_delta=push_d,
                gate_delta=gate_d,
                reason=reason,
                process_alive=True,
            )

        self._push_only_since_mono = None

        # True stall: HB abnormal AND both deltas zero.
        if hb_abn and push_d == 0 and gate_d == 0:
            self._state = DataPathMonitorState.STALLED
            self._was_stalled = True
            if not self._stall_notified:
                notify_stalled = True
                self._stall_notified = True
            reason = (
                f"heartbeat_age={age:.0f}s abnormal; PUSH delta=0; gate delta=0"
            )
            return StallMonitorSnapshot(
                state=self._state,
                notify_stalled=notify_stalled,
                heartbeat_age_sec=age,
                push_delta=0,
                gate_delta=0,
                reason=reason,
                process_alive=True,
            )

        # Not growing, but HB still within tolerance → RUNNING/STARTING.
        if self._hb_count <= 0:
            self._state = DataPathMonitorState.STARTING
            reason = "awaiting_first_heartbeat"
        else:
            self._state = DataPathMonitorState.RUNNING
            reason = "heartbeat_fresh_or_within_tolerance"
        return StallMonitorSnapshot(
            state=self._state,
            heartbeat_age_sec=age,
            push_delta=push_d,
            gate_delta=gate_d,
            reason=reason,
            process_alive=True,
        )


def format_stall_discord_message(
    *,
    heartbeat_age_sec: float,
    push_delta: int = 0,
    gate_delta: int = 0,
    process_alive: bool = True,
    capture_status: str = "不明",
) -> str:
    proc = "alive" if process_alive else "dead"
    return "\n".join(
        [
            "【PAPER DATA PATH STALLED】",
            f"Heartbeat更新なし: {int(round(heartbeat_age_sec))}秒",
            f"PUSH増分: {int(push_delta)}",
            f"ENTRY評価増分: {int(gate_delta)}",
            f"Paperプロセス: {proc}",
            f"Capture: {capture_status}",
        ]
    )


def format_stall_recovered_discord_message(
    *,
    push_delta: int,
    gate_delta: int,
) -> str:
    return "\n".join(
        [
            "【PAPER DATA PATH RECOVERED】",
            f"PUSH増分: {int(push_delta)}",
            f"ENTRY評価増分: {int(gate_delta)}",
        ]
    )


def format_process_dead_discord_message(*, capture_status: str = "不明") -> str:
    return "\n".join(
        [
            "【PAPER DATA PATH STALLED】",
            "Heartbeat更新なし: n/a",
            "PUSH増分: 0",
            "ENTRY評価増分: 0",
            "Paperプロセス: dead",
            f"Capture: {capture_status}",
        ]
    )
