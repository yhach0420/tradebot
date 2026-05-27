#!/usr/bin/env python3
"""Phase 120: MFE/MAE vs exit capture review (what-if only, no YAML changes)."""

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


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...] | None = None) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = fields or tuple(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cols), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 120 MFE/MAE exit review")
    parser.add_argument("--session-dir", action="append", default=None, help="Repeatable session path")
    parser.add_argument("--max-sessions", type=int, default=6)
    parser.add_argument("--day-stamp", default=None, help="Optional stamp for output filenames")
    args = parser.parse_args()

    _bootstrap()
    from research.mfe_mae_exit_review import analyze_sessions, discover_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions with structural_trades.csv"}, ensure_ascii=True))
        return 1

    result = analyze_sessions(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase120_mfe_mae_exit_review.json"
    trade_csv = REPORTS / "phase120_trade_mfe_mae.csv"
    reason_csv = REPORTS / "phase120_exit_reason_capture.csv"
    follow_csv = REPORTS / "phase120_post_exit_followthrough.csv"
    hyp_csv = REPORTS / "phase120_exit_improvement_hypotheses.csv"

    report: dict[str, Any] = {
        "phase": 120,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "exit_logic_needs_revision",
            "B": "exit_logic_reasonable",
            "C": "overlap_replace_needs_revision",
            "D": "momentum_fade_too_fast",
        },
        "sessions_analyzed": result["sessions"],
        "aggregate": result["aggregate"],
        "whatif_hypotheses": result["whatif_hypotheses"],
        "methodology": {
            "post_exit_horizons_sec": [30, 60, 180],
            "mfe_capture_rate": "exit_pnl / MFE when MFE > 0",
            "price_source": "small_paper_events.csv candidate/accepted current_price",
            "no_pilot_yaml_change": True,
            "no_entry_change": True,
            "no_pf_evaluation_goal": True,
        },
        "outputs": {
            "phase120_json": _rel(out_json),
            "trade_csv": _rel(trade_csv),
            "exit_reason_csv": _rel(reason_csv),
            "post_exit_csv": _rel(follow_csv),
            "hypotheses_csv": _rel(hyp_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(trade_csv, result["trade_rows"])
    _write_csv(reason_csv, result["exit_reason_capture"])
    _write_csv(follow_csv, result["post_exit_followthrough"])
    _write_csv(hyp_csv, result["whatif_hypotheses"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "sessions": len(session_dirs),
                "trades": result["aggregate"].get("trade_count"),
                "avg_mfe_capture_rate": result["aggregate"].get("avg_mfe_capture_rate"),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
