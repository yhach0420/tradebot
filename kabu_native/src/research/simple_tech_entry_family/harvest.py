"""Sealed Capture → 1min bars → V1 stages → W5 fill → C14. Parallelism=1. No live WS. No engine boot."""
from __future__ import annotations

import gc
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.am_entry_profit_improvement.labels import simulate_current_exit
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.e1_x34a_execution_policy.executable_board import is_executable_continuous_board
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    board_support,
    cost_exceed,
    evaluable,
    execution_eligible,
    forward_pack,
    price_action,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
CACHE = NATIVE / "results" / "research" / "simple_tech_entry_family" / "_work"


def sealed_day_caps(days: list[str], today: str) -> list[dict[str, Any]]:
    from _p1_inventory import resolve_universe
    from research.anchor_vs_event_driven.run_comparison import find_capture_dir

    out = []
    for day in days:
        if str(day) == str(today):
            raise RuntimeError("ACTIVE_DAY_IN_RESEARCH_INPUT")
        cap = find_capture_dir(str(day))
        uni = resolve_universe(str(day), cap)
        out.append(
            {
                "date": str(day),
                "capture_path": str(cap) if cap is not None else "",
                "universe_symbols": list(uni.get("symbols") or []),
                "ok": cap is not None and bool(uni.get("symbols")),
                "universe_source": uni.get("source"),
                "universe_n": int(uni.get("universe_n") or 0),
            }
        )
    return out


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _parse_iso(v: Any) -> Optional[float]:
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


class _Buf:
    __slots__ = (
        "n",
        "t",
        "bid",
        "ask",
        "bid_qty",
        "ask_qty",
        "special",
        "fresh_sec",
        "executable",
        "px",
        "state",
    )

    def __init__(self) -> None:
        self.n = 0
        self.t = np.empty(64, dtype=float)
        self.bid = np.empty(64, dtype=float)
        self.ask = np.empty(64, dtype=float)
        self.bid_qty = np.empty(64, dtype=float)
        self.ask_qty = np.empty(64, dtype=float)
        self.special = np.empty(64, dtype=bool)
        self.fresh_sec = np.empty(64, dtype=float)
        self.executable = np.empty(64, dtype=bool)
        self.px = np.empty(64, dtype=float)
        self.state = np.empty(64, dtype=object)

    def append(self, row: dict[str, Any]) -> None:
        if self.n >= self.t.size:
            new = int(self.t.size * 2)

            def _grow(arr: np.ndarray, dtype: Any) -> np.ndarray:
                out = np.empty(new, dtype=dtype)
                out[: self.n] = arr[: self.n]
                return out

            self.t = _grow(self.t, float)
            self.bid = _grow(self.bid, float)
            self.ask = _grow(self.ask, float)
            self.bid_qty = _grow(self.bid_qty, float)
            self.ask_qty = _grow(self.ask_qty, float)
            self.special = _grow(self.special, bool)
            self.fresh_sec = _grow(self.fresh_sec, float)
            self.executable = _grow(self.executable, bool)
            self.px = _grow(self.px, float)
            self.state = _grow(self.state, object)
        i = self.n
        self.t[i] = float(row["t"])
        self.bid[i] = float(row["bid"])
        self.ask[i] = float(row["ask"])
        self.bid_qty[i] = float(row["bid_qty"])
        self.ask_qty[i] = float(row["ask_qty"])
        self.special[i] = bool(row["special"])
        self.fresh_sec[i] = float(row["fresh_sec"])
        self.executable[i] = bool(row["executable"])
        self.px[i] = float(row["px"])
        self.state[i] = str(row.get("state") or "")
        self.n = i + 1

    def view(self) -> dict[str, np.ndarray]:
        n = self.n
        if n <= 0:
            z = np.asarray([], dtype=float)
            return {
                "t": z,
                "bid": z,
                "ask": z,
                "bid_qty": z,
                "ask_qty": z,
                "special": np.asarray([], dtype=bool),
                "fresh_sec": z,
                "executable": np.asarray([], dtype=bool),
                "px": z,
                "board_execution_state": np.asarray([], dtype=object),
            }
        return {
            "t": self.t[:n],
            "bid": self.bid[:n],
            "ask": self.ask[:n],
            "bid_qty": self.bid_qty[:n],
            "ask_qty": self.ask_qty[:n],
            "special": self.special[:n],
            "fresh_sec": self.fresh_sec[:n],
            "executable": self.executable[:n],
            "px": self.px[:n],
            "board_execution_state": self.state[:n],
        }


def _board_row(payload: dict[str, Any], event_t: float) -> dict[str, Any]:
    b1 = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else {}
    s1 = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else {}
    bid = _f(b1.get("Price")) if b1 else _f(payload.get("BidPrice"))
    ask = _f(s1.get("Price")) if s1 else _f(payload.get("AskPrice"))
    bq = _f(b1.get("Qty")) if b1 else _f(payload.get("BidQty"))
    aq = _f(s1.get("Qty")) if s1 else _f(payload.get("AskQty"))
    sq = payload.get("SpecialQuote")
    if sq is None:
        sq = payload.get("special_quote")
    special = bool(sq) and str(sq) not in ("", "0", "None", "null", "False", "false")
    if aq is not None and aq <= 0:
        special = True
    if bq is not None and bq <= 0:
        special = True
    fresh = _f(payload.get("board_age_sec"))
    if fresh is None:
        fresh = _f(payload.get("fresh_sec"))
    if fresh is None:
        qt = _parse_iso(payload.get("CurrentPriceTime")) or _parse_iso(payload.get("AskTime")) or _parse_iso(
            payload.get("BidTime")
        )
        fresh = float(event_t - qt) if qt is not None else 0.0
    gate = is_executable_continuous_board(payload, event_t=event_t)
    px = _f(gate.get("CurrentPrice"))
    return {
        "t": float(event_t),
        "bid": bid if bid is not None else float("nan"),
        "ask": ask if ask is not None else float("nan"),
        "bid_qty": bq if bq is not None else float("nan"),
        "ask_qty": aq if aq is not None else float("nan"),
        "special": bool(special),
        "fresh_sec": float(fresh),
        "executable": bool(gate.get("ok")),
        "state": str(gate.get("state") or ""),
        "px": px if px is not None else float("nan"),
        "cum_vol": _f(gate.get("TradingVolume")),
        "continuous": bool(gate.get("ok")) and str(gate.get("state") or "") in {"CONTINUOUS_TRADING", "LEGACY_QUOTE_ONLY"},
    }


def _snap_at(board: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return {"ok": False}
    i = int(np.searchsorted(t, t0, side="right") - 1)
    if i < 0:
        return {"ok": False}
    spread = None
    bid = float(board["bid"][i])
    ask = float(board["ask"][i])
    if bid == bid and ask == ask and bid > 0 and ask > 0:
        mid = (ask + bid) / 2.0
        if mid > 0:
            spread = (ask - bid) / mid * 10000.0
    return {
        "ok": True,
        "i": i,
        "t": float(t[i]),
        "bid": bid,
        "ask": ask,
        "bid_qty": float(board["bid_qty"][i]),
        "ask_qty": float(board["ask_qty"][i]),
        "special": bool(board["special"][i]),
        "fresh_sec": float(board["fresh_sec"][i]),
        "executable": bool(board["executable"][i]),
        "state": str(board["board_execution_state"][i] or ""),
        "px": float(board["px"][i]) if "px" in board else float("nan"),
        "spread_bps": spread,
    }


def _up_first(board: dict[str, np.ndarray], t0: float, close: float, window: float = 60.0) -> tuple[int, int]:
    t = board.get("t")
    px = board.get("px")
    if t is None or px is None or int(t.size) == 0 or not (close == close) or close <= 0:
        return 0, 0
    i0 = int(np.searchsorted(t, t0, side="right"))
    lim = float(t0) + float(window)
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti > lim + 1e-12:
            break
        if not bool(board["executable"][i]):
            continue
        p = float(px[i])
        if not (p == p) or p <= 0:
            continue
        if p > close + 1e-12:
            return 1, 0
        if p < close - 1e-12:
            return 0, 1
    return 0, 0


def _path_mfe_mae(board: dict[str, np.ndarray], fill_t: float, fill_px: float, sess_end: float) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    bid = board.get("bid")
    if t is None or bid is None or fill_px <= 0:
        return None, None
    i0 = int(np.searchsorted(t, fill_t, side="left"))
    mfe = None
    mae = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < fill_t:
            continue
        if ti > float(sess_end) + 1e-12:
            break
        b = float(bid[i])
        if not (b == b) or b <= 0:
            continue
        r = (b / float(fill_px) - 1.0)
        if mfe is None or r > mfe:
            mfe = r
        if mae is None or r < mae:
            mae = r
    return mfe, mae


def _pullback_low_time(ind: dict[str, np.ndarray], i: int) -> Optional[float]:
    lo = None
    t = None
    for k in range(max(0, i - 2), i + 1):
        v = float(ind["low"][k])
        if lo is None or v < lo:
            lo = v
            t = float(ind["minute_epoch"][k])
    return t


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_LABEL_AS_FEATURE_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
    }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        bufs: dict[str, _Buf] = {s: _Buf() for s in universe}
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
        events_n = 0
        last_et: Optional[float] = None
        last_seq: Optional[int] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                leak["UNIVERSE_SKIP_N"] += 1
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                leak["NO_EVENT_TIME_N"] += 1
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            recv = record_event_stamp(rec)
            seq = int(rec.get("sequence") or 0)
            if seq > 0:
                last_seq = seq
            if recv:
                pay["received_at"] = recv
            last_et = float(et)
            events_n += 1
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
            if not row["executable"]:
                st = str(row.get("state") or "")
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 400000 == 0:
                print(f"{day} stream events={events_n} last_et={last_et}", flush=True)

        opps: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        bar_rows: list[dict[str, Any]] = []
        integ_fail = 0
        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            bar_rows.append({"date": day, "symbol": s, **integ, **builders[s].leak})
            if not integ.get("ok"):
                integ_fail += 1
            if int(raw["minute_epoch"].size) == 0:
                continue
            ind = attach_indicators(raw)
            board = bufs[s].view()
            n = int(ind["close"].size)
            for i in range(n):
                if not evaluable(i, n):
                    continue
                t0 = float(ind["finalize_t"][i])
                if t0 + float(DEV_WAIT_SEC) > am_end + 1e-12:
                    continue
                snap = _snap_at(board, t0)
                s1 = trend_up(ind, i)
                s2 = bool(s1) and pullback_setup(ind, i)
                s3 = bool(s2) and reversal_rci(ind, i)
                s4 = bool(s3) and price_action(ind, i)
                s5 = bool(s4) and volume_confirm(ind, i)
                b_ok, bmeta = board_support(snap) if snap.get("ok") else (False, {"board_ok": False, "board_reason": "NO_BOARD"})
                s6 = bool(s5) and bool(b_ok)
                s7 = bool(s6) and bool(snap.get("ok")) and execution_eligible(snap)
                fwd = forward_pack(ind, i)
                close = float(ind["close"][i])
                up_f, dn_f = _up_first(board, t0, close)
                spread = snap.get("spread_bps") if snap.get("ok") else None
                cex = cost_exceed(fwd.get("fwd_1m"), spread if isinstance(spread, (int, float)) else None)
                rec = {
                    "date": day,
                    "session": "AM",
                    "symbol": s,
                    "i": i,
                    "bar_minute": float(ind["minute_epoch"][i]),
                    "t0": t0,
                    "open": float(ind["open"][i]),
                    "high": float(ind["high"][i]),
                    "low": float(ind["low"][i]),
                    "close": close,
                    "volume": float(ind["volume"][i]),
                    "ema9": float(ind["ema9"][i]) if ind["ema9"][i] == ind["ema9"][i] else None,
                    "ema21": float(ind["ema21"][i]) if ind["ema21"][i] == ind["ema21"][i] else None,
                    "ema21_lag3": float(ind["ema21"][i - 3]) if i >= 3 and ind["ema21"][i - 3] == ind["ema21"][i - 3] else None,
                    "bb_upper": float(ind["bb_upper"][i]) if ind["bb_upper"][i] == ind["bb_upper"][i] else None,
                    "bb_lower": float(ind["bb_lower"][i]) if ind["bb_lower"][i] == ind["bb_lower"][i] else None,
                    "rci9": float(ind["rci9"][i]) if ind["rci9"][i] == ind["rci9"][i] else None,
                    "rci9_prev": float(ind["rci9"][i - 1]) if i >= 1 and ind["rci9"][i - 1] == ind["rci9"][i - 1] else None,
                    "vwap": float(ind["vwap"][i]) if ind["vwap"][i] == ind["vwap"][i] else None,
                    "up_vol": float(ind["up_vol"][i]),
                    "down_vol": float(ind["down_vol"][i]),
                    "ask_vol": float(ind["ask_vol"][i]),
                    "bid_vol": float(ind["bid_vol"][i]),
                    "s0": True,
                    "s1": bool(s1),
                    "s2": bool(s2),
                    "s3": bool(s3),
                    "s4": bool(s4),
                    "s5": bool(s5),
                    "s6": bool(s6),
                    "s7": bool(s7),
                    "s8": False,
                    "s9": False,
                    "s10": False,
                    "board_reason": bmeta.get("board_reason"),
                    "bid": snap.get("bid") if snap.get("ok") else None,
                    "ask": snap.get("ask") if snap.get("ok") else None,
                    "bid_qty": snap.get("bid_qty") if snap.get("ok") else None,
                    "ask_qty": snap.get("ask_qty") if snap.get("ok") else None,
                    "spread_bps": spread,
                    "up_first": int(up_f),
                    "down_first": int(dn_f),
                    "cost_exceed": bool(cex),
                    "pullback_low_t": _pullback_low_time(ind, i),
                    **fwd,
                }
                if rec["ema9"] is not None and rec["vwap"] not in (None,) and rec["vwap"] and rec["vwap"] == rec["vwap"] and rec["vwap"] > 0:
                    rec["vwap_rel"] = float(close / float(rec["vwap"]) - 1.0)
                else:
                    rec["vwap_rel"] = None
                if i >= 20 and float(ind["close"][i - 20]) > 0:
                    rec["ret_20"] = float(close / float(ind["close"][i - 20]) - 1.0)
                else:
                    rec["ret_20"] = None
                rec["hh_hl"] = bool(i >= 1 and float(ind["high"][i]) >= float(ind["high"][i - 1]) and float(ind["low"][i]) >= float(ind["low"][i - 1]))
                rec["vol_med5"] = float(np.median(ind["volume"][i - 5 : i])) if i >= 5 else None
                rec["vol_accel"] = (float(ind["volume"][i]) / rec["vol_med5"]) if rec["vol_med5"] else None
                opps.append(rec)
                if s7:
                    limit = snap.get("bid")
                    if _f(limit) is None or float(limit) <= 0:
                        rec["s7"] = False
                        continue
                    fill = standalone_fill(
                        board,
                        t0=float(t0),
                        wait_sec=float(DEV_WAIT_SEC),
                        limit_price=float(limit),
                        sess_end=float(am_end),
                    )
                    rec["WOULD_FILL"] = bool(fill.get("WOULD_FILL"))
                    rec["fill_t"] = fill.get("fill_t")
                    rec["fill_price"] = fill.get("fill_price")
                    rec["nonfill_class"] = fill.get("nonfill_class")
                    rec["limit"] = float(limit)
                    if rec["WOULD_FILL"] and rec["fill_t"] is not None and rec["fill_price"] is not None:
                        ex = simulate_current_exit(
                            board,
                            date=day,
                            symbol=s,
                            session="AM",
                            fill_t=float(rec["fill_t"]),
                            fill_px=float(rec["fill_price"]),
                        )
                        rec["exit_t"] = ex.get("exit_t")
                        rec["exit_price"] = ex.get("exit_price")
                        rec["exit_reason"] = ex.get("exit_reason")
                        rec["pnl_yen_100"] = ex.get("pnl_yen_100")
                        mfe, mae = _path_mfe_mae(board, float(rec["fill_t"]), float(rec["fill_price"]), float(am_end))
                        rec["mfe_path"] = mfe
                        rec["mae_path"] = mae
                    else:
                        rec["exit_t"] = None
                        rec["exit_price"] = None
                        rec["exit_reason"] = None
                        rec["pnl_yen_100"] = None
                        rec["mfe_path"] = None
                        rec["mae_path"] = None
                    signals.append(rec)

        print(
            f"{day} events={events_n} opps={len(opps)} s7={len(signals)} bars_fail={integ_fail} last_et={last_et}",
            flush=True,
        )
        del bufs, builders
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "last_et": last_et,
            "last_seq": last_seq,
            "opps": opps,
            "signals": signals,
            "bar_rows": bar_rows,
            "leak": leak,
            "integ_fail_n": integ_fail,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def save_day_cache(path: Path, body: dict[str, Any]) -> None:
    from research.am_entry_profit_improvement.publish import json_sanitize

    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "last_seq": body.get("last_seq"),
            "leak": body.get("leak"),
            "integ_fail_n": body.get("integ_fail_n"),
            "elapsed_sec": body.get("elapsed_sec"),
            "bar_rows": body.get("bar_rows"),
            "opps": body.get("opps"),
            "signals": body.get("signals"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        body = __import__("json").loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if str(body.get("spec_sha") or "") != str(spec_sha):
        return {}
    if not body.get("ok"):
        return {}
    return body
