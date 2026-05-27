#!/usr/bin/env python3
"""
Phase 153b: entry_price_risk_guard_shadow replay review (20260525 AM).

Example::
    python kabu_native/scripts/run_phase153b_entry_price_risk_guard_shadow_review.py
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
    from research.entry_price_risk_guard_shadow_review import (
        run_phase153b_entry_price_risk_guard_shadow_review,
    )
    from small_paper.config import load_pilot_config

    session_dir = (
        repo_root / "kabu_native/results/small_paper/20260525/live_session_075733"
    )
    cfg_path = (
        native_root
        / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
    )
    reports_dir = native_root / "results" / "reports"

    if not session_dir.is_dir():
        print(f"session not found: {session_dir}", file=sys.stderr)
        return 2

    config = load_pilot_config(cfg_path)
    if not config.entry_price_risk_guard_enabled:
        print("entry_price_risk_guard_enabled must be true", file=sys.stderr)
        return 2

    report = run_phase153b_entry_price_risk_guard_shadow_review(
        session_dir,
        pilot_config=config,
        reports_dir=reports_dir,
    )
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "symbol_guard_checks": report.get("symbol_guard_checks"),
                "scenarios": report.get("scenarios"),
                "outputs": report.get("output_files"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report.get("verdict") == "entry_price_risk_guard_shadow_promising" else 1


if __name__ == "__main__":
    raise SystemExit(main())
