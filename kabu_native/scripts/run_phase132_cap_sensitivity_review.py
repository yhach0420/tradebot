#!/usr/bin/env python3
"""Phase 132: max_concurrent cap sensitivity review (counterfactual only)."""

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
PILOT_CONFIG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"


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
    parser = argparse.ArgumentParser(description="Phase 132 cap sensitivity review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.cap_sensitivity_review import PHASE132_CAPS, analyze_cap_sensitivity
    from research.mfe_mae_exit_review import discover_sessions
    from small_paper.config import load_pilot_config

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    config = load_pilot_config(PILOT_CONFIG)
    result = analyze_cap_sensitivity(session_dirs, pilot_config=config, caps=PHASE132_CAPS)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase132_cap_sensitivity_review.json"
    summary_csv = REPORTS / "phase132_cap_sensitivity_summary.csv"
    rejected_csv = REPORTS / "phase132_rejected_due_to_cap.csv"

    report: dict[str, Any] = {
        "phase": 132,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "cap_increase_promising",
            "B": "cap3_still_best",
            "C": "need_position_sizing_model",
            "D": "insufficient_data",
        },
        "session_count": result["session_count"],
        "caps_compared": list(PHASE132_CAPS),
        "aggregate": result["aggregate"],
        "newly_accepted_vs_cap3": result["newly_accepted_vs_cap3"],
        "structural_overlap_replaced_total": result["structural_overlap_replaced_total_cap3_sessions"],
        "methodology": {
            "review_only": True,
            "production_cap_unchanged": 3,
            "min_quality": 0.70,
            "pnl_method": "virtual_hold_proxy",
            "overlap_proxy": "same_symbol_overlap_accept_count",
            "rejected_reason": "max_concurrent_positions",
        },
        "outputs": {
            "json": _rel(out_json),
            "summary_csv": _rel(summary_csv),
            "rejected_csv": _rel(rejected_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(summary_csv, result["aggregate"])
    _write_csv(rejected_csv, result["rejected_due_to_cap"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "aggregate": result["aggregate"],
                "session_count": result["session_count"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
