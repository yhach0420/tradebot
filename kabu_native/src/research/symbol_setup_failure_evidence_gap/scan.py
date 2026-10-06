"""One diagnostic pass. Canonical stage order and one quote clock. Not a strategy replay."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
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
from research.symbol_setup_failure_evidence_gap import (
    BAR_HORIZONS,
    CANONICAL_STAGE_ORDER,
    PARITY_ABS_YEN,
    QUOTE_HORIZONS,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

RCA_COMPARE_STAGES = ("VOLUME_PARTICIPATION", "PRICE_ACTION_TRIGGER")


class PopulationMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


class PriceParityError(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _next_ok(ok: np.ndarray) -> np.ndarray:
    n = int(ok.size)
    nxt = np.full(n, -1, dtype=np.int32)
    nxt_i = -1
    for i in range(n - 1, -1, -1):
        if bool(ok[i]):
            nxt_i = i
        nxt[i] = nxt_i
    return nxt


def _joint_mask(board: dict[str, np.ndarray]) -> np.ndarray:
    n = int(board["t"].size)
    if n == 0:
        return np.asarray([], dtype=bool)
    bid = board["bid"]
    ask = board["ask"]
    fresh = board["fresh_sec"]
    return (
        board["executable"]
        & ~board["special"]
        & np.isfinite(fresh)
        & (fresh <= float(BOARD_FRESHNESS_SEC) + 1e-12)
        & np.isfinite(bid)
        & np.isfinite(ask)
        & (bid > 0)
        & (ask >= bid)
        & np.isfinite(board["bid_qty"])
        & np.isfinite(board["ask_qty"])
        & (board["bid_qty"] >= float(MIN_QTY) - 1e-12)
        & (board["ask_qty"] >= float(MIN_QTY) - 1e-12)
    )


def _first_quote(board: dict[str, np.ndarray], nxt: np.ndarray, event_t: float, sess_end: float) -> Optional[dict[str, float]]:
    t = board["t"]
    n = int(t.size)
    if n == 0:
        return None
    i0 = int(np.searchsorted(t, float(event_t), side="left"))
    if i0 >= n:
        return None
    j = int(nxt[i0])
    if j < 0:
        return None
    tj = float(t[j])
    if tj + 1e-12 < float(event_t) or tj > float(sess_end) + 1e-12:
        return None
    bid = float(board["bid"][j])
    ask = float(board["ask"][j])
    px = _num(board["px"][j]) if "px" in board else None
    return {"i": float(j), "t": tj, "bid": bid, "ask": ask, "px": px if px is not None and px > 0 else None}


def _future_bar(ind: dict[str, np.ndarray], i: int, t0: float, horizon: float, am_end: float) -> Optional[dict[str, float]]:
    target = float(t0) + float(horizon)
    if target > float(am_end) + 1e-12:
        return None
    fin = ind["finalize_t"]
    j = int(np.searchsorted(fin, target, side="right")) - 1
    if j <= int(i):
        return None
    fj = float(fin[j])
    if fj > target + 1e-9:
        return None
    close = _num(ind["close"][j])
    if close is None or close <= 0:
        return None
    return {"t": fj, "close": close}


def _decompose(board: dict[str, np.ndarray], nxt: np.ndarray, ind: dict[str, np.ndarray], i: int, t0: float, am_end: float) -> tuple[dict[str, Any], int]:
    fails = 0
    q0 = _first_quote(board, nxt, t0, am_end)
    close0 = _num(ind["close"][i])
    out: dict[str, Any] = {
        "signal_bar_finalize_t": float(t0),
        "signal_close": close0,
        "q0_t": None,
        "q0_delay_ms": None,
        "bid0": None,
        "ask0": None,
        "mid0": None,
        "spread0": None,
        "last0": None,
        "horizons": {},
        "bars": {},
    }
    if q0 is None:
        return out, fails
    if float(q0["t"]) + 1e-9 < float(t0):
        raise PriceParityError({"reason": "quote_before_signal", "t0": t0, "q0": q0["t"]})
    bid0 = float(q0["bid"])
    ask0 = float(q0["ask"])
    mid0 = (bid0 + ask0) / 2.0
    out.update(
        {
            "q0_t": float(q0["t"]),
            "q0_delay_ms": (float(q0["t"]) - float(t0)) * 1000.0,
            "bid0": bid0,
            "ask0": ask0,
            "mid0": mid0,
            "spread0": ask0 - bid0,
            "last0": q0["px"],
        }
    )
    for h in QUOTE_HORIZONS:
        target = float(t0) + float(h)
        qh = _first_quote(board, nxt, float(q0["t"]) + float(h), am_end)
        cell: dict[str, Any] = {"target_nominal_t": target, "qh_t": None}
        if qh is not None and ask0 > 0:
            bidh = float(qh["bid"])
            askh = float(qh["ask"])
            midh = (bidh + askh) / 2.0
            raw = midh - mid0
            entry = ask0 - mid0
            exit_ = midh - bidh
            atb = bidh - ask0
            if abs(atb - (raw - entry - exit_)) > float(PARITY_ABS_YEN):
                fails += 1
            den = ask0
            cell.update(
                {
                    "qh_t": float(qh["t"]),
                    "bidh": bidh,
                    "askh": askh,
                    "midh": midh,
                    "spreadh": askh - bidh,
                    "lasth": qh["px"],
                    "raw_mid_yen": raw,
                    "entry_half_yen": entry,
                    "exit_half_yen": exit_,
                    "ask_to_bid_yen": atb,
                    "bid_anchor_yen": bidh - bid0,
                    "last_to_last_yen": None if q0["px"] is None or qh["px"] is None else float(qh["px"]) - float(q0["px"]),
                    "raw_mid_bps": raw / den * 10000.0,
                    "entry_half_bps": entry / den * 10000.0,
                    "exit_half_bps": exit_ / den * 10000.0,
                    "ask_to_bid_bps": atb / den * 10000.0,
                    "bid_anchor_bps": (bidh - bid0) / den * 10000.0,
                    "last_to_last_bps": None if q0["px"] is None or qh["px"] is None else (float(qh["px"]) - float(q0["px"])) / den * 10000.0,
                    "qh_minus_target_sec": float(qh["t"]) - target,
                }
            )
        out["horizons"][str(h)] = cell
    for name, sec in BAR_HORIZONS:
        fb = _future_bar(ind, i, t0, float(sec), am_end)
        target = float(t0) + float(sec)
        if fb is None or close0 is None or close0 <= 0:
            out["bars"][name] = {"target_nominal_t": target, "future_bar_t": None, "future_close": None, "bar_bps": None, "bar_minus_target_sec": None}
        else:
            out["bars"][name] = {
                "target_nominal_t": target,
                "future_bar_t": fb["t"],
                "future_close": fb["close"],
                "bar_bps": (fb["close"] / close0 - 1.0) * 10000.0,
                "bar_minus_target_sec": fb["t"] - target,
            }
    return out, fails


class Bag:
    def __init__(self) -> None:
        self.n = 0
        self.values: dict[str, list[float]] = {}

    def add(self, flat: dict[str, Optional[float]]) -> None:
        self.n += 1
        for key, value in flat.items():
            self.values.setdefault(key, [])
            if value is not None:
                self.values[key].append(float(value))


def _flat(decomp: dict[str, Any]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for h in QUOTE_HORIZONS:
        cell = decomp["horizons"].get(str(h)) or {}
        out[f"raw_{h}"] = cell.get("raw_mid_bps")
        out[f"entry_{h}"] = cell.get("entry_half_bps")
        out[f"exit_{h}"] = cell.get("exit_half_bps")
        out[f"atb_{h}"] = cell.get("ask_to_bid_bps")
        out[f"bid_{h}"] = cell.get("bid_anchor_bps")
    for name, _sec in BAR_HORIZONS:
        out[f"bar_{name}"] = (decomp["bars"].get(name) or {}).get("bar_bps")
    return out


def _empty(names: tuple[str, ...]) -> dict[str, dict[str, Bag]]:
    return {name: {"input": Bag(), "pass": Bag()} for name in names}


def _walk(stages: dict[str, dict[str, Bag]], order: tuple[str, ...], flags: dict[str, bool], flat: dict[str, Optional[float]]) -> None:
    prev = True
    for name in order:
        if name == "BOARD_SUPPORT_VETO":
            continue
        if prev:
            stages[name]["input"].add(flat)
        if prev and flags[name]:
            stages[name]["pass"].add(flat)
        prev = bool(prev and flags[name])


def _merge(dst: Bag, src: Bag) -> None:
    dst.n += src.n
    for key, vals in src.values.items():
        dst.values.setdefault(key, []).extend(vals)


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
    canon = _empty(CANONICAL_STAGE_ORDER)
    rca = _empty(RCA_COMPARE_STAGES)
    signals: list[dict[str, Any]] = []
    fails = 0
    technical = 0
    board_n = 0
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
            trend = trend_up(ind, i)
            loc = bool(trend) and pullback_setup(ind, i)
            rci = bool(loc) and reversal_rci(ind, i)
            vol = bool(rci) and volume_confirm(ind, i)
            px = bool(vol) and price_action(ind, i)
            rca_px = bool(rci) and price_action(ind, i)
            rca_vol = bool(rca_px) and volume_confirm(ind, i)
            if bool(px) != bool(rca_vol):
                raise PopulationMismatch({"date": day, "symbol": sym, "reason": "conjunction_order_diverged"})
            decomp, nfail = _decompose(board, nxt, ind, i, t0, am_end)
            fails += nfail
            flat = _flat(decomp)
            canon_flags = {
                "MA_TREND": trend,
                "BB_LOCATION": loc,
                "RCI_REVERSAL": rci,
                "VOLUME_PARTICIPATION": vol,
                "PRICE_ACTION_TRIGGER": px,
            }
            _walk(canon, CANONICAL_STAGE_ORDER, canon_flags, flat)
            rca_flags = {"VOLUME_PARTICIPATION": rca_vol, "PRICE_ACTION_TRIGGER": rca_px}
            # RCA comparison uses the same price values and the prior filter order.
            prev = rci
            for name in ("PRICE_ACTION_TRIGGER", "VOLUME_PARTICIPATION"):
                if prev:
                    rca[name]["input"].add(flat)
                if prev and rca_flags[name]:
                    rca[name]["pass"].add(flat)
                prev = bool(prev and rca_flags[name])
            if not px:
                continue
            technical += 1
            snap = _snap_at(board, t0)
            board_ok, _meta = board_support(snap) if snap.get("ok") else (False, {})
            if board_ok:
                board_n += 1
                canon["BOARD_SUPPORT_VETO"]["pass"].add(flat)
            canon["BOARD_SUPPORT_VETO"]["input"].add(flat)
            signals.append(
                {
                    "date": day,
                    "symbol": sym,
                    "lineage": lineage,
                    "fold": fold,
                    "board_pass": bool(board_ok),
                    **decomp,
                }
            )
    return {
        "technical": technical,
        "board": board_n,
        "fails": fails,
        "canon": canon,
        "rca": rca,
        "signals": signals,
    }


def scan(sessions: list[dict[str, Any]], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    canon = _empty(CANONICAL_STAGE_ORDER)
    rca = _empty(RCA_COMPARE_STAGES)
    signals: list[dict[str, Any]] = []
    fails = 0
    technical = 0
    board_n = 0
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), [str(s) for s in list(sess["symbols"])], groups)
        print(f"{day} technical={one['technical']} board={one['board']} parity_fail={one['fails']}", flush=True)
        if one["technical"] != EXPECTED_S5[day] or one["board"] != EXPECTED_S6[day]:
            raise PopulationMismatch({"date": day, "got": {"technical": one["technical"], "board": one["board"]}, "expected_technical": EXPECTED_S5[day], "expected_board": EXPECTED_S6[day]})
        if one["fails"]:
            raise PriceParityError({"date": day, "fails": one["fails"]})
        technical += int(one["technical"])
        board_n += int(one["board"])
        fails += int(one["fails"])
        for name in CANONICAL_STAGE_ORDER:
            _merge(canon[name]["input"], one["canon"][name]["input"])
            _merge(canon[name]["pass"], one["canon"][name]["pass"])
        for name in RCA_COMPARE_STAGES:
            _merge(rca[name]["input"], one["rca"][name]["input"])
            _merge(rca[name]["pass"], one["rca"][name]["pass"])
        signals.extend(one["signals"])
        gc.collect()
    if technical != 121 or board_n != 44 or len(signals) != 121:
        raise PopulationMismatch({"technical": technical, "board": board_n, "rows": len(signals)})
    return {"technical": technical, "board": board_n, "fails": fails, "canon": canon, "rca": rca, "signals": signals}
