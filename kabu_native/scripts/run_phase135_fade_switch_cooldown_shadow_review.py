#!/usr/bin/env python3
"""Phase 135: Fade-switch cooldown shadow replay validation (review only)."""

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
SHADOW_CONFIG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_fade_switch_cooldown_shadow.yaml"


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
    parser = argparse.ArgumentParser(description="Phase 135 fade switch cooldown shadow replay")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_switch_cooldown_shadow_review import analyze_fade_switch_cooldown_shadow
    from research.mfe_mae_exit_review import discover_sessions
    from small_paper.config import load_pilot_config

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    config = load_pilot_config(SHADOW_CONFIG)
    result = analyze_fade_switch_cooldown_shadow(session_dirs, pilot_config=config)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase135_fade_switch_cooldown_shadow_review.json"
    details_csv = REPORTS / "phase135_fade_switch_cooldown_trade_details.csv"
    session_csv = REPORTS / "phase135_fade_switch_cooldown_session_summary.csv"

    report: dict[str, Any] = {
        "phase": 135,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "cooldown_shadow_promising",
            "B": "review_only_gain_not_reproducible",
            "C": "state_machine_too_complex",
            "D": "current_switch_best",
        },
        "session_count": result["session_count"],
        "comparison": result["comparison"],
        "phase134_reference": {
            "best_scenario": "D_fade_switch_cooldown",
            "delta_total_vs_A": 93.4824,
            "fade_switch_count": 294,
        },
        "methodology": {
            "review_only": True,
            "scenario_A": "combined_structural_exit_v1 (current switch)",
            "scenario_B": "fade_switch_cooldown_shadow",
            "exits_unchanged": True,
            "cooldown_release_events": [
                "breakdown_detected",
                "new_high_after_fade",
                "new_mfe_created",
                "momentum_recovery",
                "giveback_exceeded",
            ],
            "no_fixed_time_cooldown": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "trade_details_csv": _rel(details_csv),
            "session_summary_csv": _rel(session_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(session_csv, result["sessions"])
    _write_csv(details_csv, result["trade_details"])

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
