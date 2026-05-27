#!/usr/bin/env python3
"""Phase 128: fade_watch trigger restriction sensitivity (review only)."""

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
SHADOW_CONFIG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_fade_watch_shadow.yaml"


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
    parser = argparse.ArgumentParser(description="Phase 128 fade_watch trigger review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_watch_trigger_review import analyze_fade_watch_triggers
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
    result = analyze_fade_watch_triggers(session_dirs, pilot_config=config)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase128_fade_watch_trigger_review.json"
    sensitivity_csv = REPORTS / "phase128_trigger_sensitivity.csv"
    compare_csv = REPORTS / "phase128_improved_vs_worsened.csv"

    sensitivity_rows = []
    for s in result["sensitivity"]:
        row = dict(s)
        row["rule_json"] = json.dumps(s.get("rule") or {}, ensure_ascii=False)
        row.pop("rule", None)
        sensitivity_rows.append(row)

    compare_rows = []
    for r in result["improved_vs_worsened_rows"]:
        compare_rows.append(
            {
                **r,
                "take_reached": r.get("take_reached"),
                "overlap": r.get("overlap"),
            }
        )

    report: dict[str, Any] = {
        "phase": 128,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "restricted_fade_watch_promising",
            "B": "trigger_conditions_still_too_weak",
            "C": "fade_watch_not_worth_it",
            "D": "need_more_features",
        },
        "fade_watch_count": result["fade_watch_count"],
        "group_comparison": result["group_comparison"],
        "top_rules": result["sensitivity"][:15],
        "pareto_frontier": result["pareto_frontier"][:12],
        "methodology": {
            "review_only": True,
            "no_implementation_changes": True,
            "baseline": "combined_structural_exit_v1 immediate fade exit",
            "shadow": "combined_structural_exit_v1_fade_watch_shadow (Phase127 replay)",
            "counterfactual": "restricted rule selects fade_watch subset; others keep baseline exit",
            "precision": "improved / (improved + worsened) among matched trades",
            "pnl_thresholds": [0.05, 0.10, 0.15],
            "momentum_thresholds": [0.30, 0.40, 0.50],
        },
        "outputs": {
            "json": _rel(out_json),
            "sensitivity_csv": _rel(sensitivity_csv),
            "compare_csv": _rel(compare_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(sensitivity_csv, sensitivity_rows)
    _write_csv(compare_csv, compare_rows)

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "fade_watch_count": result["fade_watch_count"],
                "best_rule": result["sensitivity"][0].get("rule_id") if result["sensitivity"] else None,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
