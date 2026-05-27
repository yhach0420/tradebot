#!/usr/bin/env python3
"""Phase 137: Live vs replay fidelity diagnosis."""

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
    parser = argparse.ArgumentParser(description="Phase 137 replay fidelity review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--phase134-pairs", default=str(PHASE134_PAIRS))
    args = parser.parse_args()

    _bootstrap()
    from research.replay_fidelity_review import (
        analyze_replay_fidelity,
        discover_fidelity_sessions,
    )
    from small_paper.config import load_pilot_config

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_fidelity_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    config = load_pilot_config(PILOT_CONFIG)
    result = analyze_replay_fidelity(
        session_dirs,
        pilot_config=config,
        phase134_pairs_path=Path(args.phase134_pairs),
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase137_replay_fidelity_review.json"
    mismatch_csv = REPORTS / "phase137_replay_mismatch_events.csv"
    pair_csv = REPORTS / "phase137_switch_pair_match_diagnostics.csv"
    summary_csv = REPORTS / "phase137_scenario_summary.csv"
    fix_plan = REPORTS / "phase137_replay_engine_fix_plan.md"

    report: dict[str, Any] = {
        "phase": 137,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "replay_fidelity_ready",
            "B": "replay_mismatch_fixable",
            "C": "event_density_insufficient",
            "D": "need_live_engine_trace",
        },
        "aggregate": result["aggregate"],
        "sessions": result["sessions"],
        "replay_modes": result["replay_modes"],
        "phase134_reference": {
            "pair_count": 294,
            "phase136_match_rate": 0.2755,
            "phase134_cooldown_delta": 93.4824,
        },
        "methodology": {
            "review_only": True,
            "live_ground_truth": [
                "structural_trades.csv",
                "small_paper_events.csv/jsonl",
                "small_paper_summary.json",
                "structural_events.csv (when present)",
            ],
            "replay_engines_compared": [
                "combined_structural_exit_v1 full event replay",
                "cap3_entry_replay scenario A (live accepted triggers)",
            ],
            "mismatch_taxonomy": [
                "accepted_timing_mismatch",
                "exit_timing_mismatch",
                "missing_candidate_event",
                "duplicate_replay_trade",
                "position_state_mismatch",
                "cap_gate_mismatch",
                "structural_exit_policy_mismatch",
                "event_density_insufficiency",
            ],
        },
        "outputs": {
            "json": _rel(out_json),
            "mismatch_csv": _rel(mismatch_csv),
            "pair_csv": _rel(pair_csv),
            "summary_csv": _rel(summary_csv),
            "fix_plan_md": _rel(fix_plan),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(mismatch_csv, result["mismatch_events"])
    _write_csv(pair_csv, result["pair_diagnostics"])
    _write_csv(summary_csv, result["scenario_summary"])
    fix_plan.write_text(result["fix_plan_md"], encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "aggregate": result["aggregate"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
