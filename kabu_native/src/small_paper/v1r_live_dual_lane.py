"""V1R EXIT V2 live dual-lane runtime — independent Arch E Primary + FIXED600 Control.

Reuses research SoT (guards / continuation / policy). Does not own WebSocket;
consumes the same PUSH payloads already delivered by PaperMarketBusBridge /
pilot_runner._process_push_payload.

Symbol identity: canonical_symbol_key() is the sole book key (bare code, no .T).
Matches research / Capture / V1R-native ENTRY SoT.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np

from small_paper.v1r_exit_v2_contract import (
    EXIT_V2_CANDIDATE_SHA,
    FROZEN_CONTINUATION,
    FROZEN_GUARD,
    apply_arch_e_to_bundle,
    apply_fixed600_to_bundle,
)
from small_paper.v1r_exit_v2_activation_gate import (
    CONTINUATION_ID,
    GUARD_ID,
    PRIMARY_STRATEGY,
    STRATEGY_SHA,
)

JST = ZoneInfo("Asia/Tokyo")
ENV_FLAG = "V1R_EXIT_V2_LIVE_PRIMARY"
MIN_BUY1_QTY = 100.0
BOARD_FRESH_SEC = 5.0
LOT_QTY = 100
POSITION_CAP = 5


def live_primary_enabled(environ: Optional[dict] = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_FLAG, "") or "").strip().lower() in ("1", "true", "yes", "on")


def canonical_symbol_key(symbol: Any) -> str:
    """Sole dual-lane / V1R occupancy symbol key: bare code without .T suffix.

    Research boards, Capture ingress, and V1R-native ENTRY all use bare codes.
    Pilot PUSH may present '6098.T'; both must map to the same book key.
    """
    s = str(symbol or "").strip().upper()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _now() -> datetime:
    return datetime.now(JST)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


Mappingish = Any
ErrorSink = Callable[[dict[str, Any]], None]


def extract_buy1(payload: Mappingish) -> tuple[Optional[float], Optional[float]]:
    b1 = payload.get("Buy1") if isinstance(payload, dict) else None
    if isinstance(b1, dict):
        return _f(b1.get("Price")), _f(b1.get("Qty"))
    return _f(payload.get("BidPrice")), _f(payload.get("BidQty"))


def extract_sell1(payload: Mappingish) -> tuple[Optional[float], Optional[float]]:
    s1 = payload.get("Sell1") if isinstance(payload, dict) else None
    if isinstance(s1, dict):
        return _f(s1.get("Price")), _f(s1.get("Qty"))
    return _f(payload.get("AskPrice")), _f(payload.get("AskQty"))


def imbalance_from_qty(bid_qty: Optional[float], ask_qty: Optional[float]) -> Optional[float]:
    if bid_qty is None or ask_qty is None:
        return None
    den = float(bid_qty) + float(ask_qty)
    if den <= 0:
        return None
    return (float(bid_qty) - float(ask_qty)) / den


@dataclass
class LanePosition:
    symbol: str
    lane: str  # primary | control
    fill_time: float
    fill_price: float
    fill_iso: str
    session: str = "AM"
    date: str = ""
    closed: bool = False
    exit_reason: str = ""
    exit_time: float = 0.0
    exit_price: float = 0.0
    triggered_guard: bool = False
    extended: bool = False
    # decision trace flags (once each)
    traced_guard_trigger: bool = False
    traced_600_decision: bool = False
    traced_extend_750: bool = False
    traced_exit_trigger: bool = False
    traced_control_600: bool = False
    last_tick_match_trace_t: float = 0.0
    # board series since fill
    t: list[float] = field(default_factory=list)
    bid: list[float] = field(default_factory=list)
    ask: list[float] = field(default_factory=list)
    bid_qty: list[float] = field(default_factory=list)
    ask_qty: list[float] = field(default_factory=list)
    special: list[bool] = field(default_factory=list)
    fresh_sec: list[float] = field(default_factory=list)
    mid: list[float] = field(default_factory=list)
    fill_snapshot: dict[str, Any] = field(default_factory=dict)
    exact_cache: Any = field(default=None, repr=False, compare=False)


def session_end_for_position(*, date: str, session: str, fill_time: float) -> float:
    """Frozen V1R session-end clock (AM 11:30 / PM 15:00 JST). Not PBv2 11:25/15:23."""
    from research.e1_x22_actual_exit_factory.paths import session_end_epoch

    day = str(date or "").strip()
    if len(day) != 8 or not day.isdigit():
        day = datetime.fromtimestamp(float(fill_time), JST).strftime("%Y%m%d")
    sess = session if session in ("AM", "PM") else "AM"
    return float(session_end_epoch(day, sess))


def session_event_epoch(*, environ: Optional[dict[str, str]] = None) -> float:
    """Event time for Frozen session-end sweeps.

    Live production: wall clock (same calendar as the trading session).
    Certification replay: domain-B now_jst() so an off-day wall clock cannot
    SESSION_CLOSE positions whose AM 11:30 / PM 15:00 is still in the future
    on the replay tape.
    """
    from small_paper.runtime_clock import now_jst, session_clock_enabled

    if session_clock_enabled(environ=environ):
        return float(now_jst(environ=environ).timestamp())
    return float(time.time())


@dataclass
class DualLaneStats:
    primary_fills: int = 0
    control_fills: int = 0
    primary_exits: int = 0
    control_exits: int = 0
    guard_triggers: int = 0
    exit_600: int = 0
    extend_750: int = 0
    session_close: int = 0
    primary_capacity_block: int = 0
    control_capacity_block: int = 0
    ticks: int = 0
    tick_matches: int = 0
    lookup_miss_with_open: int = 0
    exceptions: int = 0
    last_seq: int = 0
    last_push_at: str = ""
    last_event_t: float = 0.0
    last_event_wall: float = 0.0
    publisher_last_seq: int = 0
    prev_seq_lag: int = 0
    prev_event_lag_sec: float = 0.0
    max_event_lag_sec: float = 0.0
    max_seq_lag: int = 0
    backlog_direction: str = "unknown"
    exact_cache_fallback: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    guard_incremental_update: int = 0
    path_materialization: int = 0
    consumer_ack_seq: int = 0
    state: str = "INIT"  # WAITING_MARKET | RUNNING | STOPPING | STOPPED | FAIL_CLOSED


class V1RLiveDualLane:
    """Independent occupancy for Arch E Primary and FIXED600 Control."""

    def __init__(self, *, trace_dir: Optional[Path] = None, position_cap: int = POSITION_CAP) -> None:
        self.cap = int(position_cap)
        self.primary: dict[str, LanePosition] = {}
        self.control: dict[str, LanePosition] = {}
        self.stats = DualLaneStats(state="WAITING_MARKET")
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.traces: list[dict[str, Any]] = []
        self.error_sink: Optional[ErrorSink] = None
        self.fail_closed: bool = False
        self.fail_reason: str = ""
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

    def bind_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def set_error_sink(self, sink: Optional[ErrorSink]) -> None:
        self.error_sink = sink

    def open_n(self, lane: str) -> int:
        book = self.primary if lane == "primary" else self.control
        return sum(1 for p in book.values() if not p.closed)

    def open_keys(self, lane: str) -> list[str]:
        book = self.primary if lane == "primary" else self.control
        return sorted(s for s, p in book.items() if not p.closed)

    def identity(self) -> dict[str, Any]:
        return {
            "primary_strategy": PRIMARY_STRATEGY,
            "strategy_sha": STRATEGY_SHA,
            "exit_candidate_sha": EXIT_V2_CANDIDATE_SHA,
            "guard_id": GUARD_ID,
            "continuation_id": CONTINUATION_ID,
            "control": "FIXED600_SHADOW_CONTROL",
            "pbv2": "SHADOW_ONLY",
            "one_m": "SHADOW_ONLY_DIAGNOSTIC",
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "symbol_key": "canonical_bare_no_dot_T",
        }

    def on_push_meta(
        self,
        *,
        sequence: int,
        push_at: str,
        publisher_last_sequence: Optional[int] = None,
        consumer_ack_sequence: Optional[int] = None,
    ) -> None:
        self.stats.last_seq = int(sequence)
        self.stats.last_push_at = str(push_at)
        if consumer_ack_sequence is not None:
            self.stats.consumer_ack_seq = int(consumer_ack_sequence)
        if publisher_last_sequence is not None:
            self.stats.publisher_last_seq = int(publisher_last_sequence)
        self._update_seq_lag_direction()
        if self.stats.state == "WAITING_MARKET":
            self.stats.state = "RUNNING"

    def note_ingress_cursors(
        self,
        *,
        publisher_last_sequence: int = 0,
        consumer_ack_sequence: int = 0,
    ) -> None:
        """Heartbeat visibility: actual Ingress publisher_seq - consumer_ack_seq."""
        self.on_push_meta(
            sequence=int(self.stats.last_seq or 0),
            push_at=self.stats.last_push_at or "",
            publisher_last_sequence=int(publisher_last_sequence),
            consumer_ack_sequence=int(consumer_ack_sequence),
        )

    def _paper_consumer_seq_lag(self) -> int:
        pub = int(self.stats.publisher_last_seq or 0)
        ack = int(self.stats.consumer_ack_seq or 0)
        if pub <= 0:
            return 0
        return max(0, pub - ack)

    def _update_seq_lag_direction(self) -> None:
        pub = int(self.stats.publisher_last_seq or 0)
        if pub <= 0:
            return
        lag = self._paper_consumer_seq_lag()
        if lag > self.stats.max_seq_lag:
            self.stats.max_seq_lag = lag
        prev = int(self.stats.prev_seq_lag)
        if lag > prev + 1:
            self.stats.backlog_direction = "increasing"
        elif lag < prev - 1:
            self.stats.backlog_direction = "decreasing"
        else:
            self.stats.backlog_direction = "stable"
        self.stats.prev_seq_lag = lag

    def _refresh_seq_lag_from_ingress_status(self) -> None:
        """Fallback: ingress_status.json publisher_last_sequence - paper_consumer_last_ack."""
        try:
            from small_paper.consumer_lag_policy import read_ingress_status

            native = Path(__file__).resolve().parents[2]
            day = ""
            for book in (self.primary, self.control):
                for pos in book.values():
                    if pos.date:
                        day = str(pos.date)
                        break
                if day:
                    break
            if not day and self.stats.last_event_t:
                day = datetime.fromtimestamp(float(self.stats.last_event_t), JST).strftime("%Y%m%d")
            if not day:
                day = _now().strftime("%Y%m%d")
            st = read_ingress_status(native, day)
            if not st:
                return
            pub = int(st.get("publisher_last_sequence") or 0)
            ack = int(st.get("paper_consumer_last_ack") or 0)
            if pub > 0:
                self.stats.publisher_last_seq = pub
            if "paper_consumer_last_ack" in st:
                self.stats.consumer_ack_seq = ack
            self._update_seq_lag_direction()
        except Exception:
            return

    def try_admit_fill(
        self,
        *,
        symbol: str,
        fill_price: float,
        fill_time: Optional[float] = None,
        payload: Optional[Mappingish] = None,
        session: str = "AM",
        date: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        """Admit fill independently into Primary and Control (separate caps).

        Occupancy isolation: only explicit V1R-native sources may mutate Primary/Control.
        """
        src = str(source or "").strip().lower()
        raw = str(symbol)
        sym = canonical_symbol_key(symbol)
        if src not in ("v1r_native", "v1r", "native"):
            self._trace(
                "admit_rejected_non_v1r_source",
                sym,
                {"source": source, "fill_price": fill_price, "symbol_raw": raw},
            )
            return {
                "symbol": sym,
                "symbol_raw": raw,
                "primary_admitted": False,
                "control_admitted": False,
                "rejected": True,
                "reason": "NON_V1R_ENTRY_SOURCE_FORBIDDEN",
                "source": source,
            }
        if self.fail_closed:
            return {
                "symbol": sym,
                "symbol_raw": raw,
                "primary_admitted": False,
                "control_admitted": False,
                "rejected": True,
                "reason": f"FAIL_CLOSED:{self.fail_reason}",
                "source": source,
            }
        ft = float(fill_time if fill_time is not None else time.time())
        ft_dt = datetime.fromtimestamp(ft, JST)
        iso = ft_dt.isoformat(timespec="seconds")
        day = str(date or "").strip()
        if len(day) != 8 or not day.isdigit():
            day = ft_dt.strftime("%Y%m%d")
        snap = dict(payload) if isinstance(payload, dict) else {}
        out = {
            "symbol": sym,
            "symbol_raw": raw,
            "symbol_canonical": sym,
            "primary_admitted": False,
            "control_admitted": False,
            "source": "v1r_native",
            "fill_snapshot_bound": bool(snap),
        }

        if self.open_n("primary") < self.cap and (sym not in self.primary or self.primary[sym].closed):
            pos = LanePosition(
                symbol=sym,
                lane="primary",
                fill_time=ft,
                fill_price=float(fill_price),
                fill_iso=iso,
                session=session,
                date=day,
                fill_snapshot=snap,
            )
            if snap:
                self._append_board(pos, snap, ft)
            self.primary[sym] = pos
            self.stats.primary_fills += 1
            out["primary_admitted"] = True
            self._trace(
                "ADMIT",
                sym,
                {
                    "lane": "primary",
                    "fill_price": fill_price,
                    "fill_time": ft,
                    "symbol_raw": raw,
                    "fill_snapshot_bound": bool(snap),
                    "snapshot": _snapshot_summary(snap),
                },
            )
        else:
            self.stats.primary_capacity_block += 1

        if self.open_n("control") < self.cap and (sym not in self.control or self.control[sym].closed):
            pos = LanePosition(
                symbol=sym,
                lane="control",
                fill_time=ft,
                fill_price=float(fill_price),
                fill_iso=iso,
                session=session,
                date=day,
                fill_snapshot=snap,
            )
            if snap:
                self._append_board(pos, snap, ft)
            self.control[sym] = pos
            self.stats.control_fills += 1
            out["control_admitted"] = True
            self._trace(
                "ADMIT",
                sym,
                {
                    "lane": "control",
                    "fill_price": fill_price,
                    "fill_time": ft,
                    "symbol_raw": raw,
                    "fill_snapshot_bound": bool(snap),
                    "snapshot": _snapshot_summary(snap),
                },
            )
        else:
            self.stats.control_capacity_block += 1
        return out

    def on_tick(
        self,
        *,
        symbol: str,
        payload: Mappingish,
        event_t: Optional[float] = None,
        push_sequence: Any = None,
    ) -> list[dict[str, Any]]:
        """Update board series and evaluate EXIT for both lanes independently."""
        if self.fail_closed:
            return []
        raw = str(symbol)
        sym = canonical_symbol_key(symbol)
        t = float(event_t if event_t is not None else time.time())
        self.stats.last_event_t = t
        self.stats.last_event_wall = time.time()
        _elag = float(self.stats.last_event_wall - t)
        if _elag > self.stats.max_event_lag_sec:
            self.stats.max_event_lag_sec = _elag
        _prev = float(self.stats.prev_event_lag_sec)
        if int(self.stats.publisher_last_seq or 0) <= 0 and self.stats.ticks > 0:
            if _elag > _prev + 0.05:
                self.stats.backlog_direction = "increasing"
            elif _elag < _prev - 0.05:
                self.stats.backlog_direction = "decreasing"
            else:
                self.stats.backlog_direction = "stable"
        self.stats.prev_event_lag_sec = _elag
        self.stats.ticks += 1
        exits: list[dict[str, Any]] = []
        try:
            matched_any = False
            for lane, book in (("primary", self.primary), ("control", self.control)):
                pos = book.get(sym)
                if pos is None or pos.closed:
                    continue
                matched_any = True
                self.stats.tick_matches += 1
                # Rate-limit TICK_MATCH disk spam (still counts tick_matches every hit).
                if (pos.last_tick_match_trace_t <= 0.0) or (t - pos.last_tick_match_trace_t >= 5.0) or len(pos.t) < 2:
                    pos.last_tick_match_trace_t = t
                    self._trace(
                        "TICK_MATCH",
                        sym,
                        {
                            "lane": lane,
                            "symbol_raw": raw,
                            "symbol_canonical": sym,
                            "off": float(t - pos.fill_time),
                            "board_n": len(pos.t),
                            "push_sequence": push_sequence,
                        },
                    )
                # In-session quotes first so a tick at exactly sess_end is usable.
                self._append_board(pos, payload, t)
            # Frozen session-close after in-session append; never observer.close_all.
            exits.extend(self.maybe_session_close(event_t=t))
            if self.fail_closed:
                return exits
            for lane, book in (("primary", self.primary), ("control", self.control)):
                pos = book.get(sym)
                if pos is None or pos.closed:
                    continue
                ctx = self._decision_context(pos)
                self._trace_decisions(pos, ctx)
                decision = self._evaluate(pos, ctx)
                if decision and decision.get("exit"):
                    if not pos.traced_exit_trigger:
                        pos.traced_exit_trigger = True
                        trig_ev = (
                            "CONTROL_600_TRIGGER"
                            if lane == "control" and not decision.get("triggered_guard")
                            else "EXIT_TRIGGER"
                        )
                        self._trace(
                            trig_ev,
                            sym,
                            {
                                "lane": lane,
                                "reason": decision.get("reason"),
                                "exit_off": decision.get("exit_off"),
                                "trigger_only": True,
                                "slot_released": False,
                            },
                        )
                    self._close(pos, decision, payload)
                    exits.append(decision)
            if not matched_any and (self.open_n("primary") + self.open_n("control")) > 0:
                # Open elsewhere — normal for other symbols. Detect legacy-key orphans.
                orphans = self._legacy_key_orphans(raw, sym)
                if orphans:
                    self.stats.lookup_miss_with_open += 1
                    self._report_error(
                        {
                            "error_type": "v1r_dual_lane_symbol_lookup_mismatch",
                            "where": "on_tick",
                            "symbol_raw": raw,
                            "symbol_canonical": sym,
                            "open_keys_primary": self.open_keys("primary"),
                            "open_keys_control": self.open_keys("control"),
                            "legacy_orphans": orphans,
                            "push_sequence": push_sequence,
                            "message": "open position exists under non-canonical key",
                        }
                    )
                    self._fail_closed("SYMBOL_LOOKUP_MISMATCH")
        except Exception as exc:
            self.stats.exceptions += 1
            self._report_error(
                {
                    "error_type": "v1r_dual_lane_exception",
                    "where": "on_tick",
                    "symbol_raw": raw,
                    "symbol_canonical": sym,
                    "lane": "both",
                    "open_keys_primary": self.open_keys("primary"),
                    "open_keys_control": self.open_keys("control"),
                    "push_sequence": push_sequence,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._fail_closed(f"EXCEPTION:{type(exc).__name__}")
        return exits

    def _legacy_key_orphans(self, raw: str, canonical: str) -> list[str]:
        alts = {raw, raw.replace(".T", ""), f"{canonical}.T", canonical.upper(), canonical.lower()}
        found = []
        for book in (self.primary, self.control):
            for k, p in book.items():
                if p.closed:
                    continue
                if k != canonical and k in alts:
                    found.append(k)
        return sorted(set(found))

    def close_open_at_session_end(
        self, *, event_t: float, session: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Alias: Frozen session-end actual EXIT + slot/native release."""
        return self.maybe_session_close(event_t=event_t, session=session)

    def maybe_session_close(
        self, *, event_t: float, session: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Frozen SESSION_CLOSE: last valid executable Buy1 at-or-before session_end.

        Required when 600/750 horizon has not been reached by AM 11:30 / PM 15:00.
        Does not invent quotes, does not use observer.close_all, does not release
        on trigger/pending/Discord/timer, and does not close the other session.
        """
        if self.fail_closed:
            return []
        t = float(event_t)
        exits: list[dict[str, Any]] = []
        for _lane, book in (("primary", self.primary), ("control", self.control)):
            for pos in list(book.values()):
                if pos.closed:
                    continue
                if session is not None and pos.session != session:
                    continue
                se = session_end_for_position(
                    date=pos.date, session=pos.session, fill_time=pos.fill_time
                )
                if t + 1e-9 < se:
                    continue
                decision = self._session_close_decision(pos, sess_end=se)
                self._close(pos, decision, pos.fill_snapshot or {})
                exits.append(decision)
        return exits

    def _last_valid_executable_bid(
        self, pos: LanePosition, *, until_t: float
    ) -> Optional[tuple[float, float]]:
        """Last Frozen-valid Buy1 at-or-before until_t. No future quote, no synthetic."""
        n = len(pos.t)
        for i in range(n - 1, -1, -1):
            ti = float(pos.t[i])
            if ti > float(until_t) + 1e-12:
                continue
            if pos.special and pos.special[i]:
                continue
            if pos.bid_qty and pos.bid_qty[i] < MIN_BUY1_QTY - 1e-12:
                continue
            if pos.fresh_sec and pos.fresh_sec[i] > BOARD_FRESH_SEC + 1e-12:
                continue
            if not pos.bid:
                continue
            px = float(pos.bid[i])
            if px <= 0:
                continue
            return ti, px
        snap = pos.fill_snapshot if isinstance(pos.fill_snapshot, dict) else {}
        if snap:
            bid, bq = extract_buy1(snap)
            special = bool(snap.get("SpecialQuote") or snap.get("special"))
            fresh = _f(snap.get("board_age_sec"))
            if fresh is None:
                fresh = _f(snap.get("fresh_sec"))
            if (
                bid is not None
                and bid > 0
                and not special
                and (bq is None or float(bq) >= MIN_BUY1_QTY - 1e-12)
                and (fresh is None or float(fresh) <= BOARD_FRESH_SEC + 1e-12)
            ):
                ft = float(pos.fill_time)
                if ft <= float(until_t) + 1e-12:
                    return ft, float(bid)
        if pos.fill_price and float(pos.fill_time) <= float(until_t) + 1e-12:
            return float(pos.fill_time), float(pos.fill_price)
        return None

    def _session_close_decision(self, pos: LanePosition, *, sess_end: float) -> dict[str, Any]:
        found = self._last_valid_executable_bid(pos, until_t=sess_end)
        if found is None:
            # No post-session quote: use fill itself (already executable). Never invent.
            et = min(float(pos.fill_time), float(sess_end))
            px = float(pos.fill_price)
        else:
            et, px = found
        ret = 0.0
        if pos.fill_price:
            ret = (float(px) / float(pos.fill_price) - 1.0) * 10000.0
        return {
            "exit": True,
            "lane": pos.lane,
            "symbol": pos.symbol,
            "reason": "SESSION_CLOSE",
            "triggered_guard": False,
            "extended": False,
            "exit_off": max(0.0, float(et) - float(pos.fill_time)),
            "exit_time": float(et),
            "exit_ret_bps": float(ret),
            "exit_price": float(px),
            "arch": "E" if pos.lane == "primary" else "A",
            "execution": "LAST_VALID_EXECUTABLE_BUY1_AT_OR_BEFORE_SESSION_END",
            "guard": None,
            "continuation": None,
        }

    def _append_board(self, pos: LanePosition, payload: Mappingish, t: float) -> None:
        se = session_end_for_position(
            date=pos.date, session=pos.session, fill_time=pos.fill_time
        )
        if float(t) > se + 1e-12:
            return
        bid, bq = extract_buy1(payload)
        ask, aq = extract_sell1(payload)
        px = _f(payload.get("CurrentPrice"))
        if bid is None and px is not None:
            bid = px - 1.0
        if ask is None and px is not None:
            ask = px + 1.0
        if bid is None or ask is None:
            return
        if bq is None:
            bq = MIN_BUY1_QTY
        if aq is None:
            aq = MIN_BUY1_QTY
        fresh = _f(payload.get("board_age_sec"))
        if fresh is None:
            fresh = _f(payload.get("fresh_sec"))
        if fresh is None:
            fresh = 0.5
            for key in ("CurrentPriceTime", "timestamp", "BidTime", "event_time"):
                raw = payload.get(key)
                if not raw:
                    continue
                try:
                    if isinstance(raw, (int, float)):
                        pt = float(raw)
                        fresh = max(0.0, float(t) - pt)
                    else:
                        s = str(raw).replace("+09:00", "")
                        pt_dt = datetime.fromisoformat(s)
                        if pt_dt.tzinfo is None:
                            pt_dt = pt_dt.replace(tzinfo=JST)
                        fresh = max(0.0, float(t) - pt_dt.timestamp())
                except Exception:
                    pass
                break
        special = bool(payload.get("SpecialQuote") or payload.get("special"))
        mid = (float(bid) + float(ask)) / 2.0
        t_use = float(t)
        if pos.t and t_use + 1e-15 < float(pos.t[-1]):
            # Truncated/out-of-order clock: keep series non-decreasing so
            # duplicate-timestamp last-index attach matches SoT (V26G8).
            t_use = float(pos.t[-1])
        pos.t.append(t_use)
        pos.bid.append(float(bid))
        pos.ask.append(float(ask))
        pos.bid_qty.append(float(bq))
        pos.ask_qty.append(float(aq))
        pos.special.append(bool(special))
        pos.fresh_sec.append(float(fresh))
        pos.mid.append(float(mid))


    def _fill_rec(self, pos: LanePosition) -> dict[str, Any]:
        return {
            "date": pos.date or _now().strftime("%Y%m%d"),
            "symbol": pos.symbol,
            "session": pos.session,
            "fill_time": pos.fill_time,
            "fill_price": pos.fill_price,
            "anchor_id": "live",
        }

    def _new_exact_cache(self, pos: LanePosition) -> dict[str, Any]:
        se = session_end_for_position(
            date=pos.date, session=pos.session, fill_time=pos.fill_time
        )
        return {
            "fill_time": float(pos.fill_time),
            "fill_price": float(pos.fill_price),
            "session": str(pos.session),
            "date": str(pos.date),
            "symbol": str(pos.symbol),
            "sess_end": float(se),
            "board_n": 0,
            "er_i0": 0,
            "last_attach_valid": -1,
            "offs": [],
            "rets": [],
            "mids": [],
            "times": [],
            "imb": [],
            "spread": [],
            "bid_qty": [],
            "ask_qty": [],
            "event_rate": [],
            "imb0": None,
            "spread0": None,
            "bid_qty0": None,
            "er0": None,
            "guard_frozen": False,
            "guard_hit": False,
            "guard_hit_index": -1,
            "guard_monitor_passed": False,
            "pol_frozen": False,
            "pol": None,
        }

    def _cache_identity_ok(self, pos: LanePosition, cache: dict[str, Any]) -> bool:
        return (
            cache.get("symbol") == pos.symbol
            and cache.get("session") == pos.session
            and cache.get("date") == pos.date
            and abs(float(cache.get("fill_time") or 0.0) - float(pos.fill_time)) <= 1e-12
            and abs(float(cache.get("fill_price") or 0.0) - float(pos.fill_price)) <= 1e-12
        )

    def _row_valid_bid(self, pos: LanePosition, i: int) -> bool:
        from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY

        if pos.special[i]:
            return False
        fresh_raw = pos.fresh_sec[i]
        fresh = float(fresh_raw) if np.isfinite(fresh_raw) else 0.0
        if fresh > BOARD_FRESHNESS_SEC + 1e-12:
            return False
        qty = pos.bid_qty[i]
        if not np.isfinite(qty) or qty < MIN_QTY:
            return False
        bid = pos.bid[i]
        return bool(np.isfinite(bid) and bid > 0)

    def _row_valid_both(self, pos: LanePosition, i: int) -> bool:
        from research.e1_x28_executable_joint import MIN_QTY

        if not self._row_valid_bid(pos, i):
            return False
        aq = pos.ask_qty[i]
        ask = pos.ask[i]
        if not np.isfinite(aq) or aq < MIN_QTY:
            return False
        return bool(np.isfinite(ask) and ask > 0)

    def _row_attach_valid(self, pos: LanePosition, i: int) -> bool:
        from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY

        if pos.special[i]:
            return False
        bid = pos.bid[i]
        ask = pos.ask[i]
        bq = pos.bid_qty[i]
        aq = pos.ask_qty[i]
        fresh_raw = pos.fresh_sec[i]
        if not (np.isfinite(bid) and np.isfinite(ask) and bid > 0 and ask > 0):
            return False
        if not (np.isfinite(bq) and np.isfinite(aq) and bq >= MIN_QTY and aq >= MIN_QTY):
            return False
        fresh = float(fresh_raw) if np.isfinite(fresh_raw) else 0.0
        return fresh <= BOARD_FRESHNESS_SEC + 1e-12

    def _exact_update_guard(self, cache: dict[str, Any]) -> None:
        if str(FROZEN_GUARD.get("kind") or "") != "imbalance":
            return
        offs = cache["offs"]
        imb = cache["imb"]
        if not offs:
            return
        i = len(offs) - 1
        o = float(offs[i])
        monitor_to = float(FROZEN_GUARD.get("monitor_to") or 120.0)
        if o > monitor_to + 1e-12:
            cache["guard_frozen"] = True
            cache["guard_hit"] = False
            return
        thr = float(FROZEN_GUARD.get("imb_threshold") or -0.1)
        pers = float(FROZEN_GUARD.get("persist_sec") or 5.0)
        vi = imb[i]
        if not (np.isfinite(vi) and float(vi) <= thr + 1e-12 and o >= pers - 1e-12):
            return
        lo = o - pers
        k0 = 0
        while k0 <= i and float(offs[k0]) < lo:
            k0 += 1
        window = imb[k0 : i + 1]
        if window and all(np.isfinite(x) and float(x) <= thr + 1e-12 for x in window):
            cache["guard_hit"] = True
            cache["guard_frozen"] = True

    def _exact_fill_attach(self, pos: LanePosition, cache: dict[str, Any], k: int, board_i: int) -> None:
        j = int(cache["last_attach_valid"])
        if j >= 0:
            bb = float(pos.bid[j])
            aa = float(pos.ask[j])
            bqq = float(pos.bid_qty[j])
            aqq = float(pos.ask_qty[j])
            midv = (bb + aa) / 2.0
            imb = (bqq - aqq) / (bqq + aqq)
            spr = (aa - bb) / midv * 10000.0
        else:
            imb = np.nan
            spr = np.nan
            bqq = np.nan
            aqq = np.nan
        cache["imb"][k] = imb
        cache["spread"][k] = spr
        cache["bid_qty"][k] = bqq
        cache["ask_qty"][k] = aqq
        ti = float(cache["times"][k])
        i0 = int(cache["er_i0"])
        while i0 < board_i and float(pos.t[i0]) < ti - 30.0:
            i0 += 1
        cache["event_rate"][k] = (board_i + 1 - i0) / 30.0

    def _exact_guard_window_hit(self, offs: list[Any], imb: list[Any], i: int) -> bool:
        """Exact persist-window hit at index i. Lookback is O(points in persist_sec), not O(board_n)."""
        monitor_to = float(FROZEN_GUARD.get("monitor_to") or 120.0)
        pers = float(FROZEN_GUARD.get("persist_sec") or 5.0)
        thr = float(FROZEN_GUARD.get("imb_threshold") or -0.1)
        o = float(offs[i])
        if o > monitor_to + 1e-12:
            return False
        vi = imb[i]
        if not (np.isfinite(vi) and float(vi) <= thr + 1e-12 and o >= pers - 1e-12):
            return False
        lo = o - pers
        j = i
        while j >= 0 and float(offs[j]) >= lo:
            x = imb[j]
            if not (np.isfinite(x) and float(x) <= thr + 1e-12):
                return False
            j -= 1
        return True

    def _exact_guard_incremental(self, cache: dict[str, Any], *, rewrite_from: int) -> None:
        """previous exact guard + current tick (and duplicate-ts rewrite region) = current exact guard.

        Must match `_exact_recompute_guard` / full-history scan on every event.
        """
        if str(FROZEN_GUARD.get("kind") or "") != "imbalance":
            return
        offs = cache["offs"]
        imb = cache["imb"]
        n = len(offs)
        if n == 0:
            return
        self.stats.guard_incremental_update += 1
        start = int(rewrite_from)
        if start >= n:
            return
        monitor_to = float(FROZEN_GUARD.get("monitor_to") or 120.0)
        hit_idx = int(cache.get("guard_hit_index", -1))
        if bool(cache.get("guard_hit")) and hit_idx >= 0 and hit_idx < start:
            cache["guard_frozen"] = True
            return
        if bool(cache.get("guard_monitor_passed")) and not bool(cache.get("guard_hit")):
            cache["guard_frozen"] = True
            cache["guard_hit"] = False
            return
        if hit_idx >= start:
            cache["guard_hit"] = False
            cache["guard_hit_index"] = -1
            cache["guard_frozen"] = False
            cache["guard_monitor_passed"] = False
        for i in range(start, n):
            o = float(offs[i])
            if o > monitor_to + 1e-12:
                if not cache.get("guard_hit"):
                    cache["guard_frozen"] = True
                    cache["guard_hit"] = False
                    cache["guard_hit_index"] = -1
                    cache["guard_monitor_passed"] = True
                else:
                    cache["guard_frozen"] = True
                return
            if self._exact_guard_window_hit(offs, imb, i):
                cache["guard_hit"] = True
                cache["guard_frozen"] = True
                cache["guard_hit_index"] = i
                cache["guard_monitor_passed"] = False
                return

    def _exact_recompute_guard(self, cache: dict[str, Any]) -> None:
        """Full-history reference. Used on exact fallback and parity tests. Not the live hot path."""
        cache["guard_frozen"] = False
        cache["guard_hit"] = False
        cache["guard_hit_index"] = -1
        cache["guard_monitor_passed"] = False
        if str(FROZEN_GUARD.get("kind") or "") != "imbalance":
            return
        offs = cache["offs"]
        imb = cache["imb"]
        monitor_to = float(FROZEN_GUARD.get("monitor_to") or 120.0)
        thr = float(FROZEN_GUARD.get("imb_threshold") or -0.1)
        pers = float(FROZEN_GUARD.get("persist_sec") or 5.0)
        for i, o_raw in enumerate(offs):
            o = float(o_raw)
            if o > monitor_to + 1e-12:
                cache["guard_frozen"] = True
                cache["guard_hit"] = False
                cache["guard_hit_index"] = -1
                cache["guard_monitor_passed"] = True
                return
            vi = imb[i]
            if not (np.isfinite(vi) and float(vi) <= thr + 1e-12 and o >= pers - 1e-12):
                continue
            lo = o - pers
            k0 = 0
            while k0 <= i and float(offs[k0]) < lo:
                k0 += 1
            window = imb[k0 : i + 1]
            if window and all(np.isfinite(x) and float(x) <= thr + 1e-12 for x in window):
                cache["guard_hit"] = True
                cache["guard_frozen"] = True
                cache["guard_hit_index"] = i
                cache["guard_monitor_passed"] = False
                return

    def _exact_append_row(
        self, pos: LanePosition, cache: dict[str, Any], i: int, *, skip_guard: bool = False
    ) -> None:
        ti = float(pos.t[i])
        if i > 0 and ti + 1e-15 < float(pos.t[i - 1]):
            # Board append clamps rewinds; if a cache rebuild still sees one,
            # treat as duplicate of the previous timestamp (do not fail-closed).
            ti = float(pos.t[i - 1])
        if self._row_attach_valid(pos, i):
            cache["last_attach_valid"] = i
        entry_t = float(pos.fill_time)
        sess_end = float(cache["sess_end"])
        if ti + 1e-12 < entry_t:
            return
        if ti > sess_end + 1e-12:
            return
        if self._row_valid_bid(pos, i):
            bid = float(pos.bid[i])
            entry_price = float(pos.fill_price)
            ret = (bid / entry_price - 1.0) * 10000.0
            mid_ret = np.nan
            if self._row_valid_both(pos, i):
                mid = (float(pos.ask[i]) + bid) / 2.0
                mid_ret = (mid / entry_price - 1.0) * 10000.0
            cache["offs"].append(ti - entry_t)
            cache["rets"].append(ret)
            cache["mids"].append(mid_ret)
            cache["times"].append(ti)
            cache["imb"].append(np.nan)
            cache["spread"].append(np.nan)
            cache["bid_qty"].append(np.nan)
            cache["ask_qty"].append(np.nan)
            cache["event_rate"].append(np.nan)
            while int(cache["er_i0"]) < i and float(pos.t[int(cache["er_i0"])]) < ti - 30.0:
                cache["er_i0"] = int(cache["er_i0"]) + 1
        rewrite_from: Optional[int] = None
        k = len(cache["times"]) - 1
        while k >= 0 and abs(float(cache["times"][k]) - ti) <= 1e-12:
            self._exact_fill_attach(pos, cache, k, i)
            rewrite_from = k
            k -= 1
        if cache["imb"]:
            v0 = cache["imb"][0]
            cache["imb0"] = float(v0) if np.isfinite(v0) else None
            s0 = cache["spread"][0]
            cache["spread0"] = float(s0) if np.isfinite(s0) else None
            b0 = cache["bid_qty"][0]
            cache["bid_qty0"] = float(b0) if np.isfinite(b0) else None
            e0 = cache["event_rate"][0]
            cache["er0"] = float(e0) if np.isfinite(e0) else None
        if (
            (not skip_guard)
            and pos.lane == "primary"
            and cache["times"]
            and rewrite_from is not None
        ):
            self._exact_guard_incremental(cache, rewrite_from=rewrite_from)

    def _path_from_cache(self, cache: dict[str, Any]) -> dict[str, Any]:
        if not cache["offs"]:
            return {"ok": False, "offs": np.array([]), "rets": np.array([]), "mids": np.array([])}
        return {
            "ok": True,
            "offs": np.asarray(cache["offs"], dtype=float),
            "rets": np.asarray(cache["rets"], dtype=float),
            "mids": np.asarray(cache["mids"], dtype=float),
            "times": np.asarray(cache["times"], dtype=float),
            "sess_end": float(cache["sess_end"]),
            "entry_t": float(cache["fill_time"]),
            "entry_price": float(cache["fill_price"]),
            "imb": np.asarray(cache["imb"], dtype=float),
            "spread": np.asarray(cache["spread"], dtype=float),
            "bid_qty": np.asarray(cache["bid_qty"], dtype=float),
            "ask_qty": np.asarray(cache["ask_qty"], dtype=float),
            "event_rate": np.asarray(cache["event_rate"], dtype=float),
            "imb0": cache["imb0"],
            "spread0": cache["spread0"],
            "bid_qty0": cache["bid_qty0"],
            "er0": cache["er0"],
        }

    def _sync_exact_cache(self, pos: LanePosition) -> dict[str, Any]:
        cache = pos.exact_cache
        reused = (
            isinstance(cache, dict)
            and self._cache_identity_ok(pos, cache)
            and int(cache.get("board_n") or 0) <= len(pos.t)
        )
        if not reused:
            cache = self._new_exact_cache(pos)
            pos.exact_cache = cache
            self.stats.cache_miss += 1
        try:
            bn = int(cache["board_n"])
            if bn > 0 and pos.t and float(pos.t[bn - 1]) > float(pos.t[-1]) + 1e-12:
                raise ValueError("board_truncated")
            for i in range(bn, len(pos.t)):
                self._exact_append_row(pos, cache, i)
            cache["board_n"] = len(pos.t)
            if reused:
                self.stats.cache_hit += 1
        except ValueError:
            cache = self._new_exact_cache(pos)
            pos.exact_cache = cache
            for i in range(len(pos.t)):
                self._exact_append_row(pos, cache, i, skip_guard=True)
            cache["board_n"] = len(pos.t)
            if pos.lane == "primary":
                self._exact_recompute_guard(cache)
            self.stats.exact_cache_fallback += 1
            self.stats.cache_miss += 1
            self._trace(
                "EXACT_CACHE_FALLBACK",
                pos.symbol,
                {"lane": pos.lane, "reason": "non_monotonic_or_reset", "slot_released": False},
            )
        return cache

    def _decision_context_full(self, pos: LanePosition) -> dict[str, Any]:
        from research.e1_x35_passive_exit.paths import build_path
        from research.v1r_exit_v2_asymmetric.states import build_trade_bundle

        off_now = float(pos.t[-1] - pos.fill_time) if pos.t else 0.0
        if len(pos.t) < 2:
            return {"ok": False, "off_now": off_now, "path": None, "bundle": None, "pol": {"ok": False}}
        board = self._board_dict(pos)
        path = build_path(
            board,
            entry_price=float(pos.fill_price),
            entry_t=float(pos.fill_time),
            sess_end=session_end_for_position(
                date=pos.date, session=pos.session, fill_time=pos.fill_time
            ),
        )
        if not path.get("ok"):
            return {"ok": False, "off_now": off_now, "path": path, "bundle": None, "pol": {"ok": False}}
        fill = self._fill_rec(pos)
        bundle = build_trade_bundle(fill, path, board)
        if pos.lane == "primary":
            pol = apply_arch_e_to_bundle(bundle)
        else:
            pol = apply_fixed600_to_bundle(bundle)
        return {"ok": True, "off_now": off_now, "path": path, "bundle": bundle, "pol": pol, "fast": False}

    def debug_rebuild_decision_context(self, pos: LanePosition) -> dict[str, Any]:
        """Test helper: Candidate-7 full rebuild (SoT)."""
        return self._decision_context_full(pos)

    def _apply_policy(self, pos: LanePosition, path: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        from research.v1r_exit_v2_asymmetric.states import build_trade_bundle

        board = self._board_dict(pos)
        fill = self._fill_rec(pos)
        path_copy = dict(path)
        for k in ("offs", "rets", "mids", "times", "imb", "spread", "bid_qty", "ask_qty", "event_rate"):
            if k in path_copy and path_copy[k] is not None:
                path_copy[k] = np.asarray(path_copy[k], dtype=float).copy()
        bundle = build_trade_bundle(fill, path_copy, board)
        if pos.lane == "primary":
            pol = apply_arch_e_to_bundle(bundle)
        else:
            pol = apply_fixed600_to_bundle(bundle)
        return pol, bundle

    def _decision_context(self, pos: LanePosition) -> dict[str, Any]:
        """One path/policy context per matching tick. Trace must not recompute Strategy."""
        off_now = float(pos.t[-1] - pos.fill_time) if pos.t else 0.0
        if len(pos.t) < 2:
            return {"ok": False, "off_now": off_now, "path": None, "bundle": None, "pol": {"ok": False}}
        force_full = str(os.environ.get("V26G8_FORCE_FULL_REBUILD", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if force_full or str(FROZEN_GUARD.get("kind") or "") != "imbalance":
            return self._decision_context_full(pos)
        cache = self._sync_exact_cache(pos)
        if not cache["offs"]:
            return {"ok": False, "off_now": off_now, "path": None, "bundle": None, "pol": {"ok": False}, "fast": True}
        if cache.get("pol_frozen") and isinstance(cache.get("pol"), dict) and cache["pol"].get("ok"):
            return {
                "ok": True,
                "off_now": off_now,
                "path": None,
                "bundle": None,
                "pol": cache["pol"],
                "fast": True,
            }
        path_off = float(cache["offs"][-1]) if cache["offs"] else -1.0
        if pos.lane == "primary":
            need_full = bool(cache.get("guard_hit") or path_off + 1e-12 >= 600.0)
        else:
            need_full = path_off + 1e-12 >= 600.0
        if not need_full:
            return {
                "ok": True,
                "off_now": off_now,
                "path": None,
                "bundle": None,
                "pol": {"ok": False},
                "fast": True,
            }
        self.stats.path_materialization += 1
        path = self._path_from_cache(cache)
        if not path.get("ok"):
            return {"ok": False, "off_now": off_now, "path": path, "bundle": None, "pol": {"ok": False}, "fast": True}
        from research.e1_x35_passive_exit.paths import build_path

        board = self._board_dict(pos)
        ref = build_path(
            board,
            entry_price=float(pos.fill_price),
            entry_t=float(pos.fill_time),
            sess_end=session_end_for_position(
                date=pos.date, session=pos.session, fill_time=pos.fill_time
            ),
        )
        use_path = path
        if ref.get("ok") and (
            int(ref["offs"].size) != int(path["offs"].size)
            or not np.allclose(ref["offs"], path["offs"], rtol=0.0, atol=1e-9)
            or not np.allclose(ref["rets"], path["rets"], rtol=0.0, atol=1e-9)
        ):
            self.stats.exact_cache_fallback += 1
            self._trace(
                "EXACT_CACHE_FALLBACK",
                pos.symbol,
                {
                    "lane": pos.lane,
                    "reason": "path_mismatch_full_rebuild",
                    "cache_n": int(path["offs"].size),
                    "ref_n": int(ref["offs"].size),
                    "slot_released": False,
                },
            )
            use_path = ref
            cache = self._new_exact_cache(pos)
            pos.exact_cache = cache
        pol, bundle = self._apply_policy(pos, use_path)
        if pol.get("ok"):
            cache["pol_frozen"] = True
            cache["pol"] = pol
        return {
            "ok": True,
            "off_now": off_now,
            "path": use_path,
            "bundle": bundle,
            "pol": pol,
            "fast": False,
        }

    def _board_dict(self, pos: LanePosition) -> dict[str, np.ndarray]:
        return {
            "t": np.asarray(pos.t, dtype=float),
            "bid": np.asarray(pos.bid, dtype=float),
            "ask": np.asarray(pos.ask, dtype=float),
            "bid_qty": np.asarray(pos.bid_qty, dtype=float),
            "ask_qty": np.asarray(pos.ask_qty, dtype=float),
            "special": np.asarray(pos.special, dtype=bool),
            "fresh_sec": np.asarray(pos.fresh_sec, dtype=float),
            "mid": np.asarray(pos.mid, dtype=float),
        }

    def _trace_decisions(self, pos: LanePosition, ctx: Optional[dict[str, Any]] = None) -> None:
        """Emit decision-horizon traces from the actual evaluation context.

        Must not recompute Strategy. ctx is the same object _evaluate consumes.
        """
        if len(pos.t) < 2:
            return
        if ctx is None:
            ctx = self._decision_context(pos)
        if not ctx.get("ok"):
            return
        pol = ctx.get("pol") or {}
        if not pol.get("ok"):
            return
        off_now = float(
            ctx.get("off_now") if ctx.get("off_now") is not None else (pos.t[-1] - pos.fill_time)
        )
        if pos.lane == "primary":
            if pol.get("triggered_guard") and not pos.traced_guard_trigger:
                pos.traced_guard_trigger = True
                self._trace(
                    "GUARD_TRIGGER",
                    pos.symbol,
                    {
                        "lane": "primary",
                        "reason": pol.get("reason"),
                        "guard_trigger_off": pol.get("guard_trigger_off"),
                        "exit_off": pol.get("exit_off"),
                        "off_now": off_now,
                        "slot_released": False,
                    },
                )
            if off_now + 1e-9 >= 600.0 and not pos.traced_600_decision and not pol.get("triggered_guard"):
                pos.traced_600_decision = True
                extended = bool(pol.get("extended"))
                self._trace(
                    "600_DECISION",
                    pos.symbol,
                    {
                        "lane": "primary",
                        "extended": extended,
                        "reason": pol.get("reason"),
                        "exit_off": pol.get("exit_off"),
                        "off_now": off_now,
                        "slot_released": False,
                    },
                )
                if extended and not pos.traced_extend_750:
                    pos.traced_extend_750 = True
                    self._trace(
                        "CONT_EXTEND_750",
                        pos.symbol,
                        {
                            "lane": "primary",
                            "continuation": FROZEN_CONTINUATION,
                            "exit_off": pol.get("exit_off"),
                            "slot_released": False,
                        },
                    )
        else:
            if off_now + 1e-9 >= 600.0 and not pos.traced_control_600:
                pos.traced_control_600 = True
                self._trace(
                    "CONTROL_600_TRIGGER",
                    pos.symbol,
                    {
                        "lane": "control",
                        "reason": pol.get("reason"),
                        "exit_off": pol.get("exit_off"),
                        "off_now": off_now,
                        "slot_released": False,
                    },
                )

    def _evaluate(self, pos: LanePosition, ctx: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        if len(pos.t) < 2:
            return None
        if ctx is None:
            ctx = self._decision_context(pos)
        if not ctx.get("ok"):
            return None
        pol = ctx.get("pol") or {}
        if not pol.get("ok"):
            return None
        # Exit only when decision horizon has been reached (causal).
        off_now = float(
            ctx.get("off_now") if ctx.get("off_now") is not None else (pos.t[-1] - pos.fill_time)
        )
        exit_off = float(pol.get("exit_off") or 0)
        if pol.get("triggered_guard"):
            trig = float(pol.get("guard_trigger_off") or exit_off)
            if off_now + 1e-9 < trig:
                return None
            if off_now + 1e-9 < exit_off:
                return None
        else:
            target = 750.0 if pol.get("extended") else 600.0
            if off_now + 1e-9 < target:
                return None
            if exit_off + 1e-9 < target - 5.0:
                return None
        # Executable Buy1 constraints at last tick
        if pos.bid_qty and pos.bid_qty[-1] < MIN_BUY1_QTY - 1e-12:
            return None
        if pos.fresh_sec and pos.fresh_sec[-1] > BOARD_FRESH_SEC + 1e-12:
            return None
        if pos.special and pos.special[-1]:
            return None
        return {
            "exit": True,
            "lane": pos.lane,
            "symbol": pos.symbol,
            "reason": pol.get("reason"),
            "triggered_guard": bool(pol.get("triggered_guard")),
            "extended": bool(pol.get("extended")),
            "exit_off": exit_off,
            "exit_time": float(pol.get("exit_time") or pos.t[-1]),
            "exit_ret_bps": float(pol.get("exit_ret_bps") or 0),
            "exit_price": float(pos.bid[-1]) if pos.bid else pos.fill_price,
            "arch": pol.get("arch"),
            "execution": "FIRST_VALID_EXECUTABLE_BUY1_AT_OR_AFTER_TRIGGER",
            "guard": FROZEN_GUARD if pol.get("triggered_guard") else None,
            "continuation": FROZEN_CONTINUATION if pol.get("extended") else None,
        }

    def _close(self, pos: LanePosition, decision: dict[str, Any], payload: Mappingish) -> None:
        # Idempotent: duplicate actual-close must not double-count or corrupt occupancy.
        if pos.closed:
            return
        pos.closed = True
        pos.exit_reason = str(decision.get("reason") or "")
        # Slot release at actual executable exit time (not trigger-only).
        # Never fall back to wall clock: `a or b if c else d` would ignore
        # decision["exit_time"] whenever pos.t is empty (15:00 leftover SESSION_CLOSE).
        if decision.get("exit_time") is not None:
            pos.exit_time = float(decision["exit_time"])
        elif pos.t:
            pos.exit_time = float(pos.t[-1])
        else:
            pos.exit_time = float(pos.fill_time)
        pos.exit_price = float(decision.get("exit_price") or 0)
        pos.triggered_guard = bool(decision.get("triggered_guard"))
        pos.extended = bool(decision.get("extended"))
        if pos.lane == "primary":
            self.stats.primary_exits += 1
            if pos.triggered_guard:
                self.stats.guard_triggers += 1
            elif pos.exit_reason == "SESSION_CLOSE":
                self.stats.session_close += 1
            elif pos.extended:
                self.stats.extend_750 += 1
            else:
                self.stats.exit_600 += 1
            ev_name = "EXIT_EXECUTED"
        else:
            self.stats.control_exits += 1
            if pos.exit_reason == "SESSION_CLOSE":
                self.stats.session_close += 1
            ev_name = "CONTROL_EXIT"
        self._trace(
            ev_name,
            pos.symbol,
            {
                "lane": pos.lane,
                "reason": pos.exit_reason,
                "exit_off": decision.get("exit_off"),
                "exit_time": pos.exit_time,
                "triggered_guard": pos.triggered_guard,
                "extended": pos.extended,
                "exit_price": pos.exit_price,
                "fill_price": pos.fill_price,
                "slot_released": True,
                "primary_open": self.open_n("primary"),
                "control_open": self.open_n("control"),
            },
        )
        self._trace(
            "SLOT_RELEASE",
            pos.symbol,
            {
                "lane": pos.lane,
                "exit_time": pos.exit_time,
                "primary_open": self.open_n("primary"),
                "control_open": self.open_n("control"),
                "slot_released": True,
            },
        )
        # Native occupancy: Primary actual EXIT only. Never Control / Discord / trigger.
        try:
            from small_paper.v1r_native_entry_live import get_native_entry

            eng = get_native_entry()
            if eng is not None:
                if pos.lane == "primary":
                    eng.note_primary_exit(
                        pos.symbol,
                        exit_time=pos.exit_time,
                        reason=pos.exit_reason,
                    )
                inv_event = (
                    "PRIMARY_ACTUAL_EXIT" if pos.lane == "primary" else "CONTROL_ACTUAL_EXIT"
                )
                eng.check_occupancy_invariant(dual=self, event=inv_event)
        except Exception as exc:
            self.stats.exceptions += 1
            self._report_error(
                {
                    "error_type": "v1r_native_occupancy_release_exception",
                    "where": "_close",
                    "symbol": pos.symbol,
                    "lane": pos.lane,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._fail_closed(f"OCCUPANCY_RELEASE:{type(exc).__name__}")
            return
        # Discord after occupancy, never a release condition.
        if pos.lane == "primary":
            self._notify_primary_exit(pos, decision)

    def _append_discord_audit(self, filename: str, row: dict[str, Any]) -> None:
        if not self.trace_dir:
            return
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            with (self.trace_dir / filename).open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            print(
                f"[V1R_DISCORD_AUDIT_WRITE_FAIL] dual file={filename} err={type(exc).__name__}:{exc}",
                flush=True,
            )

    def _notify_primary_exit(self, pos: LanePosition, decision: dict[str, Any]) -> None:
        """V1R-native Primary EXIT → trade-notify (never PBv2 Primary occupancy path)."""
        payload = {
            "symbol": canonical_symbol_key(pos.symbol),
            "source": "v1r_native",
            "role": "PAPER_PRIMARY",
            "status": "EXIT",
            "anchor": getattr(pos, "anchor", None) or "",
            "limit": float(pos.fill_price),
            "entry_price": float(pos.fill_price),
            "exit_price": float(pos.exit_price or 0),
            "fill_time": float(pos.fill_time) if getattr(pos, "fill_time", None) is not None else None,
            "entry_time": float(pos.fill_time) if getattr(pos, "fill_time", None) is not None else None,
            "exit_time": float(pos.exit_time or 0),
            "reason": pos.exit_reason,
            "triggered_guard": bool(pos.triggered_guard),
            "extended": bool(pos.extended),
            "exit_off": decision.get("exit_off"),
            "qty": 100,
        }
        try:
            hold = None
            if payload.get("entry_time") is not None and payload.get("exit_time"):
                hold = float(payload["exit_time"]) - float(payload["entry_time"])
            payload["hold_sec"] = hold
            if payload["entry_price"] and payload["exit_price"]:
                payload["pnl_yen"] = (float(payload["exit_price"]) - float(payload["entry_price"])) * 100.0
                payload["pnl_pct"] = (
                    (float(payload["exit_price"]) / float(payload["entry_price"]) - 1.0) * 100.0
                )
        except Exception:
            pass
        # Prefer native notifier (unified delivery/errors writer) when engine is live
        try:
            from small_paper.v1r_native_entry_live import get_native_entry

            eng = get_native_entry()
            if eng is not None:
                eng._notify("EXIT", payload)
                return
        except Exception:
            pass
        try:
            from notify.v1r_discord_routing import V1RNotifyKind, publish_v1r

            result = publish_v1r(
                V1RNotifyKind.EXIT,
                payload,
                test_only=False,
                sync_http=False,
                session_id="v1r-dual-exit",
            )
            delivery = {
                "ts": _now().isoformat(timespec="seconds"),
                "event": "V1R_DISCORD_DELIVERY",
                "kind": result.kind,
                "notify_kind": "EXIT",
                "status": result.status,
                "channel": result.channel,
                "env_key": result.env_key,
                "queued": result.queued,
                "error": result.error or "",
                "notification_id": result.notification_id,
                "http_status": result.http_status,
                "symbol": payload.get("symbol"),
                "source": payload.get("source"),
                "anchor": payload.get("anchor"),
                "limit": payload.get("limit"),
                "payload_status": payload.get("status"),
                "role": payload.get("role"),
                "call_site": "V1RLiveDualLane._notify_primary_exit",
            }
            self._append_discord_audit("v1r_discord_delivery.jsonl", delivery)
            ok_statuses = {"QUEUED", "SENT", "OK", "DELIVERED"}
            if result.error or result.status not in ok_statuses:
                self._append_discord_audit(
                    "v1r_discord_errors.jsonl",
                    {**delivery, "event": "V1R_DISCORD_NOTIFY_FAIL"},
                )
        except Exception as exc:
            err = {
                "ts": _now().isoformat(timespec="seconds"),
                "event": "V1R_DISCORD_NOTIFY_EXCEPTION",
                "kind": "EXIT",
                "symbol": payload.get("symbol"),
                "source": "v1r_native",
                "error": f"{type(exc).__name__}:{exc}",
                "call_site": "V1RLiveDualLane._notify_primary_exit",
                "status": "EXCEPTION",
                "channel": "",
                "queued": False,
            }
            self._append_discord_audit("v1r_discord_delivery.jsonl", err)
            self._append_discord_audit("v1r_discord_errors.jsonl", err)
            self._report_error(err)
            print(
                f"[V1R_DISCORD_NOTIFY_EXCEPTION] EXIT symbol={payload.get('symbol')} "
                f"err={type(exc).__name__}:{exc}",
                flush=True,
            )

    def _trace(self, event: str, symbol: str, extra: dict[str, Any]) -> None:
        row = {
            "ts": _now().isoformat(timespec="milliseconds"),
            "event": event,
            "symbol": canonical_symbol_key(symbol),
            **extra,
            "identity": self.identity(),
        }
        self.traces.append(row)
        if self.trace_dir:
            p = self.trace_dir / "v1r_dual_lane_trace.jsonl"
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def emit_heartbeat_summary(self) -> dict[str, Any]:
        row = {
            "ts": _now().isoformat(timespec="milliseconds"),
            "event": "HEARTBEAT_SUMMARY",
            "symbol": "",
            "heartbeat": self.heartbeat_fields(),
            "primary_open_keys": self.open_keys("primary"),
            "control_open_keys": self.open_keys("control"),
            "fail_closed": self.fail_closed,
            "fail_reason": self.fail_reason,
            "identity": self.identity(),
        }
        try:
            from small_paper.session_runtime_identity import stamp_session_identity

            stamp_session_identity(row)
        except Exception:
            pass
        self.traces.append(row)
        if self.trace_dir:
            p = self.trace_dir / "v1r_dual_lane_trace.jsonl"
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return row

    def _report_error(self, rec: dict[str, Any]) -> None:
        rec = dict(rec)
        rec.setdefault("event_time", _now().isoformat(timespec="seconds"))
        if self.error_sink is not None:
            try:
                self.error_sink(rec)
            except Exception:
                pass
        if self.trace_dir:
            p = self.trace_dir / "v1r_dual_lane_errors.jsonl"
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _fail_closed(self, reason: str) -> None:
        self.fail_closed = True
        self.fail_reason = str(reason)
        self.stats.state = "FAIL_CLOSED"
        self._trace("FAIL_CLOSED", "", {"reason": reason, "slot_released": False})

    def heartbeat_fields(self) -> dict[str, Any]:
        if int(self.stats.publisher_last_seq or 0) <= 0:
            self._refresh_seq_lag_from_ingress_status()
        lag = self._paper_consumer_seq_lag()
        processed_event_time = ""
        if self.stats.last_event_t:
            try:
                processed_event_time = datetime.fromtimestamp(
                    float(self.stats.last_event_t), JST
                ).isoformat(timespec="milliseconds")
            except (OSError, ValueError, OverflowError):
                processed_event_time = ""
        return {
            **self.identity(),
            "runtime_state": self.stats.state,
            "last_push_at": self.stats.last_push_at,
            "last_processed_sequence": self.stats.last_seq,
            "event_lag_sec": (
                (time.time() - float(self.stats.last_event_t))
                if self.stats.last_event_t
                else 0.0
            ),
            "seq_lag": lag,
            "paper_consumer_seq_lag": lag,
            "processed_event_time": processed_event_time,
            "backlog_direction": self.stats.backlog_direction,
            "max_event_lag_sec": float(self.stats.max_event_lag_sec),
            "max_seq_lag": int(self.stats.max_seq_lag),
            "publisher_last_sequence": int(self.stats.publisher_last_seq),
            "consumer_ack_sequence": int(self.stats.consumer_ack_seq),
            "last_event_t": float(self.stats.last_event_t),
            "primary_open": self.open_n("primary"),
            "control_open": self.open_n("control"),
            "primary_pending": 0,
            "control_pending": 0,
            "fail_closed": self.fail_closed,
            "fail_reason": self.fail_reason,
            "stats": {
                "primary_fills": self.stats.primary_fills,
                "control_fills": self.stats.control_fills,
                "primary_exits": self.stats.primary_exits,
                "control_exits": self.stats.control_exits,
                "guard_triggers": self.stats.guard_triggers,
                "exit_600": self.stats.exit_600,
                "extend_750": self.stats.extend_750,
                "session_close": self.stats.session_close,
                "ticks": self.stats.ticks,
                "tick_matches": self.stats.tick_matches,
                "lookup_miss_with_open": self.stats.lookup_miss_with_open,
                "exceptions": self.stats.exceptions,
                "exact_cache_fallback": self.stats.exact_cache_fallback,
                "cache_hit": self.stats.cache_hit,
                "cache_miss": self.stats.cache_miss,
                "guard_incremental_update": self.stats.guard_incremental_update,
                "path_materialization": self.stats.path_materialization,
            },
            "qty": LOT_QTY,
            "cap": self.cap,
            "trace_dir": str(self.trace_dir) if self.trace_dir else None,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "stats": self.stats.__dict__,
            "primary_open_symbols": self.open_keys("primary"),
            "control_open_symbols": self.open_keys("control"),
            "heartbeat": self.heartbeat_fields(),
            "trace_n": len(self.traces),
            "fail_closed": self.fail_closed,
        }


def _snapshot_summary(snap: Mappingish) -> dict[str, Any]:
    if not isinstance(snap, dict) or not snap:
        return {}
    bid, bq = extract_buy1(snap)
    ask, aq = extract_sell1(snap)
    return {
        "event_time": snap.get("event_time") or snap.get("CurrentPriceTime"),
        "buy1_price": bid,
        "buy1_qty": bq,
        "sell1_price": ask,
        "sell1_qty": aq,
        "current_price": _f(snap.get("CurrentPrice")),
        "fresh_sec": _f(snap.get("board_age_sec") if snap.get("board_age_sec") is not None else snap.get("fresh_sec")),
        "special_quote": bool(snap.get("SpecialQuote") or snap.get("special")),
        "imbalance": snap.get("imbalance")
        if snap.get("imbalance") is not None
        else imbalance_from_qty(bq, aq),
    }


# Process-global singleton used by pilot_runner hooks
_DUAL: Optional[V1RLiveDualLane] = None


def peek_dual_lane() -> Optional[V1RLiveDualLane]:
    """Return the process dual-lane singleton without creating one."""
    return _DUAL


def get_dual_lane(*, trace_dir: Optional[Path] = None) -> Optional[V1RLiveDualLane]:
    global _DUAL
    if not live_primary_enabled():
        return None
    if _DUAL is None:
        _DUAL = V1RLiveDualLane(trace_dir=trace_dir)
    elif trace_dir is not None and _DUAL.trace_dir is None:
        _DUAL.bind_trace_dir(Path(trace_dir))
    return _DUAL


def ensure_dual_lane(*, trace_dir: Optional[Path] = None) -> Optional[V1RLiveDualLane]:
    """Create/bind dual lane with session trace_dir (idempotent)."""
    dual = get_dual_lane(trace_dir=trace_dir)
    if dual is not None and trace_dir is not None and dual.trace_dir is None:
        dual.bind_trace_dir(Path(trace_dir))
    return dual


def reset_dual_lane_for_tests() -> None:
    global _DUAL
    _DUAL = None
