#!/usr/bin/env python3
"""
Phase 153c: Universe adoption diagnosis for 5856.T (20260525 AM).

Example::
    python kabu_native/scripts/run_phase153c_universe_low_price_diagnosis.py
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
    from research.universe_low_price_diagnosis import run_phase153c_universe_low_price_diagnosis

    session_dir = repo_root / "kabu_native/results/small_paper/20260525/live_session_075733"
    reports_dir = native_root / "results/reports"

    report = run_phase153c_universe_low_price_diagnosis(
        repo_root=repo_root,
        reports_dir=reports_dir,
        session_dir=session_dir,
    )
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "reason_5856": report.get("reason_5856"),
                "low_price_in_am_universe_count": report.get("low_price_in_am_universe_count"),
                "whatif": report.get("whatif_scenarios"),
                "outputs": report.get("output_files"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report.get("verdict") in (
        "universe_filter_promising",
        "both_universe_and_entry_guard_needed",
        "5856_outlier_only_no_universe_change",
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
