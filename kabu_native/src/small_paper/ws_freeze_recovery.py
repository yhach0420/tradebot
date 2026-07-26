"""Phase675 — WebSocket recv freeze recovery helpers (Paper ops only).

Does NOT change ENTRY/EXIT/Shadow/CAP/Universe trading logic.
Guarantees lifecycle progress (force_close / stop / summary path) even when
PUSH recv is silent or reconnect hangs.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

WS_LIFECYCLE_TICK_KEY = "__ws_lifecycle_tick__"
WS_RECONNECT_EXHAUSTED = "WS_RECONNECT_EXHAUSTED"
PUSH_RECONNECT_SILENCE_TIMEOUT = "push_reconnect_silence_timeout"
EVENT_LOOP_STALL = "EVENT_LOOP_STALL"
# Phase722: communication faults stay DEGRADED until scheduled session close.
# Do not treat these as official EXIT reasons — normalize at finalize.
DEGRADED_WS_STATE = "DEGRADED_RECONNECT_WAIT"
RECOVERY_SESSION_CLOSE = "recovery_session_close"
COMM_FAULT_STOP_REASONS = frozenset(
    {
        PUSH_RECONNECT_SILENCE_TIMEOUT,
        WS_RECONNECT_EXHAUSTED,
        "push_unexpected",
    }
)


def normalize_session_close_reason(
    stop_reason: str,
    *,
    am_pm_force_close_reason: str = "",
    force_close_due: bool = False,
) -> str:
    """Map communication-fault stops to official session-close EXIT reasons."""
    r = str(stop_reason or "").strip()
    if force_close_due and am_pm_force_close_reason:
        return str(am_pm_force_close_reason)
    if r in ("morning_session_close", "afternoon_session_close", "session_end", RECOVERY_SESSION_CLOSE):
        return r
    if r in COMM_FAULT_STOP_REASONS or r.startswith("push_"):
        # Only promote to morning/afternoon close when schedule force-close is due.
        if force_close_due and am_pm_force_close_reason:
            return str(am_pm_force_close_reason)
        return RECOVERY_SESSION_CLOSE
    return r or "session_end"

# Recv / connect
DEFAULT_RECV_POLL_SEC = 5.0
DEFAULT_WS_OPEN_TIMEOUT_SEC = 20.0
DEFAULT_WS_CLOSE_TIMEOUT_SEC = 10.0

# Reconnect budget
DEFAULT_RECONNECT_ATTEMPT_TIMEOUT_SEC = 30.0
DEFAULT_RECONNECT_OVERALL_DEADLINE_SEC = 120.0
DEFAULT_RECONNECT_MAX_ATTEMPTS = 5
DEFAULT_RECONNECT_BACKOFF_MAX_SEC = 30.0
DEFAULT_POST_RECONNECT_SILENCE_SEC = 90.0

# Lifecycle watcher
DEFAULT_LIFECYCLE_INTERVAL_SEC = 2.0

# Supervisor
DEFAULT_HB_STALL_SEC = 600.0
DEFAULT_PUSH_STALL_SEC = 600.0
DEFAULT_SUPERVISOR_MAX_RESTARTS_PER_SESSION = 1
DEFAULT_SUPERVISOR_COOLDOWN_SEC = 300.0


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def make_recv_timeout_tick(consecutive_timeouts: int) -> dict[str, Any]:
    return {
        WS_LIFECYCLE_TICK_KEY: True,
        "tick_kind": "recv_timeout",
        "consecutive_timeouts": int(consecutive_timeouts),
        "emitted_at": now_iso(),
    }


def is_lifecycle_tick(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get(WS_LIFECYCLE_TICK_KEY))


@dataclass
class ReconnectBudget:
    """Tracks reconnect attempts with hard deadlines (no silent freeze)."""

    attempt_timeout_sec: float = DEFAULT_RECONNECT_ATTEMPT_TIMEOUT_SEC
    overall_deadline_sec: float = DEFAULT_RECONNECT_OVERALL_DEADLINE_SEC
    max_attempts: int = DEFAULT_RECONNECT_MAX_ATTEMPTS
    backoff_max_sec: float = DEFAULT_RECONNECT_BACKOFF_MAX_SEC
    post_reconnect_silence_sec: float = DEFAULT_POST_RECONNECT_SILENCE_SEC

    window_started_mono: Optional[float] = None
    attempts_in_window: int = 0
    last_reconnect_mono: Optional[float] = None
    last_success_mono: Optional[float] = None
    exhausted: bool = False
    exhausted_reason: str = ""

    def begin_window_if_needed(self, mono: Optional[float] = None) -> None:
        if self.window_started_mono is None:
            self.window_started_mono = float(mono if mono is not None else time.monotonic())

    def backoff_sec(self, attempt: int, poll_interval_sec: float) -> float:
        base = min(self.backoff_max_sec, max(1.0, float(poll_interval_sec) * 2))
        return min(self.backoff_max_sec, base * max(1, attempt))

    def can_attempt(self, mono: Optional[float] = None) -> tuple[bool, str]:
        now = float(mono if mono is not None else time.monotonic())
        self.begin_window_if_needed(now)
        if self.exhausted:
            return False, self.exhausted_reason or WS_RECONNECT_EXHAUSTED
        if self.attempts_in_window >= self.max_attempts:
            self.exhausted = True
            self.exhausted_reason = WS_RECONNECT_EXHAUSTED
            return False, WS_RECONNECT_EXHAUSTED
        assert self.window_started_mono is not None
        if (now - self.window_started_mono) >= self.overall_deadline_sec:
            self.exhausted = True
            self.exhausted_reason = WS_RECONNECT_EXHAUSTED
            return False, WS_RECONNECT_EXHAUSTED
        return True, ""

    def note_attempt_start(self, mono: Optional[float] = None) -> int:
        now = float(mono if mono is not None else time.monotonic())
        self.begin_window_if_needed(now)
        self.attempts_in_window += 1
        self.last_reconnect_mono = now
        return self.attempts_in_window

    def note_success(self, mono: Optional[float] = None) -> None:
        self.last_success_mono = float(mono if mono is not None else time.monotonic())
        # keep window for silence watchdog; reset attempt storm only after push resumes
        self.exhausted = False
        self.exhausted_reason = ""

    def note_push_resumed(self) -> None:
        """Reset reconnect storm window after live PUSH resumes."""
        self.window_started_mono = None
        self.attempts_in_window = 0
        self.exhausted = False
        self.exhausted_reason = ""

    def silence_exceeded(
        self,
        *,
        last_push_mono: Optional[float],
        reconnect_succeeded_mono: Optional[float],
        mono: Optional[float] = None,
    ) -> bool:
        if reconnect_succeeded_mono is None:
            return False
        now = float(mono if mono is not None else time.monotonic())
        # If push arrived after reconnect, silence is cleared
        if last_push_mono is not None and last_push_mono >= reconnect_succeeded_mono:
            return False
        return (now - reconnect_succeeded_mono) >= self.post_reconnect_silence_sec


def effective_recv_poll_sec(poll_interval_sec: float | None) -> float:
    if poll_interval_sec is None or float(poll_interval_sec) <= 0:
        return DEFAULT_RECV_POLL_SEC
    return max(0.5, min(float(poll_interval_sec), DEFAULT_RECV_POLL_SEC))


def enrich_heartbeat_fields(
    *,
    runtime_pid: int,
    event_loop_alive: bool,
    last_push_at: Optional[str],
    last_push_mono: Optional[float],
    websocket_state: str,
    reconnect_attempt: int,
    session_state: str,
    active_positions: int,
    close_due: bool,
    consecutive_recv_timeouts: int = 0,
    recv_timeout_count: int = 0,
    mono: Optional[float] = None,
) -> dict[str, Any]:
    now = float(mono if mono is not None else time.monotonic())
    age = None
    if last_push_mono is not None:
        age = round(now - float(last_push_mono), 3)
    return {
        "emitted_at": now_iso(),
        "runtime_pid": int(runtime_pid),
        "event_loop_alive": bool(event_loop_alive),
        "last_push_at": last_push_at,
        "last_push_age_sec": age,
        "websocket_state": str(websocket_state or ""),
        "reconnect_attempt": int(reconnect_attempt),
        "session_state": str(session_state or ""),
        "active_positions": int(active_positions),
        "close_due": bool(close_due),
        "consecutive_recv_timeouts": int(consecutive_recv_timeouts),
        "recv_timeout_count": int(recv_timeout_count),
    }


def find_orphan_accepted(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return accepted rows with no matching observer_exit (by position_id or symbol+entry_time)."""
    accepted: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    for e in events:
        et = str(e.get("event_type") or e.get("event") or "")
        if et == "accepted":
            accepted.append(dict(e))
        elif et == "observer_exit":
            exits.append(dict(e))
    exit_pids = {str(x.get("position_id") or "") for x in exits if x.get("position_id")}
    exit_keys = {(x.get("symbol"), x.get("entry_time")) for x in exits}
    orphans: list[dict[str, Any]] = []
    for a in accepted:
        pid = str(a.get("position_id") or "")
        key = (a.get("symbol"), a.get("entry_time"))
        if pid and pid in exit_pids:
            continue
        if key in exit_keys:
            continue
        orphans.append(a)
    return orphans


def already_recovery_closed(events: Sequence[Mapping[str, Any]], position_id: str) -> bool:
    pid = str(position_id or "")
    if not pid:
        return False
    for e in events:
        if str(e.get("event_type") or "") != "observer_exit":
            continue
        if str(e.get("position_id") or "") != pid:
            continue
        if str(e.get("exit_reason") or "") == "recovery_forced_close":
            return True
    return False


def build_recovery_forced_close_exit(
    accepted: Mapping[str, Any],
    *,
    closed_at: str,
    recovery_note: str,
    message_index: Any = None,
    force_close_at: Optional[str] = None,
    events_path: Optional[Path] = None,
    price_decision: Any = None,
) -> dict[str, Any]:
    """Build observer_exit from accepted row using last valid market price when available.

    Does not place orders. Prefer Bid > CurrentPrice > Mid > Ask > Entry fallback.
    """
    out = dict(accepted)
    entry_px = accepted.get("entry_price")
    try:
        entry_f = float(entry_px) if entry_px is not None else None
    except (TypeError, ValueError):
        entry_f = None

    decision = price_decision
    if decision is None and events_path is not None and entry_f is not None:
        try:
            from small_paper.recovery_market_price import resolve_recovery_price_for_position

            decision = resolve_recovery_price_for_position(
                symbol=str(accepted.get("symbol") or ""),
                entry_price=entry_f,
                entry_time=accepted.get("entry_time") or accepted.get("event_time") or closed_at,
                force_close=force_close_at or closed_at,
                events_path=Path(events_path),
            )
        except Exception:
            decision = None

    if decision is not None:
        from small_paper.recovery_market_price import apply_decision_to_exit_event

        base = {
            **out,
            "event_time": closed_at,
            "event_type": "observer_exit",
            "exit_time": closed_at,
            "exit_reason": "recovery_forced_close",
            "recovery_note": recovery_note,
            "recovery_forced_close": True,
            "dry_run": True,
            "source": accepted.get("source") or "live",
            "structural_exit_reason": "recovery_forced_close",
        }
        if message_index is not None:
            base["message_index"] = message_index
        if accepted.get("position_id"):
            base["position_id"] = accepted.get("position_id")
        return apply_decision_to_exit_event(base, decision)

    # Legacy fallback (should be rare): entry/current without market scan
    cur = accepted.get("current_price", entry_px)
    try:
        cur_f = float(cur) if cur is not None else entry_f
        pnl = 0.0
        if entry_f and cur_f is not None and entry_f != 0:
            pnl = round(100.0 * (cur_f - entry_f) / entry_f, 4)
    except (TypeError, ValueError):
        pnl = 0.0
        cur_f = cur
    out.update(
        {
            "event_time": closed_at,
            "event_type": "observer_exit",
            "exit_time": closed_at,
            "exit_reason": "recovery_forced_close",
            "pnl_pct": pnl,
            "current_price": cur_f,
            "exit_price": cur_f if cur_f is not None else entry_px,
            "recovery_note": recovery_note,
            "recovery_forced_close": True,
            "dry_run": True,
            "source": accepted.get("source") or "live",
            "structural_exit_reason": "recovery_forced_close",
            "recovery_price_source": "ENTRY_PRICE_FALLBACK",
            "recovery_price_warning": "NO_MARKET_SCAN_CONTEXT",
            "previous_price_source": "ENTRY_PRICE_FORCED_ZERO",
        }
    )
    try:
        from replay.pnl_yen import compute_pnl_yen_100

        ep = float(entry_px) if entry_px is not None else None
        xp = float(out["exit_price"]) if out.get("exit_price") is not None else None
        if ep is not None and xp is not None:
            yen = round(compute_pnl_yen_100(ep, xp), 2)
            out["pnl_yen_100"] = yen
            out["actual_pnl_yen_100"] = yen
    except Exception:
        out.setdefault("pnl_yen_100", 0.0)
        out.setdefault("actual_pnl_yen_100", 0.0)
    if message_index is not None:
        out["message_index"] = message_index
    if accepted.get("position_id"):
        out["position_id"] = accepted.get("position_id")
    return out


@dataclass
class OrphanRecoveryResult:
    ok: bool
    orphan_forced_close_count: int = 0
    orphan_position_ids: list[str] = field(default_factory=list)
    skipped_already_closed: list[str] = field(default_factory=list)
    still_open_after: list[str] = field(default_factory=list)
    active_positions: int = 0
    exits_appended: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def apply_orphan_recovery_to_events(
    events: list[dict[str, Any]],
    *,
    recovery_note: str,
    closed_at: Optional[str] = None,
    only_symbols: Optional[Sequence[str]] = None,
    force_close_at: Optional[str] = None,
    events_path: Optional[Path] = None,
) -> OrphanRecoveryResult:
    """Idempotent: append recovery_forced_close once per orphan position_id."""
    closed_at = closed_at or now_iso()
    orphans = find_orphan_accepted(events)
    if only_symbols:
        allow = {s if str(s).endswith(".T") else f"{s}.T" for s in only_symbols}
        allow |= {s.replace(".T", "") for s in list(allow)}
        orphans = [
            o
            for o in orphans
            if str(o.get("symbol") or "") in allow
            or str(o.get("symbol") or "").replace(".T", "") in allow
        ]
    result = OrphanRecoveryResult(ok=True)
    for o in orphans:
        pid = str(o.get("position_id") or "")
        if pid and already_recovery_closed(events, pid):
            result.skipped_already_closed.append(pid)
            continue
        # also skip if any exit already exists for pid
        if pid and any(
            str(e.get("event_type") or "") == "observer_exit" and str(e.get("position_id") or "") == pid
            for e in events
        ):
            result.skipped_already_closed.append(pid or str(o.get("symbol")))
            continue
        exit_ev = build_recovery_forced_close_exit(
            o,
            closed_at=closed_at,
            recovery_note=recovery_note,
            message_index=o.get("message_index"),
            force_close_at=force_close_at or closed_at,
            events_path=events_path,
        )
        events.append(exit_ev)
        result.exits_appended.append(exit_ev)
        if pid:
            result.orphan_position_ids.append(pid)
        result.orphan_forced_close_count += 1

    # verify
    still = find_orphan_accepted(events)
    if only_symbols:
        allow = {s if str(s).endswith(".T") else f"{s}.T" for s in only_symbols}
        still = [o for o in still if str(o.get("symbol") or "") in allow]
    result.still_open_after = [str(o.get("position_id") or o.get("symbol")) for o in still]
    result.active_positions = len(still)
    result.ok = result.active_positions == 0
    return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def append_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(dict(r), ensure_ascii=False) + "\n")


def supervisor_attempt_path(session_dir: Path) -> Path:
    return Path(session_dir) / "runtime_supervisor_attempts.json"


def load_supervisor_attempts(session_dir: Path) -> dict[str, Any]:
    p = supervisor_attempt_path(session_dir)
    if not p.is_file():
        return {"session_id": Path(session_dir).name, "attempts": 0, "history": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"session_id": Path(session_dir).name, "attempts": 0, "history": []}


def record_supervisor_attempt(
    session_dir: Path,
    *,
    action: str,
    detail: Optional[Mapping[str, Any]] = None,
    max_attempts: int = DEFAULT_SUPERVISOR_MAX_RESTARTS_PER_SESSION,
) -> dict[str, Any]:
    data = load_supervisor_attempts(session_dir)
    data["attempts"] = int(data.get("attempts") or 0) + 1
    hist = list(data.get("history") or [])
    hist.append({"at": now_iso(), "action": action, "detail": dict(detail or {})})
    data["history"] = hist[-50:]
    data["max_attempts"] = int(max_attempts)
    data["blocked"] = int(data["attempts"]) > int(max_attempts)
    supervisor_attempt_path(session_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return data


def supervisor_may_restart(
    session_dir: Path,
    *,
    max_attempts: int = DEFAULT_SUPERVISOR_MAX_RESTARTS_PER_SESSION,
    cooldown_sec: float = DEFAULT_SUPERVISOR_COOLDOWN_SEC,
) -> tuple[bool, str]:
    data = load_supervisor_attempts(session_dir)
    attempts = int(data.get("attempts") or 0)
    if attempts >= max_attempts:
        return False, "max_restarts_per_session"
    hist = list(data.get("history") or [])
    if hist:
        last = hist[-1].get("at") or ""
        try:
            last_dt = datetime.fromisoformat(str(last))
            age = (datetime.now(JST) - last_dt).total_seconds()
            if age < cooldown_sec:
                return False, "cooldown"
        except Exception:
            pass
    return True, ""
