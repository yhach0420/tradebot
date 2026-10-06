"""State-path scan. Portfolio replay is not called."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row, _snap_at
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    board_support,
    evaluable,
    execution_eligible,
    price_action,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from research.simple_tech_redesign.v28_harvest import first_causal_bid
from research.symbol_setup_baseline_complete_development.replay import thesis_exit
from research.symbol_setup_baseline_complete_strategy_precommit.runner import account, trade_bps
from research.symbol_setup_baseline_failure_decomposition.scan import EXPECTED_FILL, EXPECTED_S5, EXPECTED_S6, FROZEN_TRADES
from research.symbol_setup_exit_noise_rca import CLOCKS
from research.symbol_setup_exit_noise_rca.state import bar_state, classify_slope, duration_bars
from small_paper.v1r_live_dual_lane import session_end_for_position


class PopulationMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _f(v: object) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x <= 0:
        return None
    return x


def _bid(board: dict, event_t: float, am_end: float) -> Optional[dict[str, float]]:
    found = first_causal_bid(board, {}, event_t=float(event_t), fill_px=1.0, sess_end=float(am_end), leak={})
    if found.get("miss") or found.get("exit_bid") is None:
        return None
    return {"t": float(found["exit_t"]), "bid": float(found["exit_bid"])}


def _with_bid(state: Optional[dict[str, Any]], board: dict, am_end: float) -> Optional[dict[str, Any]]:
    if state is None:
        return None
    row = dict(state)
    got = _bid(board, row["finalize_t"], am_end)
    row["bid"] = None if got is None else got["bid"]
    row["bid_t"] = None if got is None else got["t"]
    return row


def _marks(board: dict, loss_t: float, loss_bid: Optional[float], am_end: float) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for h in CLOCKS:
        got = _bid(board, float(loss_t) + float(h), am_end)
        if got is None or loss_bid is None or loss_bid <= 0:
            out[f"bid_{h}s_bps"] = None
        else:
            out[f"bid_{h}s_bps"] = (got["bid"] / float(loss_bid) - 1.0) * 10000.0
    return out


def _episode(ind: dict, board: dict, origin: int, am_end: float, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    n = int(ind["close"].size)
    seq: list[Optional[dict[str, Any]]] = []
    for i in range(int(origin), n):
        if float(ind["finalize_t"][i]) > float(am_end) + 1e-12:
            break
        state = _with_bid(bar_state(ind, i), board, am_end)
        seq.append(state)
        if state is None:
            break
    loss_pos = next((k for k, row in enumerate(seq) if row is not None and not row["thesis_live"]), None)
    if loss_pos is None:
        return None
    loss = seq[loss_pos]
    assert loss is not None
    forward: list[Optional[dict[str, Any]]] = []
    for step in range(1, 4):
        j = loss_pos + step
        forward.append(seq[j] if j < len(seq) else None)
    loss_bid = loss.get("bid")
    recovered_at = next((k for k, row in enumerate(forward, start=1) if row is not None and row["thesis_live"]), None)
    classified = classify_slope(forward, loss_bid) if loss["slope_only"] else None
    bar_bps = {}
    for step, row in enumerate(forward, start=1):
        if row is None or row.get("bid") is None or loss_bid is None or float(loss_bid) <= 0:
            bar_bps[f"bid_plus_{step}bar_bps"] = None
        else:
            bar_bps[f"bid_plus_{step}bar_bps"] = (float(row["bid"]) / float(loss_bid) - 1.0) * 10000.0
    return {
        **meta,
        "loss_index": loss["index"],
        "loss_t": loss["finalize_t"],
        "loss_reason": loss["reason"],
        "slope_only": bool(loss["slope_only"]),
        "ema9": loss["ema9"],
        "ema21": loss["ema21"],
        "ema21_lag3": loss["ema21_lag3"],
        "fast_slow_gap_yen": loss["fast_slow_gap_yen"],
        "fast_slow_gap_bps": loss["fast_slow_gap_bps"],
        "slow_slope_3bar_yen": loss["slow_slope_3bar_yen"],
        "slow_slope_3bar_bps": loss["slow_slope_3bar_bps"],
        "loss_bid0": loss_bid,
        "consecutive_slope_loss_bars": duration_bars(seq[loss_pos:]),
        "forward_thesis_live": [None if row is None else bool(row["thesis_live"]) for row in forward],
        "forward_slow_rising": [None if row is None else bool(row["slow_trend_rising"]) for row in forward],
        "forward_fast_slow_live": [None if row is None else bool(row["fast_slow_relation_live"]) for row in forward],
        **bar_bps,
        **_marks(board, loss["finalize_t"], loss_bid, am_end),
        "classification": None if classified is None else classified["label"],
        "recovered_within_1": recovered_at == 1,
        "recovered_within_2": recovered_at is not None and recovered_at <= 2,
        "recovered_within_3": recovered_at is not None,
        "temporary_slope_interruption": None if classified is None else classified["temporary_slope_interruption"],
        "temporary_and_price_recovered": None if classified is None else classified["temporary_and_price_recovered"],
    }


def _day(day: str, capture: Path, symbols: list[str]) -> dict[str, Any]:
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
    technical = board_n = fill_n = 0
    episodes: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        if int(raw["minute_epoch"].size) == 0:
            continue
        ind = attach_indicators(raw)
        board = bufs[sym].view()
        n = int(ind["close"].size)
        for i in range(n):
            if not evaluable(i, n):
                continue
            t0 = float(ind["finalize_t"][i])
            if t0 + float(DEV_WAIT_SEC) > am_end + 1e-12:
                continue
            if not (trend_up(ind, i) and pullback_setup(ind, i) and reversal_rci(ind, i) and price_action(ind, i) and volume_confirm(ind, i)):
                continue
            technical += 1
            snap = _snap_at(board, t0)
            board_ok, _meta = board_support(snap) if snap.get("ok") else (False, {})
            if not board_ok:
                continue
            board_n += 1
            meta = {"date": day, "symbol": sym, "signal_t": t0, "population": "BOARD_PASS_STATE_EPISODE"}
            episode = _episode(ind, board, i + 1, am_end, meta)
            episodes.append(episode or {**meta, "loss_reason": None, "slope_only": False, "classification": None})
            if not (snap.get("ok") and execution_eligible(snap)):
                continue
            limit = _f(snap.get("bid"))
            if limit is None:
                continue
            fill = standalone_fill(board, t0=t0, wait_sec=float(DEV_WAIT_SEC), limit_price=float(limit), sess_end=float(am_end))
            if not (fill.get("WOULD_FILL") and fill.get("fill_t") is not None and fill.get("fill_price") is not None):
                continue
            fill_n += 1
            ex = thesis_exit(ind, board, fill_t=float(fill["fill_t"]), fill_px=float(fill["fill_price"]), am_end=am_end)
            start = int(next((k for k in range(n) if float(ind["finalize_t"][k]) > float(fill["fill_t"]) + 1e-12), n))
            path = _episode(ind, board, start, am_end, {**meta, "population": "ACTUAL_FILL"})
            pnl = None if ex.get("exit_price") is None else account(float(fill["fill_price"]), float(ex["exit_price"]))
            net = None if pnl is None else float(pnl["net_pnl_yen"])
            fills.append(
                {
                    "date": day,
                    "symbol": sym,
                    "signal_t": t0,
                    "fill_t": float(fill["fill_t"]),
                    "fill_price": float(fill["fill_price"]),
                    "actual_exit_time": ex.get("exit_t"),
                    "actual_exit_price": ex.get("exit_price"),
                    "actual_exit_reason": ex.get("exit_reason"),
                    "actual_realized_bps": None if ex.get("exit_price") is None else trade_bps(float(fill["fill_price"]), float(ex["exit_price"])),
                    "actual_pnl_yen": net,
                    "role": "ECONOMIC_ANCHOR",
                    "path": path,
                    "session_close_separate": ex.get("exit_reason") == "SESSION_FAIL_CLOSE",
                }
            )
    return {"technical": technical, "board": board_n, "fill": fill_n, "episodes": episodes, "fills": fills}


def scan(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    technical = board_n = fill_n = 0
    episodes: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), [str(s) for s in list(sess["symbols"])])
        print(f"{day} technical={one['technical']} board={one['board']} fill={one['fill']}", flush=True)
        if one["technical"] != EXPECTED_S5[day] or one["board"] != EXPECTED_S6[day] or one["fill"] != EXPECTED_FILL[day]:
            raise PopulationMismatch({"date": day, "got": {"technical": one["technical"], "board": one["board"], "fill": one["fill"]}})
        technical += one["technical"]
        board_n += one["board"]
        fill_n += one["fill"]
        episodes.extend(one["episodes"])
        fills.extend(one["fills"])
        gc.collect()
    got = {(row["date"], row["symbol"], row["actual_pnl_yen"], row["actual_exit_reason"]) for row in fills}
    if technical != 121 or board_n != 44 or fill_n != 7 or got != FROZEN_TRADES:
        raise PopulationMismatch({"technical": technical, "board": board_n, "fill": fill_n, "trades": sorted(got)})
    return {"technical": technical, "board": board_n, "fill": fill_n, "episodes": episodes, "fills": fills}
