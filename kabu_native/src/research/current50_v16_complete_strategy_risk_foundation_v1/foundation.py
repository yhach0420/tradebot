"""Replay saved CURRENT50 capture through the sealed V16 executor.

The research simulate_session engine is not this path. No orders are sent.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook

from research.anchor_timing_robustness.grid import hm_epoch
from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor
from small_paper.paper_primary_activation import (
    ENTRY_SHA,
    EXIT_SHA,
    safe_pm_complete_sha,
)
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
CAPTURE = NATIVE / "data" / "market_capture"
PAPER = NATIVE / "results" / "small_paper"
OUT = NATIVE / "results" / "research" / "current50_v16_complete_strategy_risk_foundation_v1"
MANIFEST = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16.json"
)
V16_DAYS = ("20261007", "20261008")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_capture() -> list[dict[str, Any]]:
    rows = []
    if not CAPTURE.is_dir():
        return rows
    for day_dir in sorted(p for p in CAPTURE.iterdir() if p.is_dir() and p.name.isdigit()):
        sessions = []
        for comp in sorted(day_dir.glob("session_*/capture_completeness.json")):
            body = _load(comp)
            sessions.append(
                {
                    "session": comp.parent.name,
                    "status": body.get("status"),
                    "coverage_am": bool(body.get("coverage_am")),
                    "coverage_pm": bool(body.get("coverage_pm")),
                    "registration_coverage": body.get("registration_coverage"),
                    "research_adoptable": bool(body.get("research_adoptable")),
                }
            )
        best = None
        for row in sessions:
            rank = (
                row["status"] == "COMPLETE_CAPTURE",
                row["coverage_am"] and row["coverage_pm"],
                int(row["registration_coverage"] or 0),
            )
            if best is None or rank > best[0]:
                best = (rank, row)
        chosen = {} if best is None else best[1]
        rows.append(
            {
                "date": day_dir.name,
                "capture_sessions": len(sessions),
                "status": chosen.get("status") or "NO_CAPTURE",
                "coverage_am": bool(chosen.get("coverage_am")),
                "coverage_pm": bool(chosen.get("coverage_pm")),
                "registration_coverage": chosen.get("registration_coverage"),
                "v16_actual_paper": day_dir.name in V16_DAYS,
            }
        )
    return rows


def _paper_sessions(day: str) -> list[dict[str, Any]]:
    root = PAPER / day
    found = []
    for ledger in sorted(root.glob("live_session_*/fixed_support_x1_paper_ledger.jsonl")):
        identity_path = ledger.parent / "session_identity.json"
        identity = _load(identity_path) if identity_path.is_file() else {}
        resync_path = ledger.parent / "consumer_lag_realtime_resync.json"
        resync = _load(resync_path) if resync_path.is_file() else {}
        body = resync.get("resync") if isinstance(resync.get("resync"), dict) else {}
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        found.append(
            {
                "day": day,
                "session_dir": ledger.parent.name,
                "kind": str(identity.get("session_kind") or ""),
                "activation_id": str(identity.get("activation_id") or ""),
                "ledger": rows,
                "watermark_seq": int(body.get("resync_head_seq") or 0),
                "watermark_event_time": str(body.get("resync_head_event_time") or ""),
                "skipped_count": int(body.get("skipped_count") or 0),
            }
        )
    return found


def _symbol(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    if text.endswith(".T"):
        return text
    return f"{text}.T"


def _capture_dir(day: str) -> Optional[Path]:
    root = CAPTURE / day
    best: Optional[tuple[tuple, Path]] = None
    for comp in root.glob("session_*/capture_completeness.json"):
        body = _load(comp)
        size = sum(path.stat().st_size for path in comp.parent.glob("push_part_*.jsonl"))
        rank = (body.get("status") == "COMPLETE_CAPTURE", bool(body.get("coverage_am") and body.get("coverage_pm")), size)
        if best is None or rank > best[0]:
            best = (rank, comp.parent)
    return None if best is None else best[1]


def _new_executor(day: str, kind: str) -> FixedSupportX1SessionExecutor:
    book = FixedSupportX1SessionExecutor()
    book.notifier = None
    book.ledger_path = None
    bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
    if kind == "pm":
        book.bind_safe_pm_flatten(day=day)
    else:
        book.bind_production_schedule(day=day, kind="am")
    return book


def _feed(book: FixedSupportX1SessionExecutor, capture_dir: Path, *, seq_after: int, t_end: float) -> dict[str, Any]:
    fed = 0
    skipped_seq = 0
    order_breaks = 0
    special_n = 0
    last_seq = -1
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
                    skipped_seq += 1
                    continue
                if seq < last_seq:
                    order_breaks += 1
                last_seq = seq
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload and isinstance(rec.get("payload"), dict):
                    payload = rec["payload"]
                if not isinstance(payload, dict) or not payload:
                    continue
                payload = dict(payload)
                received = rec.get("received_at")
                if received:
                    payload.setdefault("received_at", received)
                    payload.setdefault("recorded_at", received)
                if "SpecialQuote" in payload and payload.get("SpecialQuote") not in (None, "", 0, False):
                    special_n += 1
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                book.on_market_event({"symbol": symbol, "payload": payload})
                fed += 1
                if book._closed:
                    return {
                        "fed": fed,
                        "skipped_watermark": skipped_seq,
                        "order_breaks": order_breaks,
                        "special_quote_events": special_n,
                        "closed_by_event": True,
                    }
                if fed % 200000 == 0:
                    print(f"fed {book.session} {fed}", flush=True)
    book.close_at_runtime_boundary(day=book.day, kind="pm" if book.flatten_deadline is not None else "am")
    return {
        "fed": fed,
        "skipped_watermark": skipped_seq,
        "order_breaks": order_breaks,
        "special_quote_events": special_n,
        "closed_by_event": False,
        "t_end": t_end,
    }


def _pair(rows: list[dict[str, Any]]) -> list[tuple]:
    out = []
    for row in rows:
        if row.get("event") not in {"ENTRY", "EXIT"}:
            continue
        exit_px = row.get("exit_price")
        out.append(
            (
                row.get("event"),
                str(row.get("signal_uid") or ""),
                round(float(row.get("fill_price") or 0.0), 6),
                str(row.get("exit_reason") or ""),
                None if exit_px is None else round(float(exit_px), 6),
                bool(row.get("reentry")),
                bool(row.get("slot_release")),
            )
        )
    return out


def _compare(actual: list[dict[str, Any]], replay: list[dict[str, Any]]) -> dict[str, Any]:
    left = _pair(actual)
    right = _pair(replay)
    mismatch = []
    for index, (got, exp) in enumerate(zip(left, right)):
        if got != exp:
            mismatch.append({"index": index, "actual": list(got), "replay": list(exp)})
            if len(mismatch) >= 8:
                break
    return {
        "actual_rows": len(left),
        "replay_rows": len(right),
        "equal": left == right,
        "first_mismatches": mismatch,
    }


def replay_v16() -> list[dict[str, Any]]:
    reports = []
    for day in V16_DAYS:
        capture_dir = _capture_dir(day)
        if capture_dir is None:
            reports.append({"day": day, "error": "NO_CAPTURE"})
            continue
        for session in _paper_sessions(day):
            if session["activation_id"] != "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16":
                reports.append({**{k: session[k] for k in ("day", "session_dir", "kind", "activation_id")}, "skipped": "NOT_V16"})
                continue
            if not session["ledger"]:
                reports.append({"day": day, "session_dir": session["session_dir"], "kind": session["kind"], "empty_ledger": True})
                continue
            kind = session["kind"] or "am"
            book = _new_executor(day, kind)
            t_end = float(book.sess_end)
            print(f"replay {day} {kind} watermark {session['watermark_seq']}", flush=True)
            feed = _feed(book, capture_dir, seq_after=session["watermark_seq"], t_end=t_end)
            compared = _compare(session["ledger"], book.ledger)
            counters = book.x1_counters()
            reports.append(
                {
                    "day": day,
                    "session_dir": session["session_dir"],
                    "kind": kind,
                    "activation_id": session["activation_id"],
                    "watermark_seq": session["watermark_seq"],
                    "watermark_event_time": session["watermark_event_time"],
                    "skipped_before_watermark": session["skipped_count"],
                    "admission_source": book.admission_membership_source,
                    "admission_n": len(book.admission_membership),
                    "admission_denied_n": book.admission_denied_n,
                    "feed": feed,
                    "compare": compared,
                    "counts": book.counts,
                    "unfilled_n": len(book.unfilled),
                    "trade_n": len(book.trades),
                    "cap_blocked": int(book.counts.get("cap") or 0),
                    "same_symbol_blocked": int(book.counts.get("same_symbol") or 0),
                    "occupancy_blocked": int(book.counts.get("occupancy") or 0),
                    "reentry_n": int(book.counts.get("reentry") or 0),
                    "session_flat_n": int(book.counts.get("session_flat") or 0),
                    "x1_final_ok": bool(counters.get("x1_final_summary_ok")),
                }
            )
            print(
                f"done {day} {kind} equal {compared['equal']} actual {compared['actual_rows']} replay {compared['replay_rows']}",
                flush=True,
            )
    return reports


def _economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exits = [row for row in rows if row.get("event") == "EXIT" and row.get("pnl_yen") is not None]
    pnls = [float(row["pnl_yen"]) for row in exits]
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    wins = sum(1 for pnl in pnls if pnl > 0)
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = sum(pnl for pnl in pnls if pnl < 0)
    notionals = [float(row.get("fill_price") or 0.0) * 100.0 for row in rows if row.get("event") == "ENTRY"]
    return {
        "closed_trades": len(pnls),
        "gross_pnl_yen": round(sum(pnls), 6),
        "win_rate": (wins / len(pnls)) if pnls else None,
        "avg_pnl_yen": (sum(pnls) / len(pnls)) if pnls else None,
        "profit_factor_gross": None if gross_loss >= 0 else gross_profit / abs(gross_loss),
        "max_drawdown_yen": drawdown,
        "entry_notional_min": min(notionals) if notionals else None,
        "entry_notional_median": sorted(notionals)[len(notionals) // 2] if notionals else None,
        "entry_notional_max": max(notionals) if notionals else None,
        "exit_reasons": dict(Counter(str(row.get("exit_reason") or "") for row in exits)),
        "fee_model_in_v16_executor": False,
    }


def write_outputs(capture_rows: list[dict[str, Any]], replays: list[dict[str, Any]]) -> None:
    manifest = _load(MANIFEST)
    comparable = [row for row in replays if "compare" in row]
    matched = [row for row in comparable if row["compare"]["equal"]]
    ready = bool(comparable) and len(matched) == len(comparable)
    verdict = (
        "CURRENT50_V16_COMPLETE_STRATEGY_REPLAY_READY_V1"
        if ready
        else "CURRENT50_V16_COMPLETE_STRATEGY_REPLAY_BLOCKED_V1"
    )
    nxt = (
        "COMPARE_CAPITAL_RISK_RULES_ON_PARITY_DAYS"
        if ready
        else "ALIGN_REPLAY_EVENT_STREAM_WITH_PAPER_CONSUMER_BEFORE_PNL"
    )
    complete_days = sum(
        1
        for row in capture_rows
        if row["status"] == "COMPLETE_CAPTURE" and row["coverage_am"] and row["coverage_pm"] and int(row["registration_coverage"] or 0) == 50
    )
    economics = []
    if ready:
        for day in V16_DAYS:
            ledgers = []
            for session in _paper_sessions(day):
                if session["activation_id"].endswith("V16"):
                    ledgers.extend(session["ledger"])
            economics.append({"day": day, **_economics(ledgers)})
    report = {
        "verdict": verdict,
        "next": nxt,
        "pnl_is_official_complete_strategy": False,
        "used_as_independent_holdout": False,
        "v16": {
            "activation_id": manifest["activation_id"],
            "sha256": manifest["sha256"],
            "entry_sha": ENTRY_SHA,
            "exit_sha": EXIT_SHA,
            "parent_complete_strategy_sha": "3002fdada09206568dd6de5e43f6e2c3317a55e40df2d66cdefef317a7c0fd0a",
            "complete_strategy_sha": safe_pm_complete_sha(),
            "execution_family": "X1_IMMEDIATE_ASK",
            "session_executor": "FixedSupportX1SessionExecutor",
            "research_simulate_session_is_not_the_paper_path": True,
            "shares": 100,
            "max_concurrent": 5,
            "am_signal_open": "09:00",
            "am_force_close": "11:25",
            "pm_signal_open": "12:30",
            "pm_flatten_start": "15:20:00",
            "pm_continuous_deadline": "15:25:00",
            "earliest_full_signal": "session_start + 150 seconds",
            "g_notional_gate_inside_executor": False,
            "runtime_inventory_matches_manifest": True,
            "submit_cancel_live": "0/0/0",
        },
        "capture_days": len(capture_rows),
        "complete_capture_days": complete_days,
        "v16_actual_days": list(V16_DAYS),
        "parity_sessions": len(matched),
        "compared_sessions": len(comparable),
        "formal_pnl_allowed": False,
        "economics_if_parity": economics,
        "replays": [
            {
                key: value
                for key, value in row.items()
                if key != "compare"
            }
            | {"compare": {k: v for k, v in row.get("compare", {}).items()}}
            for row in replays
        ],
        "capture": capture_rows,
        "PAPER_V16": "UNCHANGED",
        "RUNTIME_CHANGED": False,
        "submit_cancel_live": "0/0/0",
    }
    lines = [
        f"VERDICT: {verdict}",
        f"NEXT: {nxt}",
        "",
        "Paper V16 と Runtime は変更していません。submit/cancel/live = 0/0/0。",
        f"activation {manifest['activation_id']} sha256 {manifest['sha256']}",
        f"ENTRY_SHA {ENTRY_SHA}",
        f"EXIT_SHA {EXIT_SHA}",
        f"COMPLETE_STRATEGY_SHA {safe_pm_complete_sha()}",
        "実行体は FixedSupportX1SessionExecutor です。research simulate_session は V16 の Paper 経路ではありません。",
        "AM は 09:00–11:25、PM は 12:30 開始、15:20 からフラット、15:25 未満の Bid だけを EXIT に使います。",
        "100株、同時保有5、同一銘柄、占有、再ENTRY、枠解放は実行体の状態です。1,000,000円の建玉上限はこの実行体の ENTRY 条件にありません。",
        f"Capture日数 {len(capture_rows)}。AM/PM 両方と登録50が揃った COMPLETE は {complete_days} 日です。",
        f"V16 Actual Paper は {', '.join(V16_DAYS)} です。照合セッション {len(matched)} / {len(comparable)}。",
        "費用とスリッページは V16 の約定式に含まれていません。Gross の一致を Net PnL の公式評価にはしていません。",
        "既知日の再評価は独立holdoutではありません。AUTO50 の過去PnLには戻っていません。",
        "",
    ]
    for row in comparable:
        lines.append(
            f"{row['day']} {row['kind']} equal={row['compare']['equal']} actual={row['compare']['actual_rows']} replay={row['compare']['replay_rows']} watermark={row['watermark_seq']}"
        )
    report["markdown"] = "\n".join(lines) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(report["markdown"], encoding="utf-8")
    book = Workbook()
    sheet = book.active
    sheet.title = "replay"
    replay_rows = report["replays"]
    if replay_rows:
        header = ["day", "kind", "equal", "actual_rows", "replay_rows", "watermark_seq", "admission_n", "cap_blocked", "same_symbol_blocked", "unfilled_n", "trade_n"]
        sheet.append(header)
        for row in replay_rows:
            compare = row.get("compare") or {}
            sheet.append([
                row.get("day"), row.get("kind"), compare.get("equal"), compare.get("actual_rows"), compare.get("replay_rows"),
                row.get("watermark_seq"), row.get("admission_n"), row.get("cap_blocked"), row.get("same_symbol_blocked"),
                row.get("unfilled_n"), row.get("trade_n"),
            ])
    daily = book.create_sheet("capture")
    if capture_rows:
        header = list(capture_rows[0].keys())
        daily.append(header)
        for row in capture_rows:
            daily.append([row.get(key) for key in header])
    book.save(OUT / "audit.xlsx")
    print(verdict, len(matched), len(comparable), flush=True)


def main() -> None:
    capture_rows = inventory_capture()
    print(f"capture days {len(capture_rows)}", flush=True)
    replays = replay_v16()
    write_outputs(capture_rows, replays)


if __name__ == "__main__":
    main()
