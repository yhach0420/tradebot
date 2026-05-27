#!/usr/bin/env python3
"""
Phase 153d: Shadow price-risk universe filter review (20260525 AM).

Example::
    python kabu_native/scripts/run_phase153d_price_risk_universe_filter_review.py
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
    from research.price_risk_universe_filter_review import run_phase153d_price_risk_universe_filter_review

    session_dir = repo_root / "kabu_native/results/small_paper/20260525/live_session_075733"
    reports_dir = native_root / "results/reports"

    report = run_phase153d_price_risk_universe_filter_review(
        repo_root=repo_root,
        reports_dir=reports_dir,
        session_dir=session_dir,
    )
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "validation_checks": report.get("validation_checks"),
                "am_excluded": report.get("am_excluded"),
                "am_replacements": report.get("am_replacements"),
                "pnl_proxy_delta": report.get("pnl_proxy_delta"),
                "dual_defense": report.get("dual_defense"),
                "outputs": report.get("output_files"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report.get("verdict") in (
        "price_risk_universe_filter_promising",
        "core_handling_needed",
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
