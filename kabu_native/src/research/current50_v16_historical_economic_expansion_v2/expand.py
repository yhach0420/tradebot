"""Replay saved CURRENT50 captures through the sealed V16 executor.

Paper, runtime, entry, and exit code are not modified. No orders are sent.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook

from research.current50_v16_capital_risk_policy_comparison_v1.compare import (
    EXPECTED,
    SHARES,
    ResearchGrossCapExecutor,
    _baseline_ok,
    _closed,
    _entry_shift,
    _max_gross,
    _replay,
    _summary,
)
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import (
    _capture_dir,
    _new_executor,
    _symbol,
    inventory_capture,
)
from small_paper.market_capture_registration import load_symbols_from_universe_csv
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "current50_v16_historical_economic_expansion_v2"
REPORTS = NATIVE / "results" / "reports"
KNOWN = ("20261007", "20261008")
NEW_ACTUAL = "20261009"
G = 1_000_000.0
CAPITAL = 500_000.0
COST_SIDES = (0, 10, 50, 100)
B_EXPECTED = {
    "closed_trades": 677,
    "gross_pnl_yen": 24770.0,
    "max_gross_notional_yen": 999800.0,
    "gross_cap_denied": 611,
    "entries_above_g": 0,
    "unfilled": 0,
    "max_drawdown_yen": -16240.0,
}
BUSY_MARKERS = (
    "paper_trade_checked_runner",
    "market_ingress_service",
    "run_core10_dynamic40_am_pm_daily_runner",
    "run_small_paper_pilot",
    "v1r_paper_primary_launcher",
)
HOLD_LABELS = ("0-10s", "10-30s", "30-60s", "1-3m", "3-5m", "5m+")


def _busy_processes() -> list[str]:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| ForEach-Object { $_.CommandLine }"
    )
    try:
        text = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return ["PROCESS_CHECK_FAILED"]
    found = []
    for line in text.splitlines():
        for marker in BUSY_MARKERS:
            if marker in line:
                found.append(marker)
    return sorted(set(found))


def _paper_window(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 16


def _am_csv(day: str) -> Path:
    return REPORTS / f"universe_core10_dynamic40_price_risk_am_{day}.csv"


def _complete_days() -> list[dict[str, Any]]:
    rows = []
    for row in inventory_capture():
        if (
            row["status"] == "COMPLETE_CAPTURE"
            and row["coverage_am"]
            and row["coverage_pm"]
            and int(row["registration_coverage"] or 0) == 50
        ):
            symbols = load_symbols_from_universe_csv(_am_csv(row["date"])) if _am_csv(row["date"]).is_file() else []
            rows.append({**row, "am_csv_n": len(symbols), "am_csv": _am_csv(row["date"]).is_file()})
    return rows


def _trades_from(policy: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    exits = {
        row.get("signal_uid"): row
        for row in session["ledger"]
        if row.get("event") == "EXIT" and row.get("pnl_yen") is not None
    }
    out = []
    for row in session["ledger"]:
        if row.get("event") != "ENTRY":
            continue
        exit_row = exits.get(row.get("signal_uid"))
        if exit_row is None:
            continue
        entry_t = float(row.get("fill_timestamp") or 0.0)
        exit_t = float(exit_row.get("exit_fill_timestamp") or 0.0)
        hold = exit_t - entry_t if entry_t > 0 and exit_t > 0 else None
        out.append(
            {
                "policy": policy,
                "day": session["day"],
                "kind": session["kind"],
                "signal_uid": row.get("signal_uid"),
                "symbol": row.get("symbol"),
                "notional": float(row["fill_price"]) * SHARES,
                "pnl": float(exit_row["pnl_yen"]),
                "hold_sec": hold,
                "entry_t": entry_t if entry_t > 0 else None,
                "exit_t": exit_t if exit_t > 0 else None,
                "exit_reason": str(exit_row.get("exit_reason") or ""),
                "reentry": bool(row.get("reentry")),
            }
        )
    return out


def _session_public(policy: str, session: dict[str, Any], *, study_class: str) -> dict[str, Any]:
    closed = _closed(session["ledger"])
    max_gross = _max_gross(session["ledger"])
    above = sum(1 for row in closed if row["above_g"])
    return {
        "policy": policy,
        "study_class": study_class,
        "day": session["day"],
        "kind": session["kind"],
        "parity": session.get("parity"),
        "final_ok": session.get("final_ok"),
        "closed_trades": len(closed),
        "gross_pnl_yen": round(sum(row["pnl"] for row in closed), 6),
        "max_gross_notional_yen": max_gross,
        "entries_above_g": above,
        "cap_blocked": session.get("cap_blocked"),
        "gross_cap_denied": session.get("gross_cap_denied"),
        "single_name_over_g": session.get("single_name_over_g"),
        "book_sum_over_g": session.get("book_sum_over_g"),
        "reentry": session.get("reentry"),
        "session_flat": session.get("session_flat"),
        "unfilled": session.get("unfilled"),
        "same_symbol_blocked": session.get("same_symbol_blocked"),
        "occupancy_blocked": session.get("occupancy_blocked"),
        "g_breach": bool(policy == "B" and (max_gross > G + 1e-6 or above)),
        "admission_n": session.get("admission_n"),
        "evaluable": session.get("evaluable", True),
        "reason": session.get("reason") or "",
    }


def _b_matches(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    checks = {
        "closed_trades": summary["closed_trades"] == B_EXPECTED["closed_trades"],
        "gross_pnl_yen": abs(summary["gross_pnl_yen"] - B_EXPECTED["gross_pnl_yen"]) < 0.05,
        "max_gross_notional_yen": abs(summary["max_gross_notional_yen"] - B_EXPECTED["max_gross_notional_yen"]) < 0.05,
        "gross_cap_denied": summary["gross_cap_denied"] == B_EXPECTED["gross_cap_denied"],
        "entries_above_g": summary["entries_above_g"] == B_EXPECTED["entries_above_g"],
        "unfilled": summary["unfilled"] == B_EXPECTED["unfilled"],
        "max_drawdown_yen": abs(summary["max_drawdown_yen"] - B_EXPECTED["max_drawdown_yen"]) < 0.05,
        "final_ok": summary["final_ok_sessions"] == 4,
    }
    for key, ok in checks.items():
        if not ok:
            reasons.append(key)
    return not reasons, reasons


def _bind_b(day: str, kind: str) -> ResearchGrossCapExecutor:
    book = ResearchGrossCapExecutor()
    book.notifier = None
    book.ledger_path = None
    bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
    if kind == "pm":
        book.bind_safe_pm_flatten(day=day)
    else:
        book.bind_production_schedule(day=day, kind="am")
    return book


def _attach_text(day: str, kind: str) -> str:
    hour, minute = (8, 50) if kind == "am" else (12, 25)
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}T{hour:02d}:{minute:02d}:00+09:00"


def _replay_counterfactual(day: str, kind: str) -> dict[str, Any]:
    capture_dir = _capture_dir(day)
    if capture_dir is None:
        return {"day": day, "kind": kind, "evaluable": False, "reason": "NO_CAPTURE", "sessions": []}
    try:
        book_a = _new_executor(day, kind)
        book_b = _bind_b(day, kind)
    except Exception as exc:
        return {"day": day, "kind": kind, "evaluable": False, "reason": f"BIND_FAILED:{type(exc).__name__}", "sessions": []}
    if len(book_a.admission_membership) != 50 or len(book_b.admission_membership) != 50:
        return {
            "day": day,
            "kind": kind,
            "evaluable": False,
            "reason": f"ADMISSION_{len(book_a.admission_membership)}",
            "admission_source": book_a.admission_membership_source,
            "sessions": [],
        }
    attach = _attach_text(day, kind)
    fed = 0
    skipped_attach = 0
    missing_clock = 0
    order_breaks = 0
    special_n = 0
    last_seq = -1
    books = (book_a, book_b)
    for part in sorted(capture_dir.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                received = str(rec.get("received_at") or "")
                if not received:
                    missing_clock += 1
                    continue
                if received < attach:
                    skipped_attach += 1
                    continue
                seq = int(rec.get("sequence") or 0)
                if seq < last_seq:
                    order_breaks += 1
                last_seq = seq
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload:
                    continue
                payload = dict(payload)
                payload.setdefault("received_at", received)
                payload.setdefault("recorded_at", received)
                if payload.get("SpecialQuote") not in (None, "", 0, False):
                    special_n += 1
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
    sessions = []
    for policy, book in (("A", book_a), ("B", book_b)):
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
        perf = book.x1_performance()
        reason = ""
        evaluable = bool(perf["final_ok"]) and order_breaks == 0 and missing_clock == 0
        if order_breaks:
            reason = "SEQUENCE_ORDER_BREAK"
        elif missing_clock:
            reason = "MISSING_RECEIVED_AT"
        elif not perf["final_ok"]:
            reason = "FINAL_OK_FALSE"
        sessions.append(
            {
                "day": day,
                "kind": kind,
                "policy": policy,
                "fed": fed,
                "skipped_attach": skipped_attach,
                "missing_clock": missing_clock,
                "order_breaks": order_breaks,
                "special_quote_events": special_n,
                "parity": None,
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
                "admission_n": len(book.admission_membership),
                "admission_source": book.admission_membership_source,
                "evaluable": evaluable,
                "reason": reason,
                "ledger": book.ledger,
            }
        )
    print(
        f"CF {day} {kind} fed {fed} A {sessions[0]['exit_n']} B {sessions[1]['exit_n']} final {sessions[0]['final_ok']} {sessions[1]['final_ok']}",
        flush=True,
    )
    return {"day": day, "kind": kind, "evaluable": all(row["evaluable"] for row in sessions), "reason": "", "sessions": sessions}


def _classify_day(day: str, sessions: list[dict[str, Any]]) -> str:
    kinds = {row["kind"]: row for row in sessions if row["policy"] == "A"}
    ok = [kind for kind, row in kinds.items() if row.get("evaluable")]
    if len(ok) == 2:
        return "RETROSPECTIVE_V16_COUNTERFACTUAL"
    if len(ok) == 1:
        return "SESSION_ONLY_EVALUABLE"
    return "REPLAY_NOT_EVALUABLE"


def _economics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnl"]) for row in trades]
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = sum(pnl for pnl in pnls if pnl < 0)
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    by_day: dict[str, float] = defaultdict(float)
    for row in trades:
        by_day[row["day"]] += float(row["pnl"])
    daily = [{"day": day, "gross_pnl_yen": round(value, 6)} for day, value in sorted(by_day.items())]
    worst_day = min(daily, key=lambda row: row["gross_pnl_yen"]) if daily else None
    worst_trade = min(pnls) if pnls else None
    return {
        "trades": len(pnls),
        "gross_pnl_yen": round(sum(pnls), 6),
        "profit_factor": None if gross_loss >= 0 else gross_profit / abs(gross_loss),
        "max_drawdown_yen": drawdown,
        "win_rate": (sum(pnl > 0 for pnl in pnls) / len(pnls)) if pnls else None,
        "avg_gross_per_trade_yen": (sum(pnls) / len(pnls)) if pnls else None,
        "breakeven_extra_round_trip_yen": (sum(pnls) / len(pnls)) if pnls else None,
        "max_trade_loss_yen": worst_trade,
        "max_trade_loss_vs_capital": None if worst_trade is None else worst_trade / CAPITAL,
        "max_drawdown_vs_capital": drawdown / CAPITAL,
        "worst_day": worst_day,
        "daily": daily,
        "cost_sensitivity": [
            {
                "assumed_yen_per_side": cost,
                "label": "SEALED_EXECUTOR_HAS_NO_FEE" if cost == 0 else "ASSUMED_STRESS_NOT_OFFICIAL_NET",
                "pnl_yen": round(sum(pnls) - cost * 2 * len(pnls), 6),
                "profitable_days": sum(1 for row in daily if row["gross_pnl_yen"] - cost * 2 * sum(1 for trade in trades if trade["day"] == row["day"]) > 0),
            }
            for cost in COST_SIDES
        ],
    }


def _hold_table(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def label(hold: float) -> str:
        if hold < 10:
            return "0-10s"
        if hold < 30:
            return "10-30s"
        if hold < 60:
            return "30-60s"
        if hold < 180:
            return "1-3m"
        if hold < 300:
            return "3-5m"
        return "5m+"

    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in HOLD_LABELS}
    missing = 0
    for row in trades:
        if row.get("hold_sec") is None:
            missing += 1
            continue
        buckets[label(float(row["hold_sec"]))].append(row)
    out = []
    for name in HOLD_LABELS:
        rows = buckets[name]
        pnls = [float(row["pnl"]) for row in rows]
        profit = sum(pnl for pnl in pnls if pnl > 0)
        loss = sum(pnl for pnl in pnls if pnl < 0)
        reasons = Counter(row["exit_reason"] for row in rows)
        out.append(
            {
                "bucket": name,
                "trades": len(rows),
                "gross_pnl_yen": round(sum(pnls), 6),
                "profit_factor": None if loss >= 0 else profit / abs(loss),
                "max_loss_yen": min(pnls) if pnls else None,
                "exit_reasons": dict(reasons),
                "under_30s": name in ("0-10s", "10-30s"),
            }
        )
    out.append({"bucket": "TIMESTAMP_MISSING", "trades": missing})
    return out


def _exit_table(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        grouped[row["exit_reason"] or "UNKNOWN"].append(row)
    out = []
    for reason, rows in sorted(grouped.items()):
        holds = [float(row["hold_sec"]) for row in rows if row.get("hold_sec") is not None]
        under = sum(1 for hold in holds if hold < 30)
        out.append(
            {
                "exit_reason": reason,
                "trades": len(rows),
                "gross_pnl_yen": round(sum(float(row["pnl"]) for row in rows), 6),
                "loss_yen": round(sum(float(row["pnl"]) for row in rows if float(row["pnl"]) < 0), 6),
                "median_hold_sec": sorted(holds)[len(holds) // 2] if holds else None,
                "under_30s_share": (under / len(holds)) if holds else None,
            }
        )
    return out


def _reentry_table(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gaps = []
    by_symbol: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        by_symbol[(row["day"], row["kind"], row["symbol"])].append(row)
    repeat_pnl = 0.0
    repeat_n = 0
    measured = 0
    short_gaps = 0
    for rows in by_symbol.values():
        ordered = sorted(rows, key=lambda item: float(item["entry_t"] or 0))
        if len(ordered) > 1:
            repeat_n += len(ordered)
            repeat_pnl += sum(float(row["pnl"]) for row in ordered)
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.get("exit_t") is None or nxt.get("entry_t") is None:
                continue
            gap = float(nxt["entry_t"]) - float(prev["exit_t"])
            if gap < 0:
                continue
            measured += 1
            gaps.append(gap)
            if gap < 30:
                short_gaps += 1
    counts = Counter()
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    n_by_symbol: dict[str, int] = defaultdict(int)
    for row in trades:
        counts[row["symbol"]] += 1
        pnl_by_symbol[row["symbol"]] += float(row["pnl"])
        n_by_symbol[row["symbol"]] += 1
    top = sorted(pnl_by_symbol.items(), key=lambda item: item[1], reverse=True)
    return {
        "symbols": len(counts),
        "max_same_symbol_trades": max(counts.values()) if counts else 0,
        "repeat_symbol_trades": repeat_n,
        "repeat_symbol_gross_yen": round(repeat_pnl, 6),
        "measured_reentry_gaps": measured,
        "gaps_under_30s": short_gaps,
        "median_gap_sec": sorted(gaps)[len(gaps) // 2] if gaps else None,
        "top_profit_symbols": [{"symbol": symbol, "gross_pnl_yen": round(pnl, 6), "trades": n_by_symbol[symbol]} for symbol, pnl in top[:5]],
        "top_loss_symbols": [{"symbol": symbol, "gross_pnl_yen": round(pnl, 6), "trades": n_by_symbol[symbol]} for symbol, pnl in top[-5:]],
    }


def _notional_table(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    ordered = sorted(trades, key=lambda row: float(row["notional"]))
    size = max(1, len(ordered) // 4)
    out = []
    for index in range(4):
        chunk = ordered[index * size :] if index == 3 else ordered[index * size : (index + 1) * size]
        if not chunk:
            continue
        out.append(
            {
                "band": f"Q{index + 1}",
                "label": "DESCRIPTIVE_NOT_AN_ADOPTED_FILTER",
                "notional_min": chunk[0]["notional"],
                "notional_max": chunk[-1]["notional"],
                "trades": len(chunk),
                "gross_pnl_yen": round(sum(float(row["pnl"]) for row in chunk), 6),
            }
        )
    return out


def _save(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "report.json"
    temporary = OUT / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def _write_markdown(report: dict[str, Any]) -> None:
    text = _markdown(report)
    report["markdown"] = text
    _save(report)
    (OUT / "report.md").write_text(text, encoding="utf-8")


def _markdown(report: dict[str, Any]) -> str:
    phase1 = report.get("phase1") or {}
    new_day = report.get("new_actual_20261009") or {}
    retro = report.get("retrospective") or {}
    lines = [
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "Policy Bは採用していません。Paper V16 と Runtime は変更していません。submit/cancel/live = 0/0/0。",
        "新しいPaper、Shadow、実注文は起動していません。",
        "",
        "## 実行許可",
        report.get("permission_text") or "",
        "",
        "## Phase",
        f"実施: {', '.join(report.get('phases_done') or [])}",
        f"未実施: {', '.join(report.get('phases_pending') or [])}",
        "",
        "## Phase 1 既知2日",
        json.dumps(phase1.get("public") or phase1, ensure_ascii=False),
        "",
        "## 20261009",
        json.dumps(new_day.get("public") or new_day, ensure_ascii=False),
        "",
        "## 過去再生",
        json.dumps(retro.get("public") or retro, ensure_ascii=False),
        "",
        report.get("analysis_text") or "",
    ]
    return "\n".join(lines) + "\n"


def _workbook(report: dict[str, Any]) -> None:
    book = Workbook()
    sheets = {
        "Data Coverage": ["date", "status", "coverage_am", "coverage_pm", "registration_coverage", "am_csv_n"],
        "Replay Eligibility": ["date", "class", "reason"],
        "Actual Parity": ["date", "kind", "policy", "parity", "final_ok", "trades"],
        "AB Comparison": ["class", "policy", "trades", "gross", "pf", "max_dd", "max_gross", "yen_deny"],
        "Daily Economics": ["class", "policy", "day", "gross"],
        "Cost Sensitivity": ["class", "policy", "per_side", "pnl", "label"],
        "Holding Time": ["class", "policy", "bucket", "trades", "gross"],
        "Reentry": ["class", "policy", "symbol", "trades", "gross"],
        "EXIT Mechanisms": ["class", "policy", "reason", "trades", "gross", "under_30s_share"],
        "Notional Bands": ["class", "policy", "band", "min", "max", "trades", "gross"],
        "Risk": ["class", "policy", "max_trade_loss", "max_dd", "dd_vs_capital"],
        "Failure RCA": ["date", "kind", "reason"],
    }
    first = True
    for name, header in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        sheet.append(header)
    coverage = book["Data Coverage"]
    for row in report.get("coverage") or []:
        coverage.append([row.get("date"), row.get("status"), row.get("coverage_am"), row.get("coverage_pm"), row.get("registration_coverage"), row.get("am_csv_n")])
    eligibility = book["Replay Eligibility"]
    for row in report.get("eligibility") or []:
        eligibility.append([row.get("date"), row.get("class"), row.get("reason")])
    parity = book["Actual Parity"]
    for row in report.get("parity_rows") or []:
        parity.append([row.get("day"), row.get("kind"), row.get("policy"), row.get("parity"), row.get("final_ok"), row.get("closed_trades")])
    compare = book["AB Comparison"]
    for row in report.get("comparison_rows") or []:
        compare.append([row.get("class"), row.get("policy"), row.get("trades"), row.get("gross_pnl_yen"), row.get("profit_factor"), row.get("max_drawdown_yen"), row.get("max_gross_notional_yen"), row.get("gross_cap_denied")])
    daily = book["Daily Economics"]
    for row in report.get("daily_rows") or []:
        daily.append([row.get("class"), row.get("policy"), row.get("day"), row.get("gross_pnl_yen")])
    cost = book["Cost Sensitivity"]
    for row in report.get("cost_rows") or []:
        cost.append([row.get("class"), row.get("policy"), row.get("assumed_yen_per_side"), row.get("pnl_yen"), row.get("label")])
    hold = book["Holding Time"]
    for row in report.get("hold_rows") or []:
        hold.append([row.get("class"), row.get("policy"), row.get("bucket"), row.get("trades"), row.get("gross_pnl_yen")])
    reentry = book["Reentry"]
    for row in report.get("reentry_rows") or []:
        reentry.append([row.get("class"), row.get("policy"), row.get("symbol"), row.get("trades"), row.get("gross_pnl_yen")])
    exits = book["EXIT Mechanisms"]
    for row in report.get("exit_rows") or []:
        exits.append([row.get("class"), row.get("policy"), row.get("exit_reason"), row.get("trades"), row.get("gross_pnl_yen"), row.get("under_30s_share")])
    bands = book["Notional Bands"]
    for row in report.get("band_rows") or []:
        bands.append([row.get("class"), row.get("policy"), row.get("band"), row.get("notional_min"), row.get("notional_max"), row.get("trades"), row.get("gross_pnl_yen")])
    risk = book["Risk"]
    for row in report.get("risk_rows") or []:
        risk.append([row.get("class"), row.get("policy"), row.get("max_trade_loss_yen"), row.get("max_drawdown_yen"), row.get("max_drawdown_vs_capital")])
    rca = book["Failure RCA"]
    for row in report.get("failure_rows") or []:
        rca.append([row.get("date"), row.get("kind"), row.get("reason")])
    book.save(OUT / "audit.xlsx")


def _absorb(policy: str, sessions: list[dict[str, Any]], study_class: str, sink: dict[str, Any]) -> None:
    for session in sessions:
        if policy == "B" and not session.get("evaluable", True):
            continue
        if policy == "A" and study_class.startswith("RETRO") and not session.get("evaluable", True):
            continue
        public = _session_public(policy, session, study_class=study_class)
        sink["sessions"].append(public)
        if public["evaluable"]:
            sink["trades"].extend(_trades_from(policy, session))


def _class_bundle(name: str, trades: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in trades if row["policy"] == name]
    session_rows = [row for row in sessions if row["policy"] == name and row.get("evaluable")]
    econ = _economics(selected)
    econ["max_gross_notional_yen"] = max((float(row["max_gross_notional_yen"]) for row in session_rows), default=0.0)
    econ["gross_cap_denied"] = sum(int(row.get("gross_cap_denied") or 0) for row in session_rows)
    econ["cap_blocked"] = sum(int(row.get("cap_blocked") or 0) for row in session_rows)
    econ["entries_above_g"] = sum(int(row.get("entries_above_g") or 0) for row in session_rows)
    econ["unfilled"] = sum(int(row.get("unfilled") or 0) for row in session_rows)
    econ["reentry"] = sum(int(row.get("reentry") or 0) for row in session_rows)
    econ["g_breach_sessions"] = sum(1 for row in session_rows if row.get("g_breach"))
    econ["hold"] = _hold_table(selected)
    econ["exits"] = _exit_table(selected)
    econ["reentry_structure"] = _reentry_table(selected)
    econ["notional_bands"] = _notional_table(selected)
    under = sum(1 for row in selected if row.get("hold_sec") is not None and float(row["hold_sec"]) < 30)
    measured = sum(1 for row in selected if row.get("hold_sec") is not None)
    econ["under_30s_share"] = (under / measured) if measured else None
    return econ


def main() -> None:
    now = datetime.now().astimezone()
    busy = _busy_processes()
    if busy or _paper_window(now):
        report = {
            "verdict": "V16_RESEARCH_DEFERRED_FOR_ACTIVE_PAPER_V2",
            "next": "RETRY_AFTER_PAPER_AND_INGRESS_HAVE_EXITED",
            "research_closed": False,
            "heavy_research_started": False,
            "busy": busy,
            "paper_window": _paper_window(now),
            "permission_text": "Paperまたは当日の市場時間と重なるため、重い再生は開始していません。",
            "phases_done": ["PHASE0"],
            "phases_pending": ["PHASE1", "PHASE2", "PHASE3", "PHASE4"],
            "PAPER_V16": "UNCHANGED",
            "RUNTIME_CHANGED": False,
            "submit_cancel_live": "0/0/0",
            "policy_adopted": False,
        }
        _write_markdown(report)
        _workbook(report)
        print(report["verdict"], flush=True)
        return

    coverage = inventory_capture()
    complete = _complete_days()
    historical_days = [row["date"] for row in complete if row["date"] not in KNOWN and row["date"] != NEW_ACTUAL]
    report = {
        "verdict": "V16_HISTORICAL_COMPLETE_STRATEGY_ECONOMICS_BLOCKED_V2",
        "next": "FIX_BASELINE_BEFORE_EXPANSION",
        "research_closed": False,
        "heavy_research_started": True,
        "policy_adopted": False,
        "permission_text": (
            "20261009 AM live_session_081043 と PM live_session_122553 は final_ok=true、open_n=0、exit_pending_n=0、unfilled_n=0。"
            "境界時刻は executor と runtime が一致。Capture は COMPLETE_CAPTURE、登録50、dropped_event_count=0。"
            "paper_exit_code=0、submit/cancel=0/0。daily runner と Market Ingress のプロセスは終了している。"
            "確認時刻は翌営業日のPaper起動前。"
        ),
        "phases_done": ["PHASE0"],
        "phases_pending": ["PHASE1", "PHASE2", "PHASE3", "PHASE4"],
        "coverage": complete,
        "coverage_all_n": len(coverage),
        "complete_n": len(complete),
        "historical_candidate_n": len(historical_days),
        "sessions": [],
        "trades": [],
        "eligibility": [],
        "failure_rows": [],
        "PAPER_V16": "UNCHANGED",
        "RUNTIME_CHANGED": False,
        "submit_cancel_live": "0/0/0",
        "counterfactual_attach": "AM 08:50 and PM 12:25 JST match the observed V16 consumer resync clock. They are not an entry filter.",
    }
    _save(report)

    print("PHASE1 start", flush=True)
    known_a = []
    for day in KNOWN:
        known_a.extend(_replay(day, gross_cap=False))
    summary_a = _summary("A", known_a)
    ok_a, reasons_a = _baseline_ok(summary_a, known_a)
    report["phase1"] = {"A_match": ok_a, "A_reasons": reasons_a, "A_public": {key: value for key, value in summary_a.items() if key != "bands"}}
    if not ok_a:
        report["phases_done"].append("PHASE1")
        report["permission_text"] += " Phase 1 の Baseline A が既知値と一致しないため、拡張再生はしていません。"
        _write_markdown(report)
        _workbook(report)
        print("PHASE1 FAIL", reasons_a, flush=True)
        return
    known_b = []
    for day in KNOWN:
        known_b.extend(_replay(day, gross_cap=True))
    summary_b = _summary("B", known_b)
    ok_b, reasons_b = _b_matches(summary_b)
    report["phase1"]["B_match"] = ok_b
    report["phase1"]["B_reasons"] = reasons_b
    report["phase1"]["B_public"] = {key: value for key, value in summary_b.items() if key != "bands"}
    report["phase1"]["entry_shift"] = _entry_shift(known_a, known_b)
    report["phases_done"].append("PHASE1")
    for policy, sessions in (("A", known_a), ("B", known_b)):
        for session in sessions:
            session["evaluable"] = True
            report["sessions"].append(_session_public(policy, session, study_class="ACTUAL_PARITY_VALIDATED"))
            report["trades"].extend(_trades_from(policy, session))
    _save(report)
    if not ok_b:
        report["verdict"] = "V16_HISTORICAL_COMPLETE_STRATEGY_ECONOMICS_BLOCKED_V2"
        report["next"] = "POLICY_B_BASELINE_DIVERGED"
        report["phases_pending"] = ["PHASE2", "PHASE3", "PHASE4"]
        _write_markdown(report)
        _workbook(report)
        print("PHASE1 B FAIL", reasons_b, flush=True)
        return
    print("PHASE1 PASS", flush=True)

    print("PHASE2 start", flush=True)
    new_a = _replay(NEW_ACTUAL, gross_cap=False)
    parity_sessions = [row for row in new_a if row.get("parity") and row.get("final_ok")]
    excluded = [row for row in new_a if not (row.get("parity") and row.get("final_ok"))]
    new_b = []
    if parity_sessions:
        replay_b = _replay(NEW_ACTUAL, gross_cap=True)
        good_kinds = {row["kind"] for row in parity_sessions}
        new_b = [row for row in replay_b if row["kind"] in good_kinds]
    for row in new_a:
        row["evaluable"] = bool(row.get("parity") and row.get("final_ok"))
        row["reason"] = "" if row["evaluable"] else "ACTUAL_PARITY_FAILED"
        report["sessions"].append(_session_public("A", row, study_class="NEW_V16_ACTUAL"))
        if row["evaluable"]:
            report["trades"].extend(_trades_from("A", row))
        else:
            report["failure_rows"].append({"date": row["day"], "kind": row["kind"], "reason": "ACTUAL_PARITY_FAILED"})
    for row in new_b:
        row["evaluable"] = True
        row["reason"] = ""
        report["sessions"].append(_session_public("B", row, study_class="NEW_V16_ACTUAL"))
        report["trades"].extend(_trades_from("B", row))
    report["new_actual_20261009"] = {
        "a_sessions": len(new_a),
        "a_parity_sessions": len(parity_sessions),
        "excluded_sessions": [
            {"kind": row["kind"], "parity": row.get("parity"), "final_ok": row.get("final_ok"), "mismatches": row.get("first_mismatches")}
            for row in excluded
        ],
        "b_sessions": len(new_b),
    }
    report["eligibility"].append(
        {
            "date": NEW_ACTUAL,
            "class": "NEW_V16_ACTUAL" if len(parity_sessions) == 2 else "NEW_V16_ACTUAL_PARTIAL",
            "reason": "" if len(parity_sessions) == 2 else "PARITY_SESSION_EXCLUDED",
        }
    )
    report["phases_done"].append("PHASE2")
    report["phases_pending"] = ["PHASE3", "PHASE4"]
    _save(report)
    print("PHASE2 done", len(parity_sessions), flush=True)

    print("PHASE3 start", len(historical_days), flush=True)
    checkpoint_path = OUT / "report.json"
    done_days = set()
    if checkpoint_path.is_file():
        prior = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        done_days = set(prior.get("historical_done") or [])
    report["historical_done"] = sorted(done_days)
    for day in historical_days:
        if _busy_processes() or _paper_window(datetime.now().astimezone()):
            report["verdict"] = "V16_RESEARCH_DEFERRED_FOR_ACTIVE_PAPER_V2"
            report["next"] = "RESUME_REMAINING_HISTORICAL_DAYS"
            report["research_closed"] = False
            _write_markdown(report)
            _workbook(report)
            print("DEFERRED mid-run", flush=True)
            return
        if day in done_days:
            continue
        am_n = len(load_symbols_from_universe_csv(_am_csv(day))) if _am_csv(day).is_file() else 0
        if am_n != 50:
            report["eligibility"].append({"date": day, "class": "REPLAY_NOT_EVALUABLE", "reason": f"AM_CSV_{am_n}"})
            report["failure_rows"].append({"date": day, "kind": "AM/PM", "reason": f"AM_CSV_{am_n}"})
            report["historical_done"].append(day)
            _save(report)
            continue
        day_sessions = []
        for kind in ("am", "pm"):
            result = _replay_counterfactual(day, kind)
            if not result["sessions"]:
                report["failure_rows"].append({"date": day, "kind": kind, "reason": result.get("reason") or "NO_SESSION"})
                continue
            day_sessions.extend(result["sessions"])
            for session in result["sessions"]:
                if not session.get("evaluable"):
                    report["failure_rows"].append({"date": day, "kind": kind, "reason": session.get("reason") or "NOT_EVALUABLE"})
        study_class = _classify_day(day, day_sessions)
        reason = ""
        if study_class != "RETROSPECTIVE_V16_COUNTERFACTUAL":
            reason = ",".join(sorted({row.get("reason") or "UNKNOWN" for row in day_sessions if not row.get("evaluable")})) or "INCOMPLETE"
        report["eligibility"].append({"date": day, "class": study_class, "reason": reason})
        evaluable_a = {(row["kind"]) for row in day_sessions if row["policy"] == "A" and row.get("evaluable")}
        for session in day_sessions:
            report["sessions"].append(_session_public(session["policy"], session, study_class=study_class))
            keep = bool(session.get("evaluable") and session["kind"] in evaluable_a and study_class != "REPLAY_NOT_EVALUABLE")
            if keep:
                report["trades"].extend(_trades_from(session["policy"], session))
        report["historical_done"].append(day)
        _save(report)
        print("saved", day, study_class, flush=True)
    report["phases_done"].append("PHASE3")
    report["phases_pending"] = ["PHASE4"]
    _finish(report)


def _finish(report: dict[str, Any]) -> None:
    classes = ("ACTUAL_PARITY_VALIDATED", "NEW_V16_ACTUAL", "RETROSPECTIVE_V16_COUNTERFACTUAL", "SESSION_ONLY_EVALUABLE")
    comparison_rows = []
    daily_rows = []
    cost_rows = []
    hold_rows = []
    reentry_rows = []
    exit_rows = []
    band_rows = []
    risk_rows = []
    analysis = []
    for study_class in classes:
        class_trades = [row for row in report["trades"] if _class_of(report, row) == study_class]
        class_sessions = [row for row in report["sessions"] if row["study_class"] == study_class]
        for policy in ("A", "B"):
            bundle = _class_bundle(policy, class_trades, class_sessions)
            comparison_rows.append(
                {
                    "class": study_class,
                    "policy": policy,
                    "trades": bundle["trades"],
                    "gross_pnl_yen": bundle["gross_pnl_yen"],
                    "profit_factor": bundle["profit_factor"],
                    "max_drawdown_yen": bundle["max_drawdown_yen"],
                    "max_gross_notional_yen": bundle["max_gross_notional_yen"],
                    "gross_cap_denied": bundle["gross_cap_denied"],
                }
            )
            for row in bundle["daily"]:
                daily_rows.append({"class": study_class, "policy": policy, **row})
            for row in bundle["cost_sensitivity"]:
                cost_rows.append({"class": study_class, "policy": policy, **row})
            for row in bundle["hold"]:
                hold_rows.append({"class": study_class, "policy": policy, **row})
            for row in bundle["reentry_structure"]["top_loss_symbols"] + bundle["reentry_structure"]["top_profit_symbols"]:
                reentry_rows.append({"class": study_class, "policy": policy, **row})
            for row in bundle["exits"]:
                exit_rows.append({"class": study_class, "policy": policy, **row})
            for row in bundle["notional_bands"]:
                band_rows.append({"class": study_class, "policy": policy, **row})
            risk_rows.append({"class": study_class, "policy": policy, **bundle})
            analysis.append(_analysis_line(study_class, policy, bundle))
    eligible = [row for row in report["eligibility"] if row["class"] == "RETROSPECTIVE_V16_COUNTERFACTUAL"]
    session_only = [row for row in report["eligibility"] if row["class"] == "SESSION_ONLY_EVALUABLE"]
    blocked = [row for row in report["eligibility"] if row["class"] == "REPLAY_NOT_EVALUABLE"]
    report["comparison_rows"] = comparison_rows
    report["daily_rows"] = daily_rows
    report["cost_rows"] = cost_rows
    report["hold_rows"] = hold_rows
    report["reentry_rows"] = reentry_rows
    report["exit_rows"] = exit_rows
    report["band_rows"] = band_rows
    report["risk_rows"] = [
        {
            "class": row["class"],
            "policy": row["policy"],
            "max_trade_loss_yen": row.get("max_trade_loss_yen"),
            "max_drawdown_yen": row.get("max_drawdown_yen"),
            "max_drawdown_vs_capital": row.get("max_drawdown_vs_capital"),
        }
        for row in risk_rows
    ]
    report["retrospective_public"] = {
        "candidate_days": report["historical_candidate_n"],
        "full_days": len(eligible),
        "session_only_days": len(session_only),
        "not_evaluable_days": len(blocked),
    }
    report["analysis_text"] = "\n".join(analysis)
    report["phases_done"].append("PHASE4")
    report["phases_pending"] = []
    report["verdict"] = "V16_HISTORICAL_COMPLETE_STRATEGY_ECONOMICS_EVALUATED_V2"
    report["next"] = "KEEP_POLICY_B_UNADOPTED_AND_TEST_SHORT_HOLD_REENTRY_ON_A_FUTURE_SAMPLE"
    report["policy_adopted"] = False
    report["retrospective"] = {"public": report["retrospective_public"]}
    report["new_actual_20261009"]["public"] = {
        "parity_sessions": report["new_actual_20261009"].get("a_parity_sessions"),
        "excluded": report["new_actual_20261009"].get("excluded_sessions"),
    }
    report["phase1"]["public"] = {"A_match": report["phase1"]["A_match"], "B_match": report["phase1"]["B_match"]}
    # Drop raw trades from the json after the workbook is built from report fields.
    trades = report.pop("trades")
    report["trade_count_stored_for_audit"] = len(trades)
    report["trades"] = trades
    _write_markdown(report)
    _workbook(report)
    slim = dict(report)
    slim.pop("trades", None)
    _save(slim)
    (OUT / "report.md").write_text(_markdown(slim), encoding="utf-8")
    print(report["verdict"], flush=True)


def _class_of(report: dict[str, Any], trade: dict[str, Any]) -> str:
    for row in report["sessions"]:
        if row["day"] == trade["day"] and row["kind"] == trade["kind"] and row["policy"] == trade["policy"]:
            return row["study_class"]
    return ""


def _analysis_line(study_class: str, policy: str, bundle: dict[str, Any]) -> str:
    under = bundle.get("under_30s_share")
    exits = bundle.get("exits") or []
    top_exit = max(exits, key=lambda row: row["trades"]) if exits else {}
    return (
        f"{study_class} {policy}: trades {bundle['trades']}, gross {bundle['gross_pnl_yen']}, "
        f"PF {bundle['profit_factor']}, DD {bundle['max_drawdown_yen']}, "
        f"max gross {bundle['max_gross_notional_yen']}, yen deny {bundle['gross_cap_denied']}, "
        f"under 30s {under}, top exit {top_exit.get('exit_reason')}."
    )


if __name__ == "__main__":
    main()
