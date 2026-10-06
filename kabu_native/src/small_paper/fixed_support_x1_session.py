"""Causal one-event Paper executor for the fixed-entry-support candidate.

Owns open positions, CAP, same-symbol, occupancy, support, reconfirm, and exits.
Does not call the whole-session research engine and does not read events that have not arrived.
"""
from __future__ import annotations

import heapq
import json
import os
import statistics
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import numpy as np

from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)
from research.event_time_impulse_fixed_entry_support_candidate_v1 import (
    CANCEL,
    LIVE,
    MAX_CONCURRENT,
    SHARES,
    SUBMIT,
)
from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY, PRIOR_BINS_10, PRIOR_BINS_30
from research.event_time_volume_confirmed_impulse.features import spread_not_worse
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.simple_tech_entry_family.harvest import _board_row

if SUBMIT or CANCEL or LIVE:
    raise RuntimeError("x1_order_path_forbidden")

Admission = Callable[[str, float], tuple[bool, str]]
_EPS = 1e-12
BOUNDARY_CONTRADICTION = "X1_SESSION_BOUNDARY_CONTRADICTION"


def production_session_bounds(day: str, kind: str) -> tuple[str, float, float]:
    """Paper force-close is the only X1 session boundary. Not research 11:30.

    The evaluation open stays 09:00 / 12:30. That is the clock the live executor
    already used. Paper's 09:03 session_start is the pilot start, and applying it
    as the X1 signal open changes the entry stream.
    """
    from research.anchor_timing_robustness.grid import hm_epoch
    from small_paper.am_pm_session_policy import AmPmSessionPolicy

    policy = AmPmSessionPolicy.from_kind(kind)
    if str(policy.session_end) != str(policy.force_close):
        raise RuntimeError(BOUNDARY_CONTRADICTION)
    if policy.kind == "am":
        session, sh, sm = "AM", 9, 0
    else:
        session, sh, sm = "PM", 12, 30
    eh, em = (int(part) for part in str(policy.force_close).split(":"))
    start = float(hm_epoch(str(day), sh, sm))
    end = float(hm_epoch(str(day), eh, em))
    return session, start, end


def _yen(value: float) -> float:
    number = round(float(value), 6)
    nearest = round(number)
    if abs(number - nearest) < 1e-6:
        return float(nearest)
    return number


def _finite_pos(value: float) -> bool:
    return value == value and value > 0.0


def _bid_ok(row: Mapping[str, Any]) -> bool:
    fresh = float(row["fresh_sec"])
    bid = float(row["bid"])
    qty = float(row["bid_qty"])
    return bool(
        row["executable"]
        and not row["special"]
        and fresh == fresh
        and fresh <= FRESH_SEC + _EPS
        and bid == bid
        and bid > 0.0
        and qty == qty
        and qty >= MIN_QTY - _EPS
    )


class _Sym:
    """Per-symbol arrays of events already received in this session."""

    def __init__(self) -> None:
        self.cap = 256
        self.n = 0
        self.t = np.empty(self.cap, dtype=float)
        self.px = np.empty(self.cap, dtype=float)
        self.vol = np.empty(self.cap, dtype=float)
        self.ask = np.empty(self.cap, dtype=float)
        self.bidv = np.empty(self.cap, dtype=float)
        self.tick = np.empty(self.cap, dtype=float)
        self.spread = np.empty(self.cap, dtype=float)
        self.ok = np.empty(self.cap, dtype=bool)
        self.ask_px = np.empty(self.cap, dtype=float)
        self.pre_high = np.empty(self.cap, dtype=float)
        self.previous = np.empty(self.cap, dtype=float)
        self.vol10 = np.empty(self.cap, dtype=bool)
        self.vol30 = np.empty(self.cap, dtype=bool)
        self.buy = np.empty(self.cap, dtype=bool)
        self.tick_accel = np.empty(self.cap, dtype=bool)
        self.price_break = np.empty(self.cap, dtype=bool)
        self.ask10 = np.empty(self.cap, dtype=float)
        self.bid10 = np.empty(self.cap, dtype=float)
        self.classified = np.empty(self.cap, dtype=float)
        self.p_vol = np.zeros(self.cap + 1, dtype=float)
        self.p_ask = np.zeros(self.cap + 1, dtype=float)
        self.p_bid = np.zeros(self.cap + 1, dtype=float)
        self.p_tick = np.zeros(self.cap + 1, dtype=float)
        self.bids: list[tuple[float, float]] = []
        self.signals: list[int] = []
        self.sig_ptr = 0
        self.dq: deque[int] = deque()
        self.last_px = float("nan")
        self.open_episode = False
        self.frozen = float("nan")
        self.predicate_true_n = 0

    def _grow(self) -> None:
        ncap = self.cap * 2
        for name in (
            "t", "px", "vol", "ask", "bidv", "tick", "spread", "ask_px", "pre_high", "previous",
            "ask10", "bid10", "classified",
        ):
            grown = np.empty(ncap, dtype=float)
            grown[: self.n] = getattr(self, name)[: self.n]
            setattr(self, name, grown)
        for name in ("ok", "vol10", "vol30", "buy", "tick_accel", "price_break"):
            grown_b = np.empty(ncap, dtype=bool)
            grown_b[: self.n] = getattr(self, name)[: self.n]
            setattr(self, name, grown_b)
        for name in ("p_vol", "p_ask", "p_bid", "p_tick"):
            grown_p = np.zeros(ncap + 1, dtype=float)
            grown_p[: self.n + 1] = getattr(self, name)[: self.n + 1]
            setattr(self, name, grown_p)
        self.cap = ncap

    def append_raw(self, ev: Mapping[str, Any]) -> int:
        if self.n == self.cap:
            self._grow()
        i = self.n
        price = float(ev["px"])
        finite = _finite_pos(price)
        fresh = bool(ev["quote_ok"])
        self.t[i] = float(ev["t"])
        self.px[i] = price if finite else float("nan")
        self.vol[i] = float(ev["dvol"])
        self.ask[i] = float(ev["ask_vol"])
        self.bidv[i] = float(ev["bid_vol"])
        self.tick[i] = 1.0 if finite else 0.0
        self.spread[i] = (float(ev["ask"]) - float(ev["bid"])) if fresh else float("nan")
        self.ok[i] = fresh
        self.ask_px[i] = float(ev["ask"]) if fresh else float("nan")
        self.p_vol[i + 1] = self.p_vol[i] + self.vol[i]
        self.p_ask[i + 1] = self.p_ask[i] + self.ask[i]
        self.p_bid[i + 1] = self.p_bid[i] + self.bidv[i]
        self.p_tick[i + 1] = self.p_tick[i] + self.tick[i]
        self.n = i + 1
        return i

    def _at(self, prefix: np.ndarray, index: int, left: float, right: float) -> float:
        t = self.t[: self.n]
        t0 = float(t[index])
        lo = int(np.searchsorted(t, t0 - left, side="right"))
        hi = int(np.searchsorted(t, t0 - right, side="right"))
        return float(prefix[hi] - prefix[lo])

    def _median(self, prefix: np.ndarray, index: int, width: float, bins: int) -> float:
        values = [self._at(prefix, index, width * (k + 1), width * k) for k in range(1, bins + 1)]
        return float(np.median(np.asarray(values, dtype=float)))

    def seal_group(self, start: int, session_start: float, *, force_full: bool = False) -> None:
        """Feature rows [start, n) share one timestamp. Uses only rows already stored."""
        if start >= self.n:
            return
        t0 = float(self.t[start])
        while self.dq and float(self.t[self.dq[0]]) < t0 - 60.0:
            self.dq.popleft()
        pre = float(self.px[self.dq[0]]) if self.dq else float("nan")
        for index in range(start, self.n):
            self.pre_high[index] = pre
            self.previous[index] = self.last_px
            price = float(self.px[index])
            if _finite_pos(price):
                self.last_px = price
        for index in range(start, self.n):
            price = float(self.px[index])
            if not _finite_pos(price):
                continue
            while self.dq and float(self.px[self.dq[-1]]) <= price:
                self.dq.pop()
            self.dq.append(index)
        broke_any = False
        for index in range(start, self.n):
            price = float(self.px[index])
            pre_i = float(self.pre_high[index])
            prev_i = float(self.previous[index])
            broke = bool(
                prev_i == prev_i and pre_i == pre_i and price == price and prev_i <= pre_i and price > pre_i
            )
            self.price_break[index] = broke
            broke_any = broke_any or broke
        if not force_full and not broke_any:
            for index in range(start, self.n):
                self.ask10[index] = 0.0
                self.bid10[index] = 0.0
                self.classified[index] = 0.0
                self.vol10[index] = False
                self.vol30[index] = False
                self.buy[index] = False
                self.tick_accel[index] = False
                self._latch(index, False, float(self.px[index]), float(self.pre_high[index]))
            return
        tview = self.t[: self.n]
        spread = self.spread[: self.n]
        ok = self.ok[: self.n]
        for index in range(start, self.n):
            ask10 = self._at(self.p_ask, index, 10.0, 0.0)
            bid10 = self._at(self.p_bid, index, 10.0, 0.0)
            price = float(self.px[index])
            pre_i = float(self.pre_high[index])
            broke = bool(self.price_break[index])
            buy = ask10 > bid10
            classified = ask10 + bid10
            self.ask10[index] = ask10
            self.bid10[index] = bid10
            self.classified[index] = classified
            self.buy[index] = buy
            vol10 = self._at(self.p_vol, index, 10.0, 0.0)
            vol30 = self._at(self.p_vol, index, 30.0, 0.0)
            ticks10 = self._at(self.p_tick, index, 10.0, 0.0)
            ready10 = float(self.t[index]) >= float(session_start) + 70.0 - 1e-9
            ready30 = float(self.t[index]) >= float(session_start) + 150.0 - 1e-9
            vol10_ok = bool(ready10 and vol10 > self._median(self.p_vol, index, 10.0, PRIOR_BINS_10))
            vol30_ok = bool(ready30 and vol30 > self._median(self.p_vol, index, 30.0, PRIOR_BINS_30))
            tick_ok = bool(ready10 and ticks10 > self._median(self.p_tick, index, 10.0, PRIOR_BINS_10))
            self.vol10[index] = vol10_ok
            self.vol30[index] = vol30_ok
            self.tick_accel[index] = tick_ok
            predicate = bool(
                vol10_ok
                and vol30_ok
                and buy
                and classified > 0.0
                and tick_ok
                and broke
                and spread_not_worse(tview, spread, ok, index)
            )
            if predicate:
                self.predicate_true_n += 1
            self._latch(index, predicate, price, pre_i)

    def _latch(self, index: int, predicate: bool, price: float, level: float) -> None:
        finite = _finite_pos(price)
        if not self.open_episode:
            if predicate:
                self.signals.append(index)
                self.open_episode = True
                self.frozen = float(level)
            return
        if finite and price <= float(self.frozen) and not predicate:
            self.open_episode = False


def _discord_delivered(result: Any) -> bool:
    if result is False:
        return False
    final = getattr(result, "final_result", None)
    if final in {"suppressed", "failed", "skipped"}:
        return False
    return True


class FixedSupportX1SessionExecutor:
    """Sole Candidate portfolio owner. Incremental, stateful, one event at a time."""

    execution_family = "X1_IMMEDIATE_ASK"
    session_executor = "FixedSupportX1SessionExecutor"

    def __init__(self) -> None:
        self.activation_id = ""
        self.activation_sha = ""
        self.candidate_name = "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1"
        self.admission: Optional[Admission] = None
        self.notifier: Any = None
        self.ledger_path: Optional[Path] = None
        self.day = ""
        self.session = ""
        self.sess_start = 0.0
        self.sess_end = 0.0
        self._manual = False
        self._closed = False
        self._bucket: list[dict[str, Any]] = []
        self._bucket_t: Optional[float] = None
        self.syms: dict[str, _Sym] = {}
        self.open_pos: dict[str, dict[str, Any]] = {}
        self.traded_today: set[str] = set()
        self.last_cum: dict[str, float] = {}
        self.trades: list[dict[str, Any]] = []
        self.ledger: list[dict[str, Any]] = []
        self.admission_count = 0
        self.signal_seen_n = 0
        self.admission_denied_n = 0
        self.admission_denied_reasons: dict[str, int] = {}
        self.signal_quote_not_ok_n = 0
        self.signal_invalid_ask_n = 0
        self.admission_membership: list[str] = []
        self.admission_membership_source = ""
        self.screening_session_diff = False
        self.position_mutations = 0
        self.support_move_n = 0
        self.discord_failures = 0
        self.discord_attempt_n = 0
        self.discord_success_n = 0
        self.discord_failure_n = 0
        self.ledger_write_attempt_n = 0
        self.ledger_write_success_n = 0
        self.ledger_write_failure_n = 0
        self.counts = {"cap": 0, "same_symbol": 0, "occupancy": 0, "reentry": 0, "session_flat": 0}
        self._seen_events = 0
        self._board_names: dict[str, str] = {}
        self._universe_names: dict[str, str] = {}
        self.executor_session_boundary = 0.0
        self.runtime_session_boundary = 0.0
        self.final_summary_ok = False
        self.boundary_error = ""

    def start_session(
        self,
        *,
        day: str,
        session: str,
        sess_start: float,
        sess_end: float,
        admission: Optional[Admission] = None,
        ledger_dir: Optional[Path] = None,
        notifier: Any = None,
        activation_id: str = "",
        activation_sha: str = "",
    ) -> None:
        if day != self.day:
            self.traded_today = set()
            self.last_cum = {}
            self.day = str(day)
        self.session = str(session)
        self.sess_start = float(sess_start)
        self.sess_end = float(sess_end)
        self._manual = True
        self._closed = False
        self._bucket = []
        self._bucket_t = None
        self.syms = {}
        self.open_pos = {}
        if admission is not None:
            self.admission = admission
        if notifier is not None:
            self.notifier = notifier
        if activation_id:
            self.activation_id = activation_id
        if activation_sha:
            self.activation_sha = activation_sha
        if ledger_dir is not None:
            path = Path(ledger_dir)
            path.mkdir(parents=True, exist_ok=True)
            self.ledger_path = path / "fixed_support_x1_paper_ledger.jsonl"

    def on_market_event(self, event: Mapping[str, Any]) -> None:
        ev = self._normalize(event)
        if ev is None:
            return
        self._seen_events += 1
        name = str(ev.get("symbol_name") or "").strip()
        if name and ev.get("symbol"):
            self._board_names[str(ev["symbol"])] = name
        if not self._manual:
            self._ensure_clock(float(ev["t"]))
        if not self.session or self._closed:
            self._note_cum(ev)
            return
        self._note_cum(ev)
        if float(ev["t"]) > self.sess_end + _EPS:
            self._flush_bucket()
            self._close_positions()
            self._closed = True
            return
        if self._bucket_t is not None and float(ev["t"]) > float(self._bucket_t) + _EPS:
            self._flush_bucket()
        if self._bucket_t is None:
            self._bucket_t = float(ev["t"])
        self._bucket.append(ev)

    def bind_production_schedule(self, *, day: str, kind: str) -> None:
        """Adopt the Paper session schedule. Close boundary must match runtime."""
        session, start, end = production_session_bounds(day, kind)
        self.start_session(day=str(day), session=session, sess_start=start, sess_end=end)
        self._manual = True
        self.executor_session_boundary = float(end)
        self.runtime_session_boundary = float(end)
        self._assert_boundary()

    def _assert_boundary(self) -> None:
        if abs(float(self.executor_session_boundary) - float(self.runtime_session_boundary)) > _EPS:
            self.boundary_error = BOUNDARY_CONTRADICTION
            raise RuntimeError(BOUNDARY_CONTRADICTION)
        if self.session and abs(float(self.sess_end) - float(self.runtime_session_boundary)) > _EPS:
            self.boundary_error = BOUNDARY_CONTRADICTION
            raise RuntimeError(BOUNDARY_CONTRADICTION)

    def close_at_runtime_boundary(self, *, day: str, kind: str) -> dict[str, Any]:
        """Flat the book at the Paper force-close. No later market event is required."""
        session, start, end = production_session_bounds(day, kind)
        self.runtime_session_boundary = float(end)
        if not self.session:
            self.start_session(day=str(day), session=session, sess_start=start, sess_end=end)
            self._manual = True
        self.executor_session_boundary = float(self.sess_end)
        self._assert_boundary()
        if not self._closed:
            self.on_session_boundary()
        self.flush_ledger()
        perf = self.x1_performance()
        self.final_summary_ok = bool(perf["final_ok"])
        return perf

    def on_session_boundary(self, *_args: Any, **_kwargs: Any) -> None:
        if self._closed:
            return
        self._flush_bucket()
        self._close_positions()
        self._closed = True
        self.flush_ledger()

    def close_session(self) -> dict[str, Any]:
        self.on_session_boundary()
        return self.snapshot_status()

    def snapshot_status(self) -> dict[str, Any]:
        return {
            "execution_family": self.execution_family,
            "session_executor": self.session_executor,
            "activation_id": self.activation_id,
            "candidate_name": self.candidate_name,
            "open_n": len(self.open_pos),
            "trade_n": len(self.trades),
            "admission_count": self.admission_count,
            "position_mutations": self.position_mutations,
            "support_move_n": self.support_move_n,
            "discord_failures": self.discord_failures,
            "portfolio_owner": self.session_executor,
            "portfolio_owner_class": type(self).__name__,
            "session_owner_class": type(self).__name__,
            "admission_owner_class": type(self).__name__,
            "portfolio_owner_count": 1,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "future_access": False,
        }

    def _normalize(self, event: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        if "px" in event and "t" in event and "bid" in event:
            out = dict(event)
            out["symbol"] = str(out.get("symbol") or "")
            out["t"] = float(out["t"])
            out.setdefault("executable", True)
            out.setdefault("special", False)
            out.setdefault("fresh_sec", 0.0)
            out.setdefault("continuous", True)
            out.setdefault("bid_qty", 100.0)
            out.setdefault("ask_qty", 100.0)
            out.setdefault("cum_vol", None)
            return out
        payload = dict(event.get("payload") or {})
        received = event.get("received_at") or event.get("t0_push_received_at")
        if received and not payload.get("received_at"):
            payload["received_at"] = received
        when = event.get("t")
        if when is None:
            from research.anchor_vs_event_driven.run_comparison import capture_event_epoch

            when = capture_event_epoch({"payload": payload, "received_at": received}, payload)
        if when is None:
            return None
        row = _board_row(payload, float(when))
        if not row:
            return None
        row = dict(row)
        row["symbol"] = str(event.get("symbol") or "")
        row["t"] = float(when)
        board_name = str(payload.get("SymbolName") or payload.get("symbol_name") or "").strip()
        if board_name:
            row["symbol_name"] = board_name
        return row

    def _note_cum(self, ev: dict[str, Any]) -> None:
        cum = ev.get("cum_vol")
        dvol = 0.0
        if cum is not None and cum == cum and float(cum) >= 0.0:
            prev = self.last_cum.get(ev["symbol"])
            if prev is not None and float(cum) >= float(prev):
                dvol = float(cum) - float(prev)
            self.last_cum[ev["symbol"]] = float(cum)
        ev["dvol"] = dvol
        price = float(ev["px"])
        ask_vol, bid_vol = _classify(price, float(ev["bid"]), float(ev["ask"]), dvol)
        ev["ask_vol"] = ask_vol
        ev["bid_vol"] = bid_vol
        ev["quote_ok"] = _quote_ok(ev)

    def _sym(self, symbol: str) -> _Sym:
        st = self.syms.get(symbol)
        if st is None:
            st = _Sym()
            self.syms[symbol] = st
        return st

    def _flush_bucket(self) -> None:
        events = self._bucket
        self._bucket = []
        self._bucket_t = None
        if not events:
            return
        flush_t = float(events[-1]["t"])
        added: dict[str, int] = {}
        pending_before = [
            sym
            for sym, pos in self.open_pos.items()
            if pos.get("exiting") and pos.get("pending_reason") and not pos.get("fill_scheduled")
        ]
        for ev in events:
            st = self._sym(str(ev["symbol"]))
            if _bid_ok(ev) and float(ev["t"]) <= self.sess_end + _EPS:
                st.bids.append((float(ev["t"]), float(ev["bid"])))
            if not ev.get("continuous", True):
                continue
            if not (self.sess_start - _EPS <= float(ev["t"]) < self.sess_end):
                continue
            if str(ev["symbol"]) not in added:
                added[str(ev["symbol"])] = st.n
            st.append_raw(ev)
        for sym, start in added.items():
            pos = self.open_pos.get(sym)
            st = self.syms[sym]
            force = bool(
                pos is not None
                and not pos.get("exiting")
                and start <= int(pos["next_index"]) < st.n
            )
            st.seal_group(start, self.sess_start, force_full=force)
        heap: list[tuple] = []
        for sym in pending_before:
            self._arm_pending_fill(sym, flush_t, heap)
        for sym, start in added.items():
            st = self.syms[sym]
            pos = self.open_pos.get(sym)
            if (
                pos is not None
                and not pos.get("exiting")
                and int(pos["next_index"]) < st.n
                and float(st.t[int(pos["next_index"])]) <= flush_t + _EPS
            ):
                index = int(pos["next_index"])
                heapq.heappush(heap, (float(st.t[index]), 0, sym, "PATH", index))
            if st.sig_ptr < len(st.signals):
                index = int(st.signals[st.sig_ptr])
                if float(st.t[index]) <= flush_t + _EPS:
                    heapq.heappush(heap, (float(st.t[index]), 2, sym, "SIG", index))
        self._drain(heap, flush_t)

    def _drain(self, heap: list[tuple], flush_t: float) -> None:
        while heap:
            event_t, _pri, sym, kind, index = heapq.heappop(heap)
            if float(event_t) > flush_t + _EPS:
                continue
            if kind == "FILL":
                self._on_fill(sym, index)
                continue
            if kind == "PATH":
                self._on_path(sym, int(index), float(event_t), heap, flush_t)
                continue
            self._on_signal(sym, int(index), float(event_t), heap, flush_t)

    def _on_fill(self, sym: str, token: Any) -> None:
        pos = self.open_pos.get(sym)
        if pos is None or pos.get("fill_token") != token:
            return
        self._release(sym, str(pos["exit_reason"]), float(pos["exit_t"]), float(pos["exit_px"]))

    def _on_path(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        pos = self.open_pos.get(sym)
        st = self.syms[sym]
        if pos is None or pos.get("exiting") or int(pos["next_index"]) != index:
            return
        price = float(st.px[index])
        if not _finite_pos(price):
            found = self._first_bid(sym, event_t)
            if found is None:
                pos["invalid_pending"] = True
            else:
                self._schedule_exit(pos, sym, index, "FAIL_CLOSE_INVALID_DATA", event_t, found, heap)
            return
        if reconfirm(
            bool(st.vol10[index]),
            bool(st.vol30[index]),
            bool(st.buy[index]),
            float(st.classified[index]),
            bool(st.tick_accel[index]),
            bool(st.price_break[index]),
        ):
            pos["last_reconfirm_t"] = event_t
            observed = ratchet(float(st.pre_high[index]), float(pos["observed_support"]))
            if observed > float(pos["observed_support"]):
                pos["observed_support"] = float(observed)
                pos["reconfirm_raises"] += 1
            if float(pos["support"]) != float(pos["initial_support"]):
                self.support_move_n += 1
        reason = None
        if support_failure(price, float(pos["support"])):
            reason = "BREAK_SUPPORT_FAILURE"
        elif exhausted(
            participation_lost(float(st.classified[index]), float(st.ask10[index]), float(st.bid10[index])),
            activity_lost(bool(st.vol10[index]), bool(st.vol30[index]), bool(st.tick_accel[index])),
            event_t - float(pos["last_reconfirm_t"]),
        ):
            reason = "IMPULSE_EXHAUSTED"
        if reason is not None:
            found = self._first_bid(sym, event_t)
            if found is None:
                pos["pending_reason"] = reason
                pos["exiting"] = True
                pos["exit_trigger_t"] = event_t
            else:
                self._schedule_exit(pos, sym, index, reason, event_t, found, heap)
            return
        nxt = index + 1
        if nxt < st.n and float(st.t[nxt]) <= flush_t + _EPS:
            pos["next_index"] = nxt
            heapq.heappush(heap, (float(st.t[nxt]), 0, sym, "PATH", nxt))
        else:
            pos["next_index"] = nxt

    def _schedule_exit(self, pos: dict[str, Any], sym: str, index: int, reason: str, event_t: float, found: tuple[float, float], heap: list[tuple]) -> None:
        pos["exiting"] = True
        pos["exit_reason"] = reason
        pos["exit_trigger_t"] = event_t
        pos["exit_t"], pos["exit_px"] = found
        pos["fill_token"] = index
        pos["fill_scheduled"] = True
        heapq.heappush(heap, (float(found[0]), 1, sym, "FILL", index))

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        st = self.syms[sym]
        if st.sig_ptr >= len(st.signals) or int(st.signals[st.sig_ptr]) != index:
            return
        st.sig_ptr += 1
        self.signal_seen_n += 1
        if st.sig_ptr < len(st.signals):
            nxt = int(st.signals[st.sig_ptr])
            if float(st.t[nxt]) <= flush_t + _EPS:
                heapq.heappush(heap, (float(st.t[nxt]), 2, sym, "SIG", nxt))
        if self.admission is not None:
            allowed, reason = self.admission(sym, event_t)
            if not allowed:
                self.admission_denied_n += 1
                key = str(reason or "FAIL_CLOSED")
                self.admission_denied_reasons[key] = int(self.admission_denied_reasons.get(key) or 0) + 1
                return
        if not bool(st.ok[index]):
            self.signal_quote_not_ok_n += 1
            return
        pos = self.open_pos.get(sym)
        if pos is not None:
            if pos.get("exiting"):
                self.counts["occupancy"] += 1
            else:
                self.counts["same_symbol"] += 1
            return
        if len(self.open_pos) >= MAX_CONCURRENT:
            self.counts["cap"] += 1
            return
        ask = float(st.ask_px[index])
        pre = float(st.pre_high[index])
        if not (_finite_pos(ask) and pre == pre):
            self.signal_invalid_ask_n += 1
            return
        is_reentry = sym in self.traded_today
        if is_reentry:
            self.counts["reentry"] += 1
        self.traded_today.add(sym)
        self.open_pos[sym] = {
            "signal_index": int(index),
            "entry_t": event_t,
            "entry_px": ask,
            "support": pre,
            "initial_support": pre,
            "observed_support": pre,
            "last_reconfirm_t": event_t,
            "reconfirm_raises": 0,
            "reentry": is_reentry,
            "next_index": int(index) + 1,
            "exiting": False,
            "signal_uid": f"{self.day}|{self.session}|{sym}|{int(index)}",
        }
        self.admission_count += 1
        self.position_mutations += 1
        if self._ledger_entry(sym):
            self._notify_entry(sym)
        nxt = int(index) + 1
        if nxt < st.n and float(st.t[nxt]) <= flush_t + _EPS:
            heapq.heappush(heap, (float(st.t[nxt]), 0, sym, "PATH", nxt))

    def _arm_pending_fill(self, sym: str, flush_t: float, heap: list[tuple]) -> None:
        pos = self.open_pos.get(sym)
        if pos is None or pos.get("fill_scheduled"):
            return
        trigger = float(pos.get("exit_trigger_t") or pos.get("entry_t") or 0.0)
        found = self._first_bid(sym, trigger)
        if found is None or float(found[0]) > flush_t + _EPS:
            return
        reason = str(pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT")
        token = pos.get("fill_token", trigger)
        pos["fill_token"] = token
        self._schedule_exit(pos, sym, token, reason, trigger, found, heap)

    def _first_bid(self, sym: str, target: float) -> Optional[tuple[float, float]]:
        if target > self.sess_end + _EPS:
            return None
        bids = self._sym(sym).bids
        lo = 0
        hi = len(bids)
        while lo < hi:
            mid = (lo + hi) // 2
            if float(bids[mid][0]) < target - _EPS:
                lo = mid + 1
            else:
                hi = mid
        if lo >= len(bids):
            return None
        event_t, price = bids[lo]
        if float(event_t) > self.sess_end + _EPS or not _finite_pos(float(price)):
            return None
        return float(event_t), float(price)

    def _last_bid(self, sym: str, entry_t: float) -> Optional[tuple[float, float]]:
        found = None
        for event_t, price in self._sym(sym).bids:
            if float(event_t) + _EPS < float(entry_t):
                continue
            if float(event_t) > self.sess_end + _EPS:
                break
            if _finite_pos(float(price)):
                found = (float(event_t), float(price))
        return found

    def _close_positions(self) -> None:
        for sym in list(self.open_pos):
            pos = self.open_pos[sym]
            found = self._last_bid(sym, float(pos["entry_t"]))
            if found is None:
                self.final_summary_ok = False
                continue
            if pos.get("invalid_pending") and not pos.get("pending_reason") and not pos.get("exit_reason"):
                reason = "FAIL_CLOSE_INVALID_DATA"
            else:
                reason = str(pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT")
            if "exit_trigger_t" not in pos:
                pos["exit_trigger_t"] = float(self.sess_end)
            self._release(sym, reason, found[0], found[1])

    def remember_universe_names(self, meta: Mapping[str, Any]) -> None:
        """Display-only names from a universe file already loaded for the day."""
        for symbol, row in meta.items():
            if isinstance(row, Mapping):
                name = str(row.get("symbol_name") or row.get("SymbolName") or row.get("name") or "").strip()
            else:
                name = str(row or "").strip()
            if name:
                self._universe_names[str(symbol)] = name

    def _name_hint(self, symbol: str) -> dict[str, str]:
        keys = [str(symbol or "")]
        if keys[0].endswith(".T"):
            keys.append(keys[0][:-2])
        elif keys[0]:
            keys.append(keys[0] + ".T")
        board = ""
        universe = ""
        for key in keys:
            if not board:
                board = str(self._board_names.get(key) or "").strip()
            if not universe:
                universe = str(self._universe_names.get(key) or "").strip()
        return {"symbol_name_board": board, "symbol_name_universe": universe}

    def _release(self, sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        pos = self.open_pos[sym]
        if float(pos["support"]) != float(pos["initial_support"]):
            self.support_move_n += 1
            raise RuntimeError("active_support_moved")
        cap_before = len(self.open_pos)
        cap_after = cap_before - 1
        occupancy_after = sorted(name for name in self.open_pos if name != sym)
        if reason == "SESSION_FLAT":
            self.counts["session_flat"] += 1
        trade = {
            "date": self.day,
            "session": self.session,
            "symbol": sym,
            "signal_index": int(pos["signal_index"]),
            "signal_uid": pos["signal_uid"],
            "entry_t": float(pos["entry_t"]),
            "exit_t": float(exit_t),
            "entry_px": float(pos["entry_px"]),
            "exit_px": float(exit_px),
            "reason": reason,
            "pnl_yen": (float(exit_px) - float(pos["entry_px"])) * SHARES,
            "qty": SHARES,
            "reconfirm_raises": int(pos["reconfirm_raises"]),
            "initial_support": float(pos["initial_support"]),
            "final_support": float(pos["support"]),
            "reentry": bool(pos["reentry"]),
            "exit_trigger_t": float(pos.get("exit_trigger_t") or exit_t),
            "cap_before_release": cap_before,
            "cap_after_release": cap_after,
            "cap_max": MAX_CONCURRENT,
            "hold_sec": float(exit_t) - float(pos["entry_t"]),
            **self._name_hint(sym),
        }
        self.trades.append(trade)
        self.position_mutations += 1
        wrote = self._ledger_exit(trade, cap_state=cap_after, occupancy=occupancy_after)
        self.open_pos.pop(sym, None)
        if wrote:
            self._notify_exit(trade)

    def _base_ledger(self, sym: str, pos: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "activation_id": self.activation_id,
            "candidate_name": self.candidate_name,
            "execution_family": self.execution_family,
            "signal_uid": pos["signal_uid"],
            "symbol": sym,
            "signal_timestamp": float(pos["entry_t"]),
            "fill_timestamp": float(pos["entry_t"]),
            "fill_price": float(pos["entry_px"]),
            "entry_pre_break_high": float(pos["initial_support"]),
            "active_support_level": float(pos["support"]),
            "reconfirm_count": int(pos["reconfirm_raises"]),
            "cap_state": len(self.open_pos),
            "occupancy_state": sorted(self.open_pos),
            "reentry": bool(pos["reentry"]),
            "support_move_n": self.support_move_n,
        }

    def _write_ledger(self, row: Mapping[str, Any]) -> bool:
        self.ledger.append(dict(row))
        self.ledger_write_attempt_n += 1
        if self.ledger_path is None:
            self.ledger_write_failure_n += 1
            return False
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            self.ledger_write_success_n += 1
            return True
        except Exception:
            self.ledger_write_failure_n += 1
            return False

    def flush_ledger(self) -> None:
        if self.ledger_path is None:
            return
        try:
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.ledger_write_failure_n += 1

    def _ledger_entry(self, sym: str) -> bool:
        pos = self.open_pos[sym]
        return self._write_ledger({**self._base_ledger(sym, pos), "event": "ENTRY", "slot_release": False})

    def _ledger_exit(
        self,
        trade: Mapping[str, Any],
        *,
        cap_state: Optional[int] = None,
        occupancy: Optional[list[str]] = None,
    ) -> bool:
        return self._write_ledger(
            {
                "event": "EXIT",
                "activation_id": self.activation_id,
                "candidate_name": self.candidate_name,
                "execution_family": self.execution_family,
                "signal_uid": trade["signal_uid"],
                "symbol": trade["symbol"],
                "signal_timestamp": float(trade["entry_t"]),
                "fill_timestamp": float(trade["entry_t"]),
                "fill_price": float(trade["entry_px"]),
                "entry_pre_break_high": float(trade["initial_support"]),
                "active_support_level": float(trade["final_support"]),
                "reconfirm_count": int(trade["reconfirm_raises"]),
                "exit_trigger_timestamp": float(trade["exit_trigger_t"]),
                "exit_fill_timestamp": float(trade["exit_t"]),
                "exit_price": float(trade["exit_px"]),
                "exit_reason": trade["reason"],
                "pnl_yen": float(trade["pnl_yen"]),
                "cap_state": len(self.open_pos) if cap_state is None else int(cap_state),
                "occupancy_state": sorted(self.open_pos) if occupancy is None else list(occupancy),
                "slot_release": True,
                "reentry": bool(trade["reentry"]),
                "support_move_n": self.support_move_n,
            }
        )

    def _notify_entry(self, sym: str) -> None:
        if self.notifier is None:
            return
        pos = self.open_pos.get(sym)
        if pos is None:
            return
        self.discord_attempt_n += 1
        try:
            result = self.notifier.notify_entry(
                event={
                    "symbol": sym,
                    "entry_price": pos["entry_px"],
                    "signal_uid": pos["signal_uid"],
                    "qty": SHARES,
                    "support": pos["support"],
                    "cap_before": len(self.open_pos) - 1,
                    "cap_after": len(self.open_pos),
                    "cap_max": MAX_CONCURRENT,
                    **self._name_hint(sym),
                },
                payload={"CurrentPrice": pos["entry_px"]},
                open_slots=len(self.open_pos),
                session_bucket=self.session,
                x1_activation_id=self.activation_id,
                x1_execution_family=self.execution_family,
                x1_source=self.session_executor,
            )
            if _discord_delivered(result):
                self.discord_success_n += 1
            else:
                self.discord_failures += 1
                self.discord_failure_n += 1
        except Exception:
            self.discord_failures += 1
            self.discord_failure_n += 1

    def _notify_exit(self, trade: Mapping[str, Any]) -> None:
        if self.notifier is None:
            return
        self.discord_attempt_n += 1
        try:
            result = self.notifier.notify_exit(
                context={
                    "is_structural_exit": True,
                    "symbol": trade["symbol"],
                    "exit_reason": trade["reason"],
                    "current_price": trade["exit_px"],
                    "exit_price": trade["exit_px"],
                    "entry_price": trade["entry_px"],
                    "qty": trade.get("qty", SHARES),
                    "realized_pnl_yen": trade["pnl_yen"],
                    "realized_pnl_pct": 0.0,
                    "entry_t": trade["entry_t"],
                    "exit_t": trade["exit_t"],
                    "hold_sec": trade.get("hold_sec", float(trade["exit_t"]) - float(trade["entry_t"])),
                    "cap_before_release": trade.get("cap_before_release"),
                    "cap_after_release": trade.get("cap_after_release"),
                    "cap_max": trade.get("cap_max", MAX_CONCURRENT),
                    "symbol_name_board": trade.get("symbol_name_board") or "",
                    "symbol_name_universe": trade.get("symbol_name_universe") or "",
                    "activation_id": self.activation_id,
                    "execution_family": self.execution_family,
                    "source": self.session_executor,
                }
            )
            if _discord_delivered(result):
                self.discord_success_n += 1
            else:
                self.discord_failures += 1
                self.discord_failure_n += 1
        except Exception:
            self.discord_failures += 1
            self.discord_failure_n += 1

    def x1_counters(self) -> dict[str, Any]:
        """Live X1 book. These fields are not PBv2 or V1R counters."""
        entries = sum(1 for row in self.ledger if row.get("event") == "ENTRY")
        exits = sum(1 for row in self.ledger if row.get("event") == "EXIT")
        reconfirm = sum(int(pos.get("reconfirm_raises") or 0) for pos in self.open_pos.values())
        reconfirm += sum(int(trade.get("reconfirm_raises") or 0) for trade in self.trades)
        return {
            "executor_instance_id": id(self),
            "x1_market_event_n": int(self._seen_events),
            "x1_predicate_true_n": sum(int(st.predicate_true_n) for st in self.syms.values()),
            "x1_full_latched_n": sum(len(st.signals) for st in self.syms.values()),
            "x1_admission_n": int(self.admission_count),
            "x1_signal_seen_n": int(self.signal_seen_n),
            "x1_admission_denied_n": int(self.admission_denied_n),
            "x1_admission_denied_reasons": dict(self.admission_denied_reasons),
            "x1_signal_quote_not_ok_n": int(self.signal_quote_not_ok_n),
            "x1_signal_invalid_ask_n": int(self.signal_invalid_ask_n),
            "x1_admission_membership_n": len(self.admission_membership),
            "x1_admission_membership_source": str(self.admission_membership_source or ""),
            "x1_screening_session_diff": bool(self.screening_session_diff),
            "x1_entry_n": entries,
            "x1_open_n": len(self.open_pos),
            "x1_exit_pending_n": sum(1 for pos in self.open_pos.values() if pos.get("exiting")),
            "x1_exit_n": exits,
            "x1_slot_release_n": sum(1 for row in self.ledger if row.get("slot_release") is True),
            "x1_reconfirm_n": reconfirm,
            "x1_cap_blocked_n": int(self.counts.get("cap") or 0),
            "x1_same_symbol_blocked_n": int(self.counts.get("same_symbol") or 0),
            "x1_session_flat_n": int(self.counts.get("session_flat") or 0),
            "ledger_path": None if self.ledger_path is None else str(self.ledger_path),
            "ledger_write_attempt_n": int(self.ledger_write_attempt_n),
            "ledger_write_success_n": int(self.ledger_write_success_n),
            "ledger_write_failure_n": int(self.ledger_write_failure_n),
            "discord_attempt_n": int(self.discord_attempt_n),
            "discord_success_n": int(self.discord_success_n),
            "discord_failure_n": int(self.discord_failure_n),
            "notifier_bound": self.notifier is not None,
        }

    def x1_performance(self) -> dict[str, Any]:
        """Realized book after ledger rows. OPEN positions are not realized."""
        counters = self.x1_counters()
        pnls = [_yen(float(trade["pnl_yen"])) for trade in self.trades]
        gross_profit = _yen(sum(pnl for pnl in pnls if pnl > 0))
        gross_loss = _yen(sum(pnl for pnl in pnls if pnl < 0))
        net = _yen(sum(pnls))
        wins = sum(1 for pnl in pnls if pnl > 0)
        losses = sum(1 for pnl in pnls if pnl < 0)
        draws = sum(1 for pnl in pnls if pnl == 0)
        reasons: dict[str, dict[str, float]] = {}
        for reason in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT"):
            rows = [trade for trade in self.trades if trade.get("reason") == reason]
            reasons[reason] = {
                "n": float(len(rows)),
                "pnl_yen": _yen(sum(float(trade["pnl_yen"]) for trade in rows)),
            }
        final_ok = (
            int(counters["x1_entry_n"]) == int(counters["x1_exit_n"])
            and int(counters["x1_open_n"]) == 0
            and int(counters["x1_exit_pending_n"]) == 0
            and not self.boundary_error
        )
        pf = None if gross_loss >= 0 else gross_profit / abs(gross_loss)
        return {
            "trades_n": len(self.trades),
            "entry_n": int(counters["x1_entry_n"]),
            "exit_n": int(counters["x1_exit_n"]),
            "open_n": int(counters["x1_open_n"]),
            "exit_pending_n": int(counters["x1_exit_pending_n"]),
            "net_pnl_yen": net,
            "gross_profit_yen": gross_profit,
            "gross_loss_yen": gross_loss,
            "pf": pf,
            "win_n": wins,
            "loss_n": losses,
            "draw_n": draws,
            "avg_pnl_yen": 0.0 if not pnls else _yen(net / len(pnls)),
            "median_pnl_yen": 0.0 if not pnls else _yen(float(statistics.median(pnls))),
            "exit_reasons": reasons,
            "cap_blocked_n": int(counters["x1_cap_blocked_n"]),
            "same_symbol_blocked_n": int(counters["x1_same_symbol_blocked_n"]),
            "slot_release_n": int(counters["x1_slot_release_n"]),
            "session_flat_n": int(counters["x1_session_flat_n"]),
            "activation_id": self.activation_id,
            "execution_family": self.execution_family,
            "submit_cancel_live": "0/0/0",
            "final_ok": final_ok,
            "executor_session_boundary": float(self.executor_session_boundary),
            "runtime_session_boundary": float(self.runtime_session_boundary),
        }

    def _ensure_clock(self, event_t: float) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        stamp = datetime.fromtimestamp(float(event_t), ZoneInfo("Asia/Tokyo"))
        day = stamp.strftime("%Y%m%d")
        am_name, am0, am1 = production_session_bounds(day, "am")
        pm_name, pm0, pm1 = production_session_bounds(day, "pm")
        if am0 <= event_t < am1:
            name, start, end = am_name, am0, am1
        elif pm0 <= event_t < pm1:
            name, start, end = pm_name, pm0, pm1
        else:
            if self.session and event_t >= self.sess_end and not self._closed:
                self.on_session_boundary()
            return
        if self.day == day and self.session == name and not self._closed:
            return
        if self.session and not self._closed:
            self.on_session_boundary()
        manual = self._manual
        self.start_session(day=day, session=name, sess_start=start, sess_end=end)
        self.executor_session_boundary = float(end)
        self.runtime_session_boundary = float(end)
        self._manual = manual
