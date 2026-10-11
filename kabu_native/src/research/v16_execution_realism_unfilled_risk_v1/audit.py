"""Displayed 100-share feasibility and unfilled-exit risk. Does not rerun the 8-capital study."""
from __future__ import annotations

import hashlib
import json
import statistics
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil
from openpyxl import Workbook

from research.current50_v16_capital_risk_policy_comparison_v1.compare import ResearchGrossCapExecutor
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _paper_sessions, _symbol
from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY
from research.v16_one_tick_exit_complete_strategy_v1.evaluate import OneTickExit
from research.v16_one_tick_exit_capital_scaling_v2 import run as scaling
from small_paper.fixed_support_x1_session import _EPS, _Sym, _bid_ok
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_execution_realism_unfilled_risk_v1"
SCALING = NATIVE / "results" / "research" / "v16_one_tick_exit_capital_scaling_v2" / "report.json"
FROZEN_SHA = "e87713f714541f7a7fe2d98b1d1bb6aadb1b082d4e76b4211832dd9fdf8c4c91"
G = 1_000_000.0
WORKERS = 2
_ORIG_APPEND = _Sym.append_raw
_PATCHED = False


def _frozen_ok() -> bool:
    raw = (NATIVE / "src" / "research" / "v16_one_tick_exit_complete_strategy_v1" / "evaluate.py").read_bytes()
    text = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return hashlib.sha256(text).hexdigest() == FROZEN_SHA


def _install_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    def _append(self: _Sym, ev: dict[str, Any]) -> int:
        index = _ORIG_APPEND(self, ev)
        ask = ev.get("ask_qty")
        bid = ev.get("bid_qty")
        fresh = ev.get("fresh_sec")
        self.audit_ask.append(None if ask is None else float(ask))
        self.audit_bid.append(None if bid is None else float(bid))
        self.audit_fresh.append(None if fresh is None else float(fresh))
        self.audit_special.append(bool(ev.get("special")))
        self.audit_state.append(str(ev.get("state") or ""))
        self.audit_ask_cover.append(ev.get("ask_cover"))
        self.audit_bid_cover.append(ev.get("bid_cover"))
        self.audit_sell1.append(bool(ev.get("sell1")))
        self.audit_buy1.append(bool(ev.get("buy1")))
        return index

    def _init(self: _Sym) -> None:
        _Sym._audit_init(self)
        self.audit_ask: list[Optional[float]] = []
        self.audit_bid: list[Optional[float]] = []
        self.audit_fresh: list[Optional[float]] = []
        self.audit_special: list[bool] = []
        self.audit_state: list[str] = []
        self.audit_ask_cover: list[Any] = []
        self.audit_bid_cover: list[Any] = []
        self.audit_sell1: list[bool] = []
        self.audit_buy1: list[bool] = []

    _Sym._audit_init = _Sym.__init__
    _Sym.__init__ = _init
    _Sym.append_raw = _append
    _PATCHED = True


def _cover(payload: dict[str, Any], side: str) -> Optional[dict[str, Any]]:
    levels = []
    for index in range(1, 11):
        level = payload.get(f"{side}{index}")
        if not isinstance(level, dict):
            continue
        price = level.get("Price")
        qty = level.get("Qty")
        try:
            price_f = float(price)
            qty_f = float(qty)
        except (TypeError, ValueError):
            continue
        if price_f == price_f and qty_f == qty_f and qty_f > 0:
            levels.append((price_f, qty_f))
    if not levels:
        return None
    shown = 0.0
    worst = None
    used = 0
    for price, qty in levels:
        shown += qty
        worst = price
        used += 1
        if shown + 1e-9 >= MIN_QTY:
            return {"covered": True, "shown": shown, "worst_px": worst, "levels": used}
    return {"covered": False, "shown": shown, "worst_px": worst, "levels": used}


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


class _QtyMixin:
    def __init__(self) -> None:
        super().__init__()
        self.bid_log: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.reject_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.reject_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.last_quote: dict[str, dict[str, Any]] = {}
        self.window_events = 0
        self.window_by_symbol: Counter[str] = Counter()

    def _normalize(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        ev = super()._normalize(event)
        if not ev:
            return ev
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        ask = _num(ev.get("ask_qty"))
        bid = _num(ev.get("bid_qty"))
        sell = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else None
        buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else None
        ev["sell1"] = sell is not None and _num(sell.get("Qty")) is not None
        ev["buy1"] = buy is not None and _num(buy.get("Qty")) is not None
        if not ev["sell1"] or ask is None or ask < MIN_QTY:
            ev["ask_cover"] = _cover(payload, "Sell")
        if not ev["buy1"] or bid is None or bid < MIN_QTY:
            ev["bid_cover"] = _cover(payload, "Buy")
        return ev

    def _flush_bucket(self) -> None:
        events = list(self._bucket)
        for ev in events:
            symbol = str(ev.get("symbol") or "")
            stamp = float(ev["t"])
            self.last_quote[symbol] = {
                "t": stamp,
                "bid": _num(ev.get("bid")),
                "ask": _num(ev.get("ask")),
                "bid_qty": _num(ev.get("bid_qty")),
                "ask_qty": _num(ev.get("ask_qty")),
                "fresh": _num(ev.get("fresh_sec")),
                "special": bool(ev.get("special")),
                "state": str(ev.get("state") or ""),
                "executable": bool(ev.get("executable")),
            }
            allowed = stamp < float(self.flatten_deadline) - _EPS if self.flatten_deadline is not None else stamp <= self.sess_end + _EPS
            if _bid_ok(ev) and allowed:
                self.bid_log[symbol].append(
                    {
                        "t": stamp,
                        "px": float(ev["bid"]),
                        "qty": float(ev["bid_qty"]),
                        "fresh": float(ev["fresh_sec"]),
                        "special": bool(ev.get("special")),
                        "buy1": bool(ev.get("buy1")),
                        "cover": ev.get("bid_cover"),
                    }
                )
            if self.flatten_start is not None and stamp + _EPS >= float(self.flatten_start) and stamp < float(self.flatten_deadline) - _EPS:
                self.window_events += 1
                self.window_by_symbol[symbol] += 1
                if symbol in self.open_pos and not (_bid_ok(ev) and allowed):
                    reason = _reject_reason(ev, allowed)
                    self.reject_counts[symbol][reason] += 1
                    if len(self.reject_samples[symbol]) < 6:
                        self.reject_samples[symbol].append(
                            {
                                "t": stamp,
                                "reason": reason,
                                "bid": _num(ev.get("bid")),
                                "bid_qty": _num(ev.get("bid_qty")),
                                "fresh": _num(ev.get("fresh_sec")),
                                "state": str(ev.get("state") or ""),
                                "special": bool(ev.get("special")),
                            }
                        )
        super()._flush_bucket()


def _reject_reason(ev: dict[str, Any], allowed: bool) -> str:
    if not allowed:
        return "AFTER_DEADLINE"
    if not ev.get("executable"):
        return "NOT_EXECUTABLE"
    if ev.get("special"):
        return "SPECIAL"
    fresh = _num(ev.get("fresh_sec"))
    if fresh is None or fresh > FRESH_SEC + _EPS:
        return "STALE_OR_UNKNOWN_FRESH"
    bid = _num(ev.get("bid"))
    if bid is None or bid <= 0:
        return "NO_BID_PRICE"
    qty = _num(ev.get("bid_qty"))
    if qty is None:
        return "BID_QTY_UNKNOWN"
    if qty < MIN_QTY - _EPS:
        return "BID_QTY_BELOW_100"
    return "OTHER"


class QtyB(_QtyMixin, ResearchGrossCapExecutor):
    pass


class QtyE(_QtyMixin, OneTickExit):
    pass


def _blank() -> dict[str, Any]:
    return {
        "n": 0,
        "ge": 0,
        "lt": 0,
        "unknown": 0,
        "fresh_gt5": 0,
        "special": 0,
        "qty_min": None,
        "qty_max": None,
        "fresh_max": None,
        "lag_max": None,
        "values": [],
        "fresh_values": [],
        "lags": [],
        "examples": [],
        "states": {},
    }


def _push(bucket: dict[str, Any], qty: Optional[float], fresh: Optional[float], special: bool, lag: Optional[float], state: str, example: Optional[dict[str, Any]]) -> None:
    bucket["n"] += 1
    bucket["states"][state] = int(bucket["states"].get(state) or 0) + 1
    if qty is None:
        bucket["unknown"] += 1
    elif qty + 1e-9 >= MIN_QTY:
        bucket["ge"] += 1
    else:
        bucket["lt"] += 1
        if len(bucket["examples"]) < 12:
            bucket["examples"].append(example)
    if qty is not None:
        bucket["values"].append(qty)
        bucket["qty_min"] = qty if bucket["qty_min"] is None else min(bucket["qty_min"], qty)
        bucket["qty_max"] = qty if bucket["qty_max"] is None else max(bucket["qty_max"], qty)
    if fresh is not None:
        bucket["fresh_values"].append(fresh)
        bucket["fresh_max"] = fresh if bucket["fresh_max"] is None else max(bucket["fresh_max"], fresh)
        if fresh > FRESH_SEC + 1e-6:
            bucket["fresh_gt5"] += 1
    if special:
        bucket["special"] += 1
    if lag is not None:
        bucket["lags"].append(lag)
        bucket["lag_max"] = lag if bucket["lag_max"] is None else max(bucket["lag_max"], lag)


def _entry_audit(book: Any) -> dict[str, Any]:
    bucket = _blank()
    for trade in book.trades:
        symbol = str(trade["symbol"])
        index = int(trade["signal_index"])
        st = book.syms[symbol]
        ask = st.audit_ask[index] if index < len(st.audit_ask) else None
        if index >= len(st.audit_sell1) or not st.audit_sell1[index]:
            ask = None
        fresh = st.audit_fresh[index] if index < len(st.audit_fresh) else None
        special = bool(st.audit_special[index]) if index < len(st.audit_special) else False
        state = st.audit_state[index] if index < len(st.audit_state) else ""
        cover = st.audit_ask_cover[index] if index < len(st.audit_ask_cover) else None
        if ask is not None and ask != ask:
            ask = None
        if fresh is not None and fresh != fresh:
            fresh = None
        _push(
            bucket,
            ask,
            fresh,
            special,
            None,
            state,
            {"uid": trade["signal_uid"], "ask_qty": ask, "fresh": fresh, "state": state, "cover": cover},
        )
    return bucket


def _exit_audit(book: Any) -> dict[str, Any]:
    bucket = _blank()
    for trade in book.trades:
        symbol = str(trade["symbol"])
        exit_t = float(trade["exit_t"])
        exit_px = float(trade["exit_px"])
        trigger = float(trade.get("exit_trigger_t") or exit_t)
        found = None
        for row in book.bid_log.get(symbol, []):
            if abs(float(row["t"]) - exit_t) <= 1e-6 and abs(float(row["px"]) - exit_px) <= 1e-6:
                found = row
                break
        qty = None if found is None or not found.get("buy1") else found.get("qty")
        fresh = None if found is None else found.get("fresh")
        special = bool(found.get("special")) if found else False
        lag = exit_t - trigger
        _push(
            bucket,
            qty,
            fresh,
            special,
            lag,
            "",
            {
                "uid": trade["signal_uid"],
                "bid_qty": qty,
                "fresh": fresh,
                "lag": lag,
                "reason": trade["reason"],
                "cover": None if found is None else found.get("cover"),
            },
        )
    return bucket


def _unfilled_detail(book: Any, day: str, kind: str, policy: str) -> list[dict[str, Any]]:
    rows = []
    for row in book.unfilled:
        symbol = str(row.get("symbol") or "")
        entry_t = float(row["entry_t"])
        later = [item for item in book.bid_log.get(symbol, []) if float(item["t"]) + 1e-9 >= entry_t]
        before = [item for item in later if book.flatten_start is None or float(item["t"]) < float(book.flatten_start)]
        inside = [
            item
            for item in later
            if book.flatten_start is not None and float(book.flatten_start) - 1e-9 <= float(item["t"]) < float(book.flatten_deadline) - 1e-9
        ]
        last = before[-1] if before else None
        rows.append(
            {
                "policy": policy,
                "day": day,
                "kind": kind,
                "uid": row.get("signal_uid"),
                "symbol": symbol,
                "entry_t": entry_t,
                "entry_px": row.get("entry_px"),
                "notional": None if row.get("entry_px") is None else float(row["entry_px"]) * 100.0,
                "pnl_yen": row.get("pnl_yen"),
                "exit_px": row.get("exit_px"),
                "reason": row.get("reason"),
                "slot_release": True,
                "open_after": len(book.open_pos),
                "window_events_all": int(book.window_events),
                "window_events_symbol": int(book.window_by_symbol.get(symbol) or 0),
                "executable_bids_in_window": len(inside),
                "last_executable_bid_before_flat": last,
                "last_quote": book.last_quote.get(symbol),
                "reject_counts": dict(book.reject_counts.get(symbol) or {}),
                "reject_samples": list(book.reject_samples.get(symbol) or []),
                "capture_has_symbol_events_in_window": int(book.window_by_symbol.get(symbol) or 0) > 0,
                "capture_has_any_events_in_window": int(book.window_events) > 0,
            }
        )
    return rows


def _prepare(day: str, kind: str, cls: type) -> Any:
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


def _session(day: str, kind: str, seq_after: int, attach: str) -> dict[str, Any]:
    _install_patch()
    capture = _capture_dir(day)
    if capture is None:
        return {"error": "NO_CAPTURE"}
    left = _prepare(day, kind, QtyB)
    right = _prepare(day, kind, QtyE)
    if len(left.admission_membership) != 50 or len(right.admission_membership) != 50:
        return {"error": "ADMISSION"}
    fed = 0
    started = time.perf_counter()
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
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload:
                    continue
                payload = dict(payload)
                payload.setdefault("received_at", received)
                payload.setdefault("recorded_at", received)
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                for book in (left, right):
                    if not book._closed:
                        book.on_market_event({"symbol": symbol, "payload": dict(payload)})
                fed += 1
                if left._closed and right._closed:
                    break
        if left._closed and right._closed:
            break
    for book in (left, right):
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
    out = {
        "fed": fed,
        "elapsed_sec": time.perf_counter() - started,
        "books": {},
    }
    for policy, book in (("B", left), ("E", right)):
        gross = round(sum(float(trade["pnl_yen"]) for trade in book.trades), 6)
        out["books"][policy] = {
            "trades": len(book.trades),
            "gross": gross,
            "final_ok": bool(book.x1_performance()["final_ok"]),
            "open_n": len(book.open_pos),
            "pending_n": int(book.x1_performance()["exit_pending_n"]),
            "max_gross": scaling._exposure(book.ledger, G)[0],
            "breaches": scaling._exposure(book.ledger, G)[1],
            "entry": _entry_audit(book),
            "exit": _exit_audit(book),
            "unfilled": _unfilled_detail(book, day, kind, policy),
        }
    print(
        f"AUDIT {day} {kind} B {out['books']['B']['trades']} {out['books']['B']['gross']} E {out['books']['E']['trades']} {out['books']['E']['gross']}",
        flush=True,
    )
    return out


def run_day(day: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        am = _session(day, "am", 0, _attach_text(day, "am"))
        pm = _session(day, "pm", 0, _attach_text(day, "pm"))
        if am.get("error") or pm.get("error"):
            return {"day": day, "error": am.get("error") or pm.get("error")}
        return {"day": day, "elapsed_sec": time.perf_counter() - started, "am": am, "pm": pm}
    except Exception:
        return {"day": day, "error": traceback.format_exc()}


def run_known() -> dict[str, Any]:
    _install_patch()
    day = "20261009"
    sessions = [row for row in _paper_sessions(day) if row["kind"] == "pm" and "V16" in str(row["activation_id"])]
    if not sessions:
        return {"error": "NO_V16_SESSION"}
    try:
        pm = _session(day, "pm", int(sessions[0]["watermark_seq"]), "")
    except Exception:
        return {"error": traceback.format_exc()}
    pm["watermark"] = int(sessions[0]["watermark_seq"])
    return pm


def _expected() -> dict[str, dict[str, tuple[int, float]]]:
    body = json.loads(SCALING.read_text(encoding="utf-8"))
    out: dict[str, dict[str, tuple[int, float]]] = {}
    for row in body.get("daily") or []:
        if float(row["capital"]) != 500_000.0:
            continue
        out[row["day"]] = {
            "B": (int(row["b_trades"]), float(row["b_gross"])),
            "E": (int(row["e_trades"]), float(row["e_gross"])),
        }
    return out


def _matches(result: dict[str, Any], expected: dict[str, tuple[int, float]]) -> bool:
    for policy in ("B", "E"):
        trades = int(result["am"]["books"][policy]["trades"]) + int(result["pm"]["books"][policy]["trades"])
        gross = float(result["am"]["books"][policy]["gross"]) + float(result["pm"]["books"][policy]["gross"])
        old = expected.get(policy, (0, 0.0))
        if trades != old[0] or abs(gross - old[1]) >= 0.05:
            return False
    return True


def _merge(parts: list[dict[str, Any]]) -> dict[str, Any]:
    base = _blank()
    for part in parts:
        base["n"] += int(part["n"])
        base["ge"] += int(part["ge"])
        base["lt"] += int(part["lt"])
        base["unknown"] += int(part["unknown"])
        base["fresh_gt5"] += int(part["fresh_gt5"])
        base["special"] += int(part["special"])
        base["values"].extend(part.get("values") or [])
        base["fresh_values"].extend(part.get("fresh_values") or [])
        base["lags"].extend(part.get("lags") or [])
        base["examples"].extend(part.get("examples") or [])
        for key, value in (part.get("states") or {}).items():
            base["states"][key] = int(base["states"].get(key) or 0) + int(value)
        if part.get("qty_min") is not None:
            base["qty_min"] = part["qty_min"] if base["qty_min"] is None else min(base["qty_min"], part["qty_min"])
        if part.get("qty_max") is not None:
            base["qty_max"] = part["qty_max"] if base["qty_max"] is None else max(base["qty_max"], part["qty_max"])
        if part.get("fresh_max") is not None:
            base["fresh_max"] = part["fresh_max"] if base["fresh_max"] is None else max(base["fresh_max"], part["fresh_max"])
        if part.get("lag_max") is not None:
            base["lag_max"] = part["lag_max"] if base["lag_max"] is None else max(base["lag_max"], part["lag_max"])
    base["examples"] = base["examples"][:12]
    return base


def _summarize(bucket: dict[str, Any]) -> dict[str, Any]:
    values = [float(item) for item in bucket.get("values") or []]
    fresh = [float(item) for item in bucket.get("fresh_values") or []]
    lags = [float(item) for item in bucket.get("lags") or []]
    return {
        "n": bucket["n"],
        "displayed_ge_100": bucket["ge"],
        "displayed_lt_100": bucket["lt"],
        "unknown": bucket["unknown"],
        "rate_ge_100": None if not bucket["n"] else bucket["ge"] / bucket["n"],
        "fresh_gt_5s": bucket["fresh_gt5"],
        "special": bucket["special"],
        "qty_min": bucket["qty_min"],
        "qty_median": None if not values else float(statistics.median(values)),
        "fresh_max": bucket["fresh_max"],
        "fresh_median": None if not fresh else float(statistics.median(fresh)),
        "lag_max": bucket["lag_max"],
        "lag_median": None if not lags else float(statistics.median(lags)),
        "states": bucket["states"],
        "examples": bucket["examples"][:12],
    }


def _label_book(summary: dict[str, Any]) -> str:
    if summary["n"] == 0:
        return "INSUFFICIENT_DATA"
    if summary["unknown"]:
        return "INSUFFICIENT_DATA"
    if summary["displayed_lt_100"] or summary["fresh_gt_5s"] or summary["special"]:
        return "NOT_SUPPORTED"
    if summary["displayed_ge_100"] == summary["n"]:
        return "SUPPORTED"
    return "INSUFFICIENT_DATA"


def _save(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = OUT / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(OUT / "report.json")


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _book(report: dict[str, Any]) -> None:
    book = Workbook()
    names = ("Summary", "Entry", "Exit", "Unfilled", "Cost", "Timing")
    first = True
    for name in names:
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        sheet.append(["item", "value"])
    for key, value in (report.get("labels") or {}).items():
        book["Summary"].append([key, str(value)])
    for row in report.get("entry_rows") or []:
        book["Entry"].append([f"{row['policy']} {row['field']}", _cell(row["value"])])
    for row in report.get("exit_rows") or []:
        book["Exit"].append([f"{row['policy']} {row['field']}", _cell(row["value"])])
    for row in report.get("unfilled") or []:
        book["Unfilled"].append([row.get("uid"), f"{row.get('notional')} {row.get('reject_counts')}"])
    for row in report.get("costs") or []:
        book["Cost"].append([row["item"], row["value"]])
    for row in report.get("timing") or []:
        book["Timing"].append([row["item"], row["value"]])
    book.save(OUT / "audit.xlsx")


def _finish(report: dict[str, Any]) -> None:
    days = list((report.get("days") or {}).values())
    entry = {}
    exit_ = {}
    unfilled = []
    for policy in ("B", "E"):
        entry[policy] = _summarize(_merge([day["am"]["books"][policy]["entry"] for day in days] + [day["pm"]["books"][policy]["entry"] for day in days]))
        exit_[policy] = _summarize(_merge([day["am"]["books"][policy]["exit"] for day in days] + [day["pm"]["books"][policy]["exit"] for day in days]))
        for day in days:
            for kind in ("am", "pm"):
                unfilled.extend(day[kind]["books"][policy]["unfilled"])
    known = report.get("known_20261009_pm") or {}
    known_unfilled = []
    if known.get("books"):
        known_unfilled = list(known["books"]["E"]["unfilled"]) + list(known["books"]["B"]["unfilled"])
    gross_e = 837770.0
    cost50_e = 172370.0
    max_gross = max((float(day[kind]["books"]["E"]["max_gross"]) for day in days for kind in ("am", "pm")), default=None)
    breaches = sum(int(day[kind]["books"]["E"]["breaches"]) for day in days for kind in ("am", "pm"))
    entry_label = _label_book(entry["E"]) if len(days) == 29 and report.get("parity_ok") else "INSUFFICIENT_DATA"
    exit_label = _label_book(exit_["E"]) if len(days) == 29 and report.get("parity_ok") else "INSUFFICIENT_DATA"
    labels = {
        "ENTRY_EXECUTION_FEASIBILITY": entry_label,
        "EXIT_EXECUTION_FEASIBILITY": exit_label,
        "UNFILLED_RISK": "UNRESOLVED",
        "COST_ROBUSTNESS": "INSUFFICIENT_DATA",
        "CAPITAL_FEASIBILITY": "INSUFFICIENT_DATA",
        "INDEPENDENT_VALIDATION": report.get("independent_validation") or "PENDING",
    }
    body = "\n".join(
        [
            "これは保存済み板に対するモデル上の表示数量の確認です。実注文の約定保証ではありません。",
            "既存の29日×8条件は再生していません。50万円のBとB+Eだけを、凍結規則のまま数量監査用に再実行し、保存済みの日別件数とGrossと照合しました。",
            f"B+EのENTRY表示数量は {entry.get('E')} です。",
            f"B+EのEXIT表示数量は {exit_.get('E')} です。",
            f"BのENTRYは {entry.get('B')}、BのEXITは {exit_.get('B')} です。",
            "先頭数量はBuy1.Qtyを買気配、Sell1.Qtyを売気配として読みました。Buy1またはSell1が無い行はUNKNOWNです。Kabu名称のBidQtyとAskQtyは反転し得るため、PASSには使いません。先頭が100株未満のときだけ、10本までの合算で100株に届く価格を見ました。",
            "100株以上の表示は、その価格で100株が実際に約定した証明ではありません。注文順と優先順位は保存データから証明できません。",
            f"未約定は29日のB+Eで {[ (row['uid'], row['notional'], row['reject_counts'], row['capture_has_symbol_events_in_window']) for row in unfilled if row['policy']=='E' ]} です。",
            f"別枠20261009 PMは {[(row['uid'], row['notional'], row['reject_counts']) for row in known_unfilled]} です。29日の率には入れていません。",
            "未約定のpnlは空のままです。Slot解放とfinal_okは決済成立にしていません。後続気配があっても、その価格で売れたことにはしていません。",
            f"既存のB+E Gross +{gross_e:.0f}円と片道50円後 +{cost50_e:.0f}円は研究基準として維持しています。数量不足の取引を消して利益は増やしていません。",
            "証券会社手数料、信用適格、必要保証金、日中余力の契約条件は成果物の中に見つかりません。UNKNOWNです。モデル内の追加手数料は0円です。片道10円、50円、100円は従来どおり仮定ストレスです。",
            f"モデル上のB+E最大同時建玉は {max_gross} 円、G超過は {breaches} 件です。これは総約定建玉100万円のモデル制約であり、実口座の余力証明ではありません。",
            f"独立した新しい通常Captureは {report.get('new_capture_days')} です。",
            f"処理は2並列です。前回の8条件計測で4並列は2並列とほぼ同じ速度でした。今回の壁時間は {report.get('wall_sec')} 秒です。",
        ]
    )
    report.update(
        {
            "labels": labels,
            "entry": entry,
            "exit": exit_,
            "unfilled": unfilled + known_unfilled,
            "body": body,
            "entry_rows": [{"policy": policy, "field": key, "value": value} for policy, summary in entry.items() for key, value in summary.items() if key != "examples"],
            "exit_rows": [{"policy": policy, "field": key, "value": value} for policy, summary in exit_.items() for key, value in summary.items() if key != "examples"],
            "costs": [
                {"item": "sealed_extra_fee", "value": "0 yen inside the model; broker schedule UNKNOWN"},
                {"item": "spread", "value": "already inside Ask and Bid; not added again"},
                {"item": "stress_per_side_yen", "value": "0, 10, 50, 100 assumed; 50 yen B+E +172370 research reference"},
                {"item": "depth_slippage", "value": "extra level only when displayed top qty is below 100"},
            ],
            "timing": [
                {"item": "workers", "value": WORKERS},
                {"item": "wall_sec", "value": report.get("wall_sec")},
                {"item": "prior_4_vs_2_sec", "value": "3605 vs 3651 on the previous 3-day 8-book measurement"},
            ],
            "running": False,
            "policy_adopted": False,
            "paper_runtime_unchanged": True,
            "submit_cancel_live": "0/0/0",
        }
    )
    lines = [f"{key}: {value}" for key, value in labels.items()]
    text = "\n".join(lines + ["", body, "", "Paper V16、Runtime、ENTRY、EXITは変更していません。", "submit/cancel/live = 0/0/0。", "Policy BとB+EはPaperへ採用していません。"]) + "\n"
    report["markdown"] = text
    (OUT / "report.md").write_text(text, encoding="utf-8")
    slim_days = {}
    for day, value in report["days"].items():
        copied = {"day": day, "elapsed_sec": value.get("elapsed_sec"), "parity_ok": value.get("parity_ok")}
        for kind in ("am", "pm"):
            copied[kind] = {
                policy: {
                    "trades": value[kind]["books"][policy]["trades"],
                    "gross": value[kind]["books"][policy]["gross"],
                    "entry_n": value[kind]["books"][policy]["entry"]["n"],
                    "entry_lt": value[kind]["books"][policy]["entry"]["lt"],
                    "exit_unknown": value[kind]["books"][policy]["exit"]["unknown"],
                    "unfilled": value[kind]["books"][policy]["unfilled"],
                }
                for policy in ("B", "E")
            }
        slim_days[day] = copied
    # Finish uses the full buckets already summarized. Keep full days out of the saved file.
    saved = dict(report)
    saved["days"] = slim_days
    saved.pop("entry", None)
    saved["entry_summary"] = {policy: {key: value for key, value in summary.items() if key != "examples"} for policy, summary in entry.items()}
    saved["exit_summary"] = {policy: {key: value for key, value in summary.items() if key != "examples"} for policy, summary in exit_.items()}
    _save(saved)
    _book(saved)
    print(labels, flush=True)


def _new_captures() -> list[str]:
    root = NATIVE / "data" / "market_capture"
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.iterdir()):
        if path.is_dir() and path.name > "20261009":
            found.append(path.name)
    return found


def main() -> None:
    if not _frozen_ok():
        print("FROZEN_SHA_MISMATCH", flush=True)
        return
    if scaling._busy() or scaling._window(datetime.now().astimezone()):
        print("DEFERRED", flush=True)
        return
    report: dict[str, Any] = {"running": True, "days": {}, "parity_ok": True}
    path = OUT / "report.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("labels") and existing.get("running") is False:
            print(existing["labels"], flush=True)
            return
    runner = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if path.is_file():
        prior = json.loads(path.read_text(encoding="utf-8"))
        if prior.get("running") and prior.get("runner_sha") == runner and isinstance(prior.get("days"), dict):
            report = prior
    report["runner_sha"] = runner
    report["frozen_sha"] = FROZEN_SHA
    expected = _expected()
    remaining = [day for day in scaling._historical_days() if day not in report["days"]]
    started = time.perf_counter()
    if remaining:
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(run_day, day): day for day in remaining}
            pending = set(futures)
            while pending:
                if scaling._busy() or scaling._window(datetime.now().astimezone()):
                    pool.shutdown(wait=False, cancel_futures=True)
                    report["running"] = True
                    _save(report)
                    print("DEFERRED", flush=True)
                    return
                done, pending = wait(pending, timeout=15)
                for future in done:
                    result = future.result()
                    if result.get("error") or not _matches(result, expected.get(result["day"], {})):
                        report["parity_ok"] = False
                        report["errors"] = [{"day": result.get("day"), "error": result.get("error") or "PUBLISHED_MISMATCH"}]
                        report["running"] = False
                        _save(report)
                        print("PARITY_FAIL", result.get("day"), flush=True)
                        pool.shutdown(wait=False, cancel_futures=True)
                        return
                    result["parity_ok"] = True
                    report["days"][result["day"]] = result
                    _save(report)
    if "known_20261009_pm" not in report:
        report["known_20261009_pm"] = run_known()
        _save(report)
    report["wall_sec"] = float(report.get("wall_sec") or 0.0) + (time.perf_counter() - started)
    report["new_capture_days"] = _new_captures()
    report["independent_validation"] = "PENDING"
    report["parity_ok"] = True
    _finish(report)


if __name__ == "__main__":
    main()
