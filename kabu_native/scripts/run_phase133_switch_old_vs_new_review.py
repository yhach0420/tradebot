#!/usr/bin/env python3
"""Phase 133: Cross-symbol switch old vs new review (read-only)."""

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
    parser = argparse.ArgumentParser(description="Phase 133 switch old vs new review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.mfe_mae_exit_review import discover_sessions
    from research.switch_old_vs_new_review import analyze_switch_old_vs_new

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_switch_old_vs_new(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase133_switch_old_vs_new_review.json"
    pairs_csv = REPORTS / "phase133_switch_pairs.csv"
    summary_csv = REPORTS / "phase133_switch_summary.csv"
    wrong_csv = REPORTS / "phase133_wrong_switch_examples.csv"

    report = {
        "phase": 133,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "switch_logic_reasonable",
            "B": "switch_logic_hurting_pnl",
            "C": "need_switch_priority_model",
            "D": "insufficient_switch_data",
        },
        "aggregate": result["aggregate"],
        "methodology": {
            "review_only": True,
            "switch_definition": (
                "old exit (overlap/fade) then different-symbol structural entry within 300s"
            ),
            "horizons_sec": [30, 60, 180, 300, "session_end"],
            "classification_horizon": "session_end",
        },
        "outputs": {
            "json": _rel(out_json),
            "pairs_csv": _rel(pairs_csv),
            "summary_csv": _rel(summary_csv),
            "wrong_csv": _rel(wrong_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(pairs_csv, result["switch_pairs"])
    _write_csv(summary_csv, result["summary_rows"])
    _write_csv(wrong_csv, result["wrong_switch_examples"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "switch_count": result["aggregate"].get("switch_count"),
                "wrong_rate": result["aggregate"].get("wrong_rate"),
                "total_delta": result["aggregate"].get("total_delta_new_minus_old"),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
