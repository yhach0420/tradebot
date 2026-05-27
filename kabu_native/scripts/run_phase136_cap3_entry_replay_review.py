#!/usr/bin/env python3
"""Phase 136: cap=3 ExposureGate entry replay for fade switch validation."""

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
PHASE134_PAIRS = REPORTS / "phase134_fade_switch_pairs.csv"


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
    parser = argparse.ArgumentParser(description="Phase 136 cap3 entry replay review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--phase134-pairs", default=str(PHASE134_PAIRS))
    args = parser.parse_args()

    _bootstrap()
    from research.cap3_entry_replay_review import analyze_cap3_entry_replay
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
    result = analyze_cap3_entry_replay(
        session_dirs,
        pilot_config=config,
        phase134_pairs_path=Path(args.phase134_pairs),
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase136_cap3_entry_replay_review.json"
    events_csv = REPORTS / "phase136_cap3_entry_replay_events.csv"
    match_csv = REPORTS / "phase136_switch_match_diagnostics.csv"
    summary_csv = REPORTS / "phase136_scenario_summary.csv"

    report: dict[str, Any] = {
        "phase": 136,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "fade_switch_cooldown_promising_under_cap3",
            "B": "cooldown_not_helpful_under_realistic_gate",
            "C": "replay_mismatch_with_phase134",
            "D": "need_live_session_replay_engine_fix",
        },
        "session_count": result["session_count"],
        "match_stats": result["match_stats"],
        "scenario_summary": result["summary_rows"],
        "sessions": result["sessions"],
        "methodology": {
            "review_only": True,
            "max_concurrent": 3,
            "exposure_gate": True,
            "structural_exit": "combined_structural_exit_v1",
            "scenarios": {
                "A_current": "fade exit then next candidate allowed",
                "B_fade_switch_cooldown": "cross-symbol blocked until old state release (min 2 ticks)",
                "C_fade_switch_block": "cross-symbol blocked until release",
            },
            "release_events": [
                "old_breakdown_confirmed",
                "old_reacceleration_confirmed",
                "old_no_post_fade_ticks",
                "session_close",
            ],
            "no_fixed_time_cooldown": True,
        },
        "phase134_reference": {"pair_count": 294, "cooldown_delta_vs_A": 93.4824},
        "outputs": {
            "json": _rel(out_json),
            "events_csv": _rel(events_csv),
            "match_csv": _rel(match_csv),
            "summary_csv": _rel(summary_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(summary_csv, result["summary_rows"])
    _write_csv(match_csv, result["match_diagnostics"])
    _write_csv(events_csv, result["event_log"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "match_stats": result["match_stats"],
                "scenario_summary": result["summary_rows"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
