"""Phase687W43F — Evaluation readiness / recovery reachability (no PBv2 condition changes).

Tracks per-symbol market-state timestamps so freshness uses the latest observed
board/price times, not only the current payload. Separates throttle from state
updates and allows one evaluation on not-ready→ready.

V12: stale-recovery is a reservation (state machine). It MUST NOT bypass the
PBv2 5s evaluation cadence. Consumer wall-clock delay is a health metric, not
a Strategy freshness input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

READY_NOT_SUBSCRIBED = "NOT_SUBSCRIBED"
READY_WARMUP = "SUBSCRIBED_WARMUP"
READY_PRICE = "PRICE_READY"
READY_BOARD = "BOARD_READY"
READY_HISTORY = "HISTORY_READY"
READY_FEATURE = "FEATURE_READY"
READY_EVALUATION = "EVALUATION_READY"

SKIP_THROTTLED = "EVALUATION_THROTTLED"
SKIP_NOT_READY = "DATA_NOT_READY"
SKIP_DUPLICATE = "EVALUATION_DUPLICATE_SUPPRESSED"
SKIP_TRUE_STALE = "TRUE_STALE"

RECOVERY_NORMAL = "NORMAL"
RECOVERY_PENDING = "RECOVERY_PENDING"
RECOVERY_ELIGIBLE = "RECOVERY_ELIGIBLE"
RECOVERY_RECOVERED = "RECOVERED"

LOG_RECOVERY_ENTER = "RECOVERY_ENTER"
LOG_RECOVERY_EVAL = "RECOVERY_EVAL"
LOG_RECOVERY_EXIT = "RECOVERY_EXIT"


def _parse_ts(raw: Any, *, fallback: Optional[datetime] = None) -> Optional[datetime]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        from storage.intraday_recorder import parse_kabu_time

        return parse_kabu_time(raw, fallback=fallback or datetime.now(JST))
    except Exception:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt.astimezone(JST)
        except Exception:
            return None


@dataclass
class SymbolReachabilityState:
    symbol: str
    readiness: str = READY_NOT_SUBSCRIBED
    last_price_update_ts: Optional[datetime] = None
    last_board_update_ts: Optional[datetime] = None
    last_volume: Optional[float] = None
    last_high: Optional[float] = None
    history_ready: bool = False
    feature_ready: bool = False
    last_fresh_ok: bool = False
    last_eval_mono: Optional[float] = None
    last_eval_market_ts: Optional[float] = None
    last_skip_reason: Optional[str] = None
    last_evaluation_cycle_id: Optional[str] = None
    pending_recovery_eval: bool = False
    recovery_state: str = RECOVERY_NORMAL
    pending_ready_eval: bool = False
    price_state_updated_at: Optional[str] = None
    board_state_updated_at: Optional[str] = None
    history_ready_at: Optional[str] = None
    evaluation_attempted_at: Optional[str] = None


@dataclass
class EvaluationReachabilityTracker:
    """Per-session tracker for readiness + recovery evaluation triggers."""

    symbols: dict[str, SymbolReachabilityState] = field(default_factory=dict)
    evaluation_attempted_count: int = 0
    evaluation_skipped_not_ready_count: int = 0
    evaluation_skipped_stale_count: int = 0
    evaluation_skipped_throttled_count: int = 0
    evaluation_recovery_triggered_count: int = 0
    evaluation_ready_transition_count: int = 0
    ready_transition_evaluated_count: int = 0
    ready_transition_missing_evaluation_count: int = 0
    ready_transition_duplicate_evaluation_count: int = 0
    stale_recovery_count: int = 0
    stale_recovery_ready_count: int = 0
    recovery_missing_evaluation_count: int = 0
    recovery_duplicate_evaluation_count: int = 0
    false_board_stale_prevented_count: int = 0
    state_update_without_eval_count: int = 0
    duplicate_eval_suppressed_count: int = 0
    pipeline_integrity_error_count: int = 0
    normal_throttle_skip_count: int = 0
    forced_ready_evaluation_count: int = 0
    forced_recovery_evaluation_count: int = 0
    forced_eval_count: int = 0
    recovery_eval_count: int = 0
    recovery_enter_count: int = 0
    recovery_exit_count: int = 0
    push_count: int = 0
    pbv2_eval_count: int = 0
    pbv2_throttled_count: int = 0
    last_consumer_processing_delay_sec: Optional[float] = None
    max_consumer_processing_delay_sec: float = 0.0
    last_event_age_market_sec: Optional[float] = None
    recovery_log_events: int = 0
    forced_duplicate_count: int = 0
    pipeline_cycle_count: int = 0
    pipeline_order_valid_count: int = 0
    pipeline_order_invalid_count: int = 0
    _cycle_seq: int = 0
    _seen_cycle_ids: set[str] = field(default_factory=set)
    _flushed: bool = False

    def get(self, symbol: str) -> SymbolReachabilityState:
        st = self.symbols.get(symbol)
        if st is None:
            st = SymbolReachabilityState(symbol=symbol, readiness=READY_WARMUP)
            self.symbols[symbol] = st
        return st

    def mark_subscribed(self, symbols: set[str], *, continuing: set[str]) -> None:
        """Refresh: keep readiness for continuing symbols; warmup new ones."""
        for sym in symbols:
            st = self.get(sym)
            if sym in continuing and st.readiness not in (READY_NOT_SUBSCRIBED,):
                # keep history/readiness; force one re-eval after refresh register
                if st.readiness == READY_EVALUATION:
                    st.pending_ready_eval = True
                continue
            # new symbol
            st.readiness = READY_WARMUP
            st.history_ready = False
            st.feature_ready = False
            st.pending_ready_eval = False
            st.last_fresh_ok = False

    def mark_unsubscribed(self, symbols: set[str]) -> None:
        for sym in symbols:
            st = self.symbols.get(sym)
            if st is None:
                continue
            st.readiness = READY_NOT_SUBSCRIBED
            st.pending_ready_eval = False
            st.pending_recovery_eval = False
            st.recovery_state = RECOVERY_NORMAL

    def update_from_payload(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        reference_now: Optional[datetime] = None,
        feature_complete: bool = False,
        history_ticks: int = 0,
        min_history_ticks: int = 3,
    ) -> SymbolReachabilityState:
        """Always-safe state update (may run even when evaluation is throttled)."""
        st = self.get(symbol)
        now = reference_now or datetime.now(JST)
        price_ts = _parse_ts(payload.get("CurrentPriceTime"), fallback=now)
        bid_ts = _parse_ts(payload.get("BidTime"), fallback=now)
        ask_ts = _parse_ts(payload.get("AskTime"), fallback=now)
        board_ts = None
        for ts in (bid_ts, ask_ts):
            if ts is None:
                continue
            if board_ts is None or ts > board_ts:
                board_ts = ts

        prev_fresh = st.last_fresh_ok
        prev_ready = st.readiness == READY_EVALUATION

        if price_ts is not None and (
            st.last_price_update_ts is None or price_ts >= st.last_price_update_ts
        ):
            st.last_price_update_ts = price_ts
            st.price_state_updated_at = now.isoformat(timespec="milliseconds")

        if board_ts is not None and (
            st.last_board_update_ts is None or board_ts >= st.last_board_update_ts
        ):
            st.last_board_update_ts = board_ts
            st.board_state_updated_at = now.isoformat(timespec="milliseconds")

        vol = payload.get("TradingVolume")
        try:
            vol_f = float(vol) if vol is not None else None
        except (TypeError, ValueError):
            vol_f = None
        if vol_f is not None and (st.last_volume is None or vol_f != st.last_volume):
            st.last_volume = vol_f

        high = payload.get("HighPrice")
        try:
            high_f = float(high) if high is not None else None
        except (TypeError, ValueError):
            high_f = None
        if high_f is not None and (st.last_high is None or high_f > (st.last_high or -1e18)):
            st.last_high = high_f

        # readiness ladder (does not loosen freshness thresholds)
        if st.readiness == READY_NOT_SUBSCRIBED:
            st.readiness = READY_WARMUP
        if st.last_price_update_ts is not None and st.readiness in (READY_WARMUP, READY_NOT_SUBSCRIBED):
            st.readiness = READY_PRICE
        if st.last_board_update_ts is not None and st.readiness in (READY_WARMUP, READY_PRICE):
            st.readiness = READY_BOARD

        hist_ready = bool(history_ticks >= int(min_history_ticks))
        if hist_ready and not st.history_ready:
            st.history_ready = True
            st.history_ready_at = now.isoformat(timespec="milliseconds")
            st.readiness = READY_HISTORY
        elif hist_ready:
            st.history_ready = True
            if st.readiness in (READY_BOARD, READY_PRICE):
                st.readiness = READY_HISTORY

        feat_ready = bool(feature_complete or hist_ready)
        if feat_ready:
            st.feature_ready = True
            if st.readiness in (READY_HISTORY, READY_BOARD):
                st.readiness = READY_FEATURE

        price_ok = st.last_price_update_ts is not None
        board_ok = st.last_board_update_ts is not None
        if price_ok and board_ok and st.history_ready and st.feature_ready:
            if st.readiness != READY_EVALUATION:
                st.readiness = READY_EVALUATION
                if not st.pending_ready_eval:
                    st.pending_ready_eval = True
                    self.evaluation_ready_transition_count += 1
            else:
                st.readiness = READY_EVALUATION

        # recovery: reserve next legal 5s slot. Never force-eval per PUSH.
        if (
            st.last_eval_mono is not None
            and prev_fresh is False
            and price_ok
            and board_ok
            and st.history_ready
            and st.feature_ready
            and st.recovery_state in (RECOVERY_NORMAL, RECOVERY_RECOVERED)
            and not st.pending_recovery_eval
        ):
            st.pending_recovery_eval = True
            st.recovery_state = RECOVERY_PENDING
            self.stale_recovery_count += 1
            self.stale_recovery_ready_count += 1
            self.recovery_enter_count += 1
            self.recovery_log_events += 1

        if prev_ready and st.readiness != READY_EVALUATION:
            # once ready, do not silently drop to warmup except session reset
            if st.history_ready and st.feature_ready and price_ok and board_ok:
                st.readiness = READY_EVALUATION

        return st

    def freshness_overrides(self, symbol: str) -> dict[str, Optional[datetime]]:
        st = self.get(symbol)
        return {
            "last_price_update_ts": st.last_price_update_ts,
            "last_board_update_ts": st.last_board_update_ts,
        }

    def note_consumer_delay(
        self,
        *,
        event_time: Optional[datetime],
        wall_now: Optional[datetime] = None,
        source_received_at: Optional[datetime] = None,
    ) -> dict[str, Optional[float]]:
        """Runtime health only — must not feed Strategy freshness thresholds."""
        wall = wall_now or datetime.now(JST)
        delay = None
        event_age = None
        if event_time is not None:
            delay = max(0.0, (wall - event_time).total_seconds())
            self.last_consumer_processing_delay_sec = delay
            if delay > self.max_consumer_processing_delay_sec:
                self.max_consumer_processing_delay_sec = delay
        src = source_received_at or event_time
        if event_time is not None and src is not None:
            event_age = max(0.0, (event_time - src).total_seconds())
            self.last_event_age_market_sec = event_age
        return {
            "consumer_processing_delay_sec": delay,
            "event_age_market_sec": event_age,
        }

    def should_evaluate(
        self,
        symbol: str,
        *,
        now_mono: float,
        market_ts: Optional[float],
        poll_interval_sec: float,
        ring_only_warmup: bool,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Return (evaluate?, skip_reason, evaluation_cycle_id).

        Recovery reservations never bypass poll_interval_sec. Only the first
        not-ready→ready transition (`pending_ready_eval`) may evaluate immediately.
        """
        st = self.get(symbol)
        self.push_count += 1
        if ring_only_warmup:
            self.evaluation_skipped_not_ready_count += 1
            st.last_skip_reason = SKIP_NOT_READY
            return False, SKIP_NOT_READY, None

        # V12: recovery is NOT a force-eval. pending_ready_eval remains one-shot.
        force = bool(st.pending_ready_eval)
        if not force and st.readiness != READY_EVALUATION and not st.history_ready:
            self.evaluation_skipped_not_ready_count += 1
            st.last_skip_reason = SKIP_NOT_READY
            return False, SKIP_NOT_READY, None

        if not force and poll_interval_sec > 0:
            if market_ts is not None and st.last_eval_market_ts is not None:
                if (market_ts - st.last_eval_market_ts) < float(poll_interval_sec):
                    self.evaluation_skipped_throttled_count += 1
                    self.normal_throttle_skip_count += 1
                    self.pbv2_throttled_count += 1
                    st.last_skip_reason = SKIP_THROTTLED
                    self.state_update_without_eval_count += 1
                    return False, SKIP_THROTTLED, None
            elif st.last_eval_mono is not None:
                if (now_mono - st.last_eval_mono) < float(poll_interval_sec):
                    self.evaluation_skipped_throttled_count += 1
                    self.normal_throttle_skip_count += 1
                    self.pbv2_throttled_count += 1
                    st.last_skip_reason = SKIP_THROTTLED
                    self.state_update_without_eval_count += 1
                    return False, SKIP_THROTTLED, None

        if st.pending_recovery_eval and st.recovery_state == RECOVERY_PENDING:
            st.recovery_state = RECOVERY_ELIGIBLE
            self.recovery_log_events += 1

        self._cycle_seq += 1
        cycle = f"{symbol}:{self._cycle_seq}:{int(now_mono * 1000)}"
        if cycle in self._seen_cycle_ids or st.last_evaluation_cycle_id == cycle:
            self.duplicate_eval_suppressed_count += 1
            return False, SKIP_DUPLICATE, None
        self.pbv2_eval_count += 1
        return True, None, cycle

    def mark_evaluated(
        self,
        symbol: str,
        *,
        now_mono: float,
        market_ts: Optional[float],
        cycle_id: str,
        fresh_ok: bool,
        stale_reject: bool,
        price_state_updated_at: Optional[str] = None,
        board_state_updated_at: Optional[str] = None,
        history_updated_at: Optional[str] = None,
        feature_computed_at: Optional[str] = None,
        evaluation_attempted_at: Optional[str] = None,
    ) -> None:
        st = self.get(symbol)
        was_ready = bool(st.pending_ready_eval)
        was_recovery = bool(st.pending_recovery_eval)
        if cycle_id in self._seen_cycle_ids:
            self.duplicate_eval_suppressed_count += 1
            self.forced_duplicate_count += 1
            return
        self._seen_cycle_ids.add(cycle_id)
        st.last_eval_mono = now_mono
        if market_ts is not None:
            st.last_eval_market_ts = market_ts
        st.last_evaluation_cycle_id = cycle_id
        attempted_at = evaluation_attempted_at or datetime.now(JST).isoformat(timespec="milliseconds")
        st.evaluation_attempted_at = attempted_at
        self.evaluation_attempted_count += 1
        self.pipeline_cycle_count += 1
        # Pipeline order: state/history/feature times must be <= evaluation time
        pre_eval = [
            price_state_updated_at or st.price_state_updated_at,
            board_state_updated_at or st.board_state_updated_at,
            history_updated_at or st.history_ready_at,
            feature_computed_at,
        ]
        attempt_dt = _parse_ts(attempted_at)
        pre_dts = [_parse_ts(s) for s in pre_eval if s]
        pre_dts = [d for d in pre_dts if d is not None]
        if attempt_dt is not None and pre_dts:
            if all(d <= attempt_dt for d in pre_dts):
                self.pipeline_order_valid_count += 1
            else:
                self.pipeline_order_invalid_count += 1
                self.pipeline_integrity_error_count += 1
        else:
            self.pipeline_order_valid_count += 1
        if was_ready:
            self.ready_transition_evaluated_count += 1
            self.forced_ready_evaluation_count += 1
        if was_recovery:
            self.evaluation_recovery_triggered_count += 1
            self.recovery_eval_count += 1
            # V12: recovery evals are cadence-legal, not force-per-push.
        st.pending_ready_eval = False
        st.pending_recovery_eval = False
        st.last_fresh_ok = bool(fresh_ok)
        st.last_skip_reason = SKIP_TRUE_STALE if stale_reject else None
        if stale_reject:
            self.evaluation_skipped_stale_count += 1
            # Re-reserve on the next state update — do not force the following PUSH.
            st.recovery_state = RECOVERY_NORMAL
        elif was_recovery:
            st.recovery_state = RECOVERY_RECOVERED
            self.recovery_exit_count += 1
            self.recovery_log_events += 1
        else:
            st.recovery_state = RECOVERY_NORMAL

    def flush_pending_at_session_end(self) -> None:
        """Count ready/recovery transitions that never received an evaluation."""
        if self._flushed:
            return
        self._flushed = True
        for st in self.symbols.values():
            if st.pending_ready_eval:
                self.ready_transition_missing_evaluation_count += 1
            if st.pending_recovery_eval:
                self.recovery_missing_evaluation_count += 1

    def summary_fields(self, *, finalize: bool = False) -> dict[str, Any]:
        if finalize:
            self.flush_pending_at_session_end()
        ready = sum(1 for s in self.symbols.values() if s.readiness == READY_EVALUATION)
        price_ready = sum(1 for s in self.symbols.values() if s.last_price_update_ts is not None)
        board_ready = sum(1 for s in self.symbols.values() if s.last_board_update_ts is not None)
        hist_ready = sum(1 for s in self.symbols.values() if s.history_ready)
        feat_ready = sum(1 for s in self.symbols.values() if s.feature_ready)
        ready_n = int(self.evaluation_ready_transition_count)
        rec_n = int(self.stale_recovery_ready_count)
        ready_cov = (
            float(self.ready_transition_evaluated_count) / float(ready_n) if ready_n else 1.0
        )
        rec_cov = (
            float(self.evaluation_recovery_triggered_count) / float(rec_n) if rec_n else 1.0
        )
        return {
            "push_received_symbol_count": int(len(self.symbols)),
            "price_ready_symbol_count": int(price_ready),
            "board_ready_symbol_count": int(board_ready),
            "history_ready_symbol_count": int(hist_ready),
            "feature_ready_symbol_count": int(feat_ready),
            "evaluation_ready_symbol_count": int(ready),
            "evaluation_attempted_count": int(self.evaluation_attempted_count),
            "evaluation_skipped_not_ready_count": int(self.evaluation_skipped_not_ready_count),
            "evaluation_skipped_stale_count": int(self.evaluation_skipped_stale_count),
            "evaluation_skipped_throttled_count": int(self.evaluation_skipped_throttled_count),
            "normal_throttle_skip_count": int(self.normal_throttle_skip_count),
            "evaluation_recovery_triggered_count": int(self.evaluation_recovery_triggered_count),
            "evaluation_ready_transition_count": int(self.evaluation_ready_transition_count),
            "ready_transition_count": int(self.evaluation_ready_transition_count),
            "ready_transition_evaluated_count": int(self.ready_transition_evaluated_count),
            "ready_transition_missing_evaluation_count": int(
                self.ready_transition_missing_evaluation_count
            ),
            "ready_transition_duplicate_evaluation_count": int(
                self.ready_transition_duplicate_evaluation_count
            ),
            "stale_recovery_count": int(self.stale_recovery_count),
            "stale_recovery_ready_count": int(self.stale_recovery_ready_count),
            "recovery_missing_evaluation_count": int(self.recovery_missing_evaluation_count),
            "recovery_duplicate_evaluation_count": int(self.recovery_duplicate_evaluation_count),
            "forced_ready_evaluation_count": int(self.forced_ready_evaluation_count),
            "forced_recovery_evaluation_count": int(self.forced_recovery_evaluation_count),
            "forced_eval_count": int(self.forced_eval_count),
            "recovery_eval_count": int(self.recovery_eval_count),
            "recovery_enter_count": int(self.recovery_enter_count),
            "recovery_exit_count": int(self.recovery_exit_count),
            "push_count": int(self.push_count),
            "pbv2_eval_count": int(self.pbv2_eval_count),
            "pbv2_throttled_count": int(self.pbv2_throttled_count),
            "eval_fraction": (
                float(self.pbv2_eval_count) / float(self.push_count) if self.push_count else 0.0
            ),
            "last_consumer_processing_delay_sec": self.last_consumer_processing_delay_sec,
            "max_consumer_processing_delay_sec": float(self.max_consumer_processing_delay_sec),
            "last_event_age_market_sec": self.last_event_age_market_sec,
            "recovery_log_events": int(self.recovery_log_events),
            "forced_duplicate_count": int(self.forced_duplicate_count),
            "false_board_stale_prevented_count": int(self.false_board_stale_prevented_count),
            "state_update_without_eval_count": int(self.state_update_without_eval_count),
            "pipeline_integrity_error_count": int(self.pipeline_integrity_error_count),
            "pipeline_cycle_count": int(self.pipeline_cycle_count),
            "pipeline_order_valid_count": int(self.pipeline_order_valid_count),
            "pipeline_order_invalid_count": int(self.pipeline_order_invalid_count),
            "ready_evaluation_coverage": ready_cov,
            "recovery_evaluation_coverage": rec_cov,
            "duplicate_eval_suppressed_count": int(self.duplicate_eval_suppressed_count),
        }


def merge_freshness_snapshot_with_state(
    snap: Any,
    *,
    last_price_update_ts: Optional[datetime],
    last_board_update_ts: Optional[datetime],
    reference_now: datetime,
    tracker: Optional[EvaluationReachabilityTracker] = None,
) -> Any:
    """Prefer newer carried-forward timestamps when payload omitted/older times.

    Does not change thresholds — only which timestamp is used for age.
    """
    from small_paper.entry_scan_controller import EntryFreshnessSnapshot

    price_ts = snap.last_price_update_ts
    board_ts = snap.last_board_update_ts
    price_dt = _parse_ts(price_ts, fallback=reference_now) if isinstance(price_ts, str) else None
    board_dt = _parse_ts(board_ts, fallback=reference_now) if isinstance(board_ts, str) else None

    used_carry_board = False
    if last_price_update_ts is not None and (price_dt is None or last_price_update_ts > price_dt):
        price_dt = last_price_update_ts
    if last_board_update_ts is not None and (board_dt is None or last_board_update_ts > board_dt):
        board_dt = last_board_update_ts
        used_carry_board = True

    price_age = (
        max(0.0, (reference_now - price_dt).total_seconds()) if price_dt is not None else None
    )
    board_age = (
        max(0.0, (reference_now - board_dt).total_seconds()) if board_dt is not None else None
    )
    if used_carry_board and tracker is not None and board_age is not None:
        prev_age = snap.board_age_sec
        if prev_age is None or board_age < float(prev_age):
            tracker.false_board_stale_prevented_count += 1

    return EntryFreshnessSnapshot(
        data_source=snap.data_source,
        last_price_update_ts=price_dt.isoformat(timespec="milliseconds") if price_dt else None,
        last_board_update_ts=board_dt.isoformat(timespec="milliseconds") if board_dt else None,
        price_age_sec=price_age,
        board_age_sec=board_age,
    )
