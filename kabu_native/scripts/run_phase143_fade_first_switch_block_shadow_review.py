#!/usr/bin/env python3
"""Phase 143: First cross-symbol fade switch block shadow A/B replay."""

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
SHADOW_CONFIG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_fade_first_switch_block_shadow.yaml"


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
    parser = argparse.ArgumentParser(description="Phase 143 fade first switch block shadow")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_first_switch_block_shadow_review import (
        analyze_fade_first_switch_block_shadow,
    )
    from research.replay_fidelity_review import discover_fidelity_sessions
    from small_paper.config import load_pilot_config

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_fidelity_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    config = load_pilot_config(SHADOW_CONFIG)
    result = analyze_fade_first_switch_block_shadow(session_dirs, pilot_config=config)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase143_fade_first_switch_block_shadow_review.json"
    events_csv = REPORTS / "phase143_fade_first_switch_block_events.csv"
    session_csv = REPORTS / "phase143_fade_first_switch_block_session_summary.csv"

    report: dict[str, Any] = {
        "phase": 143,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "fade_first_switch_block_shadow_ready",
            "B": "block_not_reproducing_phase142",
            "C": "too_many_good_first_switches_blocked",
            "D": "runner_support_missing",
        },
        "session_count": result["session_count"],
        "comparison": result["comparison"],
        "phase142_reference": result["comparison"].get("phase142_reference"),
        "methodology": {
            "review_only": True,
            "scenario_A": "combined_structural_exit_v1",
            "scenario_B": "combined_structural_exit_v1_fade_first_switch_block_shadow",
            "rule": "block first cross-symbol accepted per fade exit only",
            "no_full_block": True,
            "exits_unchanged": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "events_csv": _rel(events_csv),
            "session_summary_csv": _rel(session_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(events_csv, result["block_events"])
    _write_csv(session_csv, result["sessions"])

    print(
        json.dumps(
            {"verdict": result["verdict"], "comparison": result["comparison"]},
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
