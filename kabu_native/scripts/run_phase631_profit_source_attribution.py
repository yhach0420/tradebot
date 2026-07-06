#!/usr/bin/env python3
"""Phase631 — Profit Source Attribution runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

KABU = Path(__file__).resolve().parents[1]
REPO = KABU.parent


def main() -> int:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from research.phase631_profit_source_attribution import REPORT_DIR, run

    report = run()
    print(f"verdict={report.get('verdict')}", flush=True)
    print(json.dumps(report.get("trade_counts"), ensure_ascii=False), flush=True)
    ans = report.get("mandatory_answers") or {}
    for key in (
        "1_profit_contributing_features_top20",
        "2_profit_reducing_features_top20",
        "3_strongest_pbv2_features",
        "4_or_profit_features",
        "5_add_to_pbv2",
        "6_remove_or_weaken_in_pbv2",
        "7_profit_improvement_expectation_ranking",
    ):
        items = ans.get(key) or []
        print(f"\n## {key}", flush=True)
        for i, row in enumerate(items[:20], start=1):
            print(
                f"  {i:02d}. {row.get('feature')} "
                f"[{row.get('family')}] score={row.get('score', row.get('contribution_score'))}",
                flush=True,
            )
    print(f"\nreport={REPORT_DIR / 'phase631_report.json'}", flush=True)
    return 0 if report.get("verdict") == "phase631_profit_source_done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
