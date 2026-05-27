#!/usr/bin/env python3
"""Phase 139: Hybrid-timeline fade switch policy what-if review."""

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
    parser = argparse.ArgumentParser(description="Phase 139 hybrid fade switch policy")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--phase134-pairs", default=str(PHASE134_PAIRS))
    args = parser.parse_args()

    _bootstrap()
    from research.hybrid_fade_switch_policy_review import analyze_hybrid_fade_switch_policies
    from research.replay_fidelity_review import discover_fidelity_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_fidelity_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_hybrid_fade_switch_policies(
        session_dirs,
        phase134_pairs_path=Path(args.phase134_pairs),
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase139_hybrid_fade_switch_policy_review.json"
    scenarios_csv = REPORTS / "phase139_hybrid_fade_switch_scenarios.csv"
    pairs_csv = REPORTS / "phase139_hybrid_fade_switch_pairs.csv"
    summary_csv = REPORTS / "phase139_hybrid_fade_switch_summary.csv"

    report: dict[str, Any] = {
        "phase": 139,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "fade_switch_policy_promising",
            "B": "current_switch_best",
            "C": "cooldown_not_helpful",
            "D": "need_priority_model",
        },
        "replay_mode": result["replay_mode"],
        "fade_switch_count": result["fade_switch_count"],
        "scenarios": result["scenarios"],
        "cooldown_release_reason_counts": result["cooldown_release_reason_counts"],
        "phase134_reference": result["phase134_reference"],
        "phase138_baseline": {"pair_match_rate": 1.0, "hybrid_replay_ready": True},
        "methodology": {
            "review_only": True,
            "timeline": "hybrid_live_accepted_structural_exit",
            "A": "current: allow fade switch",
            "B": "fade_switch_block: keep old path",
            "C": "fade_switch_cooldown: block until breakdown/reaccel/no_post_fade_ticks",
            "D": "fade_switch_priority: quality_gap_and_rank gate",
            "no_production_changes": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "scenarios_csv": _rel(scenarios_csv),
            "pairs_csv": _rel(pairs_csv),
            "summary_csv": _rel(summary_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(scenarios_csv, result["scenarios"])
    _write_csv(pairs_csv, result["pairs"])
    _write_csv(summary_csv, result["summary_rows"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "fade_switch_count": result["fade_switch_count"],
                "scenarios": result["scenarios"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
