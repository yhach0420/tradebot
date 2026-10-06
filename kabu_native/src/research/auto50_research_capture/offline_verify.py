"""Offline proof that existing Kabu tapes feed Frozen X1. No registration and no orders."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(r"C:\Users\yhach\Documents\tradebotfile\kabu_native")
sys.path.insert(0, str(NATIVE))
sys.path.insert(0, str(NATIVE / "src"))

from research.auto50_research_capture.mode import (  # noqa: E402
    MODE_ID,
    contract_gaps,
    exact_capture_record,
    execute_research_capture,
    plan_capture,
    rank_auto50,
    select_valid_auto50,
    to_x1_event,
)

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "research" / "auto50_existing_evidence_and_immediate_exact_capture_v1"
CAP = NATIVE / "data" / "market_capture" / "20260820"
FEATURES = NATIVE / "results" / "reports" / "features_20260910.csv"
SYM_RE = re.compile(br'"symbol":"([0-9A-Z]{4})(?:\.T)?"')
HISTORICAL = {
    "days": 35,
    "manifest_sha256": "4215c9141e410ff167d23eae06d207f0a35d39c562c39ad52178f34ad78af648",
    "median_exact_n": 43,
    "median_missing_n": 7,
    "observed_trades": 10741,
    "observed_only_net": 1822750,
    "baseline_net": 3820130,
    "decision_interval": [-20055630, 21878380],
    "complete_economics": "NOT_IDENTIFIED",
    "label": "HISTORICAL_PARTIAL_EXACT_REFERENCE",
}


def _largest_session(day_dir: Path) -> Path:
    totals: dict[Path, int] = {}
    for part in day_dir.rglob("push_part_*.jsonl"):
        totals[part.parent] = totals.get(part.parent, 0) + part.stat().st_size
    if not totals:
        raise SystemExit("capture_missing")
    return max(totals, key=totals.get)


def _self_checks() -> dict:
    ranked = rank_auto50(FEATURES, fixed=[])
    probe_calls = {"n": 0}

    def probe(symbol: str) -> dict:
        probe_calls["n"] += 1
        if symbol in {"1111", "2222"}:
            return {"verdict": "INVALID_SYMBOL"}
        if symbol == "3333":
            return {"verdict": "RATE_LIMIT"}
        return {"verdict": "VALID_SYMBOL"}

    book = [{"symbol": f"{i:04d}", "score": 1000 - i, "rank": i} for i in range(1, 60)]
    book[0]["symbol"] = "1111"
    book[1]["symbol"] = "2222"
    selected = select_valid_auto50(book, probe)
    blocked = select_valid_auto50([{"symbol": "3333", "score": 1, "rank": 1}], probe)
    plan = plan_capture(NATIVE, "20261007", FEATURES)
    denied_calls = {"n": 0}

    def denied_register(_symbols: list[str]) -> None:
        denied_calls["n"] += 1

    denied = execute_research_capture(
        plan["gate"],
        ranked["raw_auto50"],
        trading_date="20261007",
        probe_fn=probe,
        out_dir=OUT / "_denied_should_not_exist",
        register_fn=denied_register,
    )
    sample = exact_capture_record(
        {"CurrentPrice": 1000, "Buy1": {"Price": 999, "Qty": 100, "Sign": "0000"}, "Sell1": {"Price": 1001, "Qty": 100, "Sign": "0000"}},
        symbol="7203",
        received_at_jst="2026-10-07T09:00:00.000+09:00",
        sequence=1,
    )
    return {
        "raw_n": len(ranked["raw_auto50"]),
        "eligible_n": ranked["eligible_n"],
        "fixed_required": ranked["fixed_included"],
        "refill_ok": selected["ok"] and len(selected["valid_auto50"]) == 50,
        "dropped": [row["symbol"] for row in selected["drop_reason"]],
        "refilled": [row["symbol"] for row in selected["refill_reason"]],
        "temporary_fail_closed": blocked["ok"] is False and blocked["reason"] == "RATE_LIMIT",
        "gate_allow": plan["gate"]["allow"],
        "gate_detail": plan["gate"]["detail"],
        "kabu_called": plan["kabu_called"],
        "probe_calls_for_self_check_only": probe_calls["n"],
        "envelope_keeps_special_quote": "SpecialQuote" in sample and sample["SpecialQuote"] is None,
        "envelope_keeps_raw": sample["original_payload"]["CurrentPrice"] == 1000,
        "denied_gate_skips_register": denied["registered"] is False and denied_calls["n"] == 0 and not (OUT / "_denied_should_not_exist").exists(),
    }


def _replay(session_dir: Path) -> dict:
    from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor

    book = FixedSupportX1SessionExecutor()
    book.notifier = None
    book.ledger_path = None
    book.bind_production_schedule(day="20260820", kind="am")
    am_start, am_end = book.sess_start, book.sess_end
    pm_on = False
    pm_start = pm_end = None
    last_seq = -1
    order_breaks = 0
    fed = 0
    gaps: dict[str, int] = {}
    depth10 = 0
    special_absent = 0
    sampled = 0
    error = ""
    parts = sorted(session_dir.glob("push_part_*.jsonl"))
    try:
        for part in parts:
            with part.open("rb") as handle:
                for line in handle:
                    matched = SYM_RE.search(line)
                    if not matched:
                        continue
                    rec = json.loads(line)
                    event = to_x1_event(rec)
                    if event is None:
                        continue
                    seq = int(event["ingest_sequence"] or 0)
                    if seq < last_seq:
                        order_breaks += 1
                    last_seq = max(last_seq, seq)
                    if sampled < 200:
                        sampled += 1
                        for name in contract_gaps(rec):
                            gaps[name] = gaps.get(name, 0) + 1
                        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                        if isinstance(payload.get("Sell10"), dict) and isinstance(payload.get("Buy10"), dict):
                            depth10 += 1
                        if "SpecialQuote" not in payload and "special_quote" not in payload:
                            special_absent += 1
                    when = datetime.fromisoformat(str(event["received_at"]).replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=JST)
                    stamp = when.astimezone(JST).timestamp()
                    if not pm_on and am_start <= stamp < am_end:
                        book.on_market_event(event)
                        fed += 1
                        continue
                    if not pm_on and stamp >= am_end:
                        book.close_at_runtime_boundary(day="20260820", kind="am")
                        book.bind_production_schedule(day="20260820", kind="pm")
                        pm_start, pm_end = book.sess_start, book.sess_end
                        pm_on = True
                    if pm_on and pm_start is not None and pm_start <= stamp < float(pm_end):
                        book.on_market_event(event)
                        fed += 1
        if not pm_on:
            book.close_at_runtime_boundary(day="20260820", kind="am")
        else:
            book.close_at_runtime_boundary(day="20260820", kind="pm")
    except Exception as exc:
        error = type(exc).__name__
    counters = book.x1_counters()
    pnls = [float(trade["pnl_yen"]) for trade in book.trades]
    return {
        "day": "20260820",
        "session_dir": session_dir.name,
        "fed_events": fed,
        "order_breaks": order_breaks,
        "sample_n": sampled,
        "contract_gap_counts": gaps,
        "depth10_in_sample": depth10,
        "special_quote_absent_in_sample": special_absent,
        "signal_n": int(counters.get("x1_signal_seen_n") or 0),
        "fill_n": int(counters.get("x1_admission_n") or 0),
        "exit_n": int(counters.get("x1_exit_n") or 0),
        "pending_n": int(counters.get("x1_exit_pending_n") or 0),
        "cap_n": int(counters.get("x1_cap_blocked_n") or 0),
        "reentry_n": int(book.counts.get("reentry") or 0),
        "occupancy_n": int(book.counts.get("occupancy") or 0),
        "session_flat_n": int(counters.get("x1_session_flat_n") or 0),
        "trade_n": len(book.trades),
        "net_yen": sum(pnls),
        "error": error,
        "label": "OFFLINE_EXISTING_CAPTURE_REPLAY_NOT_AN_AUTO50_DAY",
    }


def _pass_flag(checks: dict, replay: dict) -> bool:
    return bool(
        checks["raw_n"] == 50
        and checks["refill_ok"]
        and checks["dropped"] == ["1111", "2222"]
        and checks["temporary_fail_closed"]
        and checks["gate_allow"] is False
        and checks["kabu_called"] is False
        and replay["error"] == ""
        and replay["order_breaks"] == 0
        and replay["fill_n"] > 0
        and replay["exit_n"] > 0
        and replay["fed_events"] > 0
        and set(replay["contract_gap_counts"]) <= {"SpecialQuote"}
        and checks["envelope_keeps_special_quote"]
        and checks["envelope_keeps_raw"]
        and checks["denied_gate_skips_register"]
    )


def _write(checks: dict, replay: dict, passed: bool) -> None:
    from openpyxl import Workbook

    verdict = "AUTO50_IMMEDIATE_EXACT_CAPTURE_FRAMEWORK_READY_V1" if passed else "AUTO50_CAPTURE_FRAMEWORK_OFFLINE_REPLAY_FAILED_V1"
    nxt = "COMPLETE_CURRENT_RUNTIME_OPERATIONAL_VALIDATION_THEN_START_AUTO50_DAY1_V1"
    report = {
        "ANALYSIS_ID": "AUTO50_EXISTING_EVIDENCE_AND_IMMEDIATE_EXACT_CAPTURE_V1",
        "VERDICT": verdict,
        "NEXT": nxt,
        "MODE": MODE_ID,
        "HISTORICAL_PARTIAL_EXACT_REFERENCE": HISTORICAL,
        "NEW_FULL_EXACT_AUTO50": {"days": 0, "cum_trades": 0, "cum_net": 0, "note": "Day1 has not started"},
        "answers": {
            "keep_existing_35d": True,
            "call_existing_35d_exact_complete": False,
            "capture_mode_implemented": True,
            "schema_saved_by_envelope": True,
            "cross_symbol_order_saved": True,
            "paper_conflict_fail_closed": checks["gate_allow"] is False,
            "offline_replay_pass": passed,
            "current_paper_validation_first": True,
            "day1_after_validation_session": True,
            "must_wait_20_days": False,
            "day1_exact_result": True,
            "day1_changes_strategy": False,
            "paid_data": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        },
        "self_checks": checks,
        "offline_replay": replay,
        "RUNTIME_CHANGED": False,
        "STRATEGY_CHANGED": False,
        "UNIVERSE_CHANGED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "paid_data_purchase": False,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    a = report["answers"]
    text = "\n".join(
        [
            "# AUTO50 existing evidence and immediate exact capture v1",
            "",
            f"VERDICT: `{verdict}`",
            "",
            f"NEXT: `{nxt}`",
            "",
            "既存35日は HISTORICAL_PARTIAL_EXACT_REFERENCE として残す。新しい FULL EXACT 日はまだ 0 日で、過去の本と混ぜない。",
            "",
            "## 14 answers",
            "",
            "1. 既存35日データは今後も使う。YES。",
            "2. 既存35日を AUTO50 exact complete strategy とは呼ばない。NO。",
            "3. AUTO50 full50 capture mode は研究用モジュールとして実装した。YES。",
            "4. Frozen X1 の必須フィールドは envelope と raw PUSH payload に保存する。YES。既存テープに SpecialQuote キーが無いイベントは、特別気配ではないものとして envelope 側で null を明示する。",
            "5. received_at と ingest sequence で銘柄横断の順序を保存する。YES。",
            "6. current Paper の登録が生きているあいだ、および運用検証が開いているあいだは AUTO50_CAPTURE_FAIL_CLOSED。今回の gate は allow=false で拒否した。",
            f"7. 既存 20260820 capture の offline replay は {'PASS' if passed else 'FAIL'}。",
            "8. 次の full session は CURRENT50 Paper を優先する。YES。",
            "9. その full day が成功した翌取引日から AUTO50 Day1 を開始できる。次のセッション自体は CURRENT50 Paper のまま。",
            "10. 20日待つ必要はない。NO。",
            "11. FULL_EXACT_AUTO50_DAY の当日から Exact replay を出す。YES。",
            "12. Day1 の結果で strategy は変えない。NO。",
            "13. 追加有料データは使わない。NO。",
            "14. submit/cancel/live は 0/0/0。",
            "",
            f"HISTORICAL_REFERENCE days=35 median exact n=43 observed trades=10741 observed-only net=1822750 baseline=3820130 complete economics=NOT_IDENTIFIED。",
            f"Offline replay trades={replay['trade_n']} fills={replay['fill_n']} exits={replay['exit_n']} cap={replay['cap_n']} order_breaks={replay['order_breaks']}。",
            "この replay の損益は AUTO50 Day1 ではない。",
            "",
            "```",
            f"VERDICT = {verdict}",
            f"NEXT = {nxt}",
            "RUNTIME_CHANGED = false",
            "STRATEGY_CHANGED = false",
            "UNIVERSE_CHANGED = false",
            "submit/cancel/live = 0/0/0",
            "```",
            "",
        ]
    )
    (OUT / "report.md").write_text(text, encoding="utf-8")
    book = Workbook()
    sheets = {
        "SUMMARY": [report["answers"] | {"VERDICT": verdict, "NEXT": nxt}],
        "HISTORICAL_REFERENCE": [HISTORICAL],
        "NEW_FULL_EXACT": [report["NEW_FULL_EXACT_AUTO50"]],
        "OFFLINE_REPLAY": [{k: v for k, v in replay.items() if k != "contract_gap_counts"} | {"contract_gaps": json.dumps(replay["contract_gap_counts"])}],
        "SELF_CHECKS": [checks],
    }
    first = True
    for name, rows in sheets.items():
        ws = book.active if first else book.create_sheet(name[:31])
        first = False
        ws.title = name[:31]
        cols: list[str] = []
        for row in rows:
            for key in row:
                if key not in cols:
                    cols.append(key)
        ws.append(cols)
        for row in rows:
            ws.append([json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], (dict, list)) else row[k] for k in cols])
    book.save(OUT / "audit.xlsx")


def main() -> None:
    checks = _self_checks()
    print("checks", checks, flush=True)
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        prior = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
        replay = prior["offline_replay"]
        passed = _pass_flag(checks, replay)
        _write(checks, replay, passed)
        print("PASS" if passed else "FAIL", flush=True)
        return
    replay = _replay(_largest_session(CAP))
    print("replay", {k: replay[k] for k in ("fed_events", "fill_n", "exit_n", "order_breaks", "error", "contract_gap_counts")}, flush=True)
    passed = _pass_flag(checks, replay)
    _write(checks, replay, passed)
    print("PASS" if passed else "FAIL", flush=True)


if __name__ == "__main__":
    main()
