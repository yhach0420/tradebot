#!/usr/bin/env python3
"""
Phase 151: Replay review for combined_structural_exit_v1_take_exit_shadow.

Example::
    python kabu_native/scripts/run_phase151_take_exit_shadow_review.py
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
    from research.take_exit_shadow_review import run_phase151_take_exit_shadow_review
    from small_paper.config import load_pilot_config

    session_dir = repo_root / "kabu_native/results/small_paper/20260525/live_session_075733"
    cfg_path = native_root / "configs/small_paper_pilot_q070_cap3_take_exit_shadow.yaml"
    reports_dir = native_root / "results/reports"

    if not session_dir.is_dir():
        print(f"session not found: {session_dir}", file=sys.stderr)
        return 2

    config = load_pilot_config(cfg_path)
    if config.structural_exit_policy != "combined_structural_exit_v1_take_exit_shadow":
        print("config structural_exit_policy mismatch", file=sys.stderr)
        return 2

    report = run_phase151_take_exit_shadow_review(
        session_dir,
        pilot_config=config,
        reports_dir=reports_dir,
    )
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "delta_pf": report.get("delta_pf"),
                "scenarios": report.get("scenarios"),
                "outputs": report.get("output_files"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report.get("verdict") in (
        "take_exit_shadow_promising",
        "take_exit_improves_but_not_enough",
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
