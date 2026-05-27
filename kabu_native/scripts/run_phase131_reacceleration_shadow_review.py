#!/usr/bin/env python3
"""Phase 131: Reacceleration shadow replay validation (review only)."""

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
SHADOW_CONFIG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_fade_watch_shadow.yaml"
PHASE127_REPORT = REPORTS / "phase127_fade_watch_shadow_test_report.json"


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
    parser = argparse.ArgumentParser(description="Phase 131 reacceleration shadow replay")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument(
        "--rerun-phase127",
        action="store_true",
        help="Also rerun Phase127 fade_watch shadow on same sessions for comparison",
    )
    args = parser.parse_args()

    _bootstrap()
    from research.mfe_mae_exit_review import discover_sessions
    from research.reacceleration_shadow_review import analyze_reacceleration_shadow
    from small_paper.config import load_pilot_config

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    config = load_pilot_config(SHADOW_CONFIG)
    result = analyze_reacceleration_shadow(
        session_dirs,
        pilot_config=config,
        phase127_report_path=PHASE127_REPORT,
        include_phase127_fade_watch=args.rerun_phase127,
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase131_reacceleration_shadow_review.json"
    details_csv = REPORTS / "phase131_reacceleration_trade_details.csv"
    session_csv = REPORTS / "phase131_reacceleration_session_summary.csv"

    report: dict[str, Any] = {
        "phase": 131,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "reacceleration_shadow_promising",
            "B": "review_only_gain_not_reproducible",
            "C": "data_density_insufficient",
            "D": "current_exit_still_best",
        },
        "session_count": result["session_count"],
        "comparison": result["comparison"],
        "phase127_reference": {
            "report_path": _rel(PHASE127_REPORT),
            "phase127_delta_total_pnl": result["comparison"].get("phase127_delta_total_pnl"),
            "phase131_delta_total_pnl": result["comparison"].get("delta_total_pnl"),
        },
        "sessions": result["sessions"],
        "methodology": {
            "review_only": True,
            "scenario_A": "combined_structural_exit_v1",
            "scenario_B": "reacceleration_shadow",
            "gate": "mfe_pct > 0.15 AND NOT breakdown_at_fade",
            "continue_signals": [
                "new_high_after_fade",
                "new_mfe_created",
                "momentum_recovery",
            ],
            "exit_signals": [
                "breakdown_detected",
                "giveback_exceeded",
                "fade_price_break",
                "session_close",
            ],
            "no_fixed_second_wait": True,
            "production_unchanged": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "details_csv": _rel(details_csv),
            "session_csv": _rel(session_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(details_csv, result["trade_details"])
    _write_csv(session_csv, result["sessions"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "comparison": result["comparison"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
