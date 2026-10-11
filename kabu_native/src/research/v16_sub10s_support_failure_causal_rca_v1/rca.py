"""Diagnose sub-10s BREAK_SUPPORT_FAILURE without changing the sealed executor.

Policy B is replayed as published. B+M adds one predeclared entry check:
the signal event's Bid is already at or below the frozen support.
That check is read from the exit predicate. It is not fit on the 29 days.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.current50_v16_capital_risk_policy_comparison_v1.compare import (
    G,
    SHARES,
    ResearchGrossCapExecutor,
    _max_gross,
)
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import (
    _capture_dir,
    _paper_sessions,
    _symbol,
)
from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.event_time_impulse_complete_strategy_v2.rules import support_failure
from small_paper.fixed_support_x1_session import (
    MAX_CONCURRENT,
    _EPS,
    _Sym,
    _finite_pos,
)
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_sub10s_support_failure_causal_rca_v1"
PRIOR = NATIVE / "results" / "research" / "current50_v16_historical_economic_expansion_v2" / "report.json"
KNOWN = ("20261007", "20261008")
NEW_ACTUAL = "20261009"
CAPITAL = 500_000.0
COST_SIDES = (0, 10, 50, 100)
BUSY = (
    "paper_trade_checked_runner",
    "market_ingress_service",
    "run_core10_dynamic40_am_pm_daily_runner",
    "run_small_paper_pilot",
    "v1r_paper_primary_launcher",
)


class DiagSym(_Sym):
    def __init__(self) -> None:
        super().__init__()
        self.bid_px = np.full(self.cap, np.nan)

    def _grow(self) -> None:
        previous = self.bid_px
        super()._grow()
        grown = np.full(self.cap, np.nan)
        grown[: previous.shape[0]] = previous
        self.bid_px = grown

    def append_raw(self, ev: Any) -> int:
        index = super().append_raw(ev)
        try:
            self.bid_px[index] = float(ev["bid"])
        except (KeyError, TypeError, ValueError):
            self.bid_px[index] = float("nan")
        return index


class _DiagBase(ResearchGrossCapExecutor):
    def _sym(self, symbol: str) -> DiagSym:
        found = self.syms.get(symbol)
        if found is None:
            found = DiagSym()
            self.syms[symbol] = found
        return found


class BidAwarePolicyB(_DiagBase):
    """Published Policy B, with bid stored for diagnosis only."""


class BidSupportPolicyBM(_DiagBase):
    """Policy B plus a predeclared bid-versus-support entry rejection."""

    def __init__(self) -> None:
        super().__init__()
        self.causal_denied_n = 0
        self.causal_missing_bid_n = 0

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        import heapq

        st = self.syms[sym]
        if st.sig_ptr >= len(st.signals) or int(st.signals[st.sig_ptr]) != index:
            return
        st.sig_ptr += 1
        self.signal_seen_n += 1
        if self._entries_closed or (
            self.flatten_start is not None and float(event_t) + _EPS >= float(self.flatten_start)
        ):
            if st.sig_ptr < len(st.signals):
                nxt = int(st.signals[st.sig_ptr])
                if float(st.t[nxt]) <= flush_t + _EPS:
                    heapq.heappush(heap, (float(st.t[nxt]), 2, sym, "SIG", nxt))
            return
        if st.sig_ptr < len(st.signals):
            nxt = int(st.signals[st.sig_ptr])
            if float(st.t[nxt]) <= flush_t + _EPS:
                heapq.heappush(heap, (float(st.t[nxt]), 2, sym, "SIG", nxt))
        if self.admission is not None:
            allowed, reason = self.admission(sym, event_t)
            if not allowed:
                self.admission_denied_n += 1
                key = str(reason or "FAIL_CLOSED")
                self.admission_denied_reasons[key] = int(self.admission_denied_reasons.get(key) or 0) + 1
                return
        if not bool(st.ok[index]):
            self.signal_quote_not_ok_n += 1
            return
        pos = self.open_pos.get(sym)
        if pos is not None:
            if pos.get("exiting"):
                self.counts["occupancy"] += 1
            else:
                self.counts["same_symbol"] += 1
            return
        if len(self.open_pos) >= MAX_CONCURRENT:
            self.counts["cap"] += 1
            return
        ask = float(st.ask_px[index])
        pre = float(st.pre_high[index])
        if not (_finite_pos(ask) and pre == pre):
            self.signal_invalid_ask_n += 1
            return
        lot = ask * SHARES
        try:
            held = sum(float(item["entry_px"]) * SHARES for item in self.open_pos.values())
        except (TypeError, KeyError, ValueError):
            self.gross_state_unknown_n += 1
            return
        if held + lot > self.gross_cap + 1e-6:
            self.gross_cap_denied_n += 1
            if lot > self.gross_cap + 1e-6:
                self.single_name_over_g_n += 1
            else:
                self.book_sum_over_g_n += 1
            return
        bid = float(st.bid_px[index])
        if not _finite_pos(bid):
            self.causal_missing_bid_n += 1
            self.causal_denied_n += 1
            return
        if bid <= pre + 1e-9:
            self.causal_denied_n += 1
            return
        is_reentry = sym in self.traded_today
        if is_reentry:
            self.counts["reentry"] += 1
        self.traded_today.add(sym)
        self.open_pos[sym] = {
            "signal_index": int(index),
            "entry_t": event_t,
            "entry_px": ask,
            "support": pre,
            "initial_support": pre,
            "observed_support": pre,
            "last_reconfirm_t": event_t,
            "reconfirm_raises": 0,
            "reentry": is_reentry,
            "next_index": int(index) + 1,
            "exiting": False,
            "signal_uid": f"{self.day}|{self.session}|{sym}|{int(index)}",
        }
        self.admission_count += 1
        self.position_mutations += 1
        if self._ledger_entry(sym):
            self._notify_entry(sym)
        nxt = int(index) + 1
        if nxt < st.n and float(st.t[nxt]) <= flush_t + _EPS:
            heapq.heappush(heap, (float(st.t[nxt]), 0, sym, "PATH", nxt))


def _busy() -> list[str]:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| ForEach-Object { $_.CommandLine }"
    )
    try:
        text = subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True, errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return ["PROCESS_CHECK_FAILED"]
    return sorted({marker for marker in BUSY if marker in text})


def _paper_window(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 16


def _bind(book: ResearchGrossCapExecutor, day: str, kind: str) -> None:
    book.notifier = None
    book.ledger_path = None
    bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
    if kind == "pm":
        book.bind_safe_pm_flatten(day=day)
    else:
        book.bind_production_schedule(day=day, kind="am")


def _finite(value: float) -> bool:
    return value == value and value > 0.0


def _diagnose(st: DiagSym, trade: dict[str, Any]) -> dict[str, Any]:
    index = int(trade["signal_index"])
    support = float(trade["initial_support"])
    current = float(st.px[index])
    ask = float(st.ask_px[index])
    bid = float(st.bid_px[index])
    bid_ok = _finite(bid)
    entry_failed = bool(support_failure(current, support))
    trigger: Optional[int] = None
    for probe in range(index + 1, st.n):
        price = float(st.px[probe])
        if support_failure(price, support):
            trigger = probe
            break
    trigger_px = None if trigger is None else float(st.px[trigger])
    trigger_delay = None if trigger is None else float(st.t[trigger]) - float(st.t[index])
    same_timestamp = bool(trigger is not None and abs(float(st.t[trigger]) - float(st.t[index])) <= 1e-6)
    trigger_fresh = None if trigger is None else bool(st.ok[trigger])
    flags = []
    if entry_failed:
        flags.append("M1")
    if same_timestamp:
        flags.append("M4_SAME_TIMESTAMP")
    if trigger is not None and trigger_fresh is False:
        flags.append("M4_STALE_TRIGGER")
    if bid_ok and bid <= support + 1e-9:
        flags.append("ENTRY_BID_AT_OR_BELOW_SUPPORT")
    if trigger is not None and bid_ok and not same_timestamp and trigger_fresh is not False:
        if float(trigger_px) + 1e-9 >= bid and bid <= support + 1e-9:
            flags.append("M2")
        elif float(trigger_px) < bid - 1e-9:
            flags.append("M3")
    if trigger is not None and not bid_ok:
        flags.append("M5_BID_MISSING")
    if trigger is None and trade["reason"] == "BREAK_SUPPORT_FAILURE":
        flags.append("M5_TRIGGER_NOT_RECONSTRUCTED")
    primary = "M5"
    for name in ("M1", "M4_SAME_TIMESTAMP", "M4_STALE_TRIGGER", "M2", "M3"):
        if name in flags:
            primary = name
            break
    if primary == "M5" and "M3" not in flags and trigger is not None and bid_ok and not same_timestamp:
        if not (bid <= support + 1e-9):
            primary = "M3"
            flags.append("M3")
    hold = float(trade["hold_sec"])
    return {
        "symbol": trade["symbol"],
        "signal_uid": trade["signal_uid"],
        "reason": trade["reason"],
        "pnl": float(trade["pnl_yen"]),
        "hold_sec": hold,
        "support": support,
        "entry_ask": ask,
        "entry_current": current,
        "entry_bid": bid if bid_ok else None,
        "spread": (ask - bid) if bid_ok else None,
        "current_minus_support": current - support,
        "bid_minus_support": (bid - support) if bid_ok else None,
        "entry_already_failed": entry_failed,
        "trigger_delay_sec": trigger_delay,
        "trigger_px": trigger_px,
        "same_timestamp": same_timestamp,
        "trigger_fresh": trigger_fresh,
        "flags": flags,
        "primary": primary,
        "sub10_bsf": trade["reason"] == "BREAK_SUPPORT_FAILURE" and hold < 10.0,
        "bid_le_support": bool(bid_ok and bid <= support + 1e-9),
    }


def _feed(day: str, kind: str, seq_after: int, attach: str) -> dict[str, Any]:
    capture_dir = _capture_dir(day)
    if capture_dir is None:
        return {"error": "NO_CAPTURE"}
    book_b = BidAwarePolicyB()
    book_m = BidSupportPolicyBM()
    _bind(book_b, day, kind)
    _bind(book_m, day, kind)
    if len(book_b.admission_membership) != 50:
        return {"error": f"ADMISSION_{len(book_b.admission_membership)}"}
    fed = 0
    missing_clock = 0
    order_breaks = 0
    last_seq = -1
    books = (book_b, book_m)
    for part in sorted(capture_dir.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                seq = int(rec.get("sequence") or 0)
                if seq <= seq_after:
                    continue
                received = str(rec.get("received_at") or "")
                if not received:
                    missing_clock += 1
                    continue
                if attach and received < attach:
                    continue
                if seq < last_seq:
                    order_breaks += 1
                last_seq = seq
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload:
                    continue
                payload = dict(payload)
                payload.setdefault("received_at", received)
                payload.setdefault("recorded_at", received)
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                for book in books:
                    if not book._closed:
                        book.on_market_event({"symbol": symbol, "payload": dict(payload)})
                fed += 1
                if fed % 200000 == 0:
                    print(f"fed {day} {kind} {fed}", flush=True)
                if all(book._closed for book in books):
                    break
        if all(book._closed for book in books):
            break
    for book in books:
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
    rows = [_diagnose(book_b.syms[str(trade["symbol"])], trade) for trade in book_b.trades]
    for row in rows:
        row["day"] = day
        row["kind"] = kind
    unfilled = []
    for row in book_b.unfilled:
        unfilled.append(
            {
                "day": day,
                "kind": kind,
                "symbol": row.get("symbol"),
                "signal_uid": row.get("signal_uid"),
                "pnl_yen": row.get("pnl_yen"),
                "exit_px": row.get("exit_px"),
                "entry_px": row.get("entry_px"),
                "reason": row.get("reason"),
            }
        )
    perf_b = book_b.x1_performance()
    perf_m = book_m.x1_performance()
    summary = {
        "day": day,
        "kind": kind,
        "fed": fed,
        "missing_clock": missing_clock,
        "order_breaks": order_breaks,
        "final_ok_b": bool(perf_b["final_ok"]),
        "open_n_b": int(perf_b["open_n"]),
        "pending_n_b": int(perf_b["exit_pending_n"]),
        "trades_b": int(perf_b["trades_n"]),
        "gross_b": float(perf_b["net_pnl_yen"]),
        "unfilled_b": len(book_b.unfilled),
        "trades_m": int(perf_m["trades_n"]),
        "gross_m": float(perf_m["net_pnl_yen"]),
        "final_ok_m": bool(perf_m["final_ok"]),
        "unfilled_m": len(book_m.unfilled),
        "max_gross_b": _max_gross(book_b.ledger),
        "max_gross_m": _max_gross(book_m.ledger),
        "yen_deny_b": int(book_b.gross_cap_denied_n),
        "yen_deny_m": int(book_m.gross_cap_denied_n),
        "causal_deny_m": int(book_m.causal_denied_n),
        "causal_missing_bid_m": int(book_m.causal_missing_bid_n),
        "above_g_b": sum(1 for trade in book_b.trades if float(trade["entry_px"]) * SHARES > G + 1e-6),
        "above_g_m": sum(1 for trade in book_m.trades if float(trade["entry_px"]) * SHARES > G + 1e-6),
    }
    m_rows = []
    b_uids = {row["signal_uid"] for row in rows}
    for trade in book_m.trades:
        m_rows.append(
            {
                "day": day,
                "kind": kind,
                "signal_uid": trade["signal_uid"],
                "symbol": trade["symbol"],
                "reason": trade["reason"],
                "pnl": float(trade["pnl_yen"]),
                "hold_sec": float(trade["hold_sec"]),
                "in_b": trade["signal_uid"] in b_uids,
            }
        )
    print(
        f"RCA {day} {kind} B {summary['trades_b']} {summary['gross_b']} M {summary['trades_m']} {summary['gross_m']} unfilled {summary['unfilled_b']}",
        flush=True,
    )
    return {"summary": summary, "rows": rows, "unfilled": unfilled, "m_rows": m_rows}


def _label(row: dict[str, Any]) -> str:
    if row["sub10_bsf"]:
        return "SUB10_BREAK_SUPPORT"
    if row["reason"] == "BREAK_SUPPORT_FAILURE":
        return "LONGER_BREAK_SUPPORT"
    if row["reason"] == "IMPULSE_EXHAUSTED":
        return "IMPULSE_EXHAUSTED"
    if row["reason"] == "SESSION_FLAT":
        return "SESSION_FLAT"
    return str(row["reason"])


def _rate(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    return sum(1 for row in rows if row.get(key)) / len(rows)


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_label(row)].append(row)
    out = {}
    for name, items in grouped.items():
        distances = [float(row["current_minus_support"]) for row in items if row.get("current_minus_support") is not None]
        out[name] = {
            "trades": len(items),
            "gross_pnl_yen": round(sum(float(row["pnl"]) for row in items), 6),
            "bid_le_support_rate": _rate(items, "bid_le_support"),
            "entry_already_failed_rate": _rate(items, "entry_already_failed"),
            "same_timestamp_rate": _rate(items, "same_timestamp"),
            "median_current_minus_support": sorted(distances)[len(distances) // 2] if distances else None,
            "mechanisms": dict(Counter(row["primary"] for row in items)),
        }
    return out


def _robustness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[row["day"]].append(row)
    compared = 0
    detection_higher = 0
    for items in by_day.values():
        sub = [row for row in items if row["sub10_bsf"]]
        impulse = [row for row in items if row["reason"] == "IMPULSE_EXHAUSTED"]
        if not sub or not impulse:
            continue
        compared += 1
        if (_rate(sub, "bid_le_support") or 0) > (_rate(impulse, "bid_le_support") or 0):
            detection_higher += 1
    sub = [row for row in rows if row["sub10_bsf"]]
    counts = Counter(row["symbol"] for row in sub)
    top_share = (counts.most_common(1)[0][1] / len(sub)) if sub else None
    return {
        "days_with_both_sub10_and_impulse": compared,
        "days_bid_flag_higher_on_sub10": detection_higher,
        "top_symbol_share_of_sub10": top_share,
        "top_symbols": [{"symbol": symbol, "trades": count} for symbol, count in counts.most_common(5)],
    }


def _economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnl"]) for row in rows]
    profit = sum(pnl for pnl in pnls if pnl > 0)
    loss = sum(pnl for pnl in pnls if pnl < 0)
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "trades": len(pnls),
        "gross_pnl_yen": round(sum(pnls), 6),
        "profit_factor": None if loss >= 0 else profit / abs(loss),
        "max_drawdown_yen": drawdown,
        "cost_sensitivity": [
            {
                "assumed_yen_per_side": cost,
                "label": "SEALED_EXECUTOR_HAS_NO_FEE" if cost == 0 else "ASSUMED_STRESS_NOT_OFFICIAL_NET",
                "pnl_yen": round(sum(pnls) - cost * 2 * len(pnls), 6),
            }
            for cost in COST_SIDES
        ],
    }


def _opportunity(b_rows: list[dict[str, Any]], m_rows: list[dict[str, Any]]) -> dict[str, Any]:
    kept = {row["signal_uid"] for row in m_rows}
    rejected = [row for row in b_rows if row["signal_uid"] not in kept]
    winners = [row for row in rejected if float(row["pnl"]) > 0]
    losers = [row for row in rejected if float(row["pnl"]) < 0]
    return {
        "rejected_trades": len(rejected),
        "rejected_winner_trades": len(winners),
        "rejected_winner_gross_yen": round(sum(float(row["pnl"]) for row in winners), 6),
        "rejected_loser_trades": len(losers),
        "rejected_loser_gross_yen": round(sum(float(row["pnl"]) for row in losers), 6),
        "note": "These are B trades absent from B+M. Later trades that exist only because a slot was freed are not in this rejected set.",
    }


def _save(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = OUT / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(OUT / "report.json")


def _write_book(report: dict[str, Any]) -> None:
    book = Workbook()
    specs = [
        ("Source Identity", ["item", "value"]),
        ("Accounting Integrity", ["day", "kind", "final_ok", "unfilled", "null_pnl", "gross_includes_unfilled"]),
        ("EXIT Predicate", ["item", "value"]),
        ("Event Timeline", ["day", "kind", "symbol", "reason", "hold_sec", "primary", "trigger_delay_sec", "current_minus_support", "bid_minus_support"]),
        ("Failure Mechanisms", ["primary", "trades", "gross"]),
        ("Entry-Time Features", ["label", "trades", "gross", "bid_le_support_rate", "already_failed_rate", "median_distance"]),
        ("Matched Controls", ["comparison", "value"]),
        ("Causal Separation", ["item", "value"]),
        ("Complete Strategy Economics", ["policy", "trades", "gross", "pf", "max_dd", "max_gross"]),
        ("Cost Sensitivity", ["policy", "per_side", "pnl", "label"]),
        ("Robustness", ["item", "value"]),
        ("Evidence Gaps", ["item", "value"]),
    ]
    first = True
    for name, header in specs:
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        sheet.append(header)
    for key, value in (report.get("identity") or {}).items():
        book["Source Identity"].append([key, str(value)])
    for row in report.get("accounting_rows") or []:
        book["Accounting Integrity"].append([row.get("day"), row.get("kind"), row.get("final_ok_b"), row.get("unfilled_b"), row.get("null_pnl"), False])
    for key, value in (report.get("predicate") or {}).items():
        book["EXIT Predicate"].append([key, str(value)])
    for row in report.get("timeline_sample") or []:
        book["Event Timeline"].append([
            row.get("day"), row.get("kind"), row.get("symbol"), row.get("reason"), row.get("hold_sec"),
            row.get("primary"), row.get("trigger_delay_sec"), row.get("current_minus_support"), row.get("bid_minus_support"),
        ])
    for key, value in (report.get("mechanism_counts") or {}).items():
        book["Failure Mechanisms"].append([key, value.get("trades"), value.get("gross")])
    for key, value in (report.get("feature_groups") or {}).items():
        book["Entry-Time Features"].append([
            key, value.get("trades"), value.get("gross_pnl_yen"), value.get("bid_le_support_rate"),
            value.get("entry_already_failed_rate"), value.get("median_current_minus_support"),
        ])
    for key, value in (report.get("controls") or {}).items():
        book["Matched Controls"].append([key, str(value)])
    for key, value in (report.get("separation") or {}).items():
        book["Causal Separation"].append([key, str(value)])
    for row in report.get("economics_rows") or []:
        book["Complete Strategy Economics"].append([
            row.get("policy"), row.get("trades"), row.get("gross_pnl_yen"), row.get("profit_factor"),
            row.get("max_drawdown_yen"), row.get("max_gross"),
        ])
    for row in report.get("cost_rows") or []:
        book["Cost Sensitivity"].append([row.get("policy"), row.get("assumed_yen_per_side"), row.get("pnl_yen"), row.get("label")])
    for key, value in (report.get("robustness") or {}).items():
        book["Robustness"].append([key, str(value)])
    for key, value in (report.get("gaps") or {}).items():
        book["Evidence Gaps"].append([key, str(value)])
    book.save(OUT / "audit.xlsx")


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        f"ENTRY_TIME_SEPARATION: {report['entry_time_separation']}",
        f"COMPLETE_STRATEGY_ECONOMICS: {report['complete_strategy_economics']}",
        f"LATER_SAMPLE_VALIDATION: {report['later_sample_validation']}",
        "",
        report.get("body") or "",
    ]
    return "\n".join(lines) + "\n"


def _historical_days() -> list[str]:
    body = json.loads(PRIOR.read_text(encoding="utf-8"))
    return [row["date"] for row in body.get("eligibility") or [] if row.get("class") == "RETROSPECTIVE_V16_COUNTERFACTUAL"]


def _replay_day(day: str, kind: str, study_class: str) -> dict[str, Any]:
    watermark = 0
    attach = _attach_text(day, kind)
    if study_class != "RETROSPECTIVE_V16_COUNTERFACTUAL":
        sessions = [row for row in _paper_sessions(day) if row["kind"] == kind and "V16" in str(row["activation_id"])]
        if not sessions:
            return {"error": "NO_V16_SESSION"}
        watermark = int(sessions[0]["watermark_seq"])
        attach = ""
    return _feed(day, kind, watermark, attach)


def main() -> None:
    now = datetime.now().astimezone()
    busy = _busy()
    if busy or _paper_window(now):
        report = {
            "verdict": "V16_SUB10S_SUPPORT_FAILURE_MECHANISM_UNRESOLVED_V1",
            "next": "RETRY_AFTER_PAPER_AND_INGRESS_HAVE_EXITED",
            "entry_time_separation": "NOT_EVALUABLE",
            "complete_strategy_economics": "NOT_EVALUABLE",
            "later_sample_validation": "PENDING",
            "body": "Paperまたは市場時間と重なるため、Replayは開始していません。",
            "policy_adopted": False,
            "submit_cancel_live": "0/0/0",
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
        _save(report)
        _write_book(report)
        print(report["verdict"], flush=True)
        return
    days = _historical_days()
    jobs = [(day, kind, "RETROSPECTIVE_V16_COUNTERFACTUAL") for day in days for kind in ("am", "pm")]
    jobs += [(day, kind, "ACTUAL_PARITY_VALIDATED") for day in KNOWN for kind in ("am", "pm")]
    jobs += [(NEW_ACTUAL, kind, "NEW_V16_ACTUAL") for kind in ("am", "pm")]
    report: dict[str, Any] = {
        "running": True,
        "done": [],
        "rows": [],
        "m_rows": [],
        "summaries": [],
        "unfilled": [],
        "policy_adopted": False,
        "submit_cancel_live": "0/0/0",
        "runtime_changed": False,
    }
    if (OUT / "report.json").is_file():
        prior = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
        if prior.get("running") and prior.get("rows") is not None:
            report = prior
    done = set(report.get("done") or [])
    for day, kind, study_class in jobs:
        token = f"{day}:{kind}"
        if token in done:
            continue
        if _busy() or _paper_window(datetime.now().astimezone()):
            report["running"] = True
            _save(report)
            print("DEFERRED", flush=True)
            return
        result = _replay_day(day, kind, study_class)
        if result.get("error"):
            report["summaries"].append({"day": day, "kind": kind, "study_class": study_class, "error": result["error"]})
        else:
            for row in result["rows"]:
                row["study_class"] = study_class
            for row in result["m_rows"]:
                row["study_class"] = study_class
            result["summary"]["study_class"] = study_class
            result["summary"]["null_pnl"] = all(row.get("pnl_yen") is None and row.get("exit_px") is None for row in result["unfilled"])
            report["rows"].extend(result["rows"])
            report["m_rows"].extend(result["m_rows"])
            report["summaries"].append(result["summary"])
            report["unfilled"].extend(result["unfilled"])
        done.add(token)
        report["done"] = sorted(done)
        report["running"] = True
        _save(report)
    _finish(report)


def _finish(report: dict[str, Any]) -> None:
    retro = [row for row in report["rows"] if row["study_class"] == "RETROSPECTIVE_V16_COUNTERFACTUAL"]
    sub = [row for row in retro if row["sub10_bsf"]]
    impulse = [row for row in retro if row["reason"] == "IMPULSE_EXHAUSTED"]
    mechanisms: dict[str, dict[str, Any]] = {}
    for name, items in Counter(row["primary"] for row in sub).items():
        chosen = [row for row in sub if row["primary"] == name]
        mechanisms[name] = {"trades": len(chosen), "gross": round(sum(float(row["pnl"]) for row in chosen), 6)}
    detection = _rate(sub, "bid_le_support")
    false_reject = _rate(impulse, "bid_le_support")
    already = _rate(sub, "entry_already_failed")
    same_ts = _rate(sub, "same_timestamp")
    m3 = _rate(sub, "primary") if False else sum(1 for row in sub if row["primary"] == "M3") / len(sub) if sub else None
    robust = _robustness(retro)
    gap = None if detection is None or false_reject is None else detection - false_reject
    separation_supported = bool(
        gap is not None
        and gap >= 0.15
        and (robust["top_symbol_share_of_sub10"] or 1) < 0.40
        and robust["days_with_both_sub10_and_impulse"]
        and robust["days_bid_flag_higher_on_sub10"] / robust["days_with_both_sub10_and_impulse"] >= 0.70
    )
    m_retro = [row for row in report["m_rows"] if row["study_class"] == "RETROSPECTIVE_V16_COUNTERFACTUAL"]
    econ_b = _economics(retro)
    econ_m = _economics(m_retro)
    summaries = [row for row in report["summaries"] if row.get("study_class") == "RETROSPECTIVE_V16_COUNTERFACTUAL" and "gross_b" in row]
    econ_b["max_gross"] = max((float(row["max_gross_b"]) for row in summaries), default=None)
    econ_m["max_gross"] = max((float(row["max_gross_m"]) for row in summaries), default=None)
    improved = bool(
        separation_supported
        and econ_m["gross_pnl_yen"] > econ_b["gross_pnl_yen"]
        and econ_m["cost_sensitivity"][2]["pnl_yen"] > econ_b["cost_sensitivity"][2]["pnl_yen"]
        and (econ_m["max_gross"] or 0) <= G + 1e-6
    )
    unfilled = report.get("unfilled") or []
    null_ok = all(row.get("pnl_yen") is None and row.get("exit_px") is None for row in unfilled)
    identity = {
        "executor": "FixedSupportX1SessionExecutor",
        "activation_sha": "49fb47c67a1da39a99edfba8b88d66c2e8009a07d6db94c269d6e8cdd05a2240",
        "complete_strategy_sha": "7bbb29f78f581d7c7538b0d1947422e22932a74c5cc1933396c231da04bca3b3",
        "support_failure": "finite CurrentPrice <= frozen ENTRY_PRE_BREAK_HIGH",
        "predeclared_bm": "reject when signal Bid is missing or Bid <= pre_high",
        "bm_fitted_on_pnl": False,
    }
    predicate = {
        "price_field": "CurrentPrice stored as px",
        "level": "pre_high at the signal row, the max CurrentPrice in the prior 60 seconds",
        "entry_fill": "Ask on the signal row",
        "exit_fill": "first executable Bid at or after the trigger event",
        "same_row_exit": False,
        "path_starts": "the next stored row after the signal index",
        "support_ratchet_used_for_exit": False,
        "active_support_must_equal_initial": True,
    }
    controls = {
        "sub10_bid_le_support_rate": detection,
        "impulse_bid_le_support_rate": false_reject,
        "rate_gap": gap,
        "sub10_entry_current_already_failed": already,
        "sub10_same_timestamp_rate": same_ts,
        "sub10_primary_m3_rate": m3,
    }
    gaps = {
        "minutes_before_entry": "60/30/10 second pre-entry paths were not reconstructed as separate series. The signal row and the prior-60-second high are the entry-time observations.",
        "unfilled_true_pnl": "SESSION_EXIT_UNFILLED has no fill and is omitted from gross. No substitute price was created.",
        "later_sample": "The 29 days are the exploration sample. A later V16 actual day is still required.",
    }
    body = _body(retro, sub, econ_b, econ_m, controls, robust, unfilled, null_ok, separation_supported, improved, summaries)
    m5_share = (sum(1 for row in sub if str(row["primary"]).startswith("M5")) / len(sub)) if sub else 1.0
    verdict = (
        "V16_SUB10S_SUPPORT_FAILURE_MECHANISM_IDENTIFIED_V1"
        if sub and m5_share < 0.30
        else "V16_SUB10S_SUPPORT_FAILURE_MECHANISM_UNRESOLVED_V1"
    )
    report.update(
        {
            "verdict": verdict,
            "next": "KEEP_BM_UNADOPTED_AND_VALIDATE_THE_FROZEN_BID_SUPPORT_CHECK_ON_A_LATER_V16_ACTUAL_DAY" if separation_supported else "DO_NOT_ADD_AN_ENTRY_FILTER_UNTIL_A_LATER_SAMPLE_OR_A_STRONGER_ENTRY_TIME_SIGNAL",
            "entry_time_separation": "SUPPORTED" if separation_supported else "NOT_SUPPORTED",
            "complete_strategy_economics": "IMPROVED_IN_RESEARCH" if improved else "NOT_IMPROVED",
            "later_sample_validation": "PENDING",
            "identity": identity,
            "predicate": predicate,
            "feature_groups": _group_metrics(retro),
            "mechanism_counts": mechanisms,
            "controls": controls,
            "robustness": robust,
            "gaps": gaps,
            "opportunity": _opportunity(retro, m_retro),
            "economics_rows": [{"policy": "B", **econ_b}, {"policy": "B+M", **econ_m}],
            "cost_rows": [{"policy": "B", **row} for row in econ_b["cost_sensitivity"]] + [{"policy": "B+M", **row} for row in econ_m["cost_sensitivity"]],
            "accounting_rows": summaries,
            "timeline_sample": [row for row in sub[:200]],
            "separation": controls,
            "body": body,
            "running": False,
            "policy_adopted": False,
            "unfilled_null_pnl": null_ok,
            "unfilled_n": len(unfilled),
        }
    )
    text = _markdown(report)
    report["markdown"] = text
    slim = dict(report)
    slim.pop("rows", None)
    slim.pop("m_rows", None)
    (OUT / "report.md").write_text(text, encoding="utf-8")
    _save(slim)
    _write_book(slim)
    print(report["verdict"], report["entry_time_separation"], report["complete_strategy_economics"], flush=True)


def _body(retro, sub, econ_b, econ_m, controls, robust, unfilled, null_ok, separation_supported, improved, summaries) -> str:
    final_bad = [row for row in summaries if not row.get("final_ok_b") or row.get("open_n_b") or row.get("pending_n_b")]
    return "\n".join(
        [
            "Policy BもB+Mも採用していません。Paper V16 と Runtime は変更していません。submit/cancel/live = 0/0/0。",
            "",
            f"過去29日のPolicy Bを再実行し、{len(retro)}件を診断しました。10秒未満のBREAK_SUPPORT_FAILUREは {len(sub)}件です。",
            f"ENTRY時点で現値がすでに支持線以下だった割合は {controls['sub10_entry_current_already_failed']} です。",
            f"同一時刻の次行で割れた割合は {controls['sub10_same_timestamp_rate']} です。",
            f"主分類が実際の価格抜け（M3）である割合は {controls['sub10_primary_m3_rate']} です。",
            f"ENTRY Bidが支持線以下である割合は、10秒未満の失敗で {controls['sub10_bid_le_support_rate']}、IMPULSE_EXHAUSTEDで {controls['impulse_bid_le_support_rate']} です。",
            f"ENTRY時点の分離は {'SUPPORTED' if separation_supported else 'NOT_SUPPORTED'} です。",
            f"BのGrossは {econ_b['gross_pnl_yen']}円、事前宣言したB+MのGrossは {econ_m['gross_pnl_yen']}円です。経済判定は {'IMPROVED_IN_RESEARCH' if improved else 'NOT_IMPROVED'} です。",
            f"SESSION_EXIT_UNFILLEDは {len(unfilled)}件で、pnlとexit価格が空であることは {null_ok} です。Grossには含めていません。",
            f"final_okでない、または未決済が残ったセッションは {len(final_bad)}件です。",
            f"日付頑健性: 比較できた日 {robust['days_with_both_sub10_and_impulse']}、うちBid条件が失敗側で高い日 {robust['days_bid_flag_higher_on_sub10']}。",
            "この29日は探索標本です。後日のV16 Actualでの検証は PENDING です。",
        ]
    )


if __name__ == "__main__":
    main()
