#!/usr/bin/env python3
"""
Phase185: Multi-session vwap_dev shadow reject candidate review.

Writes:
  kabu_native/results/reports/phase185_vwap_dev_shadow_candidate_multisession_review.json
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
    from research.phase185_vwap_dev_shadow_candidate_multisession_review import (
        evaluate_vwap_dev_multisession_review,
    )

    parser = argparse.ArgumentParser(description="Phase185 vwap_dev multisession shadow review")
    parser.add_argument(
        "--out",
        default="kabu_native/results/reports/phase185_vwap_dev_shadow_candidate_multisession_review.json",
    )
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    out.parent.mkdir(parents=True, exist_ok=True)

    report = evaluate_vwap_dev_multisession_review(repo_root=repo)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    agg = (report.get("aggregate") or {}).get("scenarios") or {}
    v = report.get("verdict") or {}
    print(
        json.dumps(
            {
                "sessions": report.get("session_count_included"),
                "aggregate_A_pf": (agg.get("A_current") or {}).get("profit_factor"),
                "aggregate_B_pf": (agg.get("B_exclude_vwap_ge_2p5") or {}).get("profit_factor"),
                "aggregate_B_delta_pf": v.get("aggregate_b_delta_pf"),
                "aggregate_B_delta_pnl": v.get("aggregate_b_delta_total_pnl_pct"),
                "supported": v.get("vwap_dev_shadow_reject_supported"),
                "output": str(out).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
