#!/usr/bin/env python3
"""
Phase187: VWAP adverse cluster analysis.

Writes:
  kabu_native/results/reports/phase187_vwap_adverse_cluster_analysis.json
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
    from research.phase187_vwap_adverse_cluster_analysis import evaluate_vwap_adverse_cluster_analysis

    parser = argparse.ArgumentParser(description="Phase187 VWAP adverse cluster analysis")
    parser.add_argument(
        "--out",
        default="kabu_native/results/reports/phase187_vwap_adverse_cluster_analysis.json",
    )
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    out.parent.mkdir(parents=True, exist_ok=True)

    report = evaluate_vwap_adverse_cluster_analysis(repo_root=repo)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    v = report.get("verdict") or {}
    r30 = (report.get("within_candidate_analysis") or {}).get("r30_split") or {}
    print(
        json.dumps(
            {
                "candidates": report.get("candidate_trade_count"),
                "r30_lt_0_pf": (r30.get("r30_lt_0") or {}).get("profit_factor"),
                "r30_gte_0_pf": (r30.get("r30_gte_0") or {}).get("profit_factor"),
                "best_post_hoc": v.get("best_post_hoc_scenario_by_pf"),
                "adverse_cluster_supported": v.get("adverse_r30_cluster_worse_than_non_adverse"),
                "output": str(out).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
