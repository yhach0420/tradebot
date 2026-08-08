"""E1_X5_G1 next-PUSH confirmation guard (research + optional Paper candidate).

Does NOT modify BASE E1_X5 decision core / forward_shadow constants.
Confirmation variants C1/C2/C3 with optional STATE_REARM.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.e1_x5_forward_shadow import (
    CAP,
    SPREAD_MAX_BPS,
    THRESHOLD,
    E1X5ForwardShadowSession,
    ShadowPosition,
)

JST = ZoneInfo("Asia/Tokyo")


class GuardVariant(str, Enum):
    BASE = "BASE"
    C1_NEXT_PUSH_HOLD = "C1_NEXT_PUSH_HOLD"
    C2_NO_LOWER_BID = "C2_NO_LOWER_BID"
    C3_BID_REBOUND = "C3_BID_REBOUND"


@dataclass
class PendingArm:
    signal_id: str
    symbol: str
    arm_sequence: int
    arm_time: datetime
    arm_bid: float
    arm_ask: float
    arm_score: float
    arm_spread_bps: float
    arm_sample_id: str
    arm_day: str
    arm_mid: Optional[float]
    variant: str
    min_bid_since_arm: float
    prev_push_bid: Optional[float] = None
    pending_push_count: int = 0
    last_seen_sequence: int = 0


@dataclass
class PendingLogRow:
    signal_id: str
    symbol: str
    action: str  # ARM | CONFIRM | CANCEL
    reason: str
    variant: str
    arm_sequence: int
    arm_time: str
    arm_bid: float
    arm_ask: float
    confirmation_sequence: Optional[int] = None
    confirmation_time: Optional[str] = None
    min_bid_since_arm: Optional[float] = None
    pending_push_count: int = 0
    entry_sequence: Optional[int] = None
    entry_ask: Optional[float] = None
    confirmation_bid: Optional[float] = None


def _norm_sym(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    return s if s.endswith(".T") else f"{s}.T"


def base_predicate_ok(score: Optional[float], spread_bps: Optional[float]) -> bool:
    if score is None or spread_bps is None:
        return False
    return float(score) >= THRESHOLD - 1e-15 and float(spread_bps) <= SPREAD_MAX_BPS + 1e-9


def is_independent_push(
    *,
    arm_sequence: int,
    arm_time: datetime,
    seq: Optional[int],
    ts: datetime,
    bid: Optional[float],
    ask: Optional[float],
    last_seen_sequence: int,
) -> tuple[bool, str]:
    if seq is None:
        return False, "NO_SEQUENCE"
    try:
        seq_i = int(seq)
    except (TypeError, ValueError):
        return False, "BAD_SEQUENCE"
    if seq_i <= int(arm_sequence):
        return False, "SEQ_NOT_AFTER_ARM"
    if ts <= arm_time:
        return False, "TIME_NOT_AFTER_ARM"
    if seq_i <= int(last_seen_sequence):
        return False, "DUPLICATE_OR_INVERSION"
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return False, "INVALID_QUOTE"
    return True, "OK"


@dataclass
class E1X5GuardSession(E1X5ForwardShadowSession):
    """Shadow session with optional next-PUSH confirmation before ENTRY."""

    variant: GuardVariant = GuardVariant.BASE
    state_rearm: bool = False
    pending: dict[str, PendingArm] = field(default_factory=dict)
    pending_logs: list[PendingLogRow] = field(default_factory=list)
    disarmed_after_stop: set[str] = field(default_factory=set)
    saw_false_since_disarm: set[str] = field(default_factory=set)
    last_predicate: dict[str, bool] = field(default_factory=dict)
    confirm_count: int = 0
    cancel_count: int = 0
    arm_count: int = 0
    rearm_transition_count: int = 0
    cancel_reasons: dict[str, int] = field(default_factory=dict)
    confirm_reasons: dict[str, int] = field(default_factory=dict)

    def config_id(self) -> str:
        return f"{self.variant.value}" + ("+STATE_REARM" if self.state_rearm else "")

    def config_hash(self) -> str:
        raw = f"{self.variant.value}|rearm={int(self.state_rearm)}|thr={THRESHOLD}|spread={SPREAD_MAX_BPS}|cap={CAP}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _bump_cancel(self, reason: str) -> None:
        self.cancel_count += 1
        self.cancel_reasons[reason] = self.cancel_reasons.get(reason, 0) + 1

    def _bump_confirm(self, reason: str) -> None:
        self.confirm_count += 1
        self.confirm_reasons[reason] = self.confirm_reasons.get(reason, 0) + 1

    def cancel_pending(self, symbol: str, reason: str, *, seq: Optional[int] = None, ts: Optional[datetime] = None) -> None:
        sym = _norm_sym(symbol)
        arm = self.pending.pop(sym, None)
        if arm is None:
            return
        self._bump_cancel(reason)
        self.pending_logs.append(
            PendingLogRow(
                signal_id=arm.signal_id,
                symbol=sym,
                action="CANCEL",
                reason=reason,
                variant=arm.variant,
                arm_sequence=arm.arm_sequence,
                arm_time=arm.arm_time.isoformat(),
                arm_bid=arm.arm_bid,
                arm_ask=arm.arm_ask,
                confirmation_sequence=seq,
                confirmation_time=ts.isoformat() if ts else None,
                min_bid_since_arm=arm.min_bid_since_arm,
                pending_push_count=arm.pending_push_count,
            )
        )

    def cancel_all_pending(self, reason: str) -> None:
        for sym in list(self.pending.keys()):
            self.cancel_pending(sym, reason)

    def note_predicate_observation(self, symbol: str, *, score: Optional[float], spread_bps: Optional[float], valid_eval: bool) -> None:
        """Track base predicate for STATE_REARM (SCORE evals only)."""
        if not self.state_rearm or not valid_eval:
            return
        sym = _norm_sym(symbol)
        ok = base_predicate_ok(score, spread_bps)
        prev = self.last_predicate.get(sym)
        self.last_predicate[sym] = ok
        if sym in self.disarmed_after_stop and ok is False:
            self.saw_false_since_disarm.add(sym)

    def _rearm_allowed(self, symbol: str, *, score: float, spread_bps: float) -> bool:
        if not self.state_rearm:
            return True
        sym = _norm_sym(symbol)
        if sym not in self.disarmed_after_stop:
            return True
        if sym not in self.saw_false_since_disarm:
            return False
        # rising edge false→true
        if not base_predicate_ok(score, spread_bps):
            return False
        return True

    def _clear_disarm_on_rearm(self, symbol: str) -> None:
        sym = _norm_sym(symbol)
        if sym in self.disarmed_after_stop:
            self.rearm_transition_count += 1
        self.disarmed_after_stop.discard(sym)
        self.saw_false_since_disarm.discard(sym)

    def try_entry(
        self,
        *,
        symbol: str,
        ts: datetime,
        bid: Optional[float],
        ask: Optional[float],
        score: float,
        spread_bps: Optional[float],
        sample_id: str = "",
        day: str = "",
        mid: Optional[float] = None,
        event_sequence: Any = None,
    ) -> Optional[str]:
        if self.variant == GuardVariant.BASE:
            return super().try_entry(
                symbol=symbol,
                ts=ts,
                bid=bid,
                ask=ask,
                score=score,
                spread_bps=spread_bps,
                sample_id=sample_id,
                day=day,
                mid=mid,
                event_sequence=event_sequence,
            )

        if not self.enabled:
            return "DISABLED"
        ts = self._coerce_ts(ts)
        sym = _norm_sym(symbol)
        ek = self._event_key(sym, ts, sample_id, event_sequence)
        if ek in self._evaluated_event_keys:
            self.duplicate_eval_suppressed += 1
            return "DUPLICATE_EVENT"
        self._evaluated_event_keys.add(ek)
        self.evaluated_count += 1

        self.note_predicate_observation(sym, score=score, spread_bps=spread_bps, valid_eval=True)

        reason, spread_bps = self.evaluate_entry_gates(
            symbol=sym, ts=ts, bid=bid, ask=ask, score=score, spread_bps=spread_bps
        )
        if reason is not None:
            self._log_candidate(ts, sym, score, spread_bps, bid, ask, mid, False, reason)
            if reason in {
                "SCORE_BELOW_THRESHOLD",
                "SPREAD_OVER_5BPS",
                "SAME_SYMBOL_OPEN",
                "CAP5_BLOCKED",
            } and sym in self.pending:
                self.cancel_pending(sym, reason, seq=event_sequence, ts=ts)
            return reason

        if not self._rearm_allowed(sym, score=float(score), spread_bps=float(spread_bps)):
            reason = "DISARMED_AFTER_STOP"
            self._log_candidate(ts, sym, score, spread_bps, bid, ask, mid, False, reason)
            return reason

        # Already pending — do not re-arm on same signal chain
        if sym in self.pending:
            self._log_candidate(ts, sym, score, spread_bps, bid, ask, mid, False, "ALREADY_PENDING")
            return "ALREADY_PENDING"

        if event_sequence is None:
            reason = "NO_SEQUENCE"
            self._log_candidate(ts, sym, score, spread_bps, bid, ask, mid, False, reason)
            return reason

        signal_id = f"{sym}:{int(event_sequence)}:{uuid.uuid4().hex[:8]}"
        arm = PendingArm(
            signal_id=signal_id,
            symbol=sym,
            arm_sequence=int(event_sequence),
            arm_time=ts,
            arm_bid=float(bid),
            arm_ask=float(ask),
            arm_score=float(score),
            arm_spread_bps=float(spread_bps),
            arm_sample_id=sample_id,
            arm_day=day or ts.strftime("%Y%m%d"),
            arm_mid=mid,
            variant=self.variant.value,
            min_bid_since_arm=float(bid),
            prev_push_bid=None,
            pending_push_count=0,
            last_seen_sequence=int(event_sequence),
        )
        self.pending[sym] = arm
        self.arm_count += 1
        self.pending_logs.append(
            PendingLogRow(
                signal_id=signal_id,
                symbol=sym,
                action="ARM",
                reason="PENDING_CONFIRMATION",
                variant=self.variant.value,
                arm_sequence=arm.arm_sequence,
                arm_time=arm.arm_time.isoformat(),
                arm_bid=arm.arm_bid,
                arm_ask=arm.arm_ask,
                min_bid_since_arm=arm.min_bid_since_arm,
            )
        )
        self._log_candidate(ts, sym, score, spread_bps, bid, ask, mid, False, "PENDING_CONFIRMATION")
        return "PENDING_CONFIRMATION"

    def on_missing_score(self, **kwargs: Any) -> Optional[str]:
        sym = _norm_sym(str(kwargs.get("symbol") or ""))
        ts = kwargs.get("ts")
        seq = kwargs.get("event_sequence")
        if sym in self.pending:
            self.cancel_pending(sym, "NO_EVALUATION", seq=seq, ts=self._coerce_ts(ts) if ts else None)
        # EXIT still via parent
        return super().on_missing_score(**kwargs)

    def confirm_on_independent_push(
        self,
        *,
        symbol: str,
        ts: Any,
        bid: Optional[float],
        ask: Optional[float],
        sequence: Optional[int],
        observe_kind: str,
        score: Optional[float] = None,
        spread_bps: Optional[float] = None,
        sample_id: str = "",
        day: str = "",
        mid: Optional[float] = None,
    ) -> Optional[str]:
        """Public confirmation entry — updates min-bid causally then decides on first independent push."""
        if self.variant == GuardVariant.BASE:
            return None
        sym = _norm_sym(symbol)
        arm = self.pending.get(sym)
        if arm is None:
            return None
        ts_dt = self._coerce_ts(ts)

        if self._sess(ts_dt) is None or (
            self._sess(arm.arm_time) is not None and self._sess(ts_dt) != self._sess(arm.arm_time)
        ):
            self.cancel_pending(sym, "SESSION_BOUNDARY", seq=sequence, ts=ts_dt)
            return "SESSION_BOUNDARY"

        ok, why = is_independent_push(
            arm_sequence=arm.arm_sequence,
            arm_time=arm.arm_time,
            seq=sequence,
            ts=ts_dt,
            bid=bid,
            ask=ask,
            last_seen_sequence=arm.last_seen_sequence,
        )
        if not ok:
            return why

        assert bid is not None and ask is not None and sequence is not None
        seq_i = int(sequence)
        min_before = arm.min_bid_since_arm
        prev_push = arm.prev_push_bid
        arm.pending_push_count += 1
        arm.last_seen_sequence = seq_i
        arm.min_bid_since_arm = min(arm.min_bid_since_arm, float(bid))

        if observe_kind in {
            "MISSING_SCORE",
            "IDENTITY_FAIL",
            "NO_EVALUATION_DECISION_TIME_MISSING",
            "TICK_BUILD_FAILED",
            "NO_SAMPLE",
        } or score is None:
            self.cancel_pending(sym, "NO_EVALUATION", seq=seq_i, ts=ts_dt)
            return "NO_EVALUATION"

        if spread_bps is None:
            spread_bps = (float(ask) - float(bid)) / float(ask) * 10000.0
        self.note_predicate_observation(sym, score=score, spread_bps=spread_bps, valid_eval=True)

        if float(score) < THRESHOLD:
            self.cancel_pending(sym, "SCORE_BELOW_THRESHOLD", seq=seq_i, ts=ts_dt)
            return "SCORE_BELOW_THRESHOLD"
        if float(spread_bps) > SPREAD_MAX_BPS + 1e-9:
            self.cancel_pending(sym, "SPREAD_OVER_5BPS", seq=seq_i, ts=ts_dt)
            return "SPREAD_OVER_5BPS"
        if sym in self.positions:
            self.cancel_pending(sym, "SAME_SYMBOL_OPEN", seq=seq_i, ts=ts_dt)
            return "SAME_SYMBOL_OPEN"
        if len(self.positions) >= CAP:
            self.cancel_pending(sym, "CAP5_BLOCKED", seq=seq_i, ts=ts_dt)
            return "CAP5_BLOCKED"

        if self.variant == GuardVariant.C2_NO_LOWER_BID:
            if float(bid) + 1e-12 < arm.arm_bid:
                # Lower bid → cancel (not wait)
                self.cancel_pending(sym, "BID_LOWER_THAN_ARM", seq=seq_i, ts=ts_dt)
                return "BID_LOWER_THAN_ARM"

        if self.variant == GuardVariant.C3_BID_REBOUND:
            rebound = float(bid) > min_before + 1e-12
            non_decreasing = prev_push is None or float(bid) + 1e-12 >= float(prev_push)
            arm.prev_push_bid = float(bid)
            if not (rebound and non_decreasing):
                # Keep pending — wait for later rebound push (still SCORE required each time)
                # User: "独立PUSHで次を満たした場合だけENTRY" — keep waiting
                return "WAITING_REBOUND"

        elif self.variant in {GuardVariant.C1_NEXT_PUSH_HOLD, GuardVariant.C2_NO_LOWER_BID}:
            arm.prev_push_bid = float(bid)

        return self._enter_from_confirm(
            arm=arm,
            sym=sym,
            ts_dt=ts_dt,
            bid=float(bid),
            ask=float(ask),
            seq_i=seq_i,
            score=float(score),
            spread_bps=float(spread_bps),
            sample_id=sample_id,
            day=day,
            mid=mid,
            reason=self.variant.value,
        )

    def _enter_from_confirm(
        self,
        *,
        arm: PendingArm,
        sym: str,
        ts_dt: datetime,
        bid: float,
        ask: float,
        seq_i: int,
        score: float,
        spread_bps: float,
        sample_id: str,
        day: str,
        mid: Optional[float],
        reason: str,
    ) -> Optional[str]:
        # Enter at confirmation ask (not arm ask)
        self.pending.pop(sym, None)
        self._clear_disarm_on_rearm(sym)
        pos = ShadowPosition(
            symbol=sym,
            entry_time=ts_dt,
            entry_ask=float(ask),
            score=float(score),
            spread_bps=float(spread_bps),
            sample_id=sample_id or arm.arm_sample_id,
            day=day or arm.arm_day,
        )
        self.positions[sym] = pos
        self.entries.append(
            {
                "timestamp": ts_dt,
                "symbol": sym,
                "score": score,
                "threshold": THRESHOLD,
                "spread_bps": spread_bps,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "entry_decision": "ENTER",
                "reject_reason": None,
                "active_positions": len(self.positions),
                "cap": CAP,
                "sample_id": pos.sample_id,
                "day": pos.day,
                "event_sequence": seq_i,
                "guard_signal_id": arm.signal_id,
                "guard_variant": arm.variant,
                "arm_sequence": arm.arm_sequence,
                "arm_ask": arm.arm_ask,
                "confirmation_sequence": seq_i,
            }
        )
        self._log_candidate(ts_dt, sym, score, spread_bps, bid, ask, mid, True, None)
        self._bump_confirm(reason)
        self.pending_logs.append(
            PendingLogRow(
                signal_id=arm.signal_id,
                symbol=sym,
                action="CONFIRM",
                reason=reason,
                variant=arm.variant,
                arm_sequence=arm.arm_sequence,
                arm_time=arm.arm_time.isoformat(),
                arm_bid=arm.arm_bid,
                arm_ask=arm.arm_ask,
                confirmation_sequence=seq_i,
                confirmation_time=ts_dt.isoformat(),
                min_bid_since_arm=arm.min_bid_since_arm,
                pending_push_count=arm.pending_push_count,
                entry_sequence=seq_i,
                entry_ask=float(ask),
                confirmation_bid=float(bid),
            )
        )
        return None

    def _close(self, pos: ShadowPosition, ts: datetime, bid: float, reason: str) -> None:
        sym = pos.symbol
        super()._close(pos, ts, bid, reason)
        if self.state_rearm and reason == "STOP":
            self.disarmed_after_stop.add(sym)
            self.saw_false_since_disarm.discard(sym)
