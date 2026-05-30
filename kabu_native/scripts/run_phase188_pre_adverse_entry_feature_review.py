#!/usr/bin/env python3
"""
Phase188: Pre-adverse entry feature review.

Writes:
  kabu_native/results/reports/phase188_pre_adverse_entry_feature_review.json
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
    from research.phase188_pre_adverse_entry_feature_review import (
        evaluate_pre_adverse_entry_feature_review,
    )

    parser = argparse.ArgumentParser(description="Phase188 pre-adverse entry feature review")
    parser.add_argument(
        "--out",
        default="kabu_native/results/reports/phase188_pre_adverse_entry_feature_review.json",
    )
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    out.parent.mkdir(parents=True, exist_ok=True)

    report = evaluate_pre_adverse_entry_feature_review(repo_root=repo)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    top3 = report.get("top_3_adverse_cluster_features") or []
    v = report.get("verdict") or {}
    print(
        json.dumps(
            {
                "labeled": (report.get("meta") or {}).get("labeled_with_r30"),
                "A_count": (report.get("clusters") or {}).get("A_adverse_r30_lt_0", {}).get("trade_count"),
                "B_count": (report.get("clusters") or {}).get("B_non_adverse_r30_gte_0", {}).get("trade_count"),
                "top3": [t.get("feature") for t in top3],
                "predictable": v.get("pre_entry_predictable"),
                "output": str(out).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
