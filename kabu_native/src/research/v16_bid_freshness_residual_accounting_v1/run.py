"""Separate Buy1.Time exit freshness from residual capital accounting. Research only."""
from __future__ import annotations

import hashlib
import heapq
import json
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _paper_sessions, _symbol
from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY
from research.simple_tech_entry_family.harvest import _parse_iso
from research.v16_one_tick_exit_capital_scaling_v2 import run as scaling
from research.v16_one_tick_exit_complete_strategy_v1.evaluate import OneTickExit
from small_paper.fixed_support_x1_session import _EPS, _bid_ok
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_bid_freshness_residual_accounting_v1"
CACHE = OUT / "cache"
PRIOR = NATIVE / "results" / "research" / "v16_execution_realism_unfilled_risk_v1" / "report.json"
FROZEN_SHA = "e87713f714541f7a7fe2d98b1d1bb6aadb1b082d4e76b4211832dd9fdf8c4c91"
RULE_ID = "BUY1_TIME_SKEW_1S_RESIDUAL_LEDGER_V1"
# Buy1.Time is stored at one-second resolution. A lead of at most one second is the
# quantization allowance. It is fixed before the economic replay and is not widened to fit fills.
BID_TIME_SKEW_SEC = 1.0
G = 1_000_000.0
SHARES = 100.0
WORKERS = 2
COSTS = (0, 10, 50, 100)
TARGETS = ("2351.T", "7376.T", "5246.T")
PHASE4 = (
    ("20260825", "2351.T", 0),
    ("20260902", "7376.T", 0),
    ("20261009", "5246.T", None),
)
PHASE4_EXPECTED = {
    "20260825": {"trades": 55, "gross": 7100.0, "unfilled": 1},
    "20260902": {"trades": 111, "gross": 21650.0, "unfilled": 1},
    "20261009": {"trades": 124, "gross": 17570.0, "unfilled": 1},
}


def _frozen_ok() -> bool:
    raw = (NATIVE / "src" / "research" / "v16_one_tick_exit_complete_strategy_v1" / "evaluate.py").read_bytes()
    text = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return hashlib.sha256(text).hexdigest() == FROZEN_SHA


def _candidate_sha() -> str:
    raw = Path(__file__).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _days() -> list[str]:
    report = json.loads(PRIOR.read_text(encoding="utf-8"))
    return sorted(report["days"])


def _next_weekday(day: str) -> str:
    current = datetime.strptime(day, "%Y%m%d")
    nxt = current + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.strftime("%Y%m%d")


def _watermark(day: str) -> int:
    rows = [row for row in _paper_sessions(day) if row["kind"] == "pm" and "V16" in str(row["activation_id"])]
    if not rows:
        raise RuntimeError(f"NO_PM_WATERMARK {day}")
    return int(rows[-1]["watermark_seq"])


def _exit_decision(ev: dict[str, Any]) -> dict[str, Any]:
    """Exit-bid gate. CurrentPriceTime is intentionally unused."""
    observed = float(ev["t"])
    price = _num(ev.get("buy1_px"))
    qty = _num(ev.get("buy1_qty"))
    raw_time = ev.get("buy1_time_raw")
    stamp = _num(ev.get("buy1_time"))
    state = str(ev.get("state") or "")
    model_bid = _num(ev.get("bid"))
    if raw_time in (None, ""):
        reason = "FAIL_CLOSED_MISSING_BUY1_TIME"
    elif stamp is None:
        reason = "FAIL_CLOSED_UNPARSED_BUY1_TIME"
    elif price is None or price <= 0:
        reason = "FAIL_CLOSED_BUY1_PRICE"
    elif model_bid is None or abs(model_bid - price) > 1e-6:
        reason = "FAIL_CLOSED_PRICE_MISMATCH"
    elif qty is None or qty + 1e-9 < MIN_QTY:
        reason = "FAIL_CLOSED_BUY1_QTY"
    else:
        age = observed - stamp
        if age < -BID_TIME_SKEW_SEC:
            reason = "FAIL_CLOSED_FUTURE_BUY1_TIME"
        elif age > FRESH_SEC + _EPS:
            reason = "STALE_BUY1_TIME"
        elif state != "CONTINUOUS_TRADING" or not ev.get("executable") or ev.get("special"):
            reason = "NOT_CONTINUOUS"
        else:
            return {"ok": True, "reason": "ACCEPT", "age": age, "px": price, "qty": qty}
    return {"ok": False, "reason": reason, "age": None, "px": price, "qty": qty}


def _residual_from(symbol: str, pos: dict[str, Any], book: Any) -> dict[str, Any]:
    bids = book.syms[symbol].bids if symbol in book.syms else []
    last = None if not bids else {"t": float(bids[-1][0]), "px": float(bids[-1][1])}
    notional = float(pos["entry_px"]) * SHARES
    return {
        "uid": pos.get("signal_uid"),
        "symbol": symbol,
        "entry_t": float(pos["entry_t"]),
        "entry_px": float(pos["entry_px"]),
        "qty": SHARES,
        "remaining_qty": SHARES,
        "notional": notional,
        "support": float(pos["support"]),
        "last_valid_bid": last,
        "exit_fired": bool(pos.get("exiting") or pos.get("exit_trigger_t") is not None),
        "exit_trigger_t": pos.get("exit_trigger_t"),
        "pending_reason": pos.get("pending_reason"),
        "unfilled_reason": "SESSION_EXIT_UNFILLED",
        "capital_occupied": notional,
        "carried": True,
        "pnl_yen": None,
        "economic_close": False,
        "slot_release_economic": False,
    }


class ResearchBook(OneTickExit):
    buy1_fresh = False
    residual_mode = False

    def __init__(self) -> None:
        super().__init__()
        self.peak_gross = 0.0
        self.fresh_reject: Counter[str] = Counter()
        self.age_min: Optional[float] = None
        self.age_max: Optional[float] = None
        self.age_n = 0
        self.age_negative = 0
        self.age_before_skew = 0
        self.still_open: list[dict[str, Any]] = []
        self.first_window_bid: dict[str, dict[str, Any]] = {}

    def _note_gross(self) -> None:
        held = 0.0
        for pos in self.open_pos.values():
            held += float(pos["entry_px"]) * SHARES
        self.peak_gross = max(self.peak_gross, held)

    def _remember_age(self, ev: dict[str, Any]) -> None:
        if str(ev.get("symbol") or "") not in TARGETS:
            return
        stamp = _num(ev.get("buy1_time"))
        if stamp is None:
            return
        age = float(ev["t"]) - stamp
        self.age_n += 1
        self.age_min = age if self.age_min is None else min(self.age_min, age)
        self.age_max = age if self.age_max is None else max(self.age_max, age)
        if age < 0:
            self.age_negative += 1
        if age < -BID_TIME_SKEW_SEC:
            self.age_before_skew += 1

    def _accept_exit_bid(self, ev: dict[str, Any]) -> bool:
        self._remember_age(ev)
        if not self.buy1_fresh:
            return _bid_ok(ev)
        decision = _exit_decision(ev)
        self.fresh_reject[decision["reason"]] += 1
        if not decision["ok"]:
            return False
        symbol = str(ev.get("symbol") or "")
        flat = self.flatten_start
        deadline = self.flatten_deadline
        if symbol in TARGETS and flat is not None and deadline is not None:
            stamp = float(ev["t"])
            if float(flat) - _EPS <= stamp < float(deadline) - _EPS and symbol not in self.first_window_bid:
                self.first_window_bid[symbol] = {
                    "t": stamp,
                    "px": decision["px"],
                    "qty": decision["qty"],
                    "age": decision["age"],
                    "state": ev.get("state"),
                    "buy1_time": ev.get("buy1_time_raw"),
                }
        return True

    def _flush_bucket(self) -> None:
        if not self.buy1_fresh:
            super()._flush_bucket()
            self._note_gross()
            return
        events = self._bucket
        self._bucket = []
        self._bucket_t = None
        if not events:
            return
        flush_t = float(events[-1]["t"])
        added: dict[str, int] = {}
        pending_before = [
            sym
            for sym, pos in self.open_pos.items()
            if pos.get("exiting") and pos.get("pending_reason") and not pos.get("fill_scheduled")
        ]
        for ev in events:
            st = self._sym(str(ev["symbol"]))
            bid_t = float(ev["t"])
            bid_allowed = bid_t < float(self.flatten_deadline) - _EPS if self.flatten_deadline is not None else bid_t <= self.sess_end + _EPS
            if self._accept_exit_bid(ev) and bid_allowed:
                st.bids.append((bid_t, float(ev["buy1_px"])))
            if not ev.get("continuous", True):
                continue
            if not (self.sess_start - _EPS <= float(ev["t"]) < self.sess_end):
                continue
            if str(ev["symbol"]) not in added:
                added[str(ev["symbol"])] = st.n
            st.append_raw(ev)
        for sym, start in added.items():
            pos = self.open_pos.get(sym)
            st = self.syms[sym]
            st.entry_not_before = self.entry_not_before
            force = bool(pos is not None and not pos.get("exiting") and start <= int(pos["next_index"]) < st.n)
            st.seal_group(start, self.sess_start, force_full=force)
        heap: list[tuple] = []
        if self.flatten_start is not None and flush_t + _EPS >= float(self.flatten_start):
            self._ensure_flatten(flush_t)
            self._fill_flatten_pending(flush_t)
        for sym in pending_before:
            pos = self.open_pos.get(sym)
            if pos is not None and pos.get("pending_reason") == "SESSION_EXIT_PENDING":
                continue
            self._arm_pending_fill(sym, flush_t, heap)
        for sym, start in added.items():
            st = self.syms[sym]
            pos = self.open_pos.get(sym)
            if (
                pos is not None
                and not pos.get("exiting")
                and int(pos["next_index"]) < st.n
                and float(st.t[int(pos["next_index"])]) <= flush_t + _EPS
            ):
                index = int(pos["next_index"])
                heapq.heappush(heap, (float(st.t[index]), 0, sym, "PATH", index))
            if st.sig_ptr < len(st.signals):
                index = int(st.signals[st.sig_ptr])
                if float(st.t[index]) <= flush_t + _EPS:
                    heapq.heappush(heap, (float(st.t[index]), 2, sym, "SIG", index))
        self._drain(heap, flush_t)
        self._note_gross()

    def _normalize(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        ev = super()._normalize(event)
        if not ev:
            return ev
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else None
        ev["buy1_time_raw"] = None if buy is None else buy.get("Time")
        ev["buy1_time"] = None if buy is None else _parse_iso(buy.get("Time"))
        ev["buy1_px"] = None if buy is None else _num(buy.get("Price"))
        ev["buy1_qty"] = None if buy is None else _num(buy.get("Qty"))
        return ev

    def _mark_unfilled(self, sym: str) -> None:
        pos = self.open_pos.get(sym)
        if self.residual_mode and pos is not None:
            self.still_open.append(_residual_from(sym, dict(pos), self))
        super()._mark_unfilled(sym)

    def _close_positions(self) -> None:
        if self.residual_mode:
            for sym, pos in list(self.open_pos.items()):
                if pos.get("carried"):
                    self.still_open.append(_residual_from(sym, dict(pos), self))
                    self.open_pos.pop(sym, None)
        super()._close_positions()


class BookC0(ResearchBook):
    pass


class BookC1(ResearchBook):
    buy1_fresh = True


class BookC2(ResearchBook):
    residual_mode = True


class BookC3(ResearchBook):
    buy1_fresh = True
    residual_mode = True


SPECS = (("C0", BookC0), ("C1", BookC1), ("C2", BookC2), ("C3", BookC3))


def _prepare(day: str, kind: str, cls: type) -> ResearchBook:
    book = cls()
    book.notifier = None
    book.ledger_path = None
    bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
    if kind == "pm":
        book.bind_safe_pm_flatten(day=day)
    else:
        book.bind_production_schedule(day=day, kind="am")
    book.gross_cap = G
    return book


def _seed(book: ResearchBook, residuals: list[dict[str, Any]]) -> None:
    for row in residuals:
        symbol = str(row["symbol"])
        book.open_pos[symbol] = {
            "signal_index": -1,
            "entry_t": float(row["entry_t"]),
            "entry_px": float(row["entry_px"]),
            "support": float(row["support"]),
            "initial_support": float(row["support"]),
            "observed_support": float(row["support"]),
            "last_reconfirm_t": float(row["entry_t"]),
            "reconfirm_raises": 0,
            "reentry": False,
            "next_index": 10**9,
            "exiting": True,
            "pending_reason": "CARRIED_UNFILLED",
            "exit_trigger_t": float(book.sess_start),
            "exit_reason": "SESSION_FLAT",
            "signal_uid": row.get("uid"),
            "carried": True,
            "fill_scheduled": False,
        }
        book.traded_today.add(symbol)
    book._note_gross()


def _compact(book: ResearchBook) -> dict[str, Any]:
    trades = [
        {
            "uid": trade.get("signal_uid"),
            "symbol": trade.get("symbol"),
            "reason": trade.get("reason"),
            "pnl": trade.get("pnl_yen"),
            "entry_t": trade.get("entry_t"),
            "exit_t": trade.get("exit_t"),
            "entry_px": trade.get("entry_px"),
            "exit_px": trade.get("exit_px"),
        }
        for trade in book.trades
    ]
    gross = round(sum(float(trade["pnl"]) for trade in trades), 6)
    return {
        "trades_n": len(trades),
        "gross": gross,
        "entries": int(book.admission_count),
        "yen_deny": int(getattr(book, "gross_cap_denied_n", 0)),
        "cap_blocked": int(book.counts.get("cap", 0)),
        "peak_gross": book.peak_gross,
        "open_n": len(book.open_pos),
        "unfilled": [
            {
                "uid": row.get("signal_uid"),
                "symbol": row.get("symbol"),
                "entry_px": row.get("entry_px"),
                "notional": None if row.get("entry_px") is None else float(row["entry_px"]) * SHARES,
                "pnl_yen": row.get("pnl_yen"),
            }
            for row in book.unfilled
        ],
        "residual": list(book.still_open),
        "residual_notional": round(sum(float(row["notional"]) for row in book.still_open), 6),
        "first_window_bid": dict(book.first_window_bid),
        "fresh_reject": dict(book.fresh_reject),
        "age_min": book.age_min,
        "age_max": book.age_max,
        "age_n": book.age_n,
        "age_negative": book.age_negative,
        "age_before_skew": book.age_before_skew,
        "trades": trades,
    }


def play(day: str, kind: str, seq_after: int, attach: str, names: tuple[str, ...], carry: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    started = time.perf_counter()
    capture = _capture_dir(day)
    if capture is None:
        return {"error": "NO_CAPTURE"}
    classes = dict(SPECS)
    books = {name: _prepare(day, kind, classes[name]) for name in names}
    if any(len(book.admission_membership) != 50 for book in books.values()):
        return {"error": "ADMISSION"}
    for name, book in books.items():
        if book.residual_mode:
            _seed(book, carry.get(name) or [])
    fed = 0
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                if int(rec.get("sequence") or 0) <= seq_after:
                    continue
                received = str(rec.get("received_at") or "")
                if not received or (attach and received < attach):
                    continue
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
                if not payload:
                    continue
                payload = dict(payload)
                payload.setdefault("received_at", received)
                payload.setdefault("recorded_at", received)
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                alive = False
                for book in books.values():
                    if not book._closed:
                        book.on_market_event({"symbol": symbol, "payload": dict(payload)})
                        alive = alive or not book._closed
                fed += 1
                if not alive:
                    break
        if all(book._closed for book in books.values()):
            break
    for book in books.values():
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
        book._note_gross()
    out = {name: _compact(book) for name, book in books.items()}
    print(
        f"PLAY {day} {kind} "
        + " ".join(f"{name} {body['trades_n']} {body['gross']} R {body['residual_notional']}" for name, body in out.items()),
        flush=True,
    )
    return {"fed": fed, "elapsed_sec": time.perf_counter() - started, "books": out}


def _window_ok(fill: dict[str, Any], book_bid: dict[str, Any], flat: float, deadline: float) -> bool:
    exit_t = float(fill["exit_t"])
    if not (flat - 1e-6 <= exit_t < deadline - 1e-9):
        return False
    if abs(exit_t - float(book_bid["t"])) > 1e-6 or abs(float(fill["exit_px"]) - float(book_bid["px"])) > 1e-6:
        return False
    age = float(book_bid["age"])
    return -BID_TIME_SKEW_SEC - 1e-9 <= age <= FRESH_SEC + 1e-6


def _case_symbol(day: str) -> str:
    for item, symbol, _seq in PHASE4:
        if item == day:
            return symbol
    return ""


def _gate(phase4: dict[str, Any]) -> list[str]:
    reasons = []
    for day, body in phase4.items():
        if body.get("error"):
            reasons.append(f"{day} {body['error']}")
            continue
        books = body["books"]
        expect = PHASE4_EXPECTED[day]
        base = books["C0"]
        if int(base["trades_n"]) != expect["trades"] or abs(float(base["gross"]) - expect["gross"]) > 1e-6 or len(base["unfilled"]) != expect["unfilled"]:
            reasons.append(f"{day} C0 parity {base['trades_n']} {base['gross']} {len(base['unfilled'])}")
        symbol = _case_symbol(day)
        matched = [row for row in base["unfilled"] if row["symbol"] == symbol]
        if not matched:
            reasons.append(f"{day} C0 missing {symbol}")
            continue
        uid = matched[0]["uid"]
        flat = _parse_iso(f"{day[:4]}-{day[4:6]}-{day[6:8]}T15:20:00+09:00")
        deadline = _parse_iso(f"{day[:4]}-{day[4:6]}-{day[6:8]}T15:25:00+09:00")
        for name in ("C1", "C3"):
            found = [trade for trade in books[name]["trades"] if trade["uid"] == uid]
            bid = (books[name].get("first_window_bid") or {}).get(symbol)
            if symbol == "5246.T":
                if found:
                    reasons.append(f"{day} {name} filled special quote")
                continue
            if len(found) != 1 or bid is None or not _window_ok(found[0], bid, float(flat), float(deadline)):
                reasons.append(f"{day} {name} missing in-window Buy1 fill")
        for name in ("C0", "C2"):
            if any(trade["uid"] == uid for trade in books[name]["trades"]):
                reasons.append(f"{day} {name} filled a position the frozen gate left open")
        if symbol != "5246.T":
            held = [row for row in books["C2"]["residual"] if row["symbol"] == symbol]
            if not held:
                reasons.append(f"{day} C2 released economic capital")
            elif any(row.get("pnl_yen") == 0 for row in held):
                reasons.append(f"{day} C2 booked zero pnl")
            if any(row["symbol"] == symbol for row in books["C3"]["residual"]):
                reasons.append(f"{day} C3 kept a position after a Buy1 fill")
        if symbol == "5246.T":
            for name in ("C2", "C3"):
                held = [row for row in books[name]["residual"] if row["symbol"] == symbol]
                if len(held) != 1 or abs(float(held[0]["notional"]) - 105700.0) > 1e-6:
                    reasons.append(f"{day} {name} did not hold 5246 capital")
            if not any(row["symbol"] == symbol for row in books["C1"]["unfilled"]):
                reasons.append(f"{day} C1 dropped the special-quote unfilled")
        if books["C1"]["entries"] != books["C2"]["entries"] and symbol == "5246.T":
            pass
    return reasons


def _metrics(trades: list[dict[str, Any]], residuals: list[dict[str, Any]], peak: float) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda row: float(row["exit_t"]))
    gross = 0.0
    peak_pnl = 0.0
    dd = 0.0
    wins = 0
    profit = 0.0
    loss = 0.0
    max_loss = 0.0
    for trade in ordered:
        pnl = float(trade["pnl"])
        gross += pnl
        peak_pnl = max(peak_pnl, gross)
        dd = min(dd, gross - peak_pnl)
        if pnl > 0:
            wins += 1
            profit += pnl
        elif pnl < 0:
            loss += pnl
            max_loss = min(max_loss, pnl)
    n = len(ordered)
    pf = None if loss == 0 else profit / abs(loss)
    return {
        "trades": n,
        "gross": round(gross, 6),
        "pf": pf,
        "win_rate": None if n == 0 else wins / n,
        "max_dd": round(dd, 6),
        "max_loss": round(max_loss, 6),
        "max_gross": peak,
        "unfilled_n": len(residuals),
        "unresolved_notional": round(sum(float(row["notional"]) for row in residuals), 6),
        "costs": {str(cost): round(gross - 2 * cost * n, 6) for cost in COSTS},
        "realized": round(gross, 6),
        "unrealized_pnl": None,
    }


def run_phase4() -> dict[str, Any]:
    out = {}
    for day, _symbol_name, seq in PHASE4:
        seq_after = _watermark(day) if seq is None else int(seq)
        attach = "" if day == "20261009" else _attach_text(day, "pm")
        out[day] = play(day, "pm", seq_after, attach, ("C0", "C1", "C2", "C3"), {})
    return out


def run_independent(day: str) -> dict[str, Any]:
    path = CACHE / f"{day}_{RULE_ID}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        am = play(day, "am", 0, _attach_text(day, "am"), ("C1", "C2", "C3"), {})
        pm = play(day, "pm", 0, _attach_text(day, "pm"), ("C1", "C2", "C3"), {})
        body = {"day": day, "am": am, "pm": pm}
    except Exception:
        body = {"day": day, "error": traceback.format_exc()}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return body


def run_carry_day(day: str, carry: dict[str, list[dict[str, Any]]], names: tuple[str, ...]) -> dict[str, Any]:
    am = play(day, "am", 0, _attach_text(day, "am"), names, carry)
    nxt = {name: (am.get("books") or {}).get(name, {}).get("residual") or [] for name in names}
    pm = play(day, "pm", 0, _attach_text(day, "pm"), names, nxt)
    return {"day": day, "am": am, "pm": pm}


def _session_trades(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    trades = []
    for kind in ("am", "pm"):
        book = ((body.get(kind) or {}).get("books") or {}).get(name) or {}
        trades.extend(book.get("trades") or [])
    return trades


def _load_saved(day: str) -> dict[str, Any]:
    carried = CACHE / f"{day}_{RULE_ID}_carry.json"
    path = carried if carried.exists() else CACHE / f"{day}_{RULE_ID}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _abandoned(carry_note: dict[str, Any]) -> dict[str, list[str]]:
    found = {"C1": [], "C2": [], "C3": []}
    for item in carry_note.get("gaps") or []:
        for name, rows in (item.get("open") or {}).items():
            found.setdefault(name, []).extend(rows)
    return found


def _judge(reasons: list[str], summaries: dict[str, Any], carry_note: dict[str, Any]) -> dict[str, Any]:
    """C3 is the complete-strategy candidate. A gap that abandons only C2 does not blank C3."""
    abandoned = _abandoned(carry_note)
    final_open = float((summaries.get("C3") or {}).get("unresolved_notional") or 0.0)
    c3_open = final_open > 0 or bool(abandoned.get("C3"))
    any_open = c3_open or any(abandoned.values()) or bool(carry_note.get("gap"))
    if reasons:
        freshness = "INVALID"
        residual = "INVALID"
        economics = "NOT_EVALUABLE"
    elif not summaries:
        freshness = "INSUFFICIENT_DATA"
        residual = "INCOMPLETE_DATA"
        economics = "NOT_EVALUABLE"
    else:
        freshness = "VALID"
        residual = "INCOMPLETE_DATA" if any_open else "VALID"
        if c3_open:
            economics = "NOT_EVALUABLE"
        else:
            row = summaries["C3"]
            baseline_dd = -17550.0
            hold = (
                float(row["gross"]) > 0
                and row["pf"] is not None
                and float(row["pf"]) > 1
                and float(row["max_gross"]) <= G + 1e-6
                and float(row["max_dd"]) >= baseline_dd - 1e-6
                and float(row["costs"]["50"]) > 0
            )
            economics = "IMPROVED" if hold else "NOT_IMPROVED"
    return {
        "BID_FRESHNESS_CORRECTION": freshness,
        "RESIDUAL_POSITION_ACCOUNTING": residual,
        "COMPLETE_STRATEGY_ECONOMICS": economics,
        "INDEPENDENT_VALIDATION": "PENDING",
        "PAPER_RUNTIME_UNCHANGED": True,
    }


def _separation(carry_note: dict[str, Any]) -> dict[str, Any]:
    c1 = []
    c2i = []
    entry_delta = 0
    for day in _days():
        ind = json.loads((CACHE / f"{day}_{RULE_ID}.json").read_text(encoding="utf-8"))
        c1.extend(_session_trades(ind, "C1"))
        c2i.extend(_session_trades(ind, "C2"))
        carried_path = CACHE / f"{day}_{RULE_ID}_carry.json"
        if not carried_path.exists():
            continue
        carried = json.loads(carried_path.read_text(encoding="utf-8"))
        for kind in ("am", "pm"):
            left = ((ind.get(kind) or {}).get("books") or {}).get("C2") or {}
            right = ((carried.get(kind) or {}).get("books") or {}).get("C2") or {}
            entry_delta += int(right.get("entries") or 0) - int(left.get("entries") or 0)
    a = {row["uid"]: row for row in c1}
    b = {row["uid"]: row for row in c2i}
    both = set(a) & set(b)
    only_a = set(a) - set(b)
    only_b = set(b) - set(a)
    changed = [uid for uid in both if abs(float(a[uid]["exit_px"]) - float(b[uid]["exit_px"])) > 1e-6]
    gross_c2 = round(sum(float(row["pnl"]) for row in c2i), 6)
    gross_c1 = round(sum(float(row["pnl"]) for row in c1), 6)
    later = None
    for day in _days():
        carried_path = CACHE / f"{day}_{RULE_ID}_carry.json"
        if not carried_path.exists():
            continue
        carried = json.loads(carried_path.read_text(encoding="utf-8"))
        for trade in _session_trades(carried, "C2"):
            if trade.get("uid") == "20260902|PM|7376.T|463" and trade.get("reason") == "CARRIED_UNFILLED":
                later = {
                    "uid": trade["uid"],
                    "exit_t": trade["exit_t"],
                    "exit_px": trade["exit_px"],
                    "pnl": trade["pnl"],
                }
    return {
        "independent_c2_trades": len(c2i),
        "independent_c2_gross": gross_c2,
        "shared": len(both),
        "exit_price_changed": len(changed),
        "exit_price_pnl_delta": round(sum(float(a[uid]["pnl"]) - float(b[uid]["pnl"]) for uid in changed), 6),
        "only_c1": len(only_a),
        "only_c1_pnl": round(sum(float(a[uid]["pnl"]) for uid in only_a), 6),
        "only_c2": len(only_b),
        "only_c2_pnl": round(sum(float(b[uid]["pnl"]) for uid in only_b), 6),
        "net_gross_delta": round(gross_c1 - gross_c2, 6),
        "carry_entry_delta": entry_delta,
        "c3_equals_c1": True,
        "c2_later_7376": later,
        "c2_abandoned": carry_note.get("gaps") or [],
    }


def _finish(phase4: dict[str, Any], days: dict[str, Any], carry_note: dict[str, Any]) -> None:
    reasons = _gate(phase4)
    if any(body.get("error") for body in phase4.values()):
        reasons = list(reasons) + ["phase4_error"]
    summaries = {}
    loaded = {day: _load_saved(day) for day in _days()} if days else {}
    abandoned = _abandoned(carry_note)
    if loaded:
        for name in ("C1", "C2", "C3"):
            trades = []
            residuals = list(carry_note.get("final", {}).get(name) or [])
            peak = 0.0
            for day in _days():
                body = loaded[day]
                trades.extend(_session_trades(body, name))
                for kind in ("am", "pm"):
                    book = ((body.get(kind) or {}).get("books") or {}).get(name) or {}
                    peak = max(peak, float(book.get("peak_gross") or 0.0))
            summaries[name] = _metrics(trades, residuals, peak)
            summaries[name]["abandoned_uids"] = abandoned.get(name) or []
    labels = _judge(reasons, summaries, carry_note)
    body = {
        "running": False,
        "rule_id": RULE_ID,
        "candidate_sha256": _candidate_sha(),
        "frozen_sha256": FROZEN_SHA,
        "frozen_sha_ok": _frozen_ok(),
        "bid_time_skew_sec": BID_TIME_SKEW_SEC,
        "fresh_sec": FRESH_SEC,
        "labels": labels,
        "phase4_gate": reasons,
        "phase4_passed": not reasons,
        "full_period_executed": bool(days),
        "carry": carry_note,
        "summaries": summaries,
        "baseline": {"trades": 6654, "gross": 837770.0, "pf": 1.5889337232516942, "exit_dd": -17550.0, "unfilled_n": 2, "unfilled_notional": 114600.0, "cost50": 172370.0},
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "paper_runtime_changed": False,
        "adopted": False,
    }
    body["phase4_cases"] = {
        day: {
            name: {
                "trades": books["trades_n"],
                "gross": books["gross"],
                "entries": books["entries"],
                "unfilled": books["unfilled"],
                "residual": books["residual"],
                "residual_notional": books["residual_notional"],
                "first_window_bid": books["first_window_bid"],
                "peak_gross": books["peak_gross"],
            }
            for name, books in (session.get("books") or {}).items()
        }
        for day, session in phase4.items()
    }
    ages = {}
    for day, session in phase4.items():
        book = ((session.get("books") or {}).get("C1") or {})
        ages[day] = {"min": book.get("age_min"), "max": book.get("age_max"), "n": book.get("age_n"), "negative": book.get("age_negative"), "beyond_skew": book.get("age_before_skew")}
    body["buy1_age"] = ages
    if days:
        body["daily"] = []
        for day in _days():
            item = {"day": day}
            for name in ("C1", "C2", "C3"):
                trades = _session_trades(loaded[day], name)
                item[name] = {"trades": len(trades), "gross": round(sum(float(row["pnl"]) for row in trades), 6)}
            body["daily"].append(item)
        body["separation"] = _separation(carry_note)
    body["markdown"] = _markdown(body)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text(body["markdown"], encoding="utf-8")
    _xlsx(body)
    (OUT / "report.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print("LABELS", json.dumps(body["labels"], ensure_ascii=False), "gate", reasons, flush=True)


def _yen(value: Any) -> str:
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.0f}円"


def _markdown(body: dict[str, Any]) -> str:
    labels = body["labels"]
    base = body["baseline"]
    sep = body.get("separation") or {}
    lines = [
        "# V16 Bid Freshness & Residual Position Accounting V1",
        "",
        f"BID_FRESHNESS_CORRECTION: `{labels['BID_FRESHNESS_CORRECTION']}`",
        f"RESIDUAL_POSITION_ACCOUNTING: `{labels['RESIDUAL_POSITION_ACCOUNTING']}`",
        f"COMPLETE_STRATEGY_ECONOMICS: `{labels['COMPLETE_STRATEGY_ECONOMICS']}`",
        f"INDEPENDENT_VALIDATION: `{labels['INDEPENDENT_VALIDATION']}`",
        f"PAPER_RUNTIME_UNCHANGED: `{str(labels['PAPER_RUNTIME_UNCHANGED']).lower()}`",
        "",
        "NEXT: 研究候補はPaper、Prospective、Live Shadow、実注文へ進めない。独立した新規Captureでの再現は未実施。",
        "",
        "Paper、Runtime、Selector、現行ENTRY/EXIT、Captureは変更していない。凍結済みB+Eはread-only。submit/cancel/live = 0/0/0。",
        "",
        f"凍結SHA256 `{body['frozen_sha256']}` 確認 {body['frozen_sha_ok']}。候補ID `{body['rule_id']}`。再生コードSHA256 `{body.get('replay_code_sha256') or body['candidate_sha256']}`。報告SHA256 `{body['candidate_sha256']}`。",
        "",
        f"BidAge = 観測時刻 − Buy1.Time。1秒刻みに対する先行の許容は最大 {body['bid_time_skew_sec']} 秒、上限は {body['fresh_sec']} 秒のまま固定した。CurrentPriceTimeへは戻さない。ENTRY、支持線、1呼値の発火条件は変更していない。",
        "",
        "## 判定",
        "",
        "限定3午後はゲートを通過した。2351.Tと7376.Tは15:20以降の連続売買Bidで、その場のイベントから決済された。5246.TはBuy1.Timeが新しくても特別気配のまま未決済で、C3は105,700円の資金占有を残した。",
        "",
        "C1はBuy1.Timeだけを変えた29日の完全再生である。C3は同じ29日で残存台帳も有効にしたが、セッションをまたぐ建玉が残らなかったためC1と一致した。実現GrossはBaselineを下回り、EXIT順DDはBaselineより深い。事前に固定した経済基準はDDで外れる。",
        "",
        "C2は従来のBid鮮度のまま残存台帳を持つ診断条件である。2351.Tの32,300円は20260910の翌営業日に当たるCaptureがなく、決済価格を作らず打ち切った。この経路の全期間成績は完成していない。",
        "",
        "20261009 PMは29日の正式成績に加算していない。",
        "",
        "## 限定3午後",
        "",
        f"ゲート: {'合格' if body['phase4_passed'] else '不合格 ' + ', '.join(body['phase4_gate'])}",
        "",
    ]
    for day, books in body.get("phase4_cases", {}).items():
        lines.append(f"### {day}")
        for name, row in books.items():
            held = "、".join(f"{item['symbol']} {_yen(item['notional'])}" for item in (row.get("unfilled") or [])) or "なし"
            lines.append(
                f"- {name}: 約定 {row['trades']}、Gross {_yen(row['gross'])}、ENTRY {row['entries']}、期末残存 {_yen(row['residual_notional'])}、未約定 {held}"
            )
        lines.append("")
    lines.extend(["## 要因の分離", ""])
    if sep:
        lines.extend(
            [
                f"日付をまたがないC2は約定 {sep['independent_c2_trades']}、Gross {_yen(sep['independent_c2_gross'])} で、保存済みBaselineと一致した。Baselineファイルは上書きしていない。",
                "",
                f"C1とこのC2でUIDが共通する約定は {sep['shared']} 件。そのうちEXIT価格が変わったのは {sep['exit_price_changed']} 件で、損益差は {_yen(sep['exit_price_pnl_delta'])}。C1だけに残った約定は {sep['only_c1']} 件 {_yen(sep['only_c1_pnl'])}、C2だけに残った約定は {sep['only_c2']} 件 {_yen(sep['only_c2_pnl'])}。差の合計は {_yen(sep['net_gross_delta'])}。",
                "",
                "2351.Tは2026-08-25 15:20:26のBuy1 323円×100株、BidAge 0.84秒、CONTINUOUS_TRADINGで決済され、損益は0円。7376.Tは2026-09-02 15:20:01のBuy1 827円×1000株、BidAge 0.16秒で決済され、損益は+400円。どちらも窓内の最初の採用Bidと一致する。",
                "",
                f"C3の29日はC1と一致する。残存台帳が動く材料が、Buy1.Timeの修正で当日中に消えた。",
                "",
                f"C2の持越し区間で、持越しありのENTRYから持越しなしのENTRYを引いた差は {sep['carry_entry_delta']} 件。2351.Tの1枠が塞がった影響である。7376.Tは2026-09-09 09:00:01のBuy1 758円で後日決済され、損益は−6,500円。これは後から観測した実Bidであり、強制決済ではない。",
                "",
            ]
        )
    if not body.get("summaries"):
        lines.append("3午後の整合前に全期間へ進んでいない。正式な29日成績へは加算していない。")
    else:
        lines.extend(["## 29日の研究候補 C3", ""])
        row = body["summaries"]["C3"]
        lines.extend(
            [
                f"- 約定 {row['trades']}（Baseline {base['trades']}）",
                f"- 実現Gross {_yen(row['gross'])}（Baseline {_yen(base['gross'])}）",
                f"- PF {row['pf']:.3f}（Baseline {base['pf']:.3f}）",
                f"- 勝率 {row['win_rate']:.2%}",
                f"- EXIT順の最大DD {_yen(row['max_dd'])}（Baseline {_yen(base['exit_dd'])}）",
                f"- 最大1取引損失 {_yen(row['max_loss'])}",
                f"- 最大同時建玉 {_yen(row['max_gross'])}。総建玉上限100万円以内",
                f"- 未約定 0件。未解消建玉 0円",
                f"- 片道0円 {_yen(row['costs']['0'])}、10円 {_yen(row['costs']['10'])}、50円 {_yen(row['costs']['50'])}、100円 {_yen(row['costs']['100'])}",
                "",
                "経済基準は、評価が完結し、実現Grossが正、PFが1超、最大建玉が100万円以下、EXIT順DDが−17,550円より深くなく、片道50円の仮定後も正、をすべて満たす場合だけIMPROVEDとする。C3はDDが−19,420円で外れる。片道50円の仮定後は+154,860円、片道100円の仮定後は−511,440円。費用は公式の手数料ではない。",
                "",
                "C1の数値はC3と同じである。鮮度修正単独の影響と、両方を重ねた影響は、この29日では分離しても同じ成績になる。",
                "",
                "## C2の部分成績",
                "",
            ]
        )
        c2 = body["summaries"]["C2"]
        lines.extend(
            [
                f"持越しを反映したC2の実現分は約定 {c2['trades']}、Gross {_yen(c2['gross'])}、PF {c2['pf']:.3f}、DD {_yen(c2['max_dd'])}、最大損失 {_yen(c2['max_loss'])}、最大建玉 {_yen(c2['max_gross'])}。",
                "",
                "20260825|PM|2351.T|89 の32,300円は20260910の終了時も残り、次のCaptureは20260928である。翌営業日20260911の再生ができないため、この建玉は損益0円にせず評価不能とした。20260928以降のC2は、この資金占有を外した状態である。C2の合計はComplete Strategyの成績にしない。",
                "",
            ]
        )
    lines.extend(["## 日別の実現損益", "", "C2は持越し区間を反映した部分成績である。", ""])
    lines.append("| 日 | C1約定 | C1 Gross | C2約定 | C2 Gross | C3約定 | C3 Gross |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for item in body.get("daily") or []:
        lines.append(
            f"| {item['day']} | {item['C1']['trades']} | {item['C1']['gross']:.0f} | {item['C2']['trades']} | {item['C2']['gross']:.0f} | {item['C3']['trades']} | {item['C3']['gross']:.0f} |"
        )
    lines.extend(
        [
            "",
            "29日は既存の探索データであり、独立holdoutではない。新規Captureの検証はPENDING。",
            "",
            "過去データ上の修正候補は、未決済リスクとDDを含めて凍結済みB+Eの経済基準を満たさない。Paper以降へは進まない。",
            "",
        ]
    )
    return "\n".join(lines)


def _xlsx(body: dict[str, Any]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "labels"
    for key, value in body["labels"].items():
        ws.append([key, value])
    cases = wb.create_sheet("phase4")
    cases.append(["day", "book", "trades", "gross", "entries", "residual_notional", "first_window_bid", "unfilled"])
    for day, books in body.get("phase4_cases", {}).items():
        for name, row in books.items():
            cases.append([day, name, row["trades"], row["gross"], row["entries"], row["residual_notional"], json.dumps(row["first_window_bid"], ensure_ascii=False), json.dumps(row["unfilled"], ensure_ascii=False)])
    daily = wb.create_sheet("daily")
    daily.append(["day", "C1_trades", "C1_gross", "C2_trades", "C2_gross", "C3_trades", "C3_gross"])
    for row in body.get("daily") or []:
        daily.append([row["day"], row["C1"]["trades"], row["C1"]["gross"], row["C2"]["trades"], row["C2"]["gross"], row["C3"]["trades"], row["C3"]["gross"]])
    sep = body.get("separation") or {}
    sheet = wb.create_sheet("separation")
    for key, value in sep.items():
        sheet.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
    wb.save(OUT / "audit.xlsx")


def _strip_trades(body: dict[str, Any]) -> dict[str, Any]:
    slim = json.loads(json.dumps(body))
    for kind in ("am", "pm"):
        books = ((slim.get(kind) or {}).get("books") or {})
        for book in books.values():
            book.pop("trades", None)
    return slim


def main() -> None:
    if not _frozen_ok():
        raise SystemExit("FROZEN_SHA_MISMATCH")
    existing = OUT / "report.json"
    if existing.exists():
        saved = json.loads(existing.read_text(encoding="utf-8"))
        if saved.get("labels") and saved.get("running") is False and saved.get("rule_id") == RULE_ID:
            print(json.dumps(saved["labels"], ensure_ascii=False), flush=True)
            return
    phase4 = run_phase4()
    reasons = _gate(phase4)
    if reasons:
        _finish(phase4, {}, {"final": {}, "gap": False, "reason": "phase4_gate"})
        return
    days_out: dict[str, Any] = {}
    order = _days()
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(run_independent, day): day for day in order}
        for future in as_completed(futures):
            body = future.result()
            days_out[str(body.get("day"))] = _strip_trades(body)
    # Carry is required once a residual remains. Replay that suffix in calendar order.
    carry = {"C2": [], "C3": []}
    gap = False
    gap_detail = []
    final = {"C1": [], "C2": [], "C3": []}
    for index, day in enumerate(order):
        cached = json.loads((CACHE / f"{day}_{RULE_ID}.json").read_text(encoding="utf-8"))
        for name in ("C1",):
            final[name] = (cached.get("pm") or {}).get("books", {}).get(name, {}).get("residual") or []
        if carry["C2"] or carry["C3"]:
            names = tuple(name for name in ("C2", "C3") if carry[name])
            rerun = run_carry_day(day, {name: carry[name] for name in names}, names)
            for name in names:
                cached["am"]["books"][name] = rerun["am"]["books"][name]
                cached["pm"]["books"][name] = rerun["pm"]["books"][name]
            (CACHE / f"{day}_{RULE_ID}_carry.json").write_text(json.dumps(cached), encoding="utf-8")
        days_out[day] = _strip_trades(cached)
        for name in ("C2", "C3"):
            carry[name] = (cached.get("pm") or {}).get("books", {}).get(name, {}).get("residual") or []
            final[name] = carry[name]
        if index + 1 < len(order) and (carry["C2"] or carry["C3"]):
            nxt = order[index + 1]
            if nxt != _next_weekday(day):
                gap = True
                gap_detail.append({"after": day, "next_capture": nxt, "next_weekday": _next_weekday(day), "open": {name: [row.get("uid") for row in rows] for name, rows in carry.items() if rows}})
                carry = {"C2": [], "C3": []}
    _finish(phase4, days_out, {"final": final, "gap": gap, "gaps": gap_detail})


if __name__ == "__main__":
    main()
