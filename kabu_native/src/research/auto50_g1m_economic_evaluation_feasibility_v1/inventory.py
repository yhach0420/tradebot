"""Read-only coverage inventory. Does not score, replay, or estimate PnL."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

NATIVE = Path(__file__).resolve().parents[3]
CAUSAL = NATIVE / "results" / "research" / "auto50_causal_freshness_reconciliation_v1" / "report.json"
CAPTURE = NATIVE / "data" / "market_capture"
RUNTIME = NATIVE / "runtime"
REPORTS = NATIVE / "results" / "reports"
OUT = NATIVE / "results" / "research" / "auto50_g1m_economic_evaluation_feasibility_v1"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def completeness_files(day: str) -> list[Path]:
    root = CAPTURE / day
    if not root.is_dir():
        return []
    return sorted(root.glob("session_*/capture_completeness.json"))


def frozen_symbols(day: str) -> list[str]:
    path = RUNTIME / f"same_day_am_frozen_universe_{day}.json"
    if not path.is_file():
        return []
    body = load_json(path)
    return [str(symbol) for symbol in body.get("canonical_symbols") or []]


def sample_push_keys(day: str) -> list[str]:
    root = CAPTURE / day
    parts = sorted(root.glob("session_*/push_part_0001.jsonl"))
    if not parts:
        return []
    with parts[0].open("rb") as handle:
        blob = handle.read(12000)
    text = blob.decode("utf-8", errors="replace")
    start = text.find("{")
    if start < 0:
        return []
    depth = 0
    for index, char in enumerate(text[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return []
                keys = sorted(str(key) for key in payload)
                inner = payload.get("payload") or payload.get("original_payload") or {}
                if isinstance(inner, dict):
                    keys.extend(f"payload.{key}" for key in sorted(inner))
                return keys
    return []


def main() -> None:
    causal = load_json(CAUSAL)
    days = [str(row["target_trading_date"]) for row in causal["daily"]]
    failed = list(causal.get("fetch_failed_symbols") or [])
    price_counts = (causal.get("price_audit") or {}).get("counts") or {}
    rows = []
    for day in days:
        files = completeness_files(day)
        symbols = frozen_symbols(day)
        feature = (REPORTS / f"features_{day}.csv").is_file()
        statuses = []
        am = False
        pm = False
        last_event = ""
        expected_end = ""
        registration = None
        research_ok = False
        for path in files:
            body = load_json(path)
            statuses.append(str(body.get("status") or ""))
            am = am or bool(body.get("coverage_am"))
            pm = pm or bool(body.get("coverage_pm"))
            last_event = str(body.get("actual_last_event_at") or last_event)
            expected_end = str(body.get("expected_end_at") or expected_end)
            if body.get("registration_coverage") is not None:
                registration = body.get("registration_coverage")
            research_ok = research_ok or bool(body.get("research_adoptable"))
        if not files and feature:
            kind = "DAILY_BARS_ONLY"
        elif not files:
            kind = "DATA_UNAVAILABLE"
        else:
            kind = "PARTIALLY_OBSERVED"
        rows.append(
            {
                "date": day,
                "class": kind,
                "capture_sessions": len(files),
                "capture_status": ",".join(statuses),
                "coverage_am": am,
                "coverage_pm": pm,
                "expected_end_at": expected_end,
                "actual_last_event_at": last_event,
                "registration_coverage": registration,
                "frozen_universe_n": len(symbols),
                "research_adoptable": research_ok,
                "feature_csv": feature,
                "fully_replayable": False,
            }
        )
    sample_day = next((row["date"] for row in rows if row["capture_sessions"]), "")
    keys = sample_push_keys(sample_day) if sample_day else []
    wanted = ["AskPrice", "BidPrice", "CurrentPriceTime", "SpecialQuote", "payload.AskPrice", "payload.BidPrice", "payload.CurrentPriceTime", "payload.SpecialQuote"]
    present = {name: name in keys for name in wanted}
    counts = {kind: sum(1 for row in rows if row["class"] == kind) for kind in ("FULLY_REPLAYABLE", "PARTIALLY_OBSERVED", "DAILY_BARS_ONLY", "DATA_UNAVAILABLE")}
    report = {
        "verdict": "AUTO50_G1M_COMPLETE_STRATEGY_EVALUATION_BLOCKED_BY_DATA_V1",
        "next": "KEEP_SELECTOR_FIXED_AND_DO_NOT_BUILD_PNL_WITHOUT_BOARD_COVERAGE",
        "pnl_computed": False,
        "used_as_independent_holdout": False,
        "selector": "AUTO50_G1M_CAUSAL_BASELINE_READY_V1",
        "target_days": len(days),
        "name_slots": len(days) * 50,
        "auto50_top50_membership_persisted": False,
        "board_slots_with_verified_quotes": None,
        "board_slot_count_reason": "AUTO50_G1M Top50 names were not saved. Capture push exists only for the registered CURRENT50 of that day.",
        "day_class_counts": counts,
        "capture_day_span_on_disk": "20260721-20261008",
        "fully_replayable_days": 0,
        "replay_classification": {
            "actual_paper": "Live session ledger. 20260820 Actual is 5 trades and remains ACTUAL_PAPER_WITH_RUNTIME_DEFECT.",
            "exact_replay": "P0-3 reads Capture JSONL through V1RNativeEntryLive. 20260820 Exact is 12 trades. That is not trade-by-trade parity with Actual.",
            "fast_replay": "P0-4 matches Exact on 20260810, 20260817, and 20260820. It does not match Actual.",
            "v16_research_replay": "research.event_time_impulse_fixed_entry_support_candidate_v1.simulate_session consumes prebuilt bid/ask books. It is not an Actual-parity ledger.",
            "v16_execution_family": "X1_IMMEDIATE_ASK",
            "v16_sha": "49fb47c67a1da39a99edfba8b88d66c2e8009a07d6db94c269d6e8cdd05a2240",
            "formal_complete_strategy_pnl_comparison_allowed": False,
        },
        "push_sample_day": sample_day,
        "push_sample_fields_present": present,
        "fetch_failed_n": len(failed),
        "fetch_failed_symbols": failed,
        "fetch_failed_rank_effect": "These names had no daily series, so the causal selector did not score them. They are absent from the eligible book, not assigned an unknown rank.",
        "provider_revision_rows": price_counts.get("PROVIDER_REVISION"),
        "provider_revision_rank_effect": "Unresolved stored-price rows were not removed from the eligible book. Their presence inside or outside Top50 is unknown. They are not treated as out of rank.",
        "additional_data": {
            "paid_contract": False,
            "historical_board_in_saved_store": "Only push JSONL for symbols registered that day.",
            "backfill": "Past sessions cannot be re-registered. Daily bars and one-minute bars do not contain Bid/Ask.",
            "one_minute_store_in_data": False,
        },
        "capital": {"BASE_CAPITAL_YEN": 500000, "MAX_LEVERAGE": 2.0, "G": 1000000, "QTY": 100, "MAX_CONCURRENT": 5},
        "PAPER_V16": "UNCHANGED",
        "RUNTIME_CHANGED": False,
        "submit_cancel_live": "0/0/0",
        "daily": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "VERDICT: AUTO50_G1M_COMPLETE_STRATEGY_EVALUATION_BLOCKED_BY_DATA_V1",
        "NEXT: KEEP_SELECTOR_FIXED_AND_DO_NOT_BUILD_PNL_WITHOUT_BOARD_COVERAGE",
        "",
        "損益は計算していません。Paper V16 は変更していません。",
        "CAPITAL: 500000 × 2.0 = 1000000。QTY=100。MAX_CONCURRENT=5。",
        f"対象: 既観測の{len(days)}日 × 50銘柄 = {len(days) * 50}枠。",
        "AUTO50_G1MのTop50名簿は成果物に保存されていません。",
        "板の有無を枠ごとに確定できないため、板あり件数は null です。",
        f"FULLY_REPLAYABLE: {counts['FULLY_REPLAYABLE']}",
        f"PARTIALLY_OBSERVED: {counts['PARTIALLY_OBSERVED']}",
        f"DAILY_BARS_ONLY: {counts['DAILY_BARS_ONLY']}",
        f"DATA_UNAVAILABLE: {counts['DATA_UNAVAILABLE']}",
        "Captureは登録されたCURRENT50のpushだけです。保存期間のフォルダは20260721から20261008です。",
        "20260820のExact Replayは12トレード、Actualは5トレードです。Actualとの一対一parityはありません。",
        "そのReplayはV1RNativeEntryLiveで、V16のX1_IMMEDIATE_ASKではありません。",
        "正式なComplete Strategy損益比較はできません。",
        "取得失敗45銘柄は日足系列がないためスコア対象外です。順位不明として圏外にはしていません。",
        f"価格未解決の保存行は{price_counts.get('PROVIDER_REVISION')}件で、Top50に入ったかは未確定です。",
        "過去日の板は再登録では戻りません。新規の有料契約は使っていません。",
        "submit/cancel/live: 0/0/0",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    book = Workbook()
    sheet = book.active
    sheet.title = "daily"
    sheet.append(list(rows[0].keys()))
    for row in rows:
        sheet.append(list(row.values()))
    failed_sheet = book.create_sheet("fetch_failed")
    failed_sheet.append(["symbol"])
    for symbol in failed:
        failed_sheet.append([symbol])
    book.save(OUT / "audit.xlsx")
    print(counts, "sample", sample_day, flush=True)


if __name__ == "__main__":
    main()
