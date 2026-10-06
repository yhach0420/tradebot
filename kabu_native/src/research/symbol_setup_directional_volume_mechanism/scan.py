"""One causal pass. Ask-classified dominance is the only added conjunct."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.simple_tech_entry_family import VOLUME_MEDIAN_BARS
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity, minute_epoch
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row, _snap_at
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    board_support,
    evaluable,
    price_action,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from research.symbol_setup_baseline_failure_decomposition.scan import EXPECTED_S5, EXPECTED_S6
from research.symbol_setup_failure_evidence_gap import QUOTE_HORIZONS
from research.symbol_setup_failure_evidence_gap.scan import PriceParityError, _decompose, _joint_mask, _next_ok
from small_paper.v1r_live_dual_lane import session_end_for_position


class PopulationMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


class ContractMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _finite_pos(v: Optional[float]) -> bool:
    return v is not None and v == v and float(v) > 0


class LockedBuilder(SymbolBarBuilder):
    """Same bars as the frozen builder, plus ask-first locked/crossed volume."""

    def __init__(self, *, am_start: float, am_end: float) -> None:
        super().__init__(am_start=am_start, am_end=am_end)
        self.locked_bars: list[dict[str, float]] = []
        self._n = 0
        self._vol = 0.0

    def on_event(
        self,
        *,
        et: float,
        px: Optional[float],
        cum_vol: Optional[float],
        bid: Optional[float],
        ask: Optional[float],
        continuous: bool,
    ) -> Optional[dict[str, float]]:
        t = float(et)
        dvol = 0.0
        if cum_vol is not None and cum_vol == cum_vol and float(cum_vol) >= 0:
            if self.last_cum is not None and float(cum_vol) >= self.last_cum:
                dvol = float(cum_vol) - float(self.last_cum)
        px_f = float(px) if _finite_pos(px) else None
        applied = False
        if px_f is not None and continuous and self.am_start - 1e-12 <= t <= self.am_end + 1e-12:
            applied = minute_epoch(t) + 1e-12 < self.am_end
        locked = False
        if applied and dvol > 0 and _finite_pos(ask) and _finite_pos(bid):
            locked = px_f + 1e-12 >= float(ask) and px_f - 1e-12 <= float(bid)
        n0 = len(self.completed)
        invalid0 = int(self.leak["INVALID_BAR_DROP_N"])
        finalized = super().on_event(et=et, px=px, cum_vol=cum_vol, bid=bid, ask=ask, continuous=continuous)
        if len(self.completed) > n0:
            self.locked_bars.append({"n": float(self._n), "vol": float(self._vol)})
            self._n = 0
            self._vol = 0.0
        elif int(self.leak["INVALID_BAR_DROP_N"]) > invalid0:
            self._n = 0
            self._vol = 0.0
        if locked:
            self._n += 1
            self._vol += dvol
        return finalized

    def close_session(self) -> None:
        super().close_session()
        if len(self.locked_bars) != len(self.completed):
            raise ContractMismatch({"reason": "locked_bar_alignment", "locked": len(self.locked_bars), "bars": len(self.completed)})


def _ratio(ind: dict[str, np.ndarray], i: int) -> Optional[float]:
    w = int(VOLUME_MEDIAN_BARS)
    vol = float(ind["volume"][i])
    base = ind["volume"][i - w : i]
    if int(base.size) != w or not np.all(np.isfinite(base)):
        return None
    med = float(np.median(base))
    if med <= 0:
        return None
    return vol / med


def _compact(day: str, sym: str, lineage: str, fold: str, ind: dict[str, np.ndarray], i: int, board_ok: bool, locked: dict[str, float], decomp: dict[str, Any]) -> dict[str, Any]:
    volume = float(ind["volume"][i])
    ask = float(ind["ask_vol"][i])
    bid = float(ind["bid_vol"][i])
    classified = ask + bid
    row: dict[str, Any] = {
        "date": day,
        "symbol": sym,
        "lineage": lineage,
        "fold": fold,
        "board_pass": bool(board_ok),
        "buy_volume_dominant": bool(ask > bid),
        "total_volume": volume,
        "relative_volume_ratio": _ratio(ind, i),
        "ask_vol": ask,
        "bid_vol": bid,
        "unclassified_volume": volume - classified,
        "classified_volume": classified,
        "classification_fraction": None if volume <= 0 else classified / volume,
        "ask_share_classified": None if classified <= 0 else ask / classified,
        "ask_minus_bid": ask - bid,
        "up_vol": float(ind["up_vol"][i]),
        "down_vol": float(ind["down_vol"][i]),
        "locked_or_crossed_event_n": locked["n"],
        "locked_or_crossed_volume": locked["vol"],
        "signal_close": decomp.get("signal_close"),
        "signal_bar_finalize_t": decomp.get("signal_bar_finalize_t"),
        "ask0": decomp.get("ask0"),
    }
    for h in QUOTE_HORIZONS:
        cell = (decomp.get("horizons") or {}).get(str(h)) or {}
        row[f"h{h}_raw"] = cell.get("raw_mid_bps")
        row[f"h{h}_bid"] = cell.get("bid_anchor_bps")
        row[f"h{h}_atb"] = cell.get("ask_to_bid_bps")
    return row


def _day(day: str, capture: Path, symbols: list[str], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    builders = {s: LockedBuilder(am_start=am_start, am_end=am_end) for s in symbols}
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
        bufs[sym].append(row)
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
    rci_n = 0
    vol_n = 0
    px_n = 0
    board_n = 0
    fails = 0
    rows: list[dict[str, Any]] = []
    lineage = groups[day]["lineage"]
    fold = groups[day]["fold"]
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        if len(builders[sym].locked_bars) != int(raw["minute_epoch"].size):
            raise ContractMismatch({"date": day, "symbol": sym, "reason": "locked_bar_alignment"})
        if int(raw["minute_epoch"].size) == 0:
            continue
        ind = attach_indicators(raw)
        board = bufs[sym].view()
        nxt = _next_ok(_joint_mask(board))
        n = int(ind["close"].size)
        for i in range(n):
            if not evaluable(i, n):
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
            ask = float(ind["ask_vol"][i])
            bidv = float(ind["bid_vol"][i])
            if not (ask == ask and bidv == bidv):
                raise ContractMismatch({"date": day, "symbol": sym, "reason": "non_finite_signed_volume"})
            decomp, nfail = _decompose(board, nxt, ind, i, t0, am_end)
            fails += int(nfail)
            base_ok = bool(price_action(ind, i))
            snap = _snap_at(board, t0)
            board_ok, _meta = board_support(snap) if snap.get("ok") else (False, {})
            packed = _compact(day, sym, lineage, fold, ind, i, bool(board_ok), builders[sym].locked_bars[i], decomp)
            packed["price_action_pass"] = base_ok
            rows.append(packed)
            if base_ok:
                px_n += 1
                if board_ok:
                    board_n += 1
    return {"rci": rci_n, "volume": vol_n, "price": px_n, "board": board_n, "fails": fails, "rows": rows}


def scan(sessions: list[dict[str, Any]], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    rci = volume = price = board = fails = 0
    rows: list[dict[str, Any]] = []
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), [str(s) for s in list(sess["symbols"])], groups)
        print(
            f"{day} rci={one['rci']} volume={one['volume']} price={one['price']} dominant_price={sum(1 for r in one['rows'] if r['buy_volume_dominant'] and r['price_action_pass'])}",
            flush=True,
        )
        if one["price"] != EXPECTED_S5[day] or one["board"] != EXPECTED_S6[day]:
            raise PopulationMismatch({"date": day, "price": one["price"], "board": one["board"], "expected_price": EXPECTED_S5[day], "expected_board": EXPECTED_S6[day]})
        if one["fails"]:
            raise PriceParityError({"date": day, "fails": one["fails"]})
        rci += int(one["rci"])
        volume += int(one["volume"])
        price += int(one["price"])
        board += int(one["board"])
        fails += int(one["fails"])
        rows.extend(one["rows"])
        gc.collect()
    return {"rci_pass_n": rci, "volume_pass_n": volume, "price_action_pass_n": price, "baseline_board_pass_n": board, "price_parity_fails": fails, "volume_rows": rows}
