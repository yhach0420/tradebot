"""Causal fills for an unchanged C3 signal stream.

ENTRY and EXIT decisions stay on the inherited book. This layer only
changes when a signal becomes a position, and when that position closes.
A triggering print is not a fill. No broker round-trip was measured.
"""
from __future__ import annotations

import heapq
from typing import Any, Optional

from research.v16_bid_freshness_residual_accounting_v1.run import SHARES, _exit_decision
from small_paper.c3_paper_session import C3PaperSessionExecutor
from small_paper.fixed_support_x1_session import _EPS, _finite_pos, _yen

SHARES_F = float(SHARES)


def proven_sell(ev: dict[str, Any]) -> dict[str, Any]:
    """Same Buy1 freshness as C3. A smaller displayed size can fill partly.

    Missing size stays unproven. The price is the displayed Buy1 price.
    """
    decision = _exit_decision(ev)
    if decision["ok"]:
        return decision
    qty = decision.get("qty")
    if decision.get("reason") != "FAIL_CLOSED_BUY1_QTY" or qty is None or float(qty) <= 0.0:
        return decision
    cloned = dict(ev)
    cloned["buy1_qty"] = max(float(qty), float(SHARES))
    again = _exit_decision(cloned)
    if not again["ok"]:
        return decision
    return {"ok": True, "reason": "PARTIAL_SIZE", "age": again.get("age"), "px": again["px"], "qty": float(qty)}


class ExecutionRealismBook(C3PaperSessionExecutor):
    """Paper signals, with fills only on a later reachable quote."""

    def __init__(self, order_delay_sec: float) -> None:
        super().__init__()
        self.order_delay_sec = float(order_delay_sec)
        self.delay_class = "ASSUMED"
        self.measured_order_latency_sec = None
        self.quotes: dict[str, dict[int, dict[str, Any]]] = {}
        self._active_flush = 0.0
        self.quote_misaligned = 0
        self.intent_n = 0
        self.intent_ids: list[str] = []
        self.exit_signal_n = 0
        self.closed: list[dict[str, Any]] = []
        self.cancels: list[dict[str, Any]] = []
        self.residuals: list[dict[str, Any]] = []
        self.hero: list[dict[str, Any]] = []
        self.violations: list[str] = []
        self.completed_symbols: set[str] = set()
        self.peak_slots = 0
        self.peak_reserved_yen = 0.0
        self.buy_full_n = 0
        self.buy_partial_n = 0
        self.sell_full_n = 0
        self.sell_partial_n = 0
        self.real_reentry_n = 0

    def _flush_bucket(self) -> None:
        events = list(self._bucket)
        if events:
            self._active_flush = float(events[-1]["t"])
        staged = [ev for ev in events if self._path_event(ev)]
        before: dict[str, int] = {}
        for ev in staged:
            sym = str(ev["symbol"])
            if sym not in before:
                before[sym] = self._sym(sym).n
        cursor = dict(before)
        for ev in staged:
            sym = str(ev["symbol"])
            index = cursor[sym]
            cursor[sym] = index + 1
            self.quotes.setdefault(sym, {})[index] = _quote_snapshot(ev)
        super()._flush_bucket()
        for sym, end in cursor.items():
            if int(self._sym(sym).n) != int(end):
                self.quote_misaligned += 1

    def _path_event(self, ev: dict[str, Any]) -> bool:
        if not ev.get("continuous", True):
            return False
        return self.sess_start - _EPS <= float(ev["t"]) < self.sess_end

    def _drain_working_sells(self, flush_t: float) -> None:
        heap: list[tuple] = []
        for sym, pos in self.open_pos.items():
            if str(pos.get("exec_state")) not in ("SELL_PENDING", "SELL_PARTIAL"):
                continue
            st = self.syms[sym]
            nxt = int(pos.get("next_index") or 0)
            if nxt < st.n and float(st.t[nxt]) <= flush_t + _EPS:
                heapq.heappush(heap, (float(st.t[nxt]), 0, sym, "PATH", nxt))
        if heap:
            self._drain(heap, flush_t)

    def _arm_pending_fill(self, sym: str, flush_t: float, heap: list[tuple]) -> None:
        del sym, flush_t, heap

    def _on_fill(self, sym: str, token: Any) -> None:
        self.violations.append(f"paper_fill {sym} {token}")

    def _release(self, sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        self.violations.append(f"paper_release {sym} {reason}")

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        existed = sym in self.open_pos
        super()._on_signal(sym, index, event_t, heap, flush_t)
        pos = self.open_pos.get(sym)
        if pos is None or existed or pos.get("exec_state"):
            return
        pos["exec_state"] = "BUY_PENDING"
        pos["signal_t"] = float(pos["entry_t"])
        pos["reserve_px"] = float(pos["entry_px"])
        pos["buy_reachable_t"] = float(pos["entry_t"]) + float(self.order_delay_sec)
        pos["remaining_buy"] = SHARES_F
        pos["filled_qty"] = 0.0
        pos["sold_qty"] = 0.0
        pos["entry_cost"] = 0.0
        pos["exit_proceeds"] = 0.0
        pos["buy_prints"] = 0
        pos["sell_prints"] = 0
        pos["entry_fill_t"] = None
        pos["exit_fill_t"] = None
        pos["exit_signal_t"] = None
        self.intent_n += 1
        self.intent_ids.append(str(pos.get("signal_uid")))
        self._note_occupancy()
        self._trace(sym, pos, "ENTRY_SIGNAL", event_t)

    def _on_path(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        pos = self.open_pos.get(sym)
        if pos is None or int(pos.get("next_index", -1)) != index:
            return
        state = str(pos.get("exec_state") or "")
        if state in ("SELL_PENDING", "SELL_PARTIAL"):
            self._try_fill_sell(sym, pos, index)
            if sym in self.open_pos:
                self._continue(sym, self.open_pos[sym], index, heap, flush_t)
            return
        if state in ("BUY_PENDING", "BUY_PARTIAL"):
            self._try_fill_buy(sym, pos, index)
            if sym not in self.open_pos:
                return
            pos = self.open_pos[sym]
            if str(pos.get("exec_state")) in ("SELL_PENDING", "SELL_PARTIAL"):
                self._continue(sym, pos, index, heap, flush_t)
                return
            if float(pos.get("filled_qty") or 0.0) <= 0.0:
                pos["exec_intercept"] = "CANCEL"
            elif str(pos.get("exec_state")) == "BUY_PARTIAL":
                pos["exec_intercept"] = "SELL_FILLED"
            else:
                pos["exec_intercept"] = "SELL"
            pos["exiting"] = False
            super()._on_path(sym, index, event_t, heap, flush_t)
            pos = self.open_pos.get(sym)
            if pos and pos.get("pending_reason") and str(pos.get("exec_state")) not in ("SELL_PENDING", "SELL_PARTIAL"):
                self._schedule_exit(pos, sym, index, str(pos["pending_reason"]), event_t, (event_t, float("nan")), heap)
            return
        pos["exec_intercept"] = "SELL"
        super()._on_path(sym, index, event_t, heap, flush_t)
        pos = self.open_pos.get(sym)
        if pos and pos.get("pending_reason") and str(pos.get("exec_state")) not in ("SELL_PENDING", "SELL_PARTIAL"):
            self._schedule_exit(pos, sym, index, str(pos["pending_reason"]), event_t, (event_t, float("nan")), heap)

    def _schedule_exit(
        self,
        pos: dict[str, Any],
        sym: str,
        index: int,
        reason: str,
        event_t: float,
        found: tuple[float, float],
        heap: list[tuple],
    ) -> None:
        del found
        mode = str(pos.get("exec_intercept") or "SELL")
        filled = float(pos.get("filled_qty") or 0.0)
        if mode == "CANCEL" and filled <= 0.0:
            self._drop_buy(sym, pos, reason, event_t)
            return
        pos["remaining_buy"] = 0.0
        self._count_buy(pos)
        if filled > 0.0:
            pos["entry_px"] = float(pos["entry_cost"]) / SHARES_F
        pos["exec_state"] = "SELL_PENDING"
        pos["exiting"] = True
        pos["exit_reason"] = str(reason)
        pos["exit_signal_t"] = float(event_t)
        pos["exit_trigger_t"] = float(event_t)
        pos["sell_reachable_t"] = float(event_t) + float(self.order_delay_sec)
        pos["fill_scheduled"] = False
        pos["exiting"] = False
        pos.pop("pending_reason", None)
        self.exit_signal_n += 1
        self._trace(sym, pos, "EXIT_SIGNAL", event_t)
        self._continue(sym, pos, index, heap, self._active_flush)

    def _try_fill_buy(self, sym: str, pos: dict[str, Any], index: int) -> None:
        quote = self.quotes.get(sym, {}).get(index)
        if not quote:
            return
        if float(quote["t"]) <= float(pos["buy_reachable_t"]) + _EPS:
            return
        ask = _finite_number(quote.get("ask"))
        qty = _finite_number(quote.get("ask_qty"))
        if ask is None or qty is None or qty <= 0.0 or not quote.get("quote_ok"):
            pos["buy_unproven"] = int(pos.get("buy_unproven") or 0) + 1
            return
        remaining = float(pos["remaining_buy"])
        take = min(remaining, qty)
        if take <= 0.0:
            return
        if pos["buy_prints"] == 0 and sym in self.completed_symbols:
            pos["real_reentry"] = True
            self.real_reentry_n += 1
        elif pos["buy_prints"] == 0:
            pos["real_reentry"] = False
        pos["filled_qty"] = float(pos["filled_qty"]) + take
        pos["remaining_buy"] = remaining - take
        pos["entry_cost"] = float(pos["entry_cost"]) + take * ask
        pos["entry_fill_t"] = float(quote["t"])
        pos["buy_prints"] = int(pos["buy_prints"]) + 1
        pos["last_buy_px"] = ask
        if float(pos["entry_fill_t"]) <= float(pos["signal_t"]) + float(self.order_delay_sec) + _EPS:
            self.violations.append(f"buy_not_causal {sym} {pos.get('signal_uid')}")
        if float(pos["remaining_buy"]) <= 1e-9:
            pos["exec_state"] = "POSITION_OPEN"
            pos["entry_px"] = float(pos["entry_cost"]) / SHARES_F
            self._count_buy(pos)
            self._trace(sym, pos, "BUY_FILLED", float(quote["t"]))
        else:
            pos["exec_state"] = "BUY_PARTIAL"
            self._trace(sym, pos, "BUY_PARTIAL", float(quote["t"]))
        self._note_occupancy()

    def _try_fill_sell(self, sym: str, pos: dict[str, Any], index: int) -> None:
        quote = self.quotes.get(sym, {}).get(index)
        if not quote:
            return
        if float(quote["t"]) <= float(pos.get("sell_reachable_t") or 0.0) + _EPS:
            return
        if self.flatten_deadline is not None and float(quote["t"]) >= float(self.flatten_deadline) - _EPS:
            return
        decision = proven_sell(quote)
        if not decision.get("ok"):
            pos["sell_unproven"] = int(pos.get("sell_unproven") or 0) + 1
            return
        price = _finite_number(decision.get("px"))
        qty = _finite_number(decision.get("qty"))
        if price is None or qty is None or qty <= 0.0:
            pos["sell_unproven"] = int(pos.get("sell_unproven") or 0) + 1
            return
        need = float(pos["filled_qty"]) - float(pos["sold_qty"])
        take = min(need, qty)
        if take <= 0.0:
            return
        pos["sold_qty"] = float(pos["sold_qty"]) + take
        pos["exit_proceeds"] = float(pos["exit_proceeds"]) + take * price
        pos["exit_fill_t"] = float(quote["t"])
        pos["sell_prints"] = int(pos["sell_prints"]) + 1
        pos["last_sell_px"] = price
        pos["exec_state"] = "SELL_PARTIAL"
        signal_t = pos.get("exit_signal_t")
        if signal_t is not None and float(pos["exit_fill_t"]) <= float(signal_t) + float(self.order_delay_sec) + _EPS:
            self.violations.append(f"sell_not_causal {sym} {pos.get('signal_uid')}")
        if float(pos["filled_qty"]) - float(pos["sold_qty"]) <= 1e-9:
            if int(pos["sell_prints"]) == 1 and abs(float(pos["sold_qty"]) - float(pos["filled_qty"])) <= 1e-9:
                self.sell_full_n += 1
            else:
                self.sell_partial_n += 1
            self._complete(sym, pos)
            return
        self._trace(sym, pos, "SELL_PARTIAL", float(quote["t"]))

    def _drop_buy(self, sym: str, pos: dict[str, Any], reason: str, event_t: float) -> None:
        if float(pos.get("filled_qty") or 0.0) <= 0.0 and sym not in self.completed_symbols:
            self.traded_today.discard(sym)
        self.cancels.append(
            {
                "signal_uid": pos.get("signal_uid"),
                "symbol": sym,
                "session": self.session,
                "reason": str(reason),
                "signal_t": pos.get("signal_t"),
                "event_t": float(event_t),
                "filled_qty": float(pos.get("filled_qty") or 0.0),
                "state": "BUY_CANCELLED",
            }
        )
        self._trace(sym, pos, "BUY_CANCELLED", event_t)
        self.open_pos.pop(sym, None)
        self._note_occupancy()

    def _complete(self, sym: str, pos: dict[str, Any]) -> None:
        qty = float(pos["sold_qty"])
        entry_avg = float(pos["entry_cost"]) / float(pos["filled_qty"])
        exit_avg = float(pos["exit_proceeds"]) / qty
        sold_cost = entry_avg * qty
        pnl = _yen(float(pos["exit_proceeds"]) - sold_cost)
        buy_t = float(pos["entry_fill_t"])
        sell_t = float(pos["exit_fill_t"])
        signal_t = float(pos["signal_t"])
        exit_signal_t = pos.get("exit_signal_t")
        row = {
            "signal_uid": pos.get("signal_uid"),
            "symbol": sym,
            "session": self.session,
            "reason": pos.get("exit_reason"),
            "signal_t": signal_t,
            "entry_fill_t": buy_t,
            "exit_signal_t": None if exit_signal_t is None else float(exit_signal_t),
            "exit_fill_t": sell_t,
            "signal_hold_sec": None if exit_signal_t is None else float(exit_signal_t) - signal_t,
            "fill_hold_sec": sell_t - buy_t,
            "entry_px": _yen(entry_avg),
            "exit_px": _yen(exit_avg),
            "qty": qty,
            "pnl_yen": pnl,
            "reentry": bool(pos.get("real_reentry")),
            "buy_prints": int(pos["buy_prints"]),
            "sell_prints": int(pos["sell_prints"]),
            "support": pos.get("initial_support"),
            "state": "POSITION_CLOSED",
        }
        self.closed.append(row)
        self.completed_symbols.add(sym)
        self._trace(sym, pos, "POSITION_CLOSED", sell_t)
        self.open_pos.pop(sym, None)
        self._note_occupancy()

    def _fail_close(self, sym: str) -> None:
        pos = self.open_pos.get(sym)
        if pos is None:
            return
        filled = float(pos.get("filled_qty") or 0.0)
        sold = float(pos.get("sold_qty") or 0.0)
        self._count_buy(pos)
        if filled <= 0.0:
            self._drop_buy(sym, pos, "BUY_TIMEOUT", float(self.sess_end))
            return
        if sold > 0.0:
            self._book_sold_portion(sym, pos)
            pos = self.open_pos.get(sym)
            if pos is None:
                return
            filled = float(pos.get("filled_qty") or 0.0)
            sold = float(pos.get("sold_qty") or 0.0)
        unsold = filled - sold
        if unsold <= 1e-9:
            return
        avg = float(pos["entry_cost"]) / filled
        row = {
            "signal_uid": pos.get("signal_uid"),
            "symbol": sym,
            "session": self.session,
            "reason": str(pos.get("exit_reason") or "SELL_TIMEOUT"),
            "signal_t": pos.get("signal_t"),
            "entry_fill_t": pos.get("entry_fill_t"),
            "qty": unsold,
            "notional": _yen(avg * unsold),
            "pnl_yen": None,
            "state": "SELL_UNFILLED",
        }
        self.residuals.append(row)
        self.still_open.append(
            {
                "symbol": sym,
                "notional": row["notional"],
                "entry_t": float(pos.get("entry_fill_t") or pos.get("signal_t") or 0.0),
                "entry_px": avg,
                "support": float(pos.get("support") or 0.0),
                "pnl_yen": None,
            }
        )
        self._trace(sym, pos, "SELL_UNFILLED", float(self.sess_end))
        self.open_pos.pop(sym, None)
        self._note_occupancy()

    def _book_sold_portion(self, sym: str, pos: dict[str, Any]) -> None:
        filled = float(pos["filled_qty"])
        sold = float(pos["sold_qty"])
        avg = float(pos["entry_cost"]) / filled
        proceeds = float(pos["exit_proceeds"])
        pnl = _yen(proceeds - avg * sold)
        self.completed_symbols.add(sym)
        self.closed.append(
            {
                "signal_uid": pos.get("signal_uid"),
                "symbol": sym,
                "session": self.session,
                "reason": pos.get("exit_reason"),
                "signal_t": float(pos["signal_t"]),
                "entry_fill_t": float(pos["entry_fill_t"]),
                "exit_signal_t": pos.get("exit_signal_t"),
                "exit_fill_t": float(pos["exit_fill_t"]),
                "signal_hold_sec": None,
                "fill_hold_sec": float(pos["exit_fill_t"]) - float(pos["entry_fill_t"]),
                "entry_px": _yen(avg),
                "exit_px": _yen(proceeds / sold),
                "qty": sold,
                "pnl_yen": pnl,
                "reentry": bool(pos.get("real_reentry")),
                "buy_prints": int(pos["buy_prints"]),
                "sell_prints": int(pos["sell_prints"]),
                "support": pos.get("initial_support"),
                "state": "SELL_PARTIAL_CLOSED",
            }
        )
        pos["entry_cost"] = avg * (filled - sold)
        pos["filled_qty"] = filled - sold
        pos["sold_qty"] = 0.0
        pos["exit_proceeds"] = 0.0
        self.sell_partial_n += 1

    def _continue(self, sym: str, pos: dict[str, Any], index: int, heap: list[tuple], flush_t: float) -> None:
        st = self.syms[sym]
        nxt = int(index) + 1
        if nxt < st.n and float(st.t[nxt]) <= float(flush_t) + _EPS:
            pos["next_index"] = nxt
            heapq.heappush(heap, (float(st.t[nxt]), 0, sym, "PATH", nxt))
        else:
            pos["next_index"] = nxt

    def _ensure_flatten(self, now: float) -> None:
        if self.flatten_start is None or float(now) + _EPS < float(self.flatten_start) or self._flatten_armed:
            return
        self._flatten_armed = True
        self._entries_closed = True
        for sym in list(self.open_pos):
            pos = self.open_pos.get(sym)
            if pos is None:
                continue
            if str(pos.get("exec_state")) in ("SELL_PENDING", "SELL_PARTIAL"):
                continue
            if float(pos.get("filled_qty") or 0.0) <= 0.0:
                self._drop_buy(sym, pos, "SESSION_FLAT", float(self.flatten_start))
                continue
            pos["remaining_buy"] = 0.0
            self._count_buy(pos)
            pos["entry_px"] = float(pos["entry_cost"]) / SHARES_F
            pos["exec_state"] = "SELL_PENDING"
            pos["exec_intercept"] = "SELL"
            pos["exiting"] = True
            pos["exit_reason"] = "SESSION_FLAT"
            pos["exit_signal_t"] = float(self.flatten_start)
            pos["exit_trigger_t"] = float(self.flatten_start)
            pos["sell_reachable_t"] = float(self.flatten_start) + float(self.order_delay_sec)
            pos["fill_scheduled"] = False
            pos["exiting"] = False
            self.exit_signal_n += 1
            self._trace(sym, pos, "EXIT_SIGNAL", float(self.flatten_start))

    def _fill_flatten_pending(self, flush_t: float) -> None:
        del flush_t

    def _close_positions(self) -> None:
        for sym in list(self.open_pos):
            self._fail_close(sym)

    def _mark_unfilled(self, sym: str) -> None:
        self._fail_close(sym)

    def _count_buy(self, pos: dict[str, Any]) -> None:
        if pos.get("_buy_counted"):
            return
        filled = float(pos.get("filled_qty") or 0.0)
        if filled <= 0.0:
            return
        pos["_buy_counted"] = True
        if abs(filled - SHARES_F) <= 1e-9 and int(pos.get("buy_prints") or 0) == 1:
            self.buy_full_n += 1
        else:
            self.buy_partial_n += 1

    def _note_occupancy(self) -> None:
        yen = 0.0
        for pos in self.open_pos.values():
            state = str(pos.get("exec_state") or "")
            if state in ("BUY_PENDING", "BUY_PARTIAL"):
                yen += float(pos.get("reserve_px") or 0.0) * SHARES_F
            else:
                yen += float(pos.get("entry_cost") or 0.0)
        self.peak_slots = max(self.peak_slots, len(self.open_pos))
        self.peak_reserved_yen = max(self.peak_reserved_yen, yen)

    def _trace(self, sym: str, pos: dict[str, Any], state: str, event_t: float) -> None:
        if not str(sym).startswith("4382"):
            return
        self.hero.append(
            {
                "state": state,
                "symbol": sym,
                "signal_uid": pos.get("signal_uid"),
                "event_t": float(event_t),
                "signal_t": pos.get("signal_t"),
                "filled_qty": pos.get("filled_qty"),
                "sold_qty": pos.get("sold_qty"),
                "reason": pos.get("exit_reason"),
                "ask_or_bid": pos.get("last_sell_px", pos.get("last_buy_px")),
            }
        )


def _quote_snapshot(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "t": float(ev["t"]),
        "ask": ev.get("ask"),
        "ask_qty": ev.get("ask_qty"),
        "quote_ok": bool(ev.get("quote_ok")),
        "buy1_px": ev.get("buy1_px"),
        "buy1_qty": ev.get("buy1_qty"),
        "buy1_time": ev.get("buy1_time"),
        "buy1_time_raw": ev.get("buy1_time_raw"),
        "bid": ev.get("bid"),
        "state": ev.get("state"),
        "executable": ev.get("executable"),
        "special": ev.get("special"),
    }


def _finite_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
