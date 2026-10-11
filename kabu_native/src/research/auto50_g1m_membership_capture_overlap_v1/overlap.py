"""Rebuild the unsaved AUTO50_G1M roster and compare it with saved Capture.

The roster is RECONSTRUCTED_MEMBERSHIP. It is not a previously sealed list.
No complete-strategy PnL is computed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from openpyxl import Workbook

from research.auto50_causal_freshness_reconciliation_v1.reconcile import (
    G,
    bar_on,
    book_from_symbols,
    buckets_for,
    capital_ok,
    download_panel,
    load_master,
    match_stored,
    old_top50,
    paper_alive,
    read_feature_rows,
    research_yahoo_ticker,
    score_bar,
    split_label,
)
from research.daytrade50_capital_efficient_selector_v2.features import selection_calendar, snapshot_days
from research.daytrade50_capital_efficient_selector_v2.lineage import previous_jpx_session
from research.daytrade50_capital_efficient_selector_v2.policy import one_lot_notional
from research.daytrade50_capital_efficient_selector_v2.target_blind_g1m import _aggregate, _target_metrics, _series

NATIVE = Path(__file__).resolve().parents[3]
SAVED = NATIVE / "results" / "research" / "auto50_causal_freshness_reconciliation_v1" / "report.json"
CAPTURE = NATIVE / "data" / "market_capture"
RUNTIME = NATIVE / "runtime"
REPORTS = NATIVE / "results" / "reports"
V2_REPORT = (
    NATIVE
    / "results"
    / "research"
    / "daytrade50_capital_efficient_selector_v2"
    / "target_blind_validation_cap500k_lev2x_g1m"
    / "report.json"
)
OUT = NATIVE / "results" / "research" / "auto50_g1m_membership_capture_overlap_v1"
ENTRY_AM = 1
ENTRY_PM = 2
EXIT_BID = 4


def sha_rows(rows: list[Mapping[str, Any]]) -> str:
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def price_exceptions(panel: Mapping[str, list[dict[str, Any]]]) -> tuple[set[str], set[tuple[str, str]], int]:
    """Same stored-row classes as the causal audit. Conflicts stay excluded."""
    indexed = {symbol: buckets_for(rows) for symbol, rows in panel.items()}
    revisions: set[str] = set()
    asof: dict[tuple[str, str], float] = {}
    conflicts: set[tuple[str, str]] = set()
    revision_rows = 0
    for day in snapshot_days(REPORTS):
        for raw in read_feature_rows(day):
            code = str(raw.get("symbol") or "").split(".")[0].split("@")[0]
            if not code.isdigit() or code not in indexed:
                continue
            try:
                stored_close = float(raw["close"])
                stored_volume = float(raw["volume"])
            except (TypeError, ValueError, KeyError):
                continue
            hits = match_stored(indexed[code], stored_close, stored_volume)
            if not hits:
                revisions.add(code)
                revision_rows += 1
                continue
            if len(hits) != 1:
                continue
            key = (code, str(hits[0]["source_bar_date"]))
            previous = asof.get(key)
            if previous is None:
                asof[key] = stored_close
            elif abs(previous - stored_close) > max(0.05, abs(stored_close) * 1e-4):
                conflicts.add(key)
    return revisions, conflicts, revision_rows


def choose_session(day: str) -> Optional[Path]:
    root = CAPTURE / day
    if not root.is_dir():
        return None
    best: Optional[tuple[tuple, Path]] = None
    for comp in root.glob("session_*/capture_completeness.json"):
        body = json.loads(comp.read_text(encoding="utf-8"))
        size = sum(path.stat().st_size for path in comp.parent.glob("push_part_*.jsonl"))
        rank = (
            body.get("status") == "COMPLETE_CAPTURE",
            bool(body.get("research_adoptable")),
            bool(body.get("coverage_am") and body.get("coverage_pm")),
            size,
        )
        if best is None or rank > best[0]:
            best = (rank, comp.parent)
    return None if best is None else best[1]


def registered_symbols(session: Path, day: str) -> list[str]:
    trace = session / "ingress_register_api_trace.json"
    if trace.is_file():
        body = json.loads(trace.read_text(encoding="utf-8"))
        rows = ((body.get("response_body") or {}).get("RegistList") or [])
        symbols = [str(row.get("Symbol") or "") for row in rows if row.get("Symbol")]
        if symbols:
            return symbols
    frozen = RUNTIME / f"same_day_am_frozen_universe_{day}.json"
    if frozen.is_file():
        body = json.loads(frozen.read_text(encoding="utf-8"))
        return [str(symbol) for symbol in body.get("canonical_symbols") or []]
    return []


def _stamp(line: bytes, at: int) -> Optional[int]:
    # "event_time":"YYYY-MM-DDTHH:MM:SS
    if at < 0 or at + 32 >= len(line):
        return None
    hour = line[at + 25 : at + 27]
    minute = line[at + 28 : at + 30]
    second = line[at + 31 : at + 33]
    if not (hour.isdigit() and minute.isdigit() and second.isdigit()):
        return None
    return int(hour) * 10000 + int(minute) * 100 + int(second)


def _price(line: bytes, key: bytes, start: int) -> Optional[float]:
    at = line.find(key, start)
    if at < 0:
        return None
    at += len(key)
    end = at
    limit = min(len(line), at + 24)
    while end < limit and line[end] in b"0123456789.-":
        end += 1
    if end == at:
        return None
    try:
        return float(line[at:end])
    except ValueError:
        return None


def scan_quotes(session: Path, wanted: set[str]) -> dict[str, int]:
    flags = {symbol: 0 for symbol in wanted}
    if not wanted:
        return flags
    encoded = {symbol.encode("ascii"): symbol for symbol in wanted}
    marker = b'"symbol":"'
    event_key = b'"event_time":"'
    ask_key = b'"AskPrice":'
    bid_key = b'"BidPrice":'
    for path in sorted(session.glob("push_part_*.jsonl")):
        with path.open("rb") as handle:
            for line in handle:
                at = line.find(marker)
                if at < 0 or at + 14 > len(line):
                    continue
                symbol = encoded.get(line[at + 10 : at + 14])
                if symbol is None:
                    continue
                stamp = _stamp(line, line.find(event_key))
                if stamp is None:
                    continue
                state = flags[symbol]
                if state != ENTRY_AM | ENTRY_PM | EXIT_BID:
                    ask = _price(line, ask_key, at)
                    bid = _price(line, bid_key, at)
                    if ask is not None and ask > 0:
                        if 90000 <= stamp <= 112500:
                            state |= ENTRY_AM
                        elif 123000 <= stamp < 152000:
                            state |= ENTRY_PM
                    if bid is not None and bid > 0 and 152000 <= stamp < 152500:
                        state |= EXIT_BID
                    flags[symbol] = state
    return flags


def reconstruct(panel, meta, alpha, sessions, days, names, revisions, conflicts, failed) -> dict[str, Any]:
    series = {symbol: _series(rows) for symbol, rows in panel.items() if rows}
    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    saved_by_day = {row["target_trading_date"]: row for row in saved["daily"]}
    membership: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    books = []
    range_mismatch = []
    alpha_mismatch = []
    replace_mismatch = []
    for day in days:
        required = previous_jpx_session(sessions, day)
        scored = []
        if required is None:
            daily.append({"date": day, "status": "NO_PREVIOUS_SESSION", "membership_certain": False})
            continue
        for code, info in meta.items():
            if code in failed or (code, required) in conflicts:
                continue
            bar = bar_on(panel.get(code) or [], required)
            if bar is None:
                continue
            item = score_bar(code, info, bar)
            if item is None or item["source_bar_date"] != required or item["source_bar_date"] >= day:
                continue
            scored.append(item)
        scored.sort(key=lambda item: (-float(item["score"]), str(item["symbol"])))
        g_pool = [item for item in scored if one_lot_notional(float(item["prior_close"])) <= G]
        g_pool.sort(key=lambda item: (-float(item["score"]), str(item["symbol"])))
        chosen = g_pool[:50]
        boundary = float(g_pool[49]["score"]) if len(g_pool) >= 50 else None
        revision_inside = sorted(item["symbol"] for item in chosen if item["symbol"] in revisions)
        revision_scored = sorted(item["symbol"] for item in scored if item["symbol"] in revisions)
        certain = len(chosen) == 50 and not revision_scored and required < day
        targets = {}
        closes = {}
        for item in chosen:
            targets[item["symbol"]] = _target_metrics(series[item["symbol"]], day) or {}
            closes[item["symbol"]] = float(item["prior_close"])
        book = book_from_symbols([item["symbol"] for item in chosen], closes, targets) if len(chosen) == 50 else {}
        if book.get("range_median") is not None:
            books.append(book)
        old = {item["symbol"] for item in old_top50(day)}
        new = {item["symbol"] for item in chosen}
        alpha_selected = sorted(symbol for symbol in new if symbol in alpha)
        replacements = len(old.symmetric_difference(new)) // 2
        prior = saved_by_day.get(day) or {}
        if prior.get("g1m_range") is not None and book.get("range_median") is not None:
            if abs(float(prior["g1m_range"]) - float(book["range_median"])) > 1e-6:
                range_mismatch.append(day)
        if prior.get("g1m_alpha_selected") is not None and alpha_selected != list(prior["g1m_alpha_selected"]):
            alpha_mismatch.append(day)
        if prior.get("old_vs_g1m_replacements") is not None and replacements != int(prior["old_vs_g1m_replacements"]):
            replace_mismatch.append(day)
        rows = []
        for rank, item in enumerate(chosen, start=1):
            lot = one_lot_notional(float(item["prior_close"]))
            rows.append(
                {
                    "label": "RECONSTRUCTED_MEMBERSHIP",
                    "target_trading_date": day,
                    "rank": rank,
                    "symbol": item["symbol"],
                    "company_name": names.get(item["symbol"], ""),
                    "prior_session": required,
                    "prior_close": item["prior_close"],
                    "score": item["score"],
                    "one_lot_notional": lot,
                    "G_eligible": lot <= G,
                    "split_status": item.get("split_status") or split_label(float(item.get("factor") or 1.0)),
                    "capture_reason": "NO_CAPTURE_SESSION",
                    "entry_quote": False,
                    "exit_bid_before_1525": False,
                }
            )
        digest = sha_rows(rows)
        for row in rows:
            row["membership_sha"] = digest
        membership.extend(rows)
        daily.append(
            {
                "date": day,
                "prior_session": required,
                "selected_n": len(chosen),
                "membership_sha": digest,
                "membership_certain": certain,
                "status": "MEMBERSHIP_UNCERTAIN" if revision_scored else ("RECONSTRUCTED" if len(chosen) == 50 else "UNDER_50"),
                "revision_symbols_scored": len(revision_scored),
                "revision_symbols_in_top50": revision_inside,
                "boundary_score": boundary,
                "g1m_range": book.get("range_median"),
                "alpha_selected": alpha_selected,
                "old_replacements": replacements,
                "failed_in_top50": sorted(symbol for symbol in new if symbol in failed),
            }
        )
        print(f"roster {day} n {len(chosen)} certain {certain}", flush=True)
    aggregate = _aggregate(books)
    saved_range = (saved.get("books") or {}).get("g1m", {}).get("range_median")
    return {
        "membership": membership,
        "daily": daily,
        "aggregate_range": aggregate.get("range_median"),
        "saved_range": saved_range,
        "range_mismatch_days": range_mismatch,
        "alpha_mismatch_days": alpha_mismatch,
        "replacement_mismatch_days": replace_mismatch,
        "fingerprint_match": saved_range is not None
        and aggregate.get("range_median") is not None
        and abs(float(saved_range) - float(aggregate["range_median"])) <= 1e-6
        and not range_mismatch
        and not alpha_mismatch
        and not replace_mismatch,
    }


def attach_capture(daily: list[dict[str, Any]], membership: list[dict[str, Any]]) -> None:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in membership:
        by_day.setdefault(row["target_trading_date"], []).append(row)
    for row in daily:
        day = row["date"]
        selected = [item["symbol"] for item in by_day.get(day, [])]
        session = choose_session(day)
        if session is None or len(selected) != 50:
            row.update(
                {
                    "capture_class": "NO_CAPTURE" if session is None else "PARTIAL_ONLY",
                    "registered_n": 0,
                    "overlap_n": 0,
                    "overlap_rate": 0.0,
                    "quote_entry_n": 0,
                    "quote_exit_bid_n": 0,
                    "full_path_n": 0,
                }
            )
            continue
        registered = registered_symbols(session, day)
        registered_set = set(registered)
        overlap = [symbol for symbol in selected if symbol in registered_set]
        flags = scan_quotes(session, set(overlap))
        entry_n = sum(1 for symbol in overlap if flags.get(symbol, 0) & (ENTRY_AM | ENTRY_PM))
        exit_n = sum(1 for symbol in overlap if flags.get(symbol, 0) & EXIT_BID)
        full_n = sum(1 for symbol in overlap if flags.get(symbol, 0) & (ENTRY_AM | ENTRY_PM) and flags.get(symbol, 0) & EXIT_BID)
        comp = json.loads((session / "capture_completeness.json").read_text(encoding="utf-8"))
        for item in by_day[day]:
            symbol = item["symbol"]
            if symbol not in registered_set:
                item["capture_reason"] = "NOT_REGISTERED"
                item["entry_quote"] = False
                item["exit_bid_before_1525"] = False
            else:
                state = flags.get(symbol, 0)
                item["entry_quote"] = bool(state & (ENTRY_AM | ENTRY_PM))
                item["exit_bid_before_1525"] = bool(state & EXIT_BID)
                if not item["entry_quote"] and not item["exit_bid_before_1525"]:
                    item["capture_reason"] = "REGISTERED_NO_USABLE_QUOTE"
                elif not item["entry_quote"]:
                    item["capture_reason"] = "NO_ASK_IN_ENTRY_WINDOW"
                elif not item["exit_bid_before_1525"]:
                    item["capture_reason"] = "NO_BID_BEFORE_1525"
                else:
                    item["capture_reason"] = "QUOTE_PATH_PRESENT"
        row.update(
            {
                "capture_session": session.name,
                "capture_status": comp.get("status"),
                "coverage_am": bool(comp.get("coverage_am")),
                "coverage_pm": bool(comp.get("coverage_pm")),
                "registered_n": len(registered_set),
                "overlap_n": len(overlap),
                "overlap_rate": len(overlap) / 50.0,
                "quote_entry_n": entry_n,
                "quote_exit_bid_n": exit_n,
                "full_path_n": full_n,
                "capture_class": "PARTIAL_ONLY",
            }
        )
        print(f"capture {day} overlap {len(overlap)} quotes {full_n}", flush=True)


def write_outputs(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    public = {key: value for key, value in report.items() if key != "membership"}
    public["membership_count"] = len(report["membership"])
    (OUT / "report.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(report["markdown"], encoding="utf-8")
    book = Workbook()
    sheet = book.active
    sheet.title = "membership"
    rows = report["membership"]
    if rows:
        header = list(rows[0].keys())
        sheet.append(header)
        for row in rows:
            sheet.append([row.get(key) for key in header])
    daily = book.create_sheet("daily")
    if report["daily"]:
        header = list(report["daily"][0].keys())
        daily.append(header)
        for row in report["daily"]:
            daily.append([json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key) for key in header])
    book.save(OUT / "audit.xlsx")


def main() -> None:
    if paper_alive():
        print("paper alive", flush=True)
        return
    identity = capital_ok()
    active, _rejected = load_master()
    meta = {row["code"]: row for row in active}
    names = {row["code"]: row["name"] for row in active}
    alpha = {row["code"] for row in active if row["code"][3].isalpha()}
    panel, failed = download_panel(
        [research_yahoo_ticker(row["code"]) for row in active],
        date(2026, 3, 1),
        date(2026, 10, 9),
    )
    failed_set = set(failed)
    sessions = sorted({row["source_bar_date"] for symbol in ("1301", "7203") for row in panel.get(symbol, [])})
    saved_v2 = json.loads(V2_REPORT.read_text(encoding="utf-8"))
    unavailable = set(saved_v2.get("target_close_unavailable_dates") or [])
    session_set = set(sessions)
    days = [day for day in selection_calendar(REPORTS) if day in session_set and day <= "20261007" and day not in unavailable]
    revisions, conflicts, revision_rows = price_exceptions(panel)
    print(f"days {len(days)} revision_symbols {len(revisions)} revision_rows {revision_rows} failed {len(failed_set)}", flush=True)
    built = reconstruct(panel, meta, alpha, sessions, days, names, revisions, conflicts, failed_set)
    attach_capture(built["daily"], built["membership"])
    certain_days = sum(1 for row in built["daily"] if row.get("membership_certain"))
    overlap_slots = sum(int(row.get("overlap_n") or 0) for row in built["daily"])
    quote_slots = sum(int(row.get("full_path_n") or 0) for row in built["daily"])
    rates = [float(row.get("overlap_rate") or 0.0) for row in built["daily"]]
    partial_days = sum(1 for row in built["daily"] if row.get("capture_class") == "PARTIAL_ONLY")
    full_days = sum(
        1
        for row in built["daily"]
        if row.get("membership_certain") and int(row.get("full_path_n") or 0) == 50 and int(row.get("overlap_n") or 0) == 50
    )
    roster_unique = (
        bool(built["fingerprint_match"])
        and len(days) == 87
        and all(int(row.get("selected_n") or 0) == 50 for row in built["daily"])
    )
    verdict = (
        "AUTO50_G1M_MEMBERSHIP_AND_CAPTURE_OVERLAP_RESOLVED_V1"
        if roster_unique
        else "AUTO50_G1M_MEMBERSHIP_RECONSTRUCTION_INCOMPLETE_V1"
    )
    nxt = (
        "CLOSE_AUTO50_PAST_PNL_AND_MOVE_TO_CURRENT50_CAPTURE_V16_RESEARCH"
        if full_days == 0
        else "DATA_COMPLETE_DAYS_EXIST_BUT_DO_NOT_COMPUTE_PNL_IN_THIS_PHASE"
    )
    if not roster_unique:
        nxt = "DO_NOT_TREAT_RECONSTRUCTION_AS_UNIQUE"
    report = {
        "verdict": verdict,
        "next": nxt,
        "label": "RECONSTRUCTED_MEMBERSHIP",
        "pnl_computed": False,
        "used_as_independent_holdout": False,
        "capital_match": identity["match"],
        "CAPITAL": {"BASE_CAPITAL_YEN": 500000, "MAX_LEVERAGE": 2.0, "G": 1000000, "QTY": 100, "MAX_CONCURRENT": 5},
        "target_days": len(days),
        "membership_certain_days": certain_days,
        "name_slots": len(built["membership"]),
        "capture_overlap_slots": overlap_slots,
        "quote_path_slots": quote_slots,
        "mean_overlap_rate": (sum(rates) / len(rates)) if rates else None,
        "full_replay_days": full_days,
        "partial_only_days": partial_days,
        "fingerprint_match": built["fingerprint_match"],
        "aggregate_range": built["aggregate_range"],
        "saved_range": built["saved_range"],
        "range_mismatch_days": built["range_mismatch_days"],
        "alpha_mismatch_days": built["alpha_mismatch_days"],
        "replacement_mismatch_days": built["replacement_mismatch_days"],
        "revision_symbol_n": len(revisions),
        "revision_rows": revision_rows,
        "saved_provider_revision_rows": 1175,
        "fetch_failed_n": len(failed_set),
        "fetch_failed_in_any_top50": sorted({symbol for row in built["daily"] for symbol in row.get("failed_in_top50") or []}),
        "formal_complete_strategy_pnl_allowed": False,
        "actual_vs_v1r_not_used_as_v16_proof": True,
        "PAPER_V16": "UNCHANGED",
        "RUNTIME_CHANGED": False,
        "submit_cancel_live": "0/0/0",
        "daily": built["daily"],
        "membership": built["membership"],
        "markdown": "",
    }
    report["markdown"] = "\n".join(
        [
            f"VERDICT: {verdict}",
            f"NEXT: {nxt}",
            "",
            "ラベル: RECONSTRUCTED_MEMBERSHIP。過去に正式保存された名簿ではありません。",
            "損益は計算していません。Paper V16 と Runtime は変更していません。",
            "CAPITAL: 500000 × 2.0 = 1000000。QTY=100。MAX_CONCURRENT=5。",
            f"名簿を確定できた日数: {certain_days} / {len(days)}",
            f"4350枠のうち登録Captureと重なった枠: {overlap_slots}",
            f"ENTRYのAskと15:25未満のBidが両方あった枠: {quote_slots}",
            f"平均重複率: {report['mean_overlap_rate']}",
            f"Full Replay可能日数: {full_days}",
            f"Partial Only日数: {partial_days}",
            f"保存済み集計との一致: {built['fingerprint_match']}",
            f"価格未解決の銘柄数: {len(revisions)}。スコア対象に入った日は MEMBERSHIP_UNCERTAIN です。",
            "取得失敗銘柄は日足がないためTop50に入れていません。順位を補正していません。",
            "Actual PaperとV1R Exact Replayの差は、V16のReplay一致の証拠にしていません。",
            "正式なComplete Strategy損益比較はできません。",
            "submit/cancel/live: 0/0/0",
            "",
        ]
    )
    write_outputs(report)
    print(verdict, certain_days, overlap_slots, quote_slots, flush=True)


if __name__ == "__main__":
    main()
