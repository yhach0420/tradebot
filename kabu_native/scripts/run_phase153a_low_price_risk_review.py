#!/usr/bin/env python3
"""
Phase 153a: Low-price / tick-ratio risk quantification (20260525 AM).

Example::
    python kabu_native/scripts/run_phase153a_low_price_risk_review.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def main() -> int:
    repo_root, native_root = _bootstrap()
    from research.low_price_risk_review import run_phase153a_low_price_risk_review

    session_dir = (
        repo_root / "kabu_native/results/small_paper/20260525/live_session_075733"
    )
    reports_dir = native_root / "results" / "reports"

    if not session_dir.is_dir():
        print(f"session not found: {session_dir}", file=sys.stderr)
        return 2

    report = run_phase153a_low_price_risk_review(session_dir, reports_dir=reports_dir)
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "low_price_lt_50_count": report.get("low_price_lt_50_count"),
                "filter_whatif": report.get("filter_whatif"),
                "outputs": report.get("output_files"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
