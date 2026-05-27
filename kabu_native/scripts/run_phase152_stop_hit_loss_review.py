#!/usr/bin/env python3
"""
Phase 152: stop_hit loss review for 2026-05-25 AM live_session_075733.

Example::
    python kabu_native/scripts/run_phase152_stop_hit_loss_review.py
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
    from research.stop_hit_loss_review import run_phase152_stop_hit_loss_review
    from small_paper.config import load_pilot_config

    session_dir = (
        repo_root / "kabu_native/results/small_paper/20260525/live_session_075733"
    )
    cfg_path = (
        native_root / "configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
    )
    reports_dir = native_root / "results" / "reports"

    if not session_dir.is_dir():
        print(f"session not found: {session_dir}", file=sys.stderr)
        return 2

    config = load_pilot_config(cfg_path)
    report = run_phase152_stop_hit_loss_review(
        session_dir,
        pilot_config=config,
        reports_dir=reports_dir,
    )
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "stop_hit_count": report.get("stop_hit_count"),
                "stop_hit_loss_sum_pct": report.get("stop_hit_loss_sum_pct"),
                "whatif": report.get("whatif_scenarios"),
                "outputs": report.get("output_files"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report.get("verdict") in (
        "early_adverse_exit_promising",
        "tighter_stop_promising",
        "reject_high_risk_entry_promising",
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
