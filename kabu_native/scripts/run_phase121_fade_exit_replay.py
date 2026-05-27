#!/usr/bin/env python3
"""Phase 121: Fade exit what-if replay (review only, no implementation)."""

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
    cols = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 121 fade exit replay")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_exit_replay import SCENARIOS, analyze_fade_replay
    from research.mfe_mae_exit_review import discover_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_fade_replay(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase121_fade_exit_replay.json"
    scen_csv = REPORTS / "phase121_fade_exit_scenarios.csv"
    detail_csv = REPORTS / "phase121_fade_exit_trade_detail.csv"

    report: dict[str, Any] = {
        "phase": 121,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "fade_exit_needs_revision",
            "B": "current_fade_exit_best",
            "C": "trail_after_take_promising",
            "D": "hold_longer_not_helpful",
        },
        "fade_trade_count": result["fade_trade_count"],
        "by_exit_reason": result["by_exit_reason"],
        "scenarios": [{"id": s[0], "label": s[1]} for s in SCENARIOS],
        "scenario_summaries": result["scenario_summaries"],
        "methodology": {
            "fade_reasons": list(result["by_exit_reason"].keys()),
            "hold_scenarios": "exit at actual_close + N seconds (price from events)",
            "giveback_scenarios": "from entry, exit when pnl <= peak * (1 - giveback)",
            "baseline": "A_current = realized structural trade pnl",
            "price_source": "small_paper_events.csv",
            "no_implementation": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "scenarios_csv": _rel(scen_csv),
            "detail_csv": _rel(detail_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(scen_csv, result["scenario_summaries"])
    _write_csv(detail_csv, result["trade_details"])

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
