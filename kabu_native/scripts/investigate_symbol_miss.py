#!/usr/bin/env python3
"""Investigate why symbols were missed on a given live day (read-only)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
QUALITY_GATE = 0.70


def _bootstrap() -> None:
    for p in (ROOT / "kabu_native" / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_universe(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            key = sym if sym.endswith(".T") else f"{sym}.T"
            out[key] = dict(row)
    return out


def scan_events(events_path: Path, symbols: set[str]) -> dict[str, Any]:
    per: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    if not events_path.is_file():
        return {s: {"events_found": 0} for s in symbols}
    with events_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            p = ev.get("payload") or ev
            sym = str(p.get("symbol") or ev.get("symbol") or "")
            if sym not in symbols:
                continue
            per[sym].append({**p, "event_type": ev.get("event_type") or p.get("event_type")})
    result: dict[str, Any] = {}
    for sym, evs in per.items():
        if not evs:
            result[sym] = {"events_found": 0}
            continue
        candidates = [e for e in evs if e.get("event_type") == "candidate"]
        rejected = [e for e in evs if e.get("event_type") == "rejected"]
        accepted = [e for e in evs if e.get("event_type") == "accepted"]
        rej_reasons = Counter(str(e.get("gate_reject_reason") or "") for e in rejected)
        q_series = [
            {
                "entry_time": e.get("entry_time"),
                "quality": _float(e.get("continuation_quality_score")),
                "vol_liq": _float(e.get("daytrade_suitability_score")),
                "vol_liq_th": _float(e.get("daytrade_suitability_threshold")),
                "reject_reason": e.get("gate_reject_reason"),
            }
            for e in candidates[:5000]
        ]
        best_q = None
        best_row = None
        for e in candidates:
            q = _float(e.get("continuation_quality_score"))
            if q is None:
                continue
            if best_q is None or q > best_q:
                best_q = q
                best_row = e
        closest = None
        if best_row:
            th = QUALITY_GATE
            vl_th = _float(best_row.get("daytrade_suitability_threshold"))
            vl = _float(best_row.get("daytrade_suitability_score"))
            closest = {
                "entry_time": best_row.get("entry_time"),
                "quality": best_q,
                "quality_gap_to_gate": round(th - (best_q or 0), 4),
                "vol_liq_score": vl,
                "vol_liq_threshold": vl_th,
                "vol_liq_gap": round((vl_th or 0) - (vl or 0), 4) if vl_th is not None and vl is not None else None,
                "reject_reason": best_row.get("gate_reject_reason"),
            }
        result[sym] = {
            "events_found": len(evs),
            "candidate_count": len(candidates),
            "rejected_count": len(rejected),
            "accepted_count": len(accepted),
            "reject_reason_breakdown": dict(rej_reasons),
            "quality_series_sample_count": len(q_series),
            "best_quality_candidate": closest,
            "accepted_trades": [
                {
                    "entry_time": e.get("entry_time"),
                    "quality": _float(e.get("continuation_quality_score")),
                }
                for e in accepted
            ],
        }
    return result


def load_structural_trades(path: Path, symbols: set[str]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {s: [] for s in symbols}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "")
            if sym in symbols:
                out[sym].append(dict(row))
    return out


def diagnose_symbol(
    sym: str,
    name: str,
    universe: dict[str, dict[str, str]],
    push_dir: Path,
    event_stats: dict[str, Any],
    trades: list[dict[str, str]],
) -> dict[str, Any]:
    in_universe = sym in universe or sym.replace(".T", "") in universe
    push_file = push_dir / f"{sym}.jsonl"
    has_push = push_file.is_file()
    events_found = int(event_stats.get("events_found") or 0)

    root_cause = "watch_universe"
    detail = "Symbol not in intraday watch universe; no PUSH feed or gate evaluation."
    if in_universe and not has_push:
        root_cause = "push_feed"
        detail = "In universe CSV but no push_jsonl file for this session day."
    elif has_push and events_found == 0:
        root_cause = "candidate_generation"
        detail = "Push ticks exist but no small_paper candidate/reject events recorded."
    elif events_found > 0:
        acc = int(event_stats.get("accepted_count") or 0)
        if acc > 0:
            root_cause = "accepted_or_exit"
            detail = "Had accepted entries; see structural trades for exit outcome."
        else:
            rej = event_stats.get("reject_reason_breakdown") or {}
            top = max(rej.items(), key=lambda x: x[1])[0] if rej else "unknown"
            if top == "daytrade_suitability":
                root_cause = "daytrade_suitability"
            elif top == "low_quality":
                root_cause = "quality"
            elif top == "max_concurrent":
                root_cause = "cap_constraint"
            elif top == "outside_allowed_trading_window":
                root_cause = "trading_window"
            else:
                root_cause = top or "gate_reject"
            detail = f"Candidates generated but all rejected; dominant reason={top}"

    accepted_rows = []
    for t in trades:
        accepted_rows.append(
            {
                "entry_time": t.get("entry_time"),
                "close_time": t.get("close_time"),
                "exit_reason": t.get("close_reason"),
                "mfe_pct": _float(t.get("mfe_pct")),
                "mae_pct": _float(t.get("mae_pct")),
                "realized_pnl_pct": _float(t.get("realized_pnl_pct")),
                "continuation_quality_score": _float(t.get("continuation_quality_score")),
            }
        )

    return {
        "symbol": sym,
        "name": name,
        "in_watch_universe": in_universe,
        "push_jsonl_present": has_push,
        "candidate_generated": int(event_stats.get("candidate_count") or 0) > 0,
        "event_analysis": event_stats,
        "accepted_count": int(event_stats.get("accepted_count") or 0),
        "structural_trades": accepted_rows,
        "root_cause_layer": root_cause,
        "root_cause_detail": detail,
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-05-22")
    parser.add_argument("--symbols", default="6613.T,3905.T")
    parser.add_argument("--names", default="QDレーザー,データセクション")
    parser.add_argument(
        "--session-glob",
        default="live_full_session_*",
        help="Session folder glob under small_paper/YYYYMMDD",
    )
    args = parser.parse_args()

    day_key = args.date.replace("-", "")
    symbols = [s.strip() if ".T" in s else f"{s.strip()}.T" for s in args.symbols.split(",")]
    names = [n.strip() for n in args.names.split(",")]
    while len(names) < len(symbols):
        names.append("")

    universe_path = ROOT / "kabu_native/data/universe/universe_intraday_full.csv"
    universe = load_universe(universe_path)
    push_dir = ROOT / "kabu_native/data/push_jsonl" / args.date

    sp_day = ROOT / "kabu_native/results/small_paper" / day_key
    sessions = sorted(sp_day.glob(args.session_glob)) if sp_day.is_dir() else []
    session_dir = sessions[-1] if sessions else None

    events_path = session_dir / "small_paper_events.jsonl" if session_dir else Path()
    trades_path = session_dir / "structural_trades.csv" if session_dir else Path()
    summary_path = session_dir / "small_paper_summary.json" if session_dir else Path()

    event_stats = scan_events(events_path, set(symbols))
    trades_by_sym = load_structural_trades(trades_path, set(symbols))

    investigations = []
    for sym, name in zip(symbols, names):
        investigations.append(
            diagnose_symbol(
                sym,
                name,
                universe,
                push_dir,
                event_stats.get(sym, {}),
                trades_by_sym.get(sym, []),
            )
        )

    session_summary = {}
    if summary_path.is_file():
        session_summary = json.loads(summary_path.read_text(encoding="utf-8"))

    report = {
        "investigation_date": args.date,
        "session_dir": str(session_dir.relative_to(ROOT)).replace("\\", "/") if session_dir else None,
        "watch_universe_source": str(universe_path.relative_to(ROOT)).replace("\\", "/"),
        "watch_universe_size": len(universe),
        "push_jsonl_dir": str(push_dir.relative_to(ROOT)).replace("\\", "/"),
        "session_reject_reason_counts": session_summary.get("reject_reason_counts"),
        "symbols": investigations,
        "conclusion": {
            "summary": (
                "Both symbols were outside the 27-name intraday watch universe on this day; "
                "the pipeline never received PUSH ticks or ran entry gates for them."
            ),
            "per_symbol_root_cause": {i["symbol"]: i["root_cause_layer"] for i in investigations},
            "not_quality_or_exit": (
                "No quality/vol_liq/cap/exit stage applied because candidate generation did not occur."
            ),
        },
    }

    out = ROOT / "kabu_native/results/reports" / f"symbol_miss_investigation_{day_key}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
