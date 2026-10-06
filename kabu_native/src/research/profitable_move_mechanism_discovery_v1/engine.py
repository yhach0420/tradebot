"""Causal 1m bars + executable Ask/Bid quotes. No strategy positions. No PnL."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare
from research.new_full_strategy_architecture_construction_v2 import AM_END_HM, AM_START_HM, SESSION_FLATTEN_HM
from research.new_full_strategy_implementation_and_dev_eval_v1.engine import arrival_epoch, timer_epochs
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import (
    ask_ok,
    ask_px,
    bid_ok,
    bid_px,
    payload_of,
    quote_snap,
)
from research.simple_tech_entry_family.bars import minute_epoch
from research.simple_tech_entry_family.stages import board_support


def _fin(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


def _empty_bar_lists() -> dict[str, list[float]]:
    return {
        "minute_epoch": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
        "finalize_t": [],
    }


class DiscoveryEngine:
    """Timer-before-coincident-ingress 1m OHLCV plus executable quote tapes."""

    def __init__(self, day: str, universe: list[str]) -> None:
        self.day = str(day)
        self.universe = [_bare(s) for s in universe if _bare(s)]
        self.uni_set = set(self.universe)
        self.am_start = hm_epoch(day, int(AM_START_HM[0]), int(AM_START_HM[1]))
        self.flatten_t = hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1]))
        self.am_end = hm_epoch(day, int(AM_END_HM[0]), int(AM_END_HM[1]))
        self.timers = timer_epochs(day)
        self.timer_i = 0
        self.last_arrival = float("-inf")
        self.bars: dict[str, dict[str, list[float]]] = {s: _empty_bar_lists() for s in self.universe}
        self.pending_bar: dict[str, Optional[dict[str, float]]] = {s: None for s in self.universe}
        self.last_cum: dict[str, Optional[float]] = {s: None for s in self.universe}
        self.last_rec: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.last_pay: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.ask_t: dict[str, list[float]] = {s: [] for s in self.universe}
        self.ask_px: dict[str, list[float]] = {s: [] for s in self.universe}
        self.bid_t: dict[str, list[float]] = {s: [] for s in self.universe}
        self.bid_px: dict[str, list[float]] = {s: [] for s in self.universe}
        self.standing: dict[float, dict[str, dict[str, Any]]] = {}
        self.flags = {
            "MISSING_ARRIVAL_N": 0,
            "MISSING_SEQUENCE_N": 0,
            "SEQUENCE_DISORDER_N": 0,
            "ARRIVAL_FIELD_RECEIVED_AT_JST_N": 0,
            "ARRIVAL_FIELD_RECEIVED_AT_N": 0,
            "CURRENT_PRICE_TIME_AS_ARRIVAL_N": 0,
            "FEATURE_LOOKAHEAD_N": 0,
            "LABEL_USED_AS_INPUT_N": 0,
        }
        self._last_seq: Optional[int] = None

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
        self._apply(rec, float(arrival))
        self.last_arrival = float(arrival)

    def finish(self) -> None:
        while self.timer_i < len(self.timers):
            self._fire_timer(float(self.timers[self.timer_i]))
            self.timer_i += 1

    def result(self) -> dict[str, Any]:
        bars: dict[str, dict[str, np.ndarray]] = {}
        ask: dict[str, dict[str, np.ndarray]] = {}
        bid: dict[str, dict[str, np.ndarray]] = {}
        for s in self.universe:
            b = self.bars[s]
            bars[s] = {k: np.asarray(v, dtype=float) for k, v in b.items()}
            ask[s] = {
                "t": np.asarray(self.ask_t[s], dtype=float),
                "px": np.asarray(self.ask_px[s], dtype=float),
            }
            bid[s] = {
                "t": np.asarray(self.bid_t[s], dtype=float),
                "px": np.asarray(self.bid_px[s], dtype=float),
            }
        return {
            "date": self.day,
            "universe": list(self.universe),
            "timers": [float(t) for t in self.timers],
            "flatten_t": float(self.flatten_t),
            "am_end": float(self.am_end),
            "flags": dict(self.flags),
            "bars": bars,
            "ask": ask,
            "bid": bid,
            "standing": self.standing,
        }

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
        row: dict[str, dict[str, Any]] = {}
        for sym in self.universe:
            row[sym] = self._standing_pack(sym, float(t))
        self.standing[float(t)] = row

    def _finalize_symbol(self, sym: str, e: float, t: float) -> None:
        slot = self.pending_bar.get(sym)
        if not slot or abs(float(slot["minute_epoch"]) - float(e)) > 1e-9:
            return
        b = self.bars[sym]
        b["minute_epoch"].append(float(e))
        b["open"].append(float(slot["open"]))
        b["high"].append(float(slot["high"]))
        b["low"].append(float(slot["low"]))
        b["close"].append(float(slot["close"]))
        b["volume"].append(float(slot["volume"]))
        b["finalize_t"].append(float(t))
        self.pending_bar[sym] = None

    def _standing_snap(self, sym: str, clock_t: float) -> Optional[dict[str, Any]]:
        rec = self.last_rec.get(sym)
        pay = self.last_pay.get(sym)
        if rec is None or pay is None:
            return None
        return quote_snap(rec, pay, float(clock_t))

    def _standing_pack(self, sym: str, t: float) -> dict[str, Any]:
        snap = self._standing_snap(sym, float(t))
        if snap is None:
            return {
                "ask_ok": False,
                "bid_ok": False,
                "ask": float("nan"),
                "bid": float("nan"),
                "bid_qty": float("nan"),
                "ask_qty": float("nan"),
                "board_support": False,
                "bid_gt_ask": False,
            }
        ap = ask_px(snap)
        bp = bid_px(snap)
        def _qty(v: Any) -> float:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return float("nan")
            return x if x == x else float("nan")

        bqf = _qty(snap.get("bid_qty"))
        aqf = _qty(snap.get("ask_qty"))
        bid_gt = bqf == bqf and aqf == aqf and bqf > aqf
        ok_board, _meta = board_support(snap)
        return {
            "ask_ok": bool(ask_ok(snap)),
            "bid_ok": bool(bid_ok(snap)),
            "ask": float(ap) if ap is not None else float("nan"),
            "bid": float(bp) if bp is not None else float("nan"),
            "bid_qty": bqf,
            "ask_qty": aqf,
            "board_support": bool(ok_board),
            "bid_gt_ask": bool(bid_gt),
        }

    def _last_fired_timer(self) -> float:
        if self.timer_i <= 0:
            return float("-inf")
        return float(self.timers[self.timer_i - 1])

    def _apply(self, rec: dict[str, Any], arrival: float) -> None:
        pay = payload_of(rec)
        sym = _bare(rec.get("symbol") or pay.get("Symbol"))
        if sym not in self.uni_set:
            return
        self.last_rec[sym] = rec
        self.last_pay[sym] = pay
        if float(arrival) < float(self.am_end) - 1e-12:
            snap = quote_snap(rec, pay, float(arrival))
            if ask_ok(snap):
                px = ask_px(snap)
                if px is not None:
                    self.ask_t[sym].append(float(arrival))
                    self.ask_px[sym].append(float(px))
            if bid_ok(snap):
                px = bid_px(snap)
                if px is not None:
                    self.bid_t[sym].append(float(arrival))
                    self.bid_px[sym].append(float(px))
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
            }
        else:
            slot["high"] = max(float(slot["high"]), float(px))
            slot["low"] = min(float(slot["low"]), float(px))
            slot["close"] = float(px)
            slot["volume"] = float(slot["volume"]) + float(vol)


def replay_records(day: str, universe: list[str], records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: int(r.get("sequence") or 0))
    eng = DiscoveryEngine(day, universe)
    for rec in ordered:
        eng.ingest(rec)
    eng.finish()
    return eng.result()
