#!/usr/bin/env python3
"""Phase 142: Phase141 block scope classification and scoped-block what-if (review only)."""

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
PHASE141_EVENTS = REPORTS / "phase141_fade_switch_block_events.csv"
PHASE139_PAIRS = REPORTS / "phase139_hybrid_fade_switch_pairs.csv"
PHASE141_REVIEW = REPORTS / "phase141_fade_switch_block_shadow_review.json"
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
    parser = argparse.ArgumentParser(description="Phase 142 fade switch block scope review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--phase141-events", default=str(PHASE141_EVENTS))
    parser.add_argument("--phase139-pairs", default=str(PHASE139_PAIRS))
    parser.add_argument("--skip-replay-trades", action="store_true")
    args = parser.parse_args()

    _bootstrap()
    from research.fade_switch_block_scope_review import analyze_fade_switch_block_scope
    from research.replay_fidelity_review import discover_fidelity_sessions
    from small_paper.config import load_pilot_config

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_fidelity_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    config = load_pilot_config(PILOT_CONFIG)
    result = analyze_fade_switch_block_scope(
        session_dirs,
        pilot_config=config,
        phase141_events_path=Path(args.phase141_events),
        phase139_pairs_path=Path(args.phase139_pairs),
        phase141_review_path=PHASE141_REVIEW,
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase142_fade_switch_block_scope_review.json"
    class_csv = REPORTS / "phase142_blocked_entry_classification.csv"
    scenarios_csv = REPORTS / "phase142_block_scope_scenarios.csv"
    examples_csv = REPORTS / "phase142_overblocked_examples.csv"

    report: dict[str, Any] = {
        "phase": 142,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "scoped_block_promising",
            "B": "full_block_too_broad",
            "C": "block_not_useful",
            "D": "need_entry_relation_features",
        },
        "classification_summary": result["classification_summary"],
        "scenarios": result["scenarios"],
        "phase141_reference": result.get("phase141_reference"),
        "phase139_pair_count": result.get("phase139_pair_count"),
        "first_cross_block_count": result.get("first_cross_block_count"),
        "methodology": {
            "review_only": True,
            "no_implementation_changes": True,
            "immediate_switch_sec": 300,
            "scoped_rules": [
                "C_first_cross_symbol_only",
                "D_old_not_breakdown_only",
                "E_new_not_strong_only",
                "F_combined_safe_block",
            ],
        },
        "outputs": {
            "json": _rel(out_json),
            "classification_csv": _rel(class_csv),
            "scenarios_csv": _rel(scenarios_csv),
            "overblocked_csv": _rel(examples_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(class_csv, result["classified_blocked_entries"])
    _write_csv(scenarios_csv, result["scenarios"])
    _write_csv(examples_csv, result["overblocked_examples"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "classification": result["classification_summary"],
                "scenarios": result["scenarios"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
