"""One causal pass. Volume-pass bars only. No portfolio replay."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
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
from research.symbol_setup_pullback_range_break_mechanism.trigger import range_break
from small_paper.v1r_live_dual_lane import session_end_for_position


class PopulationMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _compact(
    day: str,
    sym: str,
    lineage: str,
    fold: str,
    board_ok: bool,
    local: Optional[float],
    decomp: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": day,
        "symbol": sym,
        "lineage": lineage,
        "fold": fold,
        "board_pass": bool(board_ok),
        "local_resistance": local,
        "signal_close": decomp.get("signal_close"),
        "signal_bar_finalize_t": decomp.get("signal_bar_finalize_t"),
        "ask0": decomp.get("ask0"),
        "q0_delay_ms": decomp.get("q0_delay_ms"),
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
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
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
    volume_n = 0
    baseline_n = 0
    baseline_board_n = 0
    structure_missing = 0
    fails = 0
    repaired: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    lineage = groups[day]["lineage"]
    fold = groups[day]["fold"]
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
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
            if not trend_up(ind, i):
                continue
            if not pullback_setup(ind, i):
                continue
            if not reversal_rci(ind, i):
                continue
            if not volume_confirm(ind, i):
                continue
            volume_n += 1
            base_ok = bool(price_action(ind, i))
            repaired_ok, local = range_break(ind, i)
            if local is None:
                structure_missing += 1
            if not base_ok and not repaired_ok:
                continue
            if repaired_ok and not base_ok:
                raise PopulationMismatch({"date": day, "symbol": sym, "i": i, "reason": "range_break_outside_baseline"})
            decomp, nfail = _decompose(board, nxt, ind, i, t0, am_end)
            fails += int(nfail)
            snap = _snap_at(board, t0)
            board_ok, _meta = board_support(snap) if snap.get("ok") else (False, {})
            packed = _compact(day, sym, lineage, fold, bool(board_ok), local, decomp)
            if base_ok:
                baseline_n += 1
                baseline.append(packed)
                if board_ok:
                    baseline_board_n += 1
            if repaired_ok:
                repaired.append(packed)
    return {
        "volume": volume_n,
        "baseline": baseline_n,
        "baseline_board": baseline_board_n,
        "structure_missing": structure_missing,
        "fails": fails,
        "repaired": repaired,
        "baseline_rows": baseline,
    }


def scan(sessions: list[dict[str, Any]], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    volume = 0
    baseline = 0
    baseline_board = 0
    structure_missing = 0
    fails = 0
    repaired: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), [str(s) for s in list(sess["symbols"])], groups)
        print(
            f"{day} volume={one['volume']} baseline={one['baseline']} repaired={len(one['repaired'])} parity_fail={one['fails']}",
            flush=True,
        )
        if one["baseline"] != EXPECTED_S5[day] or one["baseline_board"] != EXPECTED_S6[day]:
            raise PopulationMismatch(
                {
                    "date": day,
                    "got": {"baseline": one["baseline"], "baseline_board": one["baseline_board"], "volume": one["volume"]},
                    "expected_baseline": EXPECTED_S5[day],
                    "expected_board": EXPECTED_S6[day],
                }
            )
        if one["fails"]:
            raise PriceParityError({"date": day, "fails": one["fails"]})
        volume += int(one["volume"])
        baseline += int(one["baseline"])
        baseline_board += int(one["baseline_board"])
        structure_missing += int(one["structure_missing"])
        fails += int(one["fails"])
        repaired.extend(one["repaired"])
        baseline_rows.extend(one["baseline_rows"])
        gc.collect()
    return {
        "volume_pass_n": volume,
        "baseline_signal_n": baseline,
        "baseline_board_pass_n": baseline_board,
        "structure_not_available_n": structure_missing,
        "price_parity_fails": fails,
        "repaired": repaired,
        "baseline_rows": baseline_rows,
    }
