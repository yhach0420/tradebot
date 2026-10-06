"""Ingress-causal Full Strategy engine for frozen V3. No PnL. No last_session_bid. No portfolio_replay."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare
from research.new_full_strategy_architecture_redesign_v1 import AM_END_HM, AM_START_HM, SESSION_FLATTEN_HM
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import (
    ask_ok,
    ask_px,
    bid_ok,
    bid_px,
    payload_of,
    quote_snap,
)
from research.simple_full_strategy_discovery_v1 import POSITION_CAP
from research.simple_tech_entry_family import WARMUP_BARS
from research.simple_tech_entry_family.bars import minute_epoch
from research.simple_tech_entry_family.stages import attach_indicators, trend_up

JST = ZoneInfo("Asia/Tokyo")
TRUE = "TRUE"
FALSE = "FALSE"
UNKNOWN = "UNKNOWN"
EXIT_TECH = "Z_MA_TREND_LOSS"
EXIT_SESSION = "SESSION_FLATTEN"


def parse_iso(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def arrival_epoch(rec: dict[str, Any]) -> tuple[Optional[float], str]:
    t = parse_iso(rec.get("received_at_jst"))
    if t is not None:
        return float(t), "received_at_jst"
    t = parse_iso(rec.get("received_at"))
    if t is not None:
        return float(t), "received_at"
    return None, ""


def timer_epochs(day: str) -> list[float]:
    start = hm_epoch(day, int(AM_START_HM[0]), int(AM_START_HM[1])) + 60.0
    flatten = hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1]))
    out: list[float] = []
    t = float(start)
    while t <= float(flatten) + 1e-9:
        out.append(t)
        t += 60.0
    return out


def _fin(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


class SessionEngine:
    def __init__(self, day: str, universe: list[str], *, debug: bool = False) -> None:
        self.day = str(day)
        self.universe = [_bare(s) for s in universe if _bare(s)]
        self.uni_set = set(self.universe)
        self.debug = bool(debug)
        self.am_start = hm_epoch(day, int(AM_START_HM[0]), int(AM_START_HM[1]))
        self.flatten_t = hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1]))
        self.am_end = hm_epoch(day, int(AM_END_HM[0]), int(AM_END_HM[1]))
        self.timers = timer_epochs(day)
        self.timer_i = 0
        self.last_arrival = float("-inf")
        self.flattened = False
        self.closes: dict[str, list[float]] = {s: [] for s in self.universe}
        self.minutes: dict[str, list[float]] = {s: [] for s in self.universe}
        self.ind: dict[str, Optional[dict[str, np.ndarray]]] = {s: None for s in self.universe}
        self.pending_bar: dict[str, Optional[dict[str, float]]] = {s: None for s in self.universe}
        self.last_cum: dict[str, Optional[float]] = {s: None for s in self.universe}
        self.last_rec: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.last_pay: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.last_quote_seq: dict[str, int] = {s: -1 for s in self.universe}
        self.entry_pending: list[dict[str, Any]] = []
        self.positions: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []
        self.unfilled_session: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.flags = {
            "MISSING_ARRIVAL_N": 0,
            "MISSING_SEQUENCE_N": 0,
            "SEQUENCE_DISORDER_N": 0,
            "ARRIVAL_FIELD_RECEIVED_AT_JST_N": 0,
            "ARRIVAL_FIELD_RECEIVED_AT_N": 0,
            "ENTRY_FILL_AFTER_FLATTEN_N": 0,
            "WALKBACK_SESSION_EXIT_N": 0,
            "DUPLICATE_EXIT_FILL_N": 0,
            "SIGNAL_AFTER_FLATTEN_N": 0,
            "CURRENT_PRICE_TIME_AS_ARRIVAL_N": 0,
        }
        self._last_seq: Optional[int] = None
        self.bar_close: dict[str, dict[float, float]] = {s: {} for s in self.universe} if debug else {}
        self.breadth_log: list[dict[str, Any]] = [] if debug else []

    def ingest(self, rec: dict[str, Any]) -> None:
        seq_raw = rec.get("sequence")
        if seq_raw is None or seq_raw == "":
            self.flags["MISSING_SEQUENCE_N"] += 1
            return
        seq = int(seq_raw)
        if self._last_seq is not None and seq < int(self._last_seq):
            self.flags["SEQUENCE_DISORDER_N"] += 1
        self._last_seq = seq
        arrival, field = arrival_epoch(rec)
        if arrival is None:
            self.flags["MISSING_ARRIVAL_N"] += 1
            return
        if field == "received_at_jst":
            self.flags["ARRIVAL_FIELD_RECEIVED_AT_JST_N"] += 1
        elif field == "received_at":
            self.flags["ARRIVAL_FIELD_RECEIVED_AT_N"] += 1
        self._fire_due(float(arrival))
        self._apply(rec, float(arrival), seq)
        self.last_arrival = float(arrival)

    def finish(self) -> None:
        while self.timer_i < len(self.timers):
            self._fire_timer(float(self.timers[self.timer_i]))
            self.timer_i += 1
        self._expire_unfilled_exits()

    def result(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "date": self.day,
            "trades": list(self.trades),
            "unfilled_session_exits": list(self.unfilled_session),
            "signals": list(self.signals) if self.debug else [],
            "flags": dict(self.flags),
            "universe_n": len(self.universe),
            "entry_pending_n": len(self.entry_pending),
            "open_n": len(self.positions),
        }
        if self.debug:
            out["bar_close"] = {s: dict(v) for s, v in self.bar_close.items()}
            out["breadth_log"] = list(self.breadth_log)
        return out

    def _fire_due(self, arrival: float) -> None:
        while self.timer_i < len(self.timers):
            t = float(self.timers[self.timer_i])
            if self.last_arrival < t <= float(arrival) + 1e-12:
                self._fire_timer(t)
                self.timer_i += 1
                continue
            break

    def _fire_timer(self, t: float) -> None:
        e = float(t) - 60.0
        for sym in self.universe:
            self._finalize_symbol(sym, e, float(t))
        is_flatten = abs(float(t) - float(self.flatten_t)) <= 1e-9
        if not is_flatten and not self.flattened:
            self._breadth_and_signals(e, float(t))
            self._try_standing_entry_fills(float(t))
        self._technical_exits(e, float(t))
        if is_flatten:
            self._on_flatten(float(t))
        self._try_standing_exit_fills(float(t))

    def _finalize_symbol(self, sym: str, e: float, t: float) -> None:
        slot = self.pending_bar.get(sym)
        if not slot or abs(float(slot["minute_epoch"]) - float(e)) > 1e-9:
            return
        close = float(slot["close"])
        self.closes[sym].append(close)
        self.minutes[sym].append(float(e))
        if self.debug:
            self.bar_close[sym][float(e)] = close
        arr = np.asarray(self.closes[sym], dtype=float)
        bars = {
            "close": arr,
            "open": arr,
            "high": arr,
            "low": arr,
            "volume": np.ones(arr.size, dtype=float),
        }
        self.ind[sym] = attach_indicators(bars)
        self.pending_bar[sym] = None

    def _state_at(self, sym: str, minute_e: float) -> str:
        mins = self.minutes.get(sym) or []
        if not mins:
            return UNKNOWN
        idx = None
        for i, m in enumerate(mins):
            if abs(float(m) - float(minute_e)) <= 1e-9:
                idx = i
                break
        if idx is None:
            return UNKNOWN
        if int(idx) < int(WARMUP_BARS) - 1:
            return UNKNOWN
        ind = self.ind.get(sym)
        if not ind:
            return UNKNOWN
        try:
            e9 = float(ind["ema9"][idx])
            e21 = float(ind["ema21"][idx])
        except (TypeError, ValueError, KeyError, IndexError):
            return UNKNOWN
        if e9 != e9 or e21 != e21:
            return UNKNOWN
        return TRUE if trend_up(ind, int(idx)) else FALSE

    def _breadth_and_signals(self, e: float, t: float) -> None:
        prev = float(e) - 60.0
        comparable: list[str] = []
        for sym in self.universe:
            a = self._state_at(sym, prev)
            b = self._state_at(sym, e)
            if a in (TRUE, FALSE) and b in (TRUE, FALSE):
                comparable.append(sym)
        n_prev = sum(1 for s in comparable if self._state_at(s, prev) == TRUE)
        n_curr = sum(1 for s in comparable if self._state_at(s, e) == TRUE)
        expanding = n_curr > n_prev
        if self.debug:
            self.breadth_log.append(
                {
                    "t": float(t),
                    "e": float(e),
                    "comparable_n": len(comparable),
                    "n_prev": n_prev,
                    "n_curr": n_curr,
                    "expanding": expanding,
                    "comparable": list(comparable),
                }
            )
        if not expanding:
            return
        for sym in comparable:
            if self._state_at(sym, prev) == FALSE and self._state_at(sym, e) == TRUE:
                self.entry_pending.append({"symbol": sym, "signal_t0": float(t)})
                self.signals.append({"symbol": sym, "signal_t0": float(t), "minute_e": float(e)})

    def _on_flatten(self, t: float) -> None:
        self.entry_pending.clear()
        self.flattened = True
        for sym, pos in self.positions.items():
            if pos.get("exit_pending"):
                continue
            pos["exit_pending"] = True
            pos["exit_reason"] = EXIT_SESSION
            pos["exit_fire_t"] = float(t)

    def _technical_exits(self, e: float, t: float) -> None:
        for sym, pos in list(self.positions.items()):
            if pos.get("exit_pending"):
                continue
            if float(t) <= float(pos["fill_t"]) + 1e-12:
                continue
            if self._state_at(sym, e) == FALSE:
                pos["exit_pending"] = True
                pos["exit_reason"] = EXIT_TECH
                pos["exit_fire_t"] = float(t)

    def _occupancy(self) -> int:
        return len(self.positions)

    def _can_entry_fill(self, sym: str, arrival: float) -> bool:
        if self.flattened or float(arrival) >= float(self.flatten_t) - 1e-12:
            return False
        if sym in self.positions:
            return False
        if self._occupancy() >= int(POSITION_CAP):
            return False
        return True

    def _fill_entry(self, sym: str, fill_t: float, px: float, seq: int) -> None:
        if self.flattened or float(fill_t) >= float(self.flatten_t) - 1e-12:
            self.flags["ENTRY_FILL_AFTER_FLATTEN_N"] += 1
            return
        if not self._can_entry_fill(sym, float(fill_t)):
            return
        pend = None
        keep: list[dict[str, Any]] = []
        for p in self.entry_pending:
            if pend is None and p.get("symbol") == sym and float(p["signal_t0"]) <= float(fill_t) + 1e-12:
                pend = p
            else:
                keep.append(p)
        if pend is None:
            return
        self.entry_pending = keep
        self.positions[sym] = {
            "symbol": sym,
            "fill_t": float(fill_t),
            "fill_px": float(px),
            "signal_t0": float(pend["signal_t0"]),
            "fill_seq": int(seq),
            "exit_pending": False,
            "exit_reason": "",
            "exit_fire_t": None,
            "exit_filled": False,
        }

    def _fill_exit(self, sym: str, fill_t: float, px: float, seq: int) -> None:
        pos = self.positions.get(sym)
        if not pos or not pos.get("exit_pending"):
            return
        if pos.get("exit_filled"):
            self.flags["DUPLICATE_EXIT_FILL_N"] += 1
            return
        if float(fill_t) >= float(self.am_end) - 1e-12:
            return
        reason = str(pos.get("exit_reason") or "")
        if reason == EXIT_SESSION and float(fill_t) + 1e-9 < float(self.flatten_t):
            self.flags["WALKBACK_SESSION_EXIT_N"] += 1
            return
        pos["exit_filled"] = True
        self.trades.append(
            {
                "date": self.day,
                "symbol": sym,
                "signal_t0": float(pos["signal_t0"]),
                "fill_t": float(pos["fill_t"]),
                "fill_price": float(pos["fill_px"]),
                "exit_t": float(fill_t),
                "exit_price": float(px),
                "exit_reason": reason,
                "fill_seq": int(pos["fill_seq"]),
                "exit_seq": int(seq),
                "hold_sec": float(fill_t) - float(pos["fill_t"]),
            }
        )
        del self.positions[sym]

    def _standing_snap(self, sym: str, clock_t: float) -> Optional[dict[str, Any]]:
        rec = self.last_rec.get(sym)
        pay = self.last_pay.get(sym)
        if rec is None or pay is None:
            return None
        return quote_snap(rec, pay, float(clock_t))

    def _try_standing_entry_fills(self, t: float) -> None:
        if self.flattened:
            return
        cands: list[tuple[int, str]] = []
        seen: set[str] = set()
        for p in self.entry_pending:
            sym = str(p.get("symbol") or "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            cands.append((int(self.last_quote_seq.get(sym, -1)), sym))
        cands.sort(key=lambda x: x[0])
        for _seq, sym in cands:
            snap = self._standing_snap(sym, float(t))
            if snap is None or not ask_ok(snap):
                continue
            px = ask_px(snap)
            if px is None:
                continue
            self._fill_entry(sym, float(t), float(px), int(self.last_quote_seq.get(sym, -1)))

    def _try_standing_exit_fills(self, t: float) -> None:
        cands: list[tuple[int, str]] = []
        for sym, pos in self.positions.items():
            if pos.get("exit_pending") and not pos.get("exit_filled"):
                cands.append((int(self.last_quote_seq.get(sym, -1)), sym))
        cands.sort(key=lambda x: x[0])
        for seq, sym in cands:
            snap = self._standing_snap(sym, float(t))
            if snap is None or not bid_ok(snap):
                continue
            px = bid_px(snap)
            if px is None:
                continue
            self._fill_exit(sym, float(t), float(px), int(seq))

    def _apply(self, rec: dict[str, Any], arrival: float, seq: int) -> None:
        pay = payload_of(rec)
        sym = _bare(rec.get("symbol") or pay.get("Symbol"))
        if sym not in self.uni_set:
            return
        self.last_rec[sym] = rec
        self.last_pay[sym] = pay
        self.last_quote_seq[sym] = int(seq)
        if float(arrival) < float(self.am_end) - 1e-12:
            snap = quote_snap(rec, pay, float(arrival))
            if self.flattened:
                pos = self.positions.get(sym)
                if pos and pos.get("exit_pending") and not pos.get("exit_filled") and bid_ok(snap):
                    px = bid_px(snap)
                    if px is not None:
                        self._fill_exit(sym, float(arrival), float(px), int(seq))
            else:
                if any(p.get("symbol") == sym for p in self.entry_pending) and ask_ok(snap):
                    px = ask_px(snap)
                    if px is not None:
                        self._fill_entry(sym, float(arrival), float(px), int(seq))
                pos = self.positions.get(sym)
                if pos and pos.get("exit_pending") and not pos.get("exit_filled") and bid_ok(snap):
                    px = bid_px(snap)
                    if px is not None:
                        self._fill_exit(sym, float(arrival), float(px), int(seq))
        if float(arrival) >= float(self.am_end) - 1e-12:
            return
        m = minute_epoch(float(arrival))
        if m + 60.0 <= self._last_fired_timer() + 1e-12:
            return
        if m + 1e-12 < float(self.am_start):
            return
        px = _fin((pay or {}).get("CurrentPrice"))
        if px is None:
            return
        cum = None
        try:
            cv = float((pay or {}).get("TradingVolume"))
            if cv == cv:
                cum = cv
        except (TypeError, ValueError):
            cum = None
        vol = 0.0
        prev_c = self.last_cum.get(sym)
        if cum is not None:
            if prev_c is not None and cum >= prev_c:
                vol = float(cum - prev_c)
            self.last_cum[sym] = cum
        slot = self.pending_bar.get(sym)
        if slot is None or abs(float(slot["minute_epoch"]) - float(m)) > 1e-9:
            self.pending_bar[sym] = {
                "minute_epoch": float(m),
                "open": float(px),
                "high": float(px),
                "low": float(px),
                "close": float(px),
                "volume": float(vol),
                "n": 1.0,
            }
        else:
            slot["high"] = max(float(slot["high"]), float(px))
            slot["low"] = min(float(slot["low"]), float(px))
            slot["close"] = float(px)
            slot["volume"] = float(slot["volume"]) + float(vol)
            slot["n"] = float(slot["n"]) + 1.0

    def _last_fired_timer(self) -> float:
        if self.timer_i <= 0:
            return float("-inf")
        return float(self.timers[self.timer_i - 1])

    def _expire_unfilled_exits(self) -> None:
        for sym, pos in list(self.positions.items()):
            if pos.get("exit_filled"):
                continue
            self.unfilled_session.append(
                {
                    "date": self.day,
                    "symbol": sym,
                    "fill_t": float(pos["fill_t"]),
                    "fill_price": float(pos["fill_px"]),
                    "exit_reason": str(pos.get("exit_reason") or ""),
                    "SESSION_EXIT_UNFILLED": True,
                }
            )
            del self.positions[sym]


def replay_records(day: str, universe: list[str], records: list[dict[str, Any]], *, debug: bool = False) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: int(r.get("sequence") or 0))
    eng = SessionEngine(day, universe, debug=debug)
    for rec in ordered:
        eng.ingest(rec)
    eng.finish()
    if eng.flags.get("SIGNAL_AFTER_FLATTEN_N"):
        pass
    return eng.result()
