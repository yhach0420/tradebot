#!/usr/bin/env python3
"""
Phase181: Entry expectancy quantitative review (post-hoc / replay only).

Target: 20260529 AM session (live_session_075135).

Writes:
  kabu_native/results/reports/phase181_entry_expectancy_feature_review.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo


def main() -> int:
    repo = _bootstrap()
    from research.phase181_entry_expectancy_review import evaluate_entry_expectancy_review

    parser = argparse.ArgumentParser(description="Phase181 entry expectancy feature review")
    parser.add_argument("--day-stamp", default="20260529")
    parser.add_argument(
        "--session-dir",
        default="kabu_native/results/small_paper/20260529/live_session_075135",
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    if not session_dir.is_absolute():
        session_dir = repo / session_dir

    out = Path("kabu_native/results/reports/phase181_entry_expectancy_feature_review.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    report = evaluate_entry_expectancy_review(
        session_dir,
        repo_root=repo,
        day_stamp=args.day_stamp,
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    agg = report.get("aggregate") or {}
    sc = report.get("post_hoc_scenarios") or {}
    print(
        json.dumps(
            {
                "paired_trades": report.get("paired_trade_count"),
                "total_pnl_pct": agg.get("total_pnl_pct"),
                "stop_hit": agg.get("stop_hit_count"),
                "verdict_6203": report.get("verdict_6203"),
                "verdict_6659": report.get("verdict_6659"),
                "scenario_F_delta_pnl": (sc.get("F") or {}).get("delta_total_pnl_vs_A"),
                "output": str(out).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
