#!/usr/bin/env python3
"""
Phase182: Extended entry analysis for quality 0.75–0.80 underperformance (5/29 AM).

Writes:
  kabu_native/results/reports/phase182_extended_entry_analysis.json
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
    from research.phase182_extended_entry_analysis import evaluate_extended_entry_analysis

    parser = argparse.ArgumentParser(description="Phase182 extended entry analysis")
    parser.add_argument("--day-stamp", default="20260529")
    parser.add_argument(
        "--session-dir",
        default="kabu_native/results/small_paper/20260529/live_session_075135",
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    if not session_dir.is_absolute():
        session_dir = repo / session_dir

    out = Path("kabu_native/results/reports/phase182_extended_entry_analysis.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    report = evaluate_extended_entry_analysis(
        session_dir,
        repo_root=repo,
        day_stamp=args.day_stamp,
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    band = report.get("quality_band_expectancy", {}).get("0.75_0.80", {})
    cands = report.get("entry_quality_improvement_candidates") or []
    print(
        json.dumps(
            {
                "quality_0.75_0.80_pf": band.get("profit_factor"),
                "extended_rate_7580": (report.get("quality_0.75_0.80_deep_dive") or {}).get(
                    "extended_entry_rate"
                ),
                "quality_vs_rolling_mfe_r": (
                    report.get("entry_feature_correlations_quality_0.75_0.80") or {}
                ).get("quality_vs_rolling_mfe"),
                "scenario_B_pf_7580": (
                    (report.get("post_hoc_scenarios") or {}).get("B") or {}
                )
                .get("quality_0.75_0.80", {})
                .get("profit_factor"),
                "candidates": [c.get("name") for c in cands],
                "output": str(out).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
