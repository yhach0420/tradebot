"""Coverage and baseline-count pass. No markout and no portfolio replay."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity, minute_epoch
from research.simple_tech_entry_family.harvest import _bare, _board_row
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    evaluable,
    price_action,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from research.symbol_setup_baseline_failure_decomposition.scan import EXPECTED_S5
from small_paper.v1r_live_dual_lane import session_end_for_position


class PopulationMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


class ContractMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _finite(v: Optional[float]) -> bool:
    return v is not None and v == v


class Probe:
    """Mirror of SymbolBarBuilder volume buckets, plus quote-presence volume."""

    def __init__(self, *, am_start: float, am_end: float) -> None:
        self.am_start = float(am_start)
        self.am_end = float(am_end)
        self.pending_m: Optional[float] = None
        self.pending: Optional[dict[str, float]] = None
        self.last_px: Optional[float] = None
        self.last_cum: Optional[float] = None
        self.ask = 0.0
        self.bid = 0.0
        self.volume = 0.0
        self.both_quote_volume = 0.0
        self.executed_in_bars = 0.0

    def on_event(
        self,
        *,
        et: float,
        px: Optional[float],
        cum_vol: Optional[float],
        bid: Optional[float],
        ask: Optional[float],
        continuous: bool,
    ) -> None:
        t = float(et)
        dvol = 0.0
        if cum_vol is not None and cum_vol == cum_vol and cum_vol >= 0:
            if self.last_cum is not None and cum_vol >= self.last_cum:
                dvol = float(cum_vol - self.last_cum)
            self.last_cum = float(cum_vol)
        px_f = float(px) if px is not None and px == px and px > 0 else None
        if t < self.am_start - 1e-12:
            if px_f is not None:
                self.last_px = px_f
            return
        if t > self.am_end + 1e-12:
            return
        if not continuous:
            if px_f is not None:
                self.last_px = px_f
            return
        if px_f is None:
            if dvol > 0 and self.pending is not None:
                self.pending["volume"] += dvol
                self.pending["executed"] += dvol
            return
        m = minute_epoch(t)
        if self.pending_m is not None and m > self.pending_m + 1e-12:
            if self.pending is not None:
                self.volume += self.pending["volume"]
                self.ask += self.pending["ask"]
                self.bid += self.pending["bid"]
                self.both_quote_volume += self.pending["both"]
                self.executed_in_bars += self.pending["executed"]
            self.pending_m = None
            self.pending = None
        if m + 1e-12 >= self.am_end:
            if px_f is not None:
                self.last_px = px_f
            return
        if self.pending is None:
            self.pending_m = m
            self.pending = {"volume": 0.0, "ask": 0.0, "bid": 0.0, "both": 0.0, "executed": 0.0}
        if dvol > 0:
            self.pending["volume"] += dvol
            self.pending["executed"] += dvol
            both = _finite(ask) and float(ask) > 0 and _finite(bid) and float(bid) > 0
            if both:
                self.pending["both"] += dvol
            if _finite(ask) and float(ask) > 0 and px_f + 1e-12 >= float(ask):
                self.pending["ask"] += dvol
            elif _finite(bid) and float(bid) > 0 and px_f - 1e-12 <= float(bid):
                self.pending["bid"] += dvol
        self.last_px = px_f

    def close_session(self) -> None:
        self.pending = None
        self.pending_m = None


def _day(day: str, capture: Path, symbols: list[str], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    probes = {s: Probe(am_start=am_start, am_end=am_end) for s in symbols}
    uni = set(symbols)
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        row = _board_row(pay, float(et))
        px = row["px"] if row["px"] == row["px"] else None
        bid = row["bid"] if row["bid"] == row["bid"] else None
        ask = row["ask"] if row["ask"] == row["ask"] else None
        kwargs = {
            "et": float(et),
            "px": px,
            "cum_vol": row.get("cum_vol"),
            "bid": bid,
            "ask": ask,
            "continuous": bool(row.get("continuous")),
        }
        builders[sym].on_event(**kwargs)
        probes[sym].on_event(**kwargs)
    eligible = 0
    both = 0
    volume = 0.0
    ask_vol = 0.0
    bid_vol = 0.0
    up_vol = 0.0
    down_vol = 0.0
    probe_volume = 0.0
    probe_ask = 0.0
    probe_bid = 0.0
    both_quote_volume = 0.0
    rci_n = 0
    vol_n = 0
    px_n = 0
    for sym in symbols:
        builders[sym].close_session()
        probes[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        n = int(raw["minute_epoch"].size)
        if n:
            eligible += n
            both += int(((raw["ask_vol"] == raw["ask_vol"]) & (raw["bid_vol"] == raw["bid_vol"])).sum())
            volume += float(raw["volume"].sum())
            ask_vol += float(raw["ask_vol"].sum())
            bid_vol += float(raw["bid_vol"].sum())
            up_vol += float(raw["up_vol"].sum())
            down_vol += float(raw["down_vol"].sum())
        probe_volume += probes[sym].volume
        probe_ask += probes[sym].ask
        probe_bid += probes[sym].bid
        both_quote_volume += probes[sym].both_quote_volume
        if n == 0:
            continue
        ind = attach_indicators(raw)
        nbar = int(ind["close"].size)
        for i in range(nbar):
            if not evaluable(i, nbar):
                continue
            t0 = float(ind["finalize_t"][i])
            if t0 + float(DEV_WAIT_SEC) > am_end + 1e-12:
                continue
            if not trend_up(ind, i) or not pullback_setup(ind, i) or not reversal_rci(ind, i):
                continue
            rci_n += 1
            if not volume_confirm(ind, i):
                continue
            vol_n += 1
            if price_action(ind, i):
                px_n += 1
    if abs(probe_volume - volume) > 1e-4 or abs(probe_ask - ask_vol) > 1e-4 or abs(probe_bid - bid_vol) > 1e-4:
        raise ContractMismatch(
            {
                "date": day,
                "bar_volume": volume,
                "probe_volume": probe_volume,
                "bar_ask": ask_vol,
                "probe_ask": probe_ask,
                "bar_bid": bid_vol,
                "probe_bid": probe_bid,
            }
        )
    if ask_vol + bid_vol > volume + 1e-4:
        raise ContractMismatch({"date": day, "reason": "classified_exceeds_total", "volume": volume, "ask": ask_vol, "bid": bid_vol})
    return {
        "date": day,
        "lineage": groups[day]["lineage"],
        "fold": groups[day]["fold"],
        "eligible_bar_n": eligible,
        "both_available_n": both,
        "volume": volume,
        "ask_vol": ask_vol,
        "bid_vol": bid_vol,
        "up_vol": up_vol,
        "down_vol": down_vol,
        "both_quote_volume": both_quote_volume,
        "rci_pass_n": rci_n,
        "volume_pass_n": vol_n,
        "price_action_pass_n": px_n,
    }


def scan(sessions: list[dict[str, Any]], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    rows = []
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), [str(s) for s in list(sess["symbols"])], groups)
        print(
            f"{day} bars={one['eligible_bar_n']} rci={one['rci_pass_n']} volume={one['volume_pass_n']} price={one['price_action_pass_n']}",
            flush=True,
        )
        if one["price_action_pass_n"] != EXPECTED_S5[day]:
            raise PopulationMismatch({"date": day, "price": one["price_action_pass_n"], "expected": EXPECTED_S5[day]})
        rows.append(one)
        gc.collect()
    return {"days": rows}
