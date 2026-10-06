"""AM Capture → completed 1m bars → frozen 3-condition first-cross → Ask1 markout. Holdout/stress sealed until freeze."""
from __future__ import annotations

import json
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

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.e1_x34a_execution_policy.executable_board import is_executable_continuous_board
from research.new_entry_breakout_continuation_v1 import (
    BOARD_FRESHNESS_SEC,
    FORBIDDEN_INPUT_DAYS,
    HORIZONS_SEC,
    MAX_RESEARCH_DATE,
    MIN_ASK_QTY_SCREEN,
    PATH_SEC,
)
from research.new_entry_breakout_continuation_v1.isolation import CACHE, TODAY
from research.new_entry_breakout_continuation_v1.rule import session_vwap, signal_at
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
CONTINUOUS_STATES = {"CONTINUOUS_TRADING", "LEGACY_QUOTE_ONLY"}
INGRESS_KEYS = ("received_at", "received_at_jst", "persisted_at", "received_at_utc")
AUDIT = {
    "HOLDOUT_READ_BEFORE_RULE_FREEZE_N": 0,
    "STRESS_READ_BEFORE_HOLDOUT_DECISION_N": 0,
    "RULE_CHANGE_AFTER_HOLDOUT_OPEN_N": 0,
    "HOLDOUT_USED_FOR_THRESHOLD_N": 0,
    "STRESS_USED_FOR_THRESHOLD_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "SPLIT_LEAKAGE_N": 0,
}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
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


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def freeze_path() -> Path:
    return CACHE / "rule_freeze.json"


def freeze_active() -> bool:
    body = _load(freeze_path())
    return bool(body.get("ENTRY_RULE_FROZEN"))


def holdout_decision_path() -> Path:
    return CACHE / "holdout_decision.json"


def holdout_decision_pass() -> bool:
    body = _load(holdout_decision_path())
    return bool(body.get("HOLDOUT_PASS"))


class BoardTape:
    __slots__ = (
        "n",
        "t",
        "bid",
        "ask",
        "bid_qty",
        "ask_qty",
        "special",
        "ask_fresh_sec",
        "bid_fresh_sec",
        "fresh_sec",
        "fresh_source",
        "executable",
        "px",
        "state",
        "cum_vol",
        "continuous",
    )

    def __init__(self) -> None:
        self.n = 0
        self.t = np.empty(64, dtype=float)
        self.bid = np.empty(64, dtype=float)
        self.ask = np.empty(64, dtype=float)
        self.bid_qty = np.empty(64, dtype=float)
        self.ask_qty = np.empty(64, dtype=float)
        self.special = np.empty(64, dtype=bool)
        self.ask_fresh_sec = np.empty(64, dtype=float)
        self.bid_fresh_sec = np.empty(64, dtype=float)
        self.fresh_sec = np.empty(64, dtype=float)
        self.fresh_source = np.empty(64, dtype=object)
        self.executable = np.empty(64, dtype=bool)
        self.px = np.empty(64, dtype=float)
        self.state = np.empty(64, dtype=object)
        self.cum_vol = np.empty(64, dtype=float)
        self.continuous = np.empty(64, dtype=bool)

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
            self.ask_fresh_sec = _grow(self.ask_fresh_sec, float)
            self.bid_fresh_sec = _grow(self.bid_fresh_sec, float)
            self.fresh_sec = _grow(self.fresh_sec, float)
            self.fresh_source = _grow(self.fresh_source, object)
            self.executable = _grow(self.executable, bool)
            self.px = _grow(self.px, float)
            self.state = _grow(self.state, object)
            self.cum_vol = _grow(self.cum_vol, float)
            self.continuous = _grow(self.continuous, bool)
        i = self.n
        self.t[i] = float(row["t"])
        self.bid[i] = float(row["bid"])
        self.ask[i] = float(row["ask"])
        self.bid_qty[i] = float(row["bid_qty"])
        self.ask_qty[i] = float(row["ask_qty"])
        self.special[i] = bool(row["special"])
        self.ask_fresh_sec[i] = float(row["ask_fresh_sec"])
        self.bid_fresh_sec[i] = float(row["bid_fresh_sec"])
        self.fresh_sec[i] = float(row["fresh_sec"])
        self.fresh_source[i] = str(row.get("fresh_source") or "UNRESOLVED")
        self.executable[i] = bool(row["executable"])
        self.px[i] = float(row["px"])
        self.state[i] = str(row.get("state") or "")
        self.cum_vol[i] = float(row.get("cum_vol") if row.get("cum_vol") == row.get("cum_vol") else float("nan"))
        self.continuous[i] = bool(row.get("continuous"))
        self.n = i + 1

    def view(self) -> dict[str, np.ndarray]:
        n = self.n
        z = np.asarray([], dtype=float)
        if n <= 0:
            return {
                "t": z,
                "bid": z,
                "ask": z,
                "bid_qty": z,
                "ask_qty": z,
                "special": np.asarray([], dtype=bool),
                "ask_fresh_sec": z,
                "bid_fresh_sec": z,
                "fresh_sec": z,
                "fresh_source": np.asarray([], dtype=object),
                "executable": np.asarray([], dtype=bool),
                "px": z,
                "board_execution_state": np.asarray([], dtype=object),
                "cum_vol": z,
                "continuous": np.asarray([], dtype=bool),
            }
        return {
            "t": self.t[:n],
            "bid": self.bid[:n],
            "ask": self.ask[:n],
            "bid_qty": self.bid_qty[:n],
            "ask_qty": self.ask_qty[:n],
            "special": self.special[:n],
            "ask_fresh_sec": self.ask_fresh_sec[:n],
            "bid_fresh_sec": self.bid_fresh_sec[:n],
            "fresh_sec": self.fresh_sec[:n],
            "fresh_source": self.fresh_source[:n],
            "executable": self.executable[:n],
            "px": self.px[:n],
            "board_execution_state": self.state[:n],
            "cum_vol": self.cum_vol[:n],
            "continuous": self.continuous[:n],
        }


def _ingress_epoch(rec: dict[str, Any], pay: dict[str, Any]) -> Optional[float]:
    for obj in (rec, pay):
        if not isinstance(obj, dict):
            continue
        for k in INGRESS_KEYS:
            t = _parse_iso(obj.get(k))
            if t is not None:
                return float(t)
    return None


def _side_quote_t(pay: dict[str, Any], *, side: str) -> Optional[float]:
    keys = ("AskTime",) if side == "ask" else ("BidTime",)
    nest_k = "Sell1" if side == "ask" else "Buy1"
    cands: list[float] = []
    for k in keys:
        t = _parse_iso(pay.get(k))
        if t is not None:
            cands.append(float(t))
    nest = pay.get(nest_k)
    if isinstance(nest, dict):
        for k in ("Time", "QuoteTime", "AskTime", "BidTime"):
            t = _parse_iso(nest.get(k))
            if t is not None:
                cands.append(float(t))
    if not cands:
        return None
    return max(cands)


def _fresh_age(clock: Optional[float], event_t: float, ingress: Optional[float]) -> tuple[float, str]:
    et = float(event_t)
    if clock is not None and float(clock) <= et + 1e-12:
        age = et - float(clock)
        return (max(0.0, age), "BOARD_EVENT_TIME")
    if ingress is not None and float(ingress) <= et + 1e-12:
        age = et - float(ingress)
        return (max(0.0, age), "INGRESS_RECEIVED_AT")
    return (float("nan"), "UNRESOLVED")


def board_row(rec: dict[str, Any], pay: dict[str, Any], event_t: float) -> dict[str, Any]:
    b1 = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
    s1 = pay.get("Sell1") if isinstance(pay.get("Sell1"), dict) else {}
    bid = _f(b1.get("Price")) if b1 else _f(pay.get("BidPrice"))
    ask = _f(s1.get("Price")) if s1 else _f(pay.get("AskPrice"))
    bq = _f(b1.get("Qty")) if b1 else _f(pay.get("BidQty"))
    aq = _f(s1.get("Qty")) if s1 else _f(pay.get("AskQty"))
    sq = pay.get("SpecialQuote")
    if sq is None:
        sq = pay.get("special_quote")
    special = bool(sq) and str(sq) not in ("", "0", "None", "null", "False", "false")
    if aq is not None and aq <= 0:
        special = True
    if bq is not None and bq <= 0:
        special = True
    ing = _ingress_epoch(rec, pay)
    ask_age, ask_src = _fresh_age(_side_quote_t(pay, side="ask"), event_t, ing)
    bid_age, bid_src = _fresh_age(_side_quote_t(pay, side="bid"), event_t, ing)
    t_ask = _side_quote_t(pay, side="ask")
    t_bid = _side_quote_t(pay, side="bid")
    cands = [t for t in (t_ask, t_bid) if t is not None and float(t) <= float(event_t) + 1e-12]
    if cands:
        board_clock = max(cands)
        board_src = "BOARD_EVENT_TIME"
        fresh = max(0.0, float(event_t) - float(board_clock))
    elif ing is not None and float(ing) <= float(event_t) + 1e-12:
        board_src = "INGRESS_RECEIVED_AT"
        fresh = max(0.0, float(event_t) - float(ing))
    else:
        fresh = float("nan")
        board_src = "UNRESOLVED"
    if board_src == "UNRESOLVED" and (ask_src == "UNRESOLVED" or bid_src == "UNRESOLVED"):
        pass
    gate = is_executable_continuous_board(pay, event_t=event_t)
    px = _f(gate.get("CurrentPrice"))
    cum = _f(gate.get("TradingVolume"))
    return {
        "t": float(event_t),
        "bid": bid if bid is not None else float("nan"),
        "ask": ask if ask is not None else float("nan"),
        "bid_qty": bq if bq is not None else float("nan"),
        "ask_qty": aq if aq is not None else float("nan"),
        "special": bool(special),
        "ask_fresh_sec": float(ask_age),
        "bid_fresh_sec": float(bid_age),
        "fresh_sec": float(fresh),
        "fresh_source": board_src,
        "executable": bool(gate.get("ok")),
        "state": str(gate.get("state") or ""),
        "px": px if px is not None else float("nan"),
        "cum_vol": cum if cum is not None else float("nan"),
        "continuous": bool(gate.get("ok")) and str(gate.get("state") or "") in CONTINUOUS_STATES,
    }


def _fresh_ok(age: Any) -> bool:
    try:
        v = float(age)
    except (TypeError, ValueError):
        return False
    return v == v and v <= float(BOARD_FRESHNESS_SEC) + 1e-12


def snap_at(board: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return {"ok": False}
    i = int(np.searchsorted(t, t0, side="right") - 1)
    if i < 0:
        return {"ok": False}
    return {
        "ok": True,
        "i": i,
        "t": float(t[i]),
        "bid": float(board["bid"][i]),
        "ask": float(board["ask"][i]),
        "bid_qty": float(board["bid_qty"][i]),
        "ask_qty": float(board["ask_qty"][i]),
        "special": bool(board["special"][i]),
        "ask_fresh_sec": float(board["ask_fresh_sec"][i]),
        "bid_fresh_sec": float(board["bid_fresh_sec"][i]),
        "fresh_sec": float(board["fresh_sec"][i]),
        "fresh_source": str(board["fresh_source"][i] or "UNRESOLVED"),
        "executable": bool(board["executable"][i]),
        "state": str(board["board_execution_state"][i] or ""),
        "continuous": bool(board["continuous"][i]),
    }


def ask_entry_ok(snap: dict[str, Any], *, require_qty: bool = False) -> tuple[bool, str]:
    if not snap.get("ok"):
        return False, "NO_BOARD"
    if not bool(snap.get("executable")) or not bool(snap.get("continuous")):
        return False, "NOT_EXECUTABLE"
    if str(snap.get("state") or "") not in CONTINUOUS_STATES:
        return False, "NOT_CONTINUOUS"
    if bool(snap.get("special")):
        return False, "SPECIAL"
    if not _fresh_ok(snap.get("ask_fresh_sec")):
        return False, "ASK_STALE"
    if not _fresh_ok(snap.get("bid_fresh_sec")):
        return False, "BID_STALE"
    ask = snap.get("ask")
    bid = snap.get("bid")
    try:
        av = float(ask)
        bv = float(bid)
    except (TypeError, ValueError):
        return False, "NO_QUOTE"
    if not (av == av) or av <= 0:
        return False, "NO_ASK"
    if not (bv == bv) or bv <= 0:
        return False, "NO_BID"
    if require_qty:
        try:
            qv = float(snap.get("ask_qty"))
        except (TypeError, ValueError):
            return False, "ASK_QTY"
        if not (qv == qv) or qv < float(MIN_ASK_QTY_SCREEN):
            return False, "ASK_QTY"
    return True, ""


def _bid_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if not bool(board["executable"][i]) or not bool(board["continuous"][i]):
        return False
    if bool(board["special"][i]):
        return False
    if str(board["board_execution_state"][i] or "") not in CONTINUOUS_STATES:
        return False
    if not _fresh_ok(board["bid_fresh_sec"][i]):
        return False
    bid = float(board["bid"][i])
    bq = float(board["bid_qty"][i])
    if not (bid == bid) or bid <= 0:
        return False
    if not (bq == bq) or bq <= 0:
        return False
    return True


def last_bid_before(board: dict[str, np.ndarray], *, t0: float, mark_t: float) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return None, None
    i_hi = int(np.searchsorted(t, float(mark_t), side="right") - 1)
    i_lo = int(np.searchsorted(t, float(t0), side="right"))
    for i in range(i_hi, i_lo - 1, -1):
        if i < 0:
            break
        ti = float(t[i])
        if ti > float(mark_t) + 1e-12:
            continue
        if ti <= float(t0) + 1e-12:
            break
        if _bid_ok(board, i):
            return float(board["bid"][i]), ti
    return None, None


def path_mfe_mae(board: dict[str, np.ndarray], *, t0: float, ask: float, path_end: float) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or int(t.size) == 0 or not (ask == ask) or ask <= 0:
        return None, None
    i0 = int(np.searchsorted(t, float(t0), side="right"))
    mfe = None
    mae = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti <= float(t0) + 1e-12:
            continue
        if ti > float(path_end) + 1e-12:
            break
        if not _bid_ok(board, i):
            continue
        bps = (float(board["bid"][i]) / float(ask) - 1.0) * 10000.0
        if mfe is None or bps > mfe:
            mfe = bps
        if mae is None or bps < mae:
            mae = bps
    return mfe, mae


def assert_split_allowed(split: str) -> None:
    s = str(split)
    if s == "DEVELOPMENT":
        return
    if s == "HOLDOUT":
        if not freeze_active():
            AUDIT["HOLDOUT_READ_BEFORE_RULE_FREEZE_N"] += 1
            raise RuntimeError("HOLDOUT_READ_BEFORE_RULE_FREEZE")
        return
    if s == "STRESS":
        if not freeze_active():
            AUDIT["HOLDOUT_READ_BEFORE_RULE_FREEZE_N"] += 1
            AUDIT["STRESS_READ_BEFORE_HOLDOUT_DECISION_N"] += 1
            raise RuntimeError("STRESS_READ_BEFORE_FREEZE")
        if not holdout_decision_pass():
            AUDIT["STRESS_READ_BEFORE_HOLDOUT_DECISION_N"] += 1
            raise RuntimeError("STRESS_READ_BEFORE_HOLDOUT_DECISION")
        return
    raise RuntimeError(f"UNKNOWN_SPLIT:{s}")


def sealed_caps(days: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    from _p1_inventory import resolve_universe

    out: list[dict[str, Any]] = []
    blockers: list[str] = []
    for day in days:
        if str(day) == str(TODAY):
            blockers.append(f"ACTIVE_DAY:{day}")
            continue
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > MAX_RESEARCH_DATE:
            AUDIT["FUTURE_DATA_N"] += 1
            blockers.append(f"FUTURE:{day}")
            continue
        cap = find_capture_dir(str(day))
        uni = resolve_universe(str(day), cap) if cap is not None else {}
        rec = {
            "date": str(day),
            "capture_path": str(cap) if cap is not None else "",
            "universe_symbols": list(uni.get("symbols") or []),
            "ok": cap is not None and bool(uni.get("symbols")),
        }
        if not rec["ok"]:
            blockers.append(f"CAPTURE_OR_UNIVERSE_MISSING:{day}")
        out.append(rec)
    return out, blockers


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    split = str(payload.get("split") or "")
    try:
        assert_split_allowed(split)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "BOARD_FRESHNESS_DRIFT"}
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    t0w = time.perf_counter()
    leak = {
        "FUTURE_BAR_N": 0,
        "BAR_INTEG_FAIL_N": 0,
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
        "FUTURE_BOARD_N": 0,
    }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        bufs: dict[str, BoardTape] = {s: BoardTape() for s in universe}
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
        events_n = 0
        last_et: Optional[float] = None
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
            if recv:
                pay["received_at"] = recv
            last_et = float(et)
            events_n += 1
            row = board_row(rec, pay, float(et))
            if str(row.get("fresh_source") or "") not in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT", "UNRESOLVED"):
                leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += 1
            bufs[sym].append(row)
            builders[sym].on_event(
                et=float(et),
                px=row["px"] if row["px"] == row["px"] else None,
                cum_vol=row.get("cum_vol"),
                bid=row["bid"] if row["bid"] == row["bid"] else None,
                ask=row["ask"] if row["ask"] == row["ask"] else None,
                continuous=bool(row.get("continuous")),
            )
            if events_n % 400000 == 0:
                print(f"{day} stream events={events_n} last_et={last_et}", flush=True)

        signals: list[dict[str, Any]] = []
        bar_meta: list[dict[str, Any]] = []
        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            leak["FUTURE_BAR_N"] += int(integ.get("FUTURE_BAR_N") or 0)
            bar_meta.append({"date": day, "symbol": s, **integ, **builders[s].leak})
            if not integ.get("ok"):
                leak["BAR_INTEG_FAIL_N"] += 1
                continue
            n = int(raw["close"].size)
            if n == 0:
                continue
            vwap = session_vwap(raw["close"], raw["volume"], raw.get("vwap_num"))
            board = bufs[s].view()
            for i in range(n):
                bits = signal_at(raw["high"], raw["close"], raw["volume"], vwap, i)
                if not bits["SIGNAL"]:
                    continue
                t0 = float(raw["finalize_t"][i])
                snap = snap_at(board, t0)
                if snap.get("ok") and float(snap.get("t") or 0.0) > t0 + 1e-12:
                    leak["FUTURE_BOARD_N"] += 1
                ok, reason = ask_entry_ok(snap, require_qty=False)
                ask = float(snap["ask"]) if ok else None
                rec_s: dict[str, Any] = {
                    "date": day,
                    "session": "AM",
                    "symbol": s,
                    "i": i,
                    "bar_start": float(raw["minute_epoch"][i]),
                    "t0": t0,
                    "close": float(raw["close"][i]),
                    "high": float(raw["high"][i]),
                    "volume": float(raw["volume"][i]),
                    "vwap": float(vwap[i]) if vwap[i] == vwap[i] else None,
                    "P1": bits["P1"],
                    "P2": bits["P2"],
                    "P3": bits["P3"],
                    "FIRST_CROSS": bits["FIRST_CROSS"],
                    "executable_signal": bool(ok),
                    "ask_reason": reason,
                    "ask_t0": ask,
                    "ask_qty": float(snap["ask_qty"]) if snap.get("ok") else None,
                    "bid_t0": float(snap["bid"]) if snap.get("ok") else None,
                    "fresh_source": snap.get("fresh_source") if snap.get("ok") else None,
                    "ask_fresh_sec": snap.get("ask_fresh_sec") if snap.get("ok") else None,
                    "bid_fresh_sec": snap.get("bid_fresh_sec") if snap.get("ok") else None,
                    "spread_bps": None,
                    "mfe_bps": None,
                    "mae_bps": None,
                    "qty100_ok": False,
                }
                if snap.get("ok"):
                    bid = float(snap["bid"])
                    av = float(snap["ask"])
                    if bid == bid and av == av and bid > 0 and av > 0:
                        mid = (av + bid) / 2.0
                        if mid > 0:
                            rec_s["spread_bps"] = (av - bid) / mid * 10000.0
                    aq = float(snap["ask_qty"]) if snap["ask_qty"] == snap["ask_qty"] else 0.0
                    rec_s["qty100_ok"] = bool(ok) and aq >= float(MIN_ASK_QTY_SCREEN)
                if ok and ask is not None and ask > 0:
                    path_end = min(float(t0) + float(PATH_SEC), float(am_end))
                    mfe, mae = path_mfe_mae(board, t0=t0, ask=float(ask), path_end=path_end)
                    rec_s["mfe_bps"] = mfe
                    rec_s["mae_bps"] = mae
                    for h in HORIZONS_SEC:
                        mark_t = min(float(t0) + float(h), float(am_end))
                        bid_h, _bt = last_bid_before(board, t0=t0, mark_t=mark_t)
                        if bid_h is None or bid_h <= 0:
                            rec_s[f"markout_{int(h)}"] = None
                        else:
                            rec_s[f"markout_{int(h)}"] = (float(bid_h) / float(ask) - 1.0) * 10000.0
                else:
                    for h in HORIZONS_SEC:
                        rec_s[f"markout_{int(h)}"] = None
                signals.append(rec_s)
        print(
            f"{day} {split} events={events_n} signals={len(signals)} evaluable="
            f"{sum(1 for r in signals if r.get('executable_signal'))} last_et={last_et}",
            flush=True,
        )
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        return {
            "ok": int(leak["BAR_INTEG_FAIL_N"]) == 0 and int(leak["FUTURE_BAR_N"]) == 0 and int(leak["FUTURE_BOARD_N"]) == 0 and int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0,
            "date": day,
            "split": split,
            "signals": signals,
            "bar_meta": bar_meta,
            "leak": leak,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None
            if int(leak["BAR_INTEG_FAIL_N"]) == 0
            and int(leak["FUTURE_BAR_N"]) == 0
            and int(leak["FUTURE_BOARD_N"]) == 0
            else "BAR_OR_FUTURE_LEAK",
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "split": split,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def harvest_split(split: str, days: tuple[str, ...] | list[str]) -> dict[str, Any]:
    assert_split_allowed(split)
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_caps(list(days))
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "inventory": invs}
    rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"{split}_{day}_signals.json"
        saved = _load(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and str(saved.get("split") or "") == split:
            rows.extend(list(saved.get("signals") or []))
            day_meta.append({"date": day, "cache": True, "signal_n": len(saved.get("signals") or [])})
            print(f"cache-hit {split} {day} n={len(saved.get('signals') or [])}", flush=True)
            continue
        body = process_day(
            {
                "date": day,
                "split": split,
                "capture_path": inv["capture_path"],
                "universe": list(inv["universe_symbols"]),
            }
        )
        if not body.get("ok"):
            return {"ok": False, "blocker": f"{split}:{day}:{body.get('blocker')}", "inventory": invs}
        _dump(cache, body)
        rows.extend(list(body.get("signals") or []))
        day_meta.append({"date": day, "cache": False, "signal_n": len(body.get("signals") or []), "leak": body.get("leak")})
    return {"ok": True, "split": split, "rows": rows, "day_meta": day_meta, "inventory": invs, "audit": dict(AUDIT)}
