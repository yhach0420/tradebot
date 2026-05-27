#!/usr/bin/env python3
"""Phase 123: Conditional fade extension what-if review (no implementation)."""

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
    parser = argparse.ArgumentParser(description="Phase 123 conditional fade extension review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.conditional_fade_extension_review import analyze_conditional_fade_extension
    from research.mfe_mae_exit_review import discover_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_conditional_fade_extension(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase123_conditional_fade_extension_review.json"
    scen_csv = REPORTS / "phase123_conditional_fade_extension_scenarios.csv"
    detail_csv = REPORTS / "phase123_selected_trade_details.csv"

    report: dict[str, Any] = {
        "phase": 123,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "conditional_extension_promising",
            "B": "unconditional_extension_better_but_risky",
            "C": "conditional_rule_too_weak",
            "D": "current_exit_best",
        },
        "fade_trade_count": result["fade_trade_count"],
        "primary_rule": result["primary_rule"],
        "best_conditional_scenario": result.get("best_conditional_scenario"),
        "scenarios": result["scenarios"],
        "methodology": {
            "baseline_A": "realized fade exit pnl",
            "unconditional_B": "all fade trades exit at actual_close + 60s",
            "conditional_C": "extend +60s only when rule matches; else current exit",
            "sensitivity": "mfe thresholds 0.10/0.15/0.20/0.25 x overlap_false on/off",
            "no_implementation": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "scenarios_csv": _rel(scen_csv),
            "detail_csv": _rel(detail_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(scen_csv, result["scenarios"])
    _write_csv(detail_csv, result["selected_trade_details"])

    primary = next(
        (s for s in result["scenarios"] if s.get("scenario_id") == "C_mfe015_overlap_false"),
        {},
    )
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "fade_trades": result["fade_trade_count"],
                "primary_total_pnl": primary.get("total_pnl"),
                "primary_worsened_rate": primary.get("worsened_rate"),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
