#!/usr/bin/env python3
"""Phase 126: State-based fade_watch exit review (event-driven, no fixed-time exit)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
SMALL_PAPER = NATIVE / "results" / "small_paper"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for row in rows:
        for k in row:
            if k not in cols:
                cols.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 126 state-based fade exit review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.mfe_mae_exit_review import discover_sessions
    from research.state_based_fade_exit_review import analyze_state_based_fade_exit

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_state_based_fade_exit(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase126_state_based_fade_exit_review.json"
    paths_csv = REPORTS / "phase126_state_machine_trade_paths.csv"
    rules_csv = REPORTS / "phase126_state_rule_results.csv"

    report: dict[str, Any] = {
        "phase": 126,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "state_based_fade_exit_promising",
            "B": "current_exit_best",
            "C": "state_signals_too_noisy",
            "D": "need_vwap_or_volume_features",
        },
        "fade_trade_count": result["fade_trade_count"],
        "vwap_available_rate": result["vwap_available_rate"],
        "scenario_summaries": result["scenario_summaries"],
        "exit_reason_counts": result["exit_reason_counts"],
        "methodology": {
            "no_fixed_time_exit": True,
            "observation_window": "session_end only (not timed exit trigger)",
            "state_machine": "fade_watch -> event-driven continue/exit",
            "giveback_frac": 0.25,
            "scenarios": {
                "A": "immediate fade exit (baseline)",
                "B": "full state_based fade_watch",
                "C": "MFE giveback only",
                "D": "reacceleration + giveback composite",
            },
            "no_implementation": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "paths_csv": _rel(paths_csv),
            "rules_csv": _rel(rules_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths_csv, result["trade_paths"])
    _write_csv(rules_csv, result["scenario_summaries"])

    best = max(result["scenario_summaries"], key=lambda s: float(s.get("total_pnl") or -1e9))
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "fade_trades": result["fade_trade_count"],
                "best_scenario": best.get("scenario_id"),
                "best_total_pnl": best.get("total_pnl"),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
