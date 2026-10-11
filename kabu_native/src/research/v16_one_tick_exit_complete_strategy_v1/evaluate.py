"""One-tick support confirmation as a research exit. The sealed executor is not edited."""
from __future__ import annotations

import heapq
import json
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook

from research.current50_v16_capital_risk_policy_comparison_v1.compare import G, ResearchGrossCapExecutor, _max_gross
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _paper_sessions, _symbol
from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)
from research.low_price_risk_review import jpx_tick_size_yen
from small_paper.fixed_support_x1_session import _EPS, _finite_pos
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_one_tick_exit_complete_strategy_v1"
PRIOR = NATIVE / "results" / "research" / "current50_v16_historical_economic_expansion_v2" / "report.json"
KNOWN = (
    ("20261007", "am", 136, 11890.0),
    ("20261007", "pm", 76, 14410.0),
    ("20261008", "am", 225, -11760.0),
    ("20261008", "pm", 240, 10230.0),
    ("20261009", "am", 272, 42400.0),
    ("20261009", "pm", 219, -780.0),
)
EXPECTED_UNFILLED = {
    "20260817|PM|6265.T|83": 334500.0,
    "20260825|PM|6138.T|299": 208000.0,
    "20260902|PM|7376.T|463": 82300.0,
    "20260909|PM|4052.T|17211": 102100.0,
}
BUSY = (
    "paper_trade_checked_runner",
    "market_ingress_service",
    "run_core10_dynamic40_am_pm_daily_runner",
    "run_small_paper_pilot",
    "v1r_paper_primary_launcher",
)
RULE = (
    "支持線と呼値はENTRY時に凍結する。支持線はENTRY直前60秒の高値、呼値はjpx_tick_size_yen(支持線)の"
    "その他銘柄表（narrow_topix500=False）である。TOPIX500の狭い呼値表は使わない。"
    "CurrentPriceが支持線以下かつ支持線から1呼値未満のときは候補だけを記録し、EXITは発火しない。"
    "CurrentPriceが支持線-1呼値以下になった時点でBREAK_SUPPORT_FAILUREを発火する。1件の価格が支持線の上から一気にそこまで抜けた場合も、その時点で発火する。"
    "候補のあとCurrentPriceが支持線より上へ戻ったときは候補を消し、未発火に戻す。支持線自体は動かさない。"
    "1呼値割れはIMPULSE_EXHAUSTEDより先に判定する。候補のあいだもIMPULSE_EXHAUSTEDは発火できる。"
    "Session close（15:20開始、15:25未満）は候補より優先し、追加の呼値を待たない。"
    "CurrentPriceの判定とBid約定は分離する。約定は発火時点以降の最初の実行可能Bidである。"
    "価格が無効なら既存のFAIL_CLOSE_INVALID_DATAに従う。特別気配は既存のflushが除外する。"
    "Bidが無ければSESSION_EXIT_UNFILLEDとし、pnlとexit価格は空のままにする。約定扱いにはしない。"
)


def support_action(price: float, level: float, tick: float, impulse: bool) -> str:
    """Research exit priority. BREAK beats impulse. A mere touch does not."""
    if support_failure(price, level - tick):
        return "BREAK_SUPPORT_FAILURE"
    if impulse:
        return "IMPULSE_EXHAUSTED"
    if support_failure(price, level):
        return "CANDIDATE"
    if price > level:
        return "CLEAR"
    return "HOLD"


class OneTickExit(ResearchGrossCapExecutor):
    """Policy B plus a one-tick confirmation before the support exit."""

    def __init__(self) -> None:
        super().__init__()
        self.delay_notes: list[dict[str, Any]] = []
        self.tick_counts: Counter[float] = Counter()

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        super()._on_signal(sym, index, event_t, heap, flush_t)
        pos = self.open_pos.get(sym)
        if pos is None or int(pos.get("signal_index", -1)) != int(index) or "frozen_tick" in pos:
            return
        level = float(pos["initial_support"])
        tick = float(jpx_tick_size_yen(level))
        pos["frozen_tick"] = tick
        pos["support_candidate"] = False
        pos["candidate_t"] = None
        pos["delay_min_px"] = None
        self.tick_counts[tick] += 1

    def _touch_delay(self, pos: dict[str, Any], price: float) -> None:
        previous = pos.get("delay_min_px")
        pos["delay_min_px"] = price if previous is None else min(float(previous), price)

    def _note_delay(self, pos: dict[str, Any], event_t: float, reason: str) -> None:
        started = pos.get("candidate_t")
        if started is None:
            return
        min_px = pos.get("delay_min_px")
        adverse = None if min_px is None else (float(pos["entry_px"]) - float(min_px)) * 100.0
        self.delay_notes.append(
            {
                "signal_uid": pos.get("signal_uid"),
                "wait_sec": float(event_t) - float(started),
                "adverse_yen": adverse,
                "exit_reason": reason,
            }
        )

    def _on_path(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        pos = self.open_pos.get(sym)
        st = self.syms[sym]
        if pos is None or pos.get("exiting") or int(pos["next_index"]) != index:
            return
        price = float(st.px[index])
        if not _finite_pos(price):
            found = self._first_bid(sym, event_t)
            if found is None:
                pos["invalid_pending"] = True
            else:
                self._schedule_exit(pos, sym, index, "FAIL_CLOSE_INVALID_DATA", event_t, found, heap)
            return
        if reconfirm(
            bool(st.vol10[index]),
            bool(st.vol30[index]),
            bool(st.buy[index]),
            float(st.classified[index]),
            bool(st.tick_accel[index]),
            bool(st.price_break[index]),
        ):
            pos["last_reconfirm_t"] = event_t
            observed = ratchet(float(st.pre_high[index]), float(pos["observed_support"]))
            if observed > float(pos["observed_support"]):
                pos["observed_support"] = float(observed)
                pos["reconfirm_raises"] += 1
            if float(pos["support"]) != float(pos["initial_support"]):
                self.support_move_n += 1
        level = float(pos["support"])
        tick = float(pos.get("frozen_tick") or jpx_tick_size_yen(level))
        impulse = exhausted(
            participation_lost(float(st.classified[index]), float(st.ask10[index]), float(st.bid10[index])),
            activity_lost(bool(st.vol10[index]), bool(st.vol30[index]), bool(st.tick_accel[index])),
            event_t - float(pos["last_reconfirm_t"]),
        )
        action = support_action(price, level, tick, impulse)
        reason = None
        if action == "BREAK_SUPPORT_FAILURE":
            self._touch_delay(pos, price)
            self._note_delay(pos, event_t, action)
            reason = action
        elif action == "CANDIDATE":
            pos["support_candidate"] = True
            if pos.get("candidate_t") is None:
                pos["candidate_t"] = event_t
            self._touch_delay(pos, price)
        elif action == "CLEAR":
            if pos.get("candidate_t") is not None:
                self._note_delay(pos, event_t, "RECOVERED_UNFIRED")
            pos["support_candidate"] = False
            pos["candidate_t"] = None
            pos["delay_min_px"] = None
        elif action == "IMPULSE_EXHAUSTED":
            if support_failure(price, level):
                pos["support_candidate"] = True
                if pos.get("candidate_t") is None:
                    pos["candidate_t"] = event_t
                self._touch_delay(pos, price)
            self._note_delay(pos, event_t, action)
            reason = action
        if reason is not None:
            found = self._first_bid(sym, event_t)
            if found is None:
                pos["pending_reason"] = reason
                pos["exiting"] = True
                pos["exit_trigger_t"] = event_t
            else:
                self._schedule_exit(pos, sym, index, reason, event_t, found, heap)
            return
        nxt = index + 1
        if nxt < st.n and float(st.t[nxt]) <= flush_t + _EPS:
            pos["next_index"] = nxt
            heapq.heappush(heap, (float(st.t[nxt]), 0, sym, "PATH", nxt))
        else:
            pos["next_index"] = nxt


def _self_check() -> None:
    level = 1000.0
    tick = 1.0
    if support_action(1000.0, level, tick, False) != "CANDIDATE":
        raise RuntimeError("touch_must_wait")
    if support_action(999.5, level, tick, False) != "CANDIDATE":
        raise RuntimeError("fractional_tick_must_wait")
    if support_action(999.0, level, tick, False) != "BREAK_SUPPORT_FAILURE":
        raise RuntimeError("one_tick_must_fire")
    if support_action(990.0, level, tick, True) != "BREAK_SUPPORT_FAILURE":
        raise RuntimeError("gap_beats_impulse")
    if support_action(1000.0, level, tick, True) != "IMPULSE_EXHAUSTED":
        raise RuntimeError("impulse_during_candidate")
    if support_action(1001.0, level, tick, False) != "CLEAR":
        raise RuntimeError("recovery_clears")
    if jpx_tick_size_yen(3000) != 1.0 or jpx_tick_size_yen(3001) != 5.0 or jpx_tick_size_yen(5001) != 10.0:
        raise RuntimeError("tick_table")


def _busy() -> list[str]:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| ForEach-Object { $_.CommandLine }"
    )
    try:
        text = subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True, errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return ["PROCESS_CHECK_FAILED"]
    return sorted({item for item in BUSY if item in text})


def _window(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 16


def _historical_days() -> list[str]:
    body = json.loads(PRIOR.read_text(encoding="utf-8"))
    return [row["date"] for row in body.get("eligibility") or [] if row.get("class") == "RETROSPECTIVE_V16_COUNTERFACTUAL"]


def _exposure(rows: list[dict[str, Any]]) -> tuple[float, int]:
    open_px: dict[str, float] = {}
    peak = 0.0
    breaches = 0
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if row.get("event") == "ENTRY":
            open_px[symbol] = float(row["fill_price"]) * 100.0
            if sum(open_px.values()) > G + 1e-6:
                breaches += 1
        elif row.get("event") == "EXIT":
            open_px.pop(symbol, None)
        peak = max(peak, sum(open_px.values()))
    return peak, breaches


def _rows(book: ResearchGrossCapExecutor, day: str, kind: str, policy: str) -> list[dict[str, Any]]:
    out = []
    for trade in book.trades:
        trigger = float(trade.get("exit_trigger_t") or trade["exit_t"])
        out.append(
            {
                "policy": policy,
                "day": day,
                "kind": kind,
                "symbol": trade["symbol"],
                "uid": trade["signal_uid"],
                "reason": trade["reason"],
                "pnl": float(trade["pnl_yen"]),
                "hold": float(trade["hold_sec"]),
                "exit_t": float(trade["exit_t"]),
                "entry_t": float(trade["entry_t"]),
                "trigger_t": trigger,
                "fill_lag": float(trade["exit_t"]) - trigger,
                "notional": float(trade["entry_px"]) * 100.0,
            }
        )
    return out


def _unfilled(book: ResearchGrossCapExecutor, day: str, kind: str, policy: str) -> list[dict[str, Any]]:
    rows = []
    for row in book.unfilled:
        px = row.get("entry_px")
        rows.append(
            {
                "policy": policy,
                "day": day,
                "kind": kind,
                "symbol": row.get("symbol"),
                "uid": row.get("signal_uid"),
                "pnl_yen": row.get("pnl_yen"),
                "exit_px": row.get("exit_px"),
                "entry_px": px,
                "notional": None if px is None else float(px) * 100.0,
                "reason": row.get("reason"),
                "slot_release": True,
            }
        )
    return rows


def _feed(day: str, kind: str, seq_after: int, attach: str) -> dict[str, Any]:
    capture = _capture_dir(day)
    if capture is None:
        return {"error": "NO_CAPTURE"}
    left = ResearchGrossCapExecutor()
    right = OneTickExit()
    for book in (left, right):
        book.notifier = None
        book.ledger_path = None
        bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
        if kind == "pm":
            book.bind_safe_pm_flatten(day=day)
        else:
            book.bind_production_schedule(day=day, kind="am")
    if len(left.admission_membership) != 50 or len(right.admission_membership) != 50:
        return {"error": f"ADMISSION_{len(left.admission_membership)}_{len(right.admission_membership)}"}
    fed = 0
    for part in sorted(capture.glob("push_part_*.jsonl")):
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
                if not left._closed:
                    left.on_market_event({"symbol": symbol, "payload": dict(payload)})
                if not right._closed:
                    right.on_market_event({"symbol": symbol, "payload": dict(payload)})
                fed += 1
                if fed % 200000 == 0:
                    print(f"fed {day} {kind} {fed}", flush=True)
                if left._closed and right._closed:
                    break
        if left._closed and right._closed:
            break
    for book in (left, right):
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
    perf_l = left.x1_performance()
    perf_r = right.x1_performance()
    peak_b, breach_b = _exposure(left.ledger)
    peak_e, breach_e = _exposure(right.ledger)
    print(
        f"ONE {day} {kind} B {perf_l['trades_n']} {perf_l['net_pnl_yen']} E {perf_r['trades_n']} {perf_r['net_pnl_yen']} unfilled {len(left.unfilled)} {len(right.unfilled)}",
        flush=True,
    )
    return {
        "day": day,
        "kind": kind,
        "fed": fed,
        "final_ok_b": bool(perf_l["final_ok"]),
        "final_ok_e": bool(perf_r["final_ok"]),
        "open_b": int(perf_l["open_n"]),
        "open_e": int(perf_r["open_n"]),
        "pending_b": int(perf_l["exit_pending_n"]),
        "pending_e": int(perf_r["exit_pending_n"]),
        "max_b": peak_b,
        "max_e": peak_e,
        "max_b_check": _max_gross(left.ledger),
        "breach_b": breach_b,
        "breach_e": breach_e,
        "yen_deny_b": int(left.gross_cap_denied_n),
        "yen_deny_e": int(right.gross_cap_denied_n),
        "above_b": sum(1 for row in left.trades if float(row["entry_px"]) * 100.0 > G + 1e-6),
        "above_e": sum(1 for row in right.trades if float(row["entry_px"]) * 100.0 > G + 1e-6),
        "ticks": {str(key): int(value) for key, value in right.tick_counts.items()},
        "rows_b": _rows(left, day, kind, "B"),
        "rows_e": _rows(right, day, kind, "E"),
        "unfilled_b": _unfilled(left, day, kind, "B"),
        "unfilled_e": _unfilled(right, day, kind, "E"),
        "delays": list(right.delay_notes),
    }


def _dd(rows: list[dict[str, Any]], key: str) -> float:
    ordered = sorted(rows, key=lambda row: (float(row[key]), str(row["uid"])))
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in ordered:
        equity += float(row["pnl"])
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _median(values: list[float]) -> Optional[float]:
    return None if not values else float(statistics.median(values))


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnl"]) for row in rows]
    profit = sum(pnl for pnl in pnls if pnl > 0)
    loss = sum(pnl for pnl in pnls if pnl < 0)
    by_reason: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_reason[row["reason"]].append(float(row["pnl"]))
    holds = [float(row["hold"]) for row in rows]
    return {
        "trades": len(pnls),
        "gross": round(sum(pnls), 6),
        "pf": None if loss >= 0 else profit / abs(loss),
        "win_rate": (sum(pnl > 0 for pnl in pnls) / len(pnls)) if pnls else None,
        "max_dd_exit": _dd(rows, "exit_t"),
        "max_dd_entry": _dd(rows, "entry_t"),
        "max_loss": min(pnls) if pnls else None,
        "median_hold": _median(holds),
        "reasons": {name: {"n": len(vals), "gross": round(sum(vals), 6)} for name, vals in sorted(by_reason.items())},
        "costs": [
            {
                "per_side": cost,
                "pnl": round(sum(pnls) - cost * 2 * len(pnls), 6),
                "label": "SEALED_NO_FEE" if cost == 0 else "ASSUMED_NOT_OFFICIAL_NET",
            }
            for cost in (0, 10, 50, 100)
        ],
    }


def _daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row["day"]] += float(row["pnl"])
        counts[row["day"]] += 1
    return [{"day": day, "trades": counts[day], "gross": round(totals[day], 6)} for day in sorted(totals)]


def _save(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = OUT / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(OUT / "report.json")


def _book(report: dict[str, Any]) -> None:
    book = Workbook()
    names = ("Summary", "Daily", "Costs", "Reasons", "Unfilled", "Shift", "Symbols")
    first = True
    for name in names:
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        sheet.append(["item", "value"])
    for key, value in (report.get("summary") or {}).items():
        book["Summary"].append([key, str(value)])
    for row in report.get("daily") or []:
        book["Daily"].append([f"{row['policy']} {row['day']}", f"{row['trades']} {row['gross']}"])
    for row in report.get("costs") or []:
        book["Costs"].append([f"{row['policy']} {row['per_side']}", row["pnl"]])
    for row in report.get("reasons") or []:
        book["Reasons"].append([f"{row['policy']} {row['reason']}", f"{row['n']} {row['gross']}"])
    for row in report.get("unfilled") or []:
        book["Unfilled"].append([f"{row['policy']} {row.get('uid')}", row.get("notional")])
    for key, value in (report.get("shift") or {}).items():
        book["Shift"].append([key, str(value)])
    for row in report.get("symbols") or []:
        book["Symbols"].append([row["symbol"], row["delta"]])
    book.save(OUT / "audit.xlsx")


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"VERDICT: {report['verdict']}",
            f"NEXT: {report['next']}",
            "",
            report.get("body") or "",
            "",
            "Paper V16、Runtime、Selector、ENTRY、EXIT、Captureは変更していません。",
            "submit/cancel/live = 0/0/0。",
            "この29日は独立holdoutではありません。独立した通常Captureでの再検証は未完了です。",
            "Policy BもこのEXITもPaperへは採用していません。",
        ]
    ) + "\n"


def _retro_sessions(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [session for session in report["sessions"] if session.get("study") == "RETRO" and "rows_b" in session]


def _finish(report: dict[str, Any]) -> None:
    errors = [session for session in report["sessions"] if session.get("error")]
    retro = _retro_sessions(report)
    retro_b = [row for session in retro for row in session["rows_b"]]
    retro_e = [row for session in retro for row in session["rows_e"]]
    known_b = [row for session in report["sessions"] if session.get("study") == "KNOWN" and "rows_b" in session for row in session["rows_b"]]
    econ_b = _econ(retro_b)
    econ_e = _econ(retro_e)
    unfilled_b = [row for session in retro for row in session["unfilled_b"]]
    unfilled_e = [row for session in retro for row in session["unfilled_e"]]
    got_unfilled = {str(row["uid"]): float(row["notional"] or 0) for row in unfilled_b}
    phase1_ok = got_unfilled == EXPECTED_UNFILLED and all(row.get("pnl_yen") is None and row.get("exit_px") is None for row in unfilled_b)
    baseline_ok = (
        not errors
        and econ_b["trades"] == 9818
        and abs(econ_b["gross"] - 712000.0) < 0.05
        and phase1_ok
    )
    known_mismatch = []
    for day, kind, trades, gross in KNOWN:
        got = [row for row in known_b if row["day"] == day and row["kind"] == kind]
        got_gross = round(sum(float(row["pnl"]) for row in got), 6)
        if len(got) != trades or abs(got_gross - gross) >= 0.05:
            known_mismatch.append(
                {"day": day, "kind": kind, "trades": len(got), "gross": got_gross, "expected_trades": trades, "expected_gross": gross}
            )
    null_e = all(row.get("pnl_yen") is None and row.get("exit_px") is None for row in unfilled_e)
    notional_b = sum(float(row["notional"] or 0) for row in unfilled_b)
    notional_e = sum(float(row["notional"] or 0) for row in unfilled_e)
    max_b = max((float(session["max_b"]) for session in retro), default=0.0)
    max_e = max((float(session["max_e"]) for session in retro), default=0.0)
    breach_e = sum(int(session["breach_e"]) for session in retro)
    above_e = sum(int(session["above_e"]) for session in retro)
    yen_deny_b = sum(int(session["yen_deny_b"]) for session in retro)
    yen_deny_e = sum(int(session["yen_deny_e"]) for session in retro)
    by_b = {row["uid"]: row for row in retro_b}
    by_e = {row["uid"]: row for row in retro_e}
    only_b = [row for row in retro_b if row["uid"] not in by_e]
    only_e = [row for row in retro_e if row["uid"] not in by_b]
    shared = [uid for uid in by_b if uid in by_e]
    shared_delta = sum(float(by_e[uid]["pnl"]) - float(by_b[uid]["pnl"]) for uid in shared)
    release_lag = [float(by_e[uid]["exit_t"]) - float(by_b[uid]["exit_t"]) for uid in shared]
    winners_lost = [row for row in only_b if float(row["pnl"]) > 0]
    losers_lost = [row for row in only_b if float(row["pnl"]) < 0]
    delays = [note for session in retro for note in session.get("delays") or []]
    fired = [note for note in delays if note.get("exit_reason") in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED")]
    recovered = [note for note in delays if note.get("exit_reason") == "RECOVERED_UNFIRED"]
    adverse = [float(note["adverse_yen"]) for note in fired if note.get("adverse_yen") is not None]
    recovered_adverse = [float(note["adverse_yen"]) for note in recovered if note.get("adverse_yen") is not None]
    risk_worse = len(unfilled_e) > len(unfilled_b) or notional_e > notional_b + 1e-6 or not null_e
    g_ok = max_e <= G + 1e-6 and breach_e == 0 and above_e == 0
    gross_better = econ_e["gross"] > econ_b["gross"]
    dd_not_worse = econ_e["max_dd_exit"] >= econ_b["max_dd_exit"] - 1e-6
    open_left = sum(int(session["open_b"]) + int(session["open_e"]) + int(session["pending_b"]) + int(session["pending_e"]) for session in retro)
    if not baseline_ok:
        verdict = "V16_ONE_TICK_EXIT_EVALUATION_BLOCKED_V1"
        nxt = "REPAIR_BASELINE_B_BEFORE_COMPARING_THE_EXIT"
    elif gross_better and dd_not_worse and g_ok and not risk_worse and open_left == 0:
        verdict = "V16_ONE_TICK_EXIT_ECONOMIC_IMPROVEMENT_FOUND_V1"
        nxt = "FREEZE_THIS_ONE_TICK_RULE_AND_REPLAY_IT_ON_A_LATER_INDEPENDENT_CAPTURE"
    else:
        verdict = "V16_ONE_TICK_EXIT_NO_ECONOMIC_IMPROVEMENT_V1"
        nxt = "CLOSE_THE_ONE_TICK_EXIT_AND_STUDY_BREAKOUT_QUALITY_AND_100_SHARE_FILL_COST"
    by_symbol_b: dict[str, float] = defaultdict(float)
    by_symbol_e: dict[str, float] = defaultdict(float)
    for row in retro_b:
        by_symbol_b[row["symbol"]] += float(row["pnl"])
    for row in retro_e:
        by_symbol_e[row["symbol"]] += float(row["pnl"])
    delta = sorted(
        ({"symbol": symbol, "delta": round(by_symbol_e[symbol] - by_symbol_b.get(symbol, 0.0), 6)} for symbol in set(by_symbol_b) | set(by_symbol_e)),
        key=lambda item: item["delta"],
    )
    daily_b = {row["day"]: row for row in _daily(retro_b)}
    daily_e = {row["day"]: row for row in _daily(retro_e)}
    days = sorted(set(daily_b) | set(daily_e))
    daily_rows = []
    worse_days = []
    better_days = []
    for day in days:
        left = daily_b.get(day, {"trades": 0, "gross": 0.0})
        right = daily_e.get(day, {"trades": 0, "gross": 0.0})
        change = round(float(right["gross"]) - float(left["gross"]), 6)
        daily_rows.append({"policy": "B", "day": day, "trades": left["trades"], "gross": left["gross"]})
        daily_rows.append({"policy": "E", "day": day, "trades": right["trades"], "gross": right["gross"]})
        if change < -1e-6:
            worse_days.append({"day": day, "delta": change})
        elif change > 1e-6:
            better_days.append({"day": day, "delta": change})
    ticks: Counter[str] = Counter()
    for session in retro:
        ticks.update(session.get("ticks") or {})
    new_unfilled = [row for row in unfilled_e if row["uid"] not in {item["uid"] for item in unfilled_b}]
    body = "\n".join(
        [
            "B+Eは研究候補の評価です。現行EXITの構造的失敗が証明されたものではありません。",
            RULE,
            f"Phase 1: 未約定4件の再確認は {phase1_ok} です。Bの未約定は {[(row['uid'], row['notional']) for row in unfilled_b]}。pnlとexit価格は空です。原因はPMの15:20以上15:25未満に実行可能Bidが無かったことです。Slotは解放済みです。Grossは確定約定だけの合計です。残存リスクはB {notional_b}円、B+E {notional_e}円の未約定ENTRY金額です。final_okがtrueでも全建玉の約定可能な決済を意味しません。",
            f"DDはEXIT約定時刻の順です。BのEXIT順DDは {econ_b['max_dd_exit']}円、ENTRY順DDは {econ_b['max_dd_entry']}円です。過去の-25450円と-25250円の差は、この2つの順序の差として残しています。",
            f"Baseline Bは {econ_b['trades']}件、Gross {econ_b['gross']}円、PF {econ_b['pf']}、勝率 {econ_b['win_rate']}、最大損失 {econ_b['max_loss']}円、最大同時建玉 {max_b}円、G超過 {sum(int(session['breach_b']) for session in retro)}件、円拒否 {yen_deny_b}件です。29日Baselineとの一致は {baseline_ok} です。",
            f"B+Eは {econ_e['trades']}件、Gross {econ_e['gross']}円、PF {econ_e['pf']}、勝率 {econ_e['win_rate']}、EXIT順DD {econ_e['max_dd_exit']}円、ENTRY順DD {econ_e['max_dd_entry']}円、最大損失 {econ_e['max_loss']}円、保有中央値 {econ_e['median_hold']}秒、最大同時建玉 {max_e}円、G超過 {breach_e}件、単銘柄G超過ENTRY {above_e}件、円拒否 {yen_deny_e}件です。",
            f"コスト後の損益は片道0、10、50、100円の順です。Bは {[row['pnl'] for row in econ_b['costs']]}、B+Eは {[row['pnl'] for row in econ_e['costs']]} です。0円は手数料の無い封印式です。10円以上は仮定費用であり公式Netではありません。AskとBidに含まれるスプレッドは再加算していません。",
            f"EXIT原因Bは {econ_b['reasons']}、B+Eは {econ_e['reasons']} です。",
            f"未約定はB {len(unfilled_b)}件、B+E {len(unfilled_e)}件です。B+Eで新たに未約定になったUIDは {[row['uid'] for row in new_unfilled]} です。Session終了時点のopenとpendingの合計は {open_left} です。",
            f"共通 {len(shared)}件の損益差合計は {round(shared_delta, 6)}円です。Slot解放時刻の差（B+Eのexit時刻からBのexit時刻を引いた値）の中央値は {_median(release_lag)}秒、最大は {max(release_lag) if release_lag else None}秒です。",
            f"Bにだけある取引は {len(only_b)}件、Gross {round(sum(float(row['pnl']) for row in only_b), 6)}円です。そのうち利益は {len(winners_lost)}件 {round(sum(float(row['pnl']) for row in winners_lost), 6)}円、損失は {len(losers_lost)}件 {round(sum(float(row['pnl']) for row in losers_lost), 6)}円です。B+Eにだけある取引は {len(only_e)}件、Gross {round(sum(float(row['pnl']) for row in only_e), 6)}円です。",
            f"確認待ちのあとEXITした記録は {len(fired)}件です。待ちのあいだの最大逆行は {max(adverse) if adverse else None}円、中央値は {_median(adverse)}円です。支持線上へ戻って未発火になった記録は {len(recovered)}件、そのあいだの最大逆行は {max(recovered_adverse) if recovered_adverse else None}円です。",
            f"日次で悪化した日は {worse_days}、改善した日は {better_days} です。銘柄差の悪化上位は {delta[:5]}、改善上位は {delta[-5:]} です。",
            f"凍結した呼値の件数は {dict(ticks)} です。",
            f"既知3日のB再実行の不一致は {known_mismatch} です。この3日は29日の合計に入れていません。Actualとの一致は要求していません。",
            "取引数が減って高コスト時の赤字が縮んだだけでは、経済的優位性とは扱いません。急落時に確認待ちが損失を広げるため、このEXITを安全な決済とは扱いません。",
            "判定は、29日のGrossがBを上回り、EXIT順の最大DDが悪化せず、最大同時建玉が100万円以内で、未約定の件数とENTRY金額が増えない場合だけ改善とします。",
        ]
    )
    report.update(
        {
            "verdict": verdict,
            "next": nxt,
            "baseline_ok": baseline_ok,
            "phase1_unfilled_ok": phase1_ok,
            "known_mismatch": known_mismatch,
            "body": body,
            "summary": {
                "b_trades": econ_b["trades"],
                "b_gross": econ_b["gross"],
                "b_pf": econ_b["pf"],
                "b_win_rate": econ_b["win_rate"],
                "b_dd_exit": econ_b["max_dd_exit"],
                "b_dd_entry": econ_b["max_dd_entry"],
                "b_max_loss": econ_b["max_loss"],
                "b_max_gross": max_b,
                "b_yen_deny": yen_deny_b,
                "e_trades": econ_e["trades"],
                "e_gross": econ_e["gross"],
                "e_pf": econ_e["pf"],
                "e_win_rate": econ_e["win_rate"],
                "e_dd_exit": econ_e["max_dd_exit"],
                "e_dd_entry": econ_e["max_dd_entry"],
                "e_max_loss": econ_e["max_loss"],
                "e_median_hold": econ_e["median_hold"],
                "e_max_gross": max_e,
                "e_g_breaches": breach_e,
                "e_above_g": above_e,
                "e_yen_deny": yen_deny_e,
                "risk_worse": risk_worse,
                "g_ok": g_ok,
                "gross_better": gross_better,
                "dd_not_worse": dd_not_worse,
                "unfilled_b": len(unfilled_b),
                "unfilled_e": len(unfilled_e),
                "unfilled_notional_b": notional_b,
                "unfilled_notional_e": notional_e,
            },
            "daily": daily_rows,
            "costs": [{"policy": "B", **row} for row in econ_b["costs"]] + [{"policy": "E", **row} for row in econ_e["costs"]],
            "reasons": [
                {"policy": policy, "reason": name, **vals}
                for policy, econ in (("B", econ_b), ("E", econ_e))
                for name, vals in econ["reasons"].items()
            ],
            "unfilled": unfilled_b + unfilled_e,
            "shift": {
                "b_only": len(only_b),
                "e_only": len(only_e),
                "shared": len(shared),
                "shared_pnl_delta": round(shared_delta, 6),
                "b_only_gross": round(sum(float(row["pnl"]) for row in only_b), 6),
                "e_only_gross": round(sum(float(row["pnl"]) for row in only_e), 6),
                "winners_lost_n": len(winners_lost),
                "winners_lost_gross": round(sum(float(row["pnl"]) for row in winners_lost), 6),
                "losers_removed_n": len(losers_lost),
                "losers_removed_gross": round(sum(float(row["pnl"]) for row in losers_lost), 6),
                "release_lag_median_sec": _median(release_lag),
                "release_lag_max_sec": max(release_lag) if release_lag else None,
                "delay_exit_n": len(fired),
                "delay_adverse_max": max(adverse) if adverse else None,
                "delay_adverse_median": _median(adverse),
                "recovered_n": len(recovered),
                "recovered_adverse_max": max(recovered_adverse) if recovered_adverse else None,
                "worse_days": worse_days,
                "better_days": better_days,
                "tick_counts": dict(ticks),
            },
            "symbols": delta[:8] + delta[-8:],
            "rule": RULE,
            "tick_table": "JPX other issues, narrow_topix500=False",
            "policy_adopted": False,
            "independent_validation": "NOT_DONE",
            "later_sample": "PENDING",
            "paper_runtime_unchanged": True,
            "submit_cancel_live": "0/0/0",
            "running": False,
            "errors": errors,
        }
    )
    text = _markdown(report)
    report["markdown"] = text
    (OUT / "report.md").write_text(text, encoding="utf-8")
    slim = {key: value for key, value in report.items() if key != "sessions"}
    _save(slim)
    _book(slim)
    print(verdict, flush=True)


def _deferred(reason: str) -> None:
    report = {
        "verdict": "V16_ONE_TICK_EXIT_EVALUATION_BLOCKED_V1",
        "next": "RETRY_AFTER_PAPER_AND_INGRESS_HAVE_EXITED",
        "body": f"Paper、Market Ingress、Capture、daily runnerのいずれかが稼働中または状態不明です。大量Replayは開始していません。理由: {reason}",
        "policy_adopted": False,
        "paper_runtime_unchanged": True,
        "submit_cancel_live": "0/0/0",
        "independent_validation": "NOT_DONE",
        "running": False,
    }
    text = _markdown(report)
    report["markdown"] = text
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text(text, encoding="utf-8")
    _save(report)
    _book(report)
    print(report["verdict"], flush=True)


def main() -> None:
    _self_check()
    busy = _busy()
    if busy or _window(datetime.now().astimezone()):
        _deferred(",".join(busy) if busy else "PAPER_WINDOW")
        return
    report: dict[str, Any] = {"running": True, "done": [], "sessions": [], "rule": RULE, "submit_cancel_live": "0/0/0"}
    if (OUT / "report.json").is_file():
        prior = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
        if prior.get("running") and isinstance(prior.get("sessions"), list):
            report = prior
    done = set(report.get("done") or [])
    jobs = [(day, kind, "RETRO", 0, _attach_text(day, kind)) for day in _historical_days() for kind in ("am", "pm")]
    for day, kind, _trades, _gross in KNOWN:
        sessions = [row for row in _paper_sessions(day) if row["kind"] == kind and "V16" in str(row["activation_id"])]
        watermark = int(sessions[0]["watermark_seq"]) if sessions else 0
        jobs.append((day, kind, "KNOWN", watermark, ""))
    for day, kind, study, watermark, attach in jobs:
        token = f"{study}:{day}:{kind}"
        if token in done:
            continue
        if _busy() or _window(datetime.now().astimezone()):
            report["running"] = True
            _save(report)
            print("DEFERRED", flush=True)
            return
        result = _feed(day, kind, watermark, attach)
        result["study"] = study
        report["sessions"].append(result)
        done.add(token)
        report["done"] = sorted(done)
        report["running"] = True
        _save(report)
    _finish(report)


if __name__ == "__main__":
    main()
