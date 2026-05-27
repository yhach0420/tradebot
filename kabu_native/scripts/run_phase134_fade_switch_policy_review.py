#!/usr/bin/env python3
"""Phase 134: Fade-exit switch policy what-if review."""

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
    parser = argparse.ArgumentParser(description="Phase 134 fade switch policy review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_switch_policy_review import analyze_fade_switch_policies
    from research.mfe_mae_exit_review import discover_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_fade_switch_policies(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase134_fade_switch_policy_review.json"
    scenarios_csv = REPORTS / "phase134_fade_switch_scenarios.csv"
    pairs_csv = REPORTS / "phase134_fade_switch_pairs.csv"
    rules_csv = REPORTS / "phase134_fade_switch_rule_candidates.csv"

    report = {
        "phase": 134,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "fade_switch_block_promising",
            "B": "fade_switch_priority_promising",
            "C": "current_switch_best",
            "D": "need_priority_features",
        },
        "fade_switch_count": result["fade_switch_count"],
        "scenarios": result["scenarios"],
        "top_rules": result["rule_candidates"][:8],
        "phase133_reference": {
            "fade_switch_total_delta_new_minus_old": -74.82,
            "note": "momentum_fade + price_momentum_fade from Phase133",
        },
        "methodology": {
            "review_only": True,
            "fade_exit_reasons": ["momentum_fade_exit", "price_momentum_fade_exit"],
            "A": "current: exit old + enter new",
            "B": "block new entry after fade; keep old path",
            "C": "priority gate on new strength",
            "D": "event cooldown until old reaccel before new entry",
            "no_fixed_time_cooldown": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "scenarios_csv": _rel(scenarios_csv),
            "pairs_csv": _rel(pairs_csv),
            "rules_csv": _rel(rules_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(scenarios_csv, result["scenarios"])
    _write_csv(pairs_csv, result["pairs"])
    _write_csv(rules_csv, result["rule_candidates"])

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
