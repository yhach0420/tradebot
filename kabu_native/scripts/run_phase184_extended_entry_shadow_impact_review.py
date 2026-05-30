#!/usr/bin/env python3
"""
Phase184: Extended entry shadow impact review (5/29 AM default).

Writes:
  kabu_native/results/reports/phase184_extended_entry_shadow_impact_review.json
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
    from research.phase184_extended_entry_shadow_impact_review import (
        evaluate_extended_entry_shadow_impact,
    )

    parser = argparse.ArgumentParser(description="Phase184 extended entry shadow impact review")
    parser.add_argument("--day-stamp", default="20260529")
    parser.add_argument(
        "--session-dir",
        default="kabu_native/results/small_paper/20260529/live_session_075135",
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    if not session_dir.is_absolute():
        session_dir = repo / session_dir

    out = Path("kabu_native/results/reports/phase184_extended_entry_shadow_impact_review.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    report = evaluate_extended_entry_shadow_impact(
        session_dir,
        repo_root=repo,
        day_stamp=args.day_stamp,
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    cmp_ = report.get("extended_flag_vs_no_flag") or {}
    sel = report.get("reject_candidate_selection") or {}
    print(
        json.dumps(
            {
                "extended_pf": (cmp_.get("extended_flag_true") or {}).get("profit_factor"),
                "non_extended_pf": (cmp_.get("extended_flag_false") or {}).get("profit_factor"),
                "worst_reason": (report.get("by_extended_reason") or {}).get("worst_reason"),
                "false_positive_rate": (report.get("false_positive") or {}).get("rate_of_extended"),
                "selected_shadow_feature": sel.get("selected_shadow_feature"),
                "output": str(out).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
