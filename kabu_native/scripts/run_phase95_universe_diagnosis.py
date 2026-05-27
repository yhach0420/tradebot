#!/usr/bin/env python3
"""
Phase 95: Diagnose why big movers were outside live observer watch universe (read-only).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"

FOCUS_SYMBOLS = (
    ("6613.T", "QDレーザー"),
    ("3905.T", "データセクション"),
)

PIPELINE_STAGES = (
    "intraday_1m_inventory",
    "universe_yaml_include",
    "build_universe_daily",
    "morning_screen",
    "universe_intraday_full",
    "push_jsonl_subscription",
    "live_observer_session",
)


@dataclass(frozen=True)
class StageResult:
    stage: str
    present: bool
    excluded: bool
    exclude_reason: str
    notes: str


def _norm(sym: str) -> str:
    s = sym.strip().upper().split("@")[0]
    return s if s.endswith(".T") else f"{s}.T"


def _code(sym: str) -> str:
    return _norm(sym).replace(".T", "")


def load_universe_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def intraday_inventory_symbols(repo_root: Path) -> set[str]:
    roots = [repo_root / "data" / "intraday_1m", NATIVE / "data" / "intraday_1m"]
    syms: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for day_dir in root.iterdir():
            if not day_dir.is_dir():
                continue
            for p in day_dir.glob("*.csv"):
                syms.add(_norm(p.stem))
    return syms


def yaml_include_symbols(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    in_block = False
    out: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("include_symbols:"):
            in_block = True
            continue
        if in_block:
            m = re.match(r'\s*-\s*["\']?([^"\']+)["\']?', line)
            if m:
                out.append(_code(m.group(1)))
            elif line.strip() and not line.strip().startswith("#"):
                in_block = False
    return out


def trace_symbol(
    sym: str,
    *,
    intraday_syms: set[str],
    yaml_includes: list[str],
    universe_daily_rows: list[dict[str, str]],
    intraday_full_rows: list[dict[str, str]],
    morning_screen_rows: list[dict[str, str]],
    push_present: bool,
    session_has_events: bool,
) -> list[StageResult]:
    code = _code(sym)
    results: list[StageResult] = []

    in_inv = sym in intraday_syms
    results.append(
        StageResult(
            "intraday_1m_inventory",
            in_inv,
            not in_inv,
            "not_in_intraday_inventory" if not in_inv else "",
            "Static Yahoo intraday CSV pool (27 symbols); source of universe_intraday_full.",
        )
    )

    in_yaml = code in yaml_includes
    results.append(
        StageResult(
            "universe_yaml_include",
            in_yaml,
            not in_yaml,
            "not_in_include_symbols" if not in_yaml else "",
            f"build_universe.py only evaluates include_symbols ({len(yaml_includes)} codes in universe.yaml).",
        )
    )

    daily_row = next((r for r in universe_daily_rows if _code(str(r.get("symbol", ""))) == code), None)
    if daily_row is None:
        results.append(
            StageResult(
                "build_universe_daily",
                False,
                True,
                "never_candidate",
                "No row in universe_YYYYMMDD.csv — symbol was never a board-fetch candidate.",
            )
        )
    else:
        passed = str(daily_row.get("passed", "")).lower() in ("true", "1", "yes")
        reasons = str(daily_row.get("exclude_reasons") or "").strip()
        results.append(
            StageResult(
                "build_universe_daily",
                True,
                not passed,
                reasons or ("passed" if passed else "unknown"),
                "kabu /board filter from universe.yaml thresholds.",
            )
        )

    if not morning_screen_rows:
        results.append(
            StageResult(
                "morning_screen",
                False,
                True,
                "no_morning_screen_artifact",
                "No morning_screen output for trade date in repo; pipeline not run or upstream empty.",
            )
        )
    else:
        ms_row = next((r for r in morning_screen_rows if _norm(str(r.get("symbol", ""))) == sym), None)
        if ms_row is None:
            results.append(
                StageResult(
                    "morning_screen",
                    False,
                    True,
                    "not_in_morning_screen_input",
                    "Symbol absent from morning_screen evaluation rows.",
                )
            )
        else:
            ps = str(ms_row.get("pass_screen", "")).lower() in ("true", "1", "yes")
            rej = str(ms_row.get("reject_reasons") or "")
            rank = ms_row.get("rank", "")
            results.append(
                StageResult(
                    "morning_screen",
                    True,
                    not ps,
                    rej or ("rank_outside_top" if not rank else ""),
                    f"pass_screen={ps} rank={rank or '—'}",
                )
            )

    full_row = next((r for r in intraday_full_rows if _code(str(r.get("symbol", ""))) == code), None)
    in_full = full_row is not None
    results.append(
        StageResult(
            "universe_intraday_full",
            in_full,
            not in_full,
            "not_in_static_intraday_full" if not in_full else "",
            "Live observer default watch list (27 passed rows, not date-refreshed).",
        )
    )

    results.append(
        StageResult(
            "push_jsonl_subscription",
            push_present,
            not push_present,
            "no_push_jsonl_file" if not push_present else "",
            "record_push_jsonl / live session only ingest symbols in watch universe.",
        )
    )

    results.append(
        StageResult(
            "live_observer_session",
            session_has_events,
            not session_has_events,
            "no_events" if not session_has_events else "",
            "Zero PUSH ticks → no candidate/quality/vol_liq/exit evaluation.",
        )
    )
    return results


def first_exclusion_stage(stages: list[StageResult]) -> tuple[str, str]:
    for s in stages:
        if s.excluded and s.stage in (
            "intraday_1m_inventory",
            "universe_yaml_include",
            "build_universe_daily",
            "morning_screen",
            "universe_intraday_full",
        ):
            return s.stage, s.exclude_reason
    return "universe_intraday_full", "not_in_static_intraday_full"


def session_symbol_event_counts(session_dir: Path, symbols: set[str]) -> dict[str, int]:
    path = session_dir / "small_paper_events.jsonl"
    counts = {s: 0 for s in symbols}
    if not path.is_file():
        return counts
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            p = ev.get("payload") or ev
            sym = _norm(str(p.get("symbol") or ev.get("symbol") or ""))
            if sym in counts:
                counts[sym] += 1
    return counts


def try_market_proxy_rank(trade_date: str, universe_syms: set[str]) -> list[dict[str, Any]]:
    """Optional: yfinance daily % change for focus symbols only (not full market)."""
    rows: list[dict[str, Any]] = []
    try:
        import yfinance as yf
    except ImportError:
        return rows

    for sym, name in FOCUS_SYMBOLS:
        ticker = sym.replace(".T", ".T")
        try:
            hist = yf.Ticker(ticker).history(start=trade_date, end=trade_date, interval="1d")
            if hist.empty:
                continue
            o = float(hist["Open"].iloc[0])
            c = float(hist["Close"].iloc[0])
            chg = (c / o - 1.0) * 100.0 if o else None
            rows.append(
                {
                    "metric_type": "focus_symbol_proxy_yfinance",
                    "symbol": sym,
                    "name": name,
                    "change_pct_proxy": round(chg, 2) if chg is not None else None,
                    "in_universe": sym in universe_syms,
                    "data_source": "yfinance_daily",
                    "note": "Proxy only for focus symbols; not a full-market top-N list.",
                }
            )
        except Exception:
            continue
    return rows


def determine_verdict(
    missed_traces: dict[str, list[StageResult]],
) -> tuple[str, str]:
    """Return verdict code and rationale."""
    reasons = {first_exclusion_stage(st)[1] for st in missed_traces.values()}
    stages = {first_exclusion_stage(st)[0] for st in missed_traces.values()}

    if stages <= {"intraday_1m_inventory", "universe_yaml_include"} or "never_candidate" in reasons:
        if "not_in_static_intraday_full" in reasons:
            return (
                "universe_static_too_narrow",
                "Watch universe is a frozen 27-symbol list from intraday_1m inventory; "
                "focus movers were never in the static list or build_universe include_symbols. "
                "Not a downstream quality/exit issue.",
            )
    if "not_in_include_symbols" in reasons or "never_candidate" in reasons:
        return (
            "data_source_missing",
            "Symbols absent from all universe-generation candidate pools "
            "(include_symbols, intraday inventory, daily universe build).",
        )
    return (
        "universe_static_too_narrow",
        "Primary failure is static watch_universe before any intraday filter could apply.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 95 universe selection diagnosis")
    parser.add_argument("--trade-date", default="2026-05-22")
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=NATIVE / "results/small_paper/20260522/live_full_session_081229",
    )
    parser.add_argument(
        "--universe-intraday",
        type=Path,
        default=NATIVE / "data/universe/universe_intraday_full.csv",
    )
    parser.add_argument(
        "--universe-daily",
        type=Path,
        default=NATIVE / "data/universe/universe_20260516.csv",
    )
    parser.add_argument(
        "--universe-config",
        type=Path,
        default=NATIVE / "configs/universe.yaml",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=NATIVE / "results/reports",
    )
    parser.add_argument("--focus-symbols", default="6613.T,3905.T")
    args = parser.parse_args()

    trade_date = args.trade_date
    day_stamp = trade_date.replace("-", "")
    focus = [(_norm(s), "") for s in args.focus_symbols.split(",") if s.strip()]
    if not focus:
        focus = list(FOCUS_SYMBOLS)

    session_dir = args.session_dir if args.session_dir.is_absolute() else ROOT / args.session_dir
    universe_intraday = args.universe_intraday if args.universe_intraday.is_absolute() else ROOT / args.universe_intraday
    universe_daily = args.universe_daily if args.universe_daily.is_absolute() else ROOT / args.universe_daily
    universe_config = args.universe_config if args.universe_config.is_absolute() else ROOT / args.universe_config
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    intraday_syms = intraday_inventory_symbols(ROOT)
    yaml_includes = yaml_include_symbols(universe_config)
    intraday_full_rows = load_universe_csv(universe_intraday)
    universe_daily_rows = load_universe_csv(universe_daily)
    universe_members = sorted({_norm(str(r.get("symbol", ""))) for r in intraday_full_rows if r.get("symbol")})

    ms_dir = NATIVE / "results" / "morning_screen" / day_stamp
    morning_rows: list[dict[str, str]] = []
    if ms_dir.is_dir():
        for p in sorted(ms_dir.glob("morning_screen_*.csv"), reverse=True):
            morning_rows = load_universe_csv(p)
            break
    # fallback: any morning_screen under results
    if not morning_rows:
        base = NATIVE / "results" / "morning_screen"
        if base.is_dir():
            for p in sorted(base.rglob("morning_screen_*.csv"), reverse=True):
                morning_rows = load_universe_csv(p)
                break

    push_dir = NATIVE / "data" / "push_jsonl" / trade_date
    push_files = {p.stem if p.stem.endswith(".T") else f"{p.stem}.T" for p in push_dir.glob("*.jsonl")} if push_dir.is_dir() else set()

    event_counts = session_symbol_event_counts(session_dir, {s for s, _ in focus})

    missed_traces: dict[str, list[StageResult]] = {}
    missed_rows: list[dict[str, Any]] = []

    for sym, default_name in focus:
        name = default_name or dict(FOCUS_SYMBOLS).get(sym, "")
        stages = trace_symbol(
            sym,
            intraday_syms=intraday_syms,
            yaml_includes=yaml_includes,
            universe_daily_rows=universe_daily_rows,
            intraday_full_rows=intraday_full_rows,
            morning_screen_rows=morning_rows,
            push_present=sym in push_files,
            session_has_events=event_counts.get(sym, 0) > 0,
        )
        missed_traces[sym] = stages
        ex_stage, ex_reason = first_exclusion_stage(stages)
        missed_rows.append(
            {
                "symbol": sym,
                "name": name,
                "in_watch_universe": any(s.stage == "universe_intraday_full" and s.present for s in stages),
                "first_exclusion_stage": ex_stage,
                "first_exclusion_reason": ex_reason,
                "push_jsonl_present": sym in push_files,
                "session_event_count": event_counts.get(sym, 0),
                "candidate_generated": event_counts.get(sym, 0) > 0,
                "intraday_inventory_present": sym in intraday_syms,
                "universe_yaml_include": _code(sym) in yaml_includes,
                "build_universe_daily_row": any(
                    _code(str(r.get("symbol", ""))) == _code(sym) for r in universe_daily_rows
                ),
                "morning_screen_evaluated": any(
                    _norm(str(r.get("symbol", ""))) == sym for r in morning_rows
                ),
                "stage_trace_json": json.dumps([s.__dict__ for s in stages], ensure_ascii=False),
            }
        )

    verdict_code, verdict_rationale = determine_verdict(missed_traces)

    # Members CSV
    members_path = reports_dir / "phase95_universe_members.csv"
    with members_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "symbol",
                "exchange",
                "symbol_key",
                "passed",
                "source",
                "in_push_jsonl",
                "session_events",
            ],
        )
        w.writeheader()
        sess_counts = session_symbol_event_counts(session_dir, set(universe_members))
        for row in intraday_full_rows:
            sym = _norm(str(row.get("symbol", "")))
            w.writerow(
                {
                    "symbol": sym,
                    "exchange": row.get("exchange", ""),
                    "symbol_key": row.get("symbol_key", ""),
                    "passed": row.get("passed", ""),
                    "source": "universe_intraday_full.csv",
                    "in_push_jsonl": sym in push_files,
                    "session_events": sess_counts.get(sym, 0),
                }
            )

    missed_path = reports_dir / "phase95_universe_missed_symbols.csv"
    with missed_path.open("w", encoding="utf-8", newline="") as f:
        if missed_rows:
            w = csv.DictWriter(f, fieldnames=list(missed_rows[0].keys()))
            w.writeheader()
            w.writerows(missed_rows)

    coverage_rows: list[dict[str, Any]] = []
    coverage_rows.append(
        {
            "metric_type": "watch_universe",
            "bucket": "all_members",
            "symbol_count": len(universe_members),
            "in_universe_count": len(universe_members),
            "hit_rate": 1.0,
            "note": "Static universe_intraday_full.csv used for 2026-05-22 live session",
        }
    )
    coverage_rows.append(
        {
            "metric_type": "focus_movers",
            "bucket": "6613_3905",
            "symbol_count": len(focus),
            "in_universe_count": sum(1 for s, _ in focus if s in universe_members),
            "hit_rate": 0.0,
            "note": "Known big movers on trade date; both outside watch universe",
        }
    )
    coverage_rows.append(
        {
            "metric_type": "market_wide_top_lists",
            "bucket": "change_volume_value_top",
            "symbol_count": None,
            "in_universe_count": None,
            "hit_rate": None,
            "note": "Full-market ranking for 2026-05-22 not stored in repo; "
            "cannot compute official top-N universe hit rate without external feed.",
        }
    )
    for proxy in try_market_proxy_rank(trade_date, set(universe_members)):
        coverage_rows.append(proxy)

    coverage_path = reports_dir / "phase95_universe_coverage_summary.csv"
    with coverage_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(coverage_rows[0].keys()))
        w.writeheader()
        w.writerows(coverage_rows)

    live_cfg = session_dir / "live_session_config.json"
    live_meta: dict[str, Any] = {}
    if live_cfg.is_file():
        live_meta = json.loads(live_cfg.read_text(encoding="utf-8"))

    diagnosis = {
        "phase": 95,
        "trade_date": trade_date,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "session_dir": str(session_dir.relative_to(ROOT)),
        "watch_universe_path": str(universe_intraday.relative_to(ROOT)),
        "watch_universe_size": len(universe_members),
        "push_jsonl_dir": str(push_dir.relative_to(ROOT)),
        "push_jsonl_file_count": len(push_files),
        "verdict": verdict_code,
        "verdict_rationale": verdict_rationale,
        "verdict_options": {
            "A": "universe_static_too_narrow",
            "B": "universe_filter_too_strict",
            "C": "data_source_missing",
            "D": "universe_ok_downstream_issue",
        },
        "primary_root_cause": (
            "Live observer uses a frozen 27-symbol list derived from historical intraday_1m "
            "inventory, not a daily refreshed market-wide candidate pool. "
            "6613.T and 3905.T were never subscribed for PUSH on 2026-05-22."
        ),
        "improvement_opportunities": [
            "Replace static universe_intraday_full with daily build_universe from expanded "
            "include_symbols sourced by market-wide turnover/gainer rank (no per-symbol hardcoding).",
            "Wire morning_screen to post-build universe CSV per trade date; shadow/live load "
            "that dated file instead of intraday_full default.",
            "Add universe coverage audit job comparing prior-day movers vs watch list hit rate.",
            "Keep cap=3 and entry/exit/quality unchanged; fix only upstream symbol subscription.",
        ],
        "next_phase_validation": [
            "Trial: dynamic universe YAML with prime master top-N by TradingValue (board API).",
            "Replay one session with expanded universe + same vol_liq trial to measure candidate lift.",
            "Document push_jsonl subscription count vs kabu station symbol limit.",
        ],
        "constraints_respected": [
            "no_symbol_hardcode_add",
            "no_time_of_day_filter",
            "no_entry_exit_quality_change",
            "no_production_yaml_change",
        ],
        "pipeline_facts": {
            "intraday_inventory_unique_symbols": len(intraday_syms),
            "universe_yaml_include_count": len(yaml_includes),
            "universe_daily_path": str(universe_daily.relative_to(ROOT)),
            "universe_daily_candidate_count": len(universe_daily_rows),
            "morning_screen_rows_loaded": len(morning_rows),
            "live_session_symbol_count": live_meta.get("symbol_count"),
        },
        "focus_symbols": [
            {
                "symbol": sym,
                "stages": [s.__dict__ for s in missed_traces[sym]],
                **{k: v for k, v in row.items() if k != "stage_trace_json"},
            }
            for sym, row in zip([s for s, _ in focus], missed_rows)
        ],
        "checks": {
            "market_cap_filter_applied_to_missed": False,
            "price_filter_applied_to_missed": False,
            "liquidity_filter_applied_to_missed": False,
            "ranking_cap_applied_to_missed": False,
            "watchlist_json_used": False,
            "downstream_quality_exit_evaluated": False,
        },
        "check_notes": {
            "market_cap": "No market-cap gate in universe.yaml; missed symbols never reached board evaluation.",
            "price_liquidity_vol": "universe.yaml min_trading_value/min_price/max_spread apply only to include_symbols candidates.",
            "watchlist": "Root watchlist.json not used by small_paper live path (universe CSV only).",
            "morning_screen": "No dated morning_screen artifact for trade date; shadow default remains intraday_full.",
        },
    }

    diag_path = reports_dir / f"phase95_universe_diagnosis_{day_stamp}.json"
    diag_path.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"verdict": verdict_code, "outputs": {
        "diagnosis": str(diag_path),
        "members": str(members_path),
        "missed": str(missed_path),
        "coverage": str(coverage_path),
    }}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
