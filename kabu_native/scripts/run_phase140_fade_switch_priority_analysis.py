#!/usr/bin/env python3
"""Phase 140: Fade switch priority deep analysis (block vs selective allow)."""

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
PHASE139_PAIRS = REPORTS / "phase139_hybrid_fade_switch_pairs.csv"


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
    parser = argparse.ArgumentParser(description="Phase 140 fade switch priority analysis")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--phase139-pairs-csv", default=str(PHASE139_PAIRS))
    parser.add_argument("--phase134-pairs", default=str(PHASE134_PAIRS))
    args = parser.parse_args()

    _bootstrap()
    from research.fade_switch_priority_analysis import analyze_fade_switch_priority
    from research.replay_fidelity_review import discover_fidelity_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_fidelity_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_fade_switch_priority(
        session_dirs,
        phase139_pairs_path=Path(args.phase134_pairs),
        phase139_pairs_csv=Path(args.phase139_pairs_csv),
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase140_fade_switch_priority_analysis.json"
    allowed_csv = REPORTS / "phase140_allowed_switch_details.csv"
    blocked_csv = REPORTS / "phase140_blocked_switch_details.csv"
    rules_csv = REPORTS / "phase140_priority_rule_candidates.csv"
    summary_csv = REPORTS / "phase140_scenario_summary.csv"

    report: dict[str, Any] = {
        "phase": 140,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "fade_switch_block_sufficient",
            "B": "selective_priority_promising",
            "C": "priority_rule_too_brittle",
            "D": "current_switch_best",
        },
        "fade_switch_count": result["fade_switch_count"],
        "allowed_switch_analysis": result["allowed_switch_analysis"],
        "blocked_switch_analysis": result["blocked_switch_analysis"],
        "best_selective_rule_id": result["best_selective_rule_id"],
        "scenarios": result["scenarios"],
        "rule_candidates_top5": result["rule_candidates"][:5],
        "phase139_reference": result["phase139_reference"],
        "methodology": {
            "review_only": True,
            "timeline": "hybrid_live_accepted_structural_exit",
            "A": "current: allow all fade switches",
            "B": "full_block: keep old path for all",
            "C": "priority_current: Phase139 quality_gap_and_rank",
            "D": "ultra_conservative: all cross-symbol switches blocked",
            "E": "selective_allow: best general rule from candidates",
            "no_production_changes": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "allowed_csv": _rel(allowed_csv),
            "blocked_csv": _rel(blocked_csv),
            "rules_csv": _rel(rules_csv),
            "summary_csv": _rel(summary_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(allowed_csv, result["allowed_switch_details"])
    _write_csv(blocked_csv, result["blocked_switch_details"])
    _write_csv(rules_csv, result["rule_candidates"])
    _write_csv(summary_csv, result["scenarios"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "scenarios": result["scenarios"],
                "allowed": result["allowed_switch_analysis"],
                "blocked": result["blocked_switch_analysis"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
