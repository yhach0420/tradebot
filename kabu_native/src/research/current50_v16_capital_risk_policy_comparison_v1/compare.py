"""Compare V16 with a research-only gross cap on the two parity days.

Runtime files are not modified. No orders are sent.
"""
from __future__ import annotations

import heapq
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import (
    V16_DAYS,
    _capture_dir,
    _compare,
    _new_executor,
    _paper_sessions,
    _symbol,
)
from small_paper.fixed_support_x1_session import (
    MAX_CONCURRENT,
    FixedSupportX1SessionExecutor,
    _EPS,
    _finite_pos,
)

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "current50_v16_capital_risk_policy_comparison_v1"
G = 1_000_000.0
SHARES = 100
EXPECTED = {
    "closed_trades": 1019,
    "gross_pnl_yen": 122480.0,
    "max_gross_notional_yen": 6877800.0,
    "entries_above_g": 170,
    "cap_blocked": 815,
    "reentry": 856,
    "unfilled": 0,
}
ASSUMED_COST_PER_SIDE_YEN = (0, 10, 50, 100)


class ResearchGrossCapExecutor(FixedSupportX1SessionExecutor):
    """V16 state machine plus an entry-time yen cap. No new exit."""

    def __init__(self) -> None:
        super().__init__()
        self.gross_cap = G
        self.gross_cap_denied_n = 0
        self.single_name_over_g_n = 0
        self.book_sum_over_g_n = 0
        self.gross_state_unknown_n = 0

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
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


def _live_final_ok() -> list[dict[str, Any]]:
    rows = []
    for day in V16_DAYS:
        for session in _paper_sessions(day):
            path = NATIVE / "results" / "small_paper" / day / session["session_dir"] / "small_paper_summary.json"
            body = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            x1 = body.get("x1_performance") if isinstance(body.get("x1_performance"), dict) else {}
            rows.append(
                {
                    "day": day,
                    "session_dir": session["session_dir"],
                    "kind": session["kind"],
                    "actual_final_ok": x1.get("final_ok"),
                    "actual_open_n": x1.get("open_n"),
                    "actual_entry_n": x1.get("entry_n"),
                    "actual_exit_n": x1.get("exit_n"),
                }
            )
    return rows


def _replay(day: str, *, gross_cap: bool) -> list[dict[str, Any]]:
    capture_dir = _capture_dir(day)
    if capture_dir is None:
        return [{"day": day, "error": "NO_CAPTURE"}]
    out = []
    for session in _paper_sessions(day):
        if session["activation_id"] != "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16" or not session["ledger"]:
            continue
        kind = session["kind"] or "am"
        book = ResearchGrossCapExecutor() if gross_cap else _new_executor(day, kind)
        if gross_cap:
            book.notifier = None
            book.ledger_path = None
            from small_paper.paper_session_executor import bind_outer_registration

            bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
            if kind == "pm":
                book.bind_safe_pm_flatten(day=day)
            else:
                book.bind_production_schedule(day=day, kind="am")
        fed = 0
        for part in sorted(capture_dir.glob("push_part_*.jsonl")):
            with part.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if rec.get("kind") not in (None, "market_push"):
                        continue
                    seq = int(rec.get("sequence") or 0)
                    if seq <= int(session["watermark_seq"]):
                        continue
                    payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                    if not payload:
                        continue
                    payload = dict(payload)
                    received = rec.get("received_at")
                    if received:
                        payload.setdefault("received_at", received)
                        payload.setdefault("recorded_at", received)
                    symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                    if not symbol:
                        continue
                    book.on_market_event({"symbol": symbol, "payload": payload})
                    fed += 1
                    if book._closed:
                        break
            if book._closed:
                break
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
        perf = book.x1_performance()
        book.final_summary_ok = bool(perf["final_ok"])
        compared = _compare(session["ledger"], book.ledger)
        out.append(
            {
                "day": day,
                "kind": kind,
                "session_dir": session["session_dir"],
                "fed": fed,
                "parity": compared["equal"],
                "actual_rows": compared["actual_rows"],
                "replay_rows": compared["replay_rows"],
                "first_mismatches": compared["first_mismatches"],
                "final_ok": bool(perf["final_ok"]),
                "open_n": int(perf["open_n"]),
                "entry_n": int(perf["entry_n"]),
                "exit_n": int(perf["exit_n"]),
                "cap_blocked": int(book.counts.get("cap") or 0),
                "same_symbol_blocked": int(book.counts.get("same_symbol") or 0),
                "occupancy_blocked": int(book.counts.get("occupancy") or 0),
                "reentry": int(book.counts.get("reentry") or 0),
                "session_flat": int(book.counts.get("session_flat") or 0),
                "unfilled": len(book.unfilled),
                "gross_cap_denied": int(getattr(book, "gross_cap_denied_n", 0)),
                "single_name_over_g": int(getattr(book, "single_name_over_g_n", 0)),
                "book_sum_over_g": int(getattr(book, "book_sum_over_g_n", 0)),
                "gross_state_unknown": int(getattr(book, "gross_state_unknown_n", 0)),
                "ledger": book.ledger,
            }
        )
        print(
            f"{'B' if gross_cap else 'A'} {day} {kind} parity {compared['equal']} final_ok {perf['final_ok']} trades {perf['trades_n']}",
            flush=True,
        )
    return out


def _closed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exits = {row.get("signal_uid"): row for row in rows if row.get("event") == "EXIT" and row.get("pnl_yen") is not None}
    out = []
    for row in rows:
        if row.get("event") != "ENTRY":
            continue
        exit_row = exits.get(row.get("signal_uid"))
        if exit_row is None:
            continue
        notional = float(row["fill_price"]) * SHARES
        out.append(
            {
                "signal_uid": row.get("signal_uid"),
                "symbol": row.get("symbol"),
                "notional": notional,
                "pnl": float(exit_row["pnl_yen"]),
                "hold_sec": float(exit_row.get("exit_fill_timestamp") or 0.0) - float(row.get("fill_timestamp") or 0.0),
                "entry_t": float(row.get("fill_timestamp") or 0.0),
                "above_g": notional > G + 1e-6,
            }
        )
    return out


def _max_gross(rows: list[dict[str, Any]]) -> float:
    open_px: dict[str, float] = {}
    peak = 0.0
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if row.get("event") == "ENTRY":
            open_px[symbol] = float(row["fill_price"]) * SHARES
        elif row.get("event") == "EXIT":
            open_px.pop(symbol, None)
        peak = max(peak, sum(open_px.values()))
    return peak


def _drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _summary(name: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    ledger: list[dict[str, Any]] = []
    for session in sessions:
        for row in session["ledger"]:
            ledger.append({**row, "day": session["day"], "kind": session["kind"]})
    closed = _closed(ledger)
    pnls = [row["pnl"] for row in closed]
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = sum(pnl for pnl in pnls if pnl < 0)
    bands = _bands(closed)
    return {
        "policy": name,
        "closed_trades": len(closed),
        "gross_pnl_yen": round(sum(pnls), 6),
        "profit_factor_gross": None if gross_loss >= 0 else gross_profit / abs(gross_loss),
        "max_drawdown_yen": _drawdown(pnls),
        "win_rate": (sum(pnl > 0 for pnl in pnls) / len(pnls)) if pnls else None,
        "max_gross_notional_yen": max((_max_gross(session["ledger"]) for session in sessions), default=0.0),
        "entries_above_g": sum(1 for row in closed if row["above_g"]),
        "cap_blocked": sum(session["cap_blocked"] for session in sessions),
        "gross_cap_denied": sum(session["gross_cap_denied"] for session in sessions),
        "single_name_over_g": sum(session["single_name_over_g"] for session in sessions),
        "book_sum_over_g": sum(session["book_sum_over_g"] for session in sessions),
        "gross_state_unknown": sum(session["gross_state_unknown"] for session in sessions),
        "same_symbol_blocked": sum(session["same_symbol_blocked"] for session in sessions),
        "occupancy_blocked": sum(session["occupancy_blocked"] for session in sessions),
        "reentry": sum(session["reentry"] for session in sessions),
        "session_flat": sum(session["session_flat"] for session in sessions),
        "unfilled": sum(session["unfilled"] for session in sessions),
        "final_ok_sessions": sum(1 for session in sessions if session["final_ok"]),
        "parity_sessions": sum(1 for session in sessions if session["parity"]),
        "avg_hold_sec": (sum(row["hold_sec"] for row in closed) / len(closed)) if closed else None,
        "bands": bands,
        "cost_sensitivity": [
            {
                "assumed_yen_per_side": cost,
                "label": "SEALED_EXECUTOR_HAS_NO_FEE" if cost == 0 else "ASSUMED_STRESS_NOT_A_BROKER_SCHEDULE",
                "round_trip_cost_yen": cost * 2,
                "total_cost_yen": cost * 2 * len(closed),
                "pnl_after_assumed_cost_yen": round(sum(pnls) - cost * 2 * len(closed), 6),
            }
            for cost in ASSUMED_COST_PER_SIDE_YEN
        ],
        "by_session": [
            {
                "day": session["day"],
                "kind": session["kind"],
                "parity": session["parity"],
                "final_ok": session["final_ok"],
                "closed_trades": session["exit_n"],
                "gross_pnl_yen": round(sum(row["pnl"] for row in _closed(session["ledger"])), 6),
                "max_gross_notional_yen": _max_gross(session["ledger"]),
                "cap_blocked": session["cap_blocked"],
                "gross_cap_denied": session["gross_cap_denied"],
                "entries_above_g": sum(1 for row in _closed(session["ledger"]) if row["above_g"]),
                "reentry": session["reentry"],
                "unfilled": session["unfilled"],
            }
            for session in sessions
        ],
    }


def _bands(closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(closed, key=lambda row: row["notional"])
    if not ordered:
        return []
    size = len(ordered) // 4
    bands = []
    for index in range(4):
        chunk = ordered[index * size: (index + 1) * size if index < 3 else len(ordered)]
        pnls = [row["pnl"] for row in chunk]
        bands.append(
            {
                "band": f"Q{index + 1}",
                "n": len(chunk),
                "notional_min": chunk[0]["notional"],
                "notional_max": chunk[-1]["notional"],
                "gross_pnl_yen": round(sum(pnls), 6),
                "gross_loss_yen": round(sum(pnl for pnl in pnls if pnl < 0), 6),
                "max_drawdown_yen": _drawdown(pnls),
                "avg_hold_sec": sum(row["hold_sec"] for row in chunk) / len(chunk),
            }
        )
    return bands


def _baseline_ok(summary: dict[str, Any], sessions: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    reasons = []
    checks = {
        "closed_trades": summary["closed_trades"] == EXPECTED["closed_trades"],
        "gross_pnl_yen": abs(summary["gross_pnl_yen"] - EXPECTED["gross_pnl_yen"]) < 0.05,
        "max_gross_notional_yen": abs(summary["max_gross_notional_yen"] - EXPECTED["max_gross_notional_yen"]) < 0.05,
        "entries_above_g": summary["entries_above_g"] == EXPECTED["entries_above_g"],
        "cap_blocked": summary["cap_blocked"] == EXPECTED["cap_blocked"],
        "reentry": summary["reentry"] == EXPECTED["reentry"],
        "unfilled": summary["unfilled"] == EXPECTED["unfilled"],
        "parity": all(session["parity"] for session in sessions),
        "final_ok": all(session["final_ok"] for session in sessions),
    }
    for key, ok in checks.items():
        if not ok:
            reasons.append(key)
    return not reasons, reasons


def _entry_shift(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    a = {row.get("signal_uid") for session in left for row in session["ledger"] if row.get("event") == "ENTRY"}
    b = {row.get("signal_uid") for session in right for row in session["ledger"] if row.get("event") == "ENTRY"}
    return {
        "a_only": len(a - b),
        "b_only": len(b - a),
        "shared": len(a & b),
    }


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in session.items() if key != "ledger"}


def write_outputs(report: dict[str, Any], sessions_a: list[dict[str, Any]], sessions_b: list[dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(report["markdown"], encoding="utf-8")
    book = Workbook()
    sheet = book.active
    sheet.title = "sessions"
    header = ["policy", "day", "kind", "parity", "final_ok", "closed_trades", "gross_pnl_yen", "max_gross_notional_yen", "cap_blocked", "gross_cap_denied", "entries_above_g", "reentry", "unfilled"]
    sheet.append(header)
    for policy, sessions in (("A", sessions_a), ("B", sessions_b)):
        for session in sessions:
            closed = _closed(session["ledger"])
            sheet.append([
                policy, session["day"], session["kind"], session["parity"], session["final_ok"],
                len(closed), round(sum(row["pnl"] for row in closed), 6), _max_gross(session["ledger"]),
                session["cap_blocked"], session["gross_cap_denied"], sum(1 for row in closed if row["above_g"]),
                session["reentry"], session["unfilled"],
            ])
    trades = book.create_sheet("trades")
    trades.append(["policy", "day", "kind", "symbol", "notional", "pnl_yen", "hold_sec", "above_g"])
    for policy, sessions in (("A", sessions_a), ("B", sessions_b)):
        for session in sessions:
            for row in _closed(session["ledger"]):
                trades.append([policy, session["day"], session["kind"], row["symbol"], row["notional"], row["pnl"], row["hold_sec"], row["above_g"]])
    book.save(OUT / "audit.xlsx")


def _markdown(report: dict[str, Any]) -> str:
    a = report.get("baseline_A") or {}
    b = report.get("policy_B") or {}
    lines = [
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "ラベルは RETROSPECTIVE_POLICY_RESEARCH です。この2日は独立holdoutではありません。Policyは採用していません。",
        "Paper V16 と Runtime は変更していません。submit/cancel/live = 0/0/0。",
        "Paperの研究Grossを、自己資金50万円で実行できる利益としては表示していません。",
        "",
        "## x1_final_ok",
        "定義は、ENTRY数とEXIT数が一致し、未決済とEXIT待ちが0で、セッション境界の矛盾が無いことです。",
        "Actual Paper の4セッションは final_ok=true です。",
        "前回研究の x1_final_ok=false は、x1_counters に存在しない項目を読んだ記録です。経済評価を止める未解決異常ではありません。",
        f"今回の Baseline A は final_ok {a.get('final_ok_sessions')} / 4 です。",
        "",
        "## Baseline A",
        f"一致: {report.get('baseline_match')}。不一致項目: {report.get('baseline_mismatch')}。",
        f"取引 {a.get('closed_trades')}、Gross {a.get('gross_pnl_yen')}円、最大同時建玉 {a.get('max_gross_notional_yen')}円。",
        f"100万円超ENTRY {a.get('entries_above_g')}、銘柄数CAP拒否 {a.get('cap_blocked')}、再ENTRY {a.get('reentry')}、未約定EXIT {a.get('unfilled')}。",
        "815件のCAP拒否は len(open_pos) >= 5 の銘柄数上限です。円建ての拒否とは別カウンタです。",
        "",
    ]
    if report["verdict"].endswith("BLOCKED_V1"):
        lines.append("Baseline が崩れたため、Policy比較は開始していません。")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "## Policy B",
            "ENTRY時の最新Ask×100株と、保有中の約定建玉の合計が100万円を超えるENTRYを拒否します。前日終値は使っていません。",
            "X1は即時Ask約定なので、未約定の別注文はありません。EXIT待ちの建玉は枠解放まで建玉金額に残します。",
            "ENTRY後の価格上昇や含み損では強制EXITを追加しません。建玉金額は約定Askで固定します。",
            f"G超過ENTRYは 0件です。円建て拒否は {b.get('gross_cap_denied')} 件で、そのうち単銘柄が100万円超は {b.get('single_name_over_g')}、合計超過は {b.get('book_sum_over_g')} です。",
            f"取引 {b.get('closed_trades')}、Gross {b.get('gross_pnl_yen')}円、PF {b.get('profit_factor_gross')}、最大DD {b.get('max_drawdown_yen')}円、勝率 {b.get('win_rate')}。",
            f"最大同時建玉 {b.get('max_gross_notional_yen')}円。銘柄数CAP拒否 {b.get('cap_blocked')}。未約定EXIT {b.get('unfilled')}。",
            f"AにあってBに無いENTRYは {report['entry_shift']['a_only']}、Bだけで新たに出たENTRYは {report['entry_shift']['b_only']} です。",
            "",
            "## C / D / E",
            "Cは NOT_EVALUABLE です。ENTRY時に分かっている無効化は、価格がENTRY直前高値以下になることです。",
            "そのEXIT約定は、その後のBidであり、ENTRY時の金額として定義されていません。インパルス枯渇とセッションフラットにも事前の損失距離がありません。",
            "DはCの上に載るため評価していません。EはDの成立後に評価するため評価していません。",
            "",
            "## コスト",
            "ENTRY Ask と EXIT Bid に含まれるスプレッドは再加算していません。",
            "V16の約定式に手数料、税、追加滑りはありません。0円は公式コストの確認値です。",
            "10円、50円、100円/片道は、証券会社の料金表ではない仮定の感度です。",
            "",
            "## 候補",
            "Aは最大同時建玉が100万円を超えるため、自己資金50万円×2.0の範囲では実行できません。",
            "評価できた実行可能候補はBです。2日の成績から採用はしません。",
            "次に見るべきなのは、この2日でパラメータを選んだあとの別のV16 Actual日です。未parityのCOMPLETE_CAPTURE日と混ぜません。",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    live = _live_final_ok()
    print("replay baseline A", flush=True)
    sessions_a: list[dict[str, Any]] = []
    for day in V16_DAYS:
        sessions_a.extend(_replay(day, gross_cap=False))
    summary_a = _summary("A", sessions_a)
    ok, reasons = _baseline_ok(summary_a, sessions_a)
    report: dict[str, Any] = {
        "verdict": "V16_CAPITAL_RISK_POLICY_COMPARISON_BLOCKED_V1",
        "next": "RESTORE_BASELINE_A_PARITY_BEFORE_POLICY_COMPARISON",
        "label": "RETROSPECTIVE_POLICY_RESEARCH",
        "used_as_independent_holdout": False,
        "policy_adopted": False,
        "paper_gross_is_not_executable_profit": True,
        "x1_final_ok_definition": "entry_n == exit_n and open_n == 0 and exit_pending_n == 0 and boundary_error is empty",
        "previous_false_flag": "The prior research report read x1_final_summary_ok from x1_counters(), which does not contain that field. Actual Paper summaries have final_ok true.",
        "actual_paper_final_ok": live,
        "cap_815_is_symbol_count": True,
        "cap_counter": "counts['cap'] when len(open_pos) >= 5",
        "yen_cap_counter": "gross_cap_denied_n",
        "baseline_match": ok,
        "baseline_mismatch": reasons,
        "baseline_A": {key: value for key, value in summary_a.items() if key != "by_session"} | {"by_session": summary_a["by_session"]},
        "policy_C": "NOT_EVALUABLE",
        "policy_D": "NOT_EVALUATED_BECAUSE_C_IS_NOT_EVALUABLE",
        "policy_E": "NOT_EVALUATED_BECAUSE_D_IS_NOT_ESTABLISHED",
        "post_entry_policy": "ENTRY_NOTIONAL_LOCK_NO_FORCED_EXIT",
        "PAPER_V16": "UNCHANGED",
        "RUNTIME_CHANGED": False,
        "submit_cancel_live": "0/0/0",
        "markdown": "",
    }
    sessions_b: list[dict[str, Any]] = []
    if ok:
        print("replay policy B", flush=True)
        for day in V16_DAYS:
            sessions_b.extend(_replay(day, gross_cap=True))
        summary_b = _summary("B", sessions_b)
        report["policy_B"] = summary_b
        report["entry_shift"] = _entry_shift(sessions_a, sessions_b)
        report["g_breach_prevented_at_entry"] = summary_b["entries_above_g"] == 0 and summary_b["max_gross_notional_yen"] <= G + 0.05
        report["verdict"] = "V16_CAPITAL_RISK_POLICY_COMPARISON_COMPLETE_V1"
        report["next"] = "COLLECT_MORE_V16_ACTUAL_DAYS_BEFORE_ADOPTING_A_CAP"
        report["executable_candidate_not_adopted"] = "B"
    report["markdown"] = _markdown(report)
    write_outputs(report, sessions_a, sessions_b)
    print(report["verdict"], ok, flush=True)


if __name__ == "__main__":
    main()
