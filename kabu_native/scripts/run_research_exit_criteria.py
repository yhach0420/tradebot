#!/usr/bin/env python3
"""
Phase 36: Research exit criteria / validation freeze meta-analysis.

Reads Logic Lab run artifacts and writes research_exit_report.json/csv,
phase_progression_analysis.json. Does not connect to paper_trade or shadow.

例::
    python kabu_native/scripts/run_research_exit_criteria.py \\
        --run-dir kabu_native/results/research/logic_lab/20260517/run_HHMMSS

    python kabu_native/scripts/run_research_exit_criteria.py \\
        --run-dir kabu_native/results/research/logic_lab/20260517/run_HHMMSS \\
        --focus-profile momentum_volume_v13_combined \\
        --phase-run-root kabu_native/results/research/logic_lab
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _paths() -> tuple[Path, Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root, native_root / "src"


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    for p in (src_root, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.research_exit_criteria import run_research_exit_analysis

    parser = argparse.ArgumentParser(
        description="Logic Lab research exit criteria (Phase 36)"
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Logic Lab run directory (profile_summary.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same as --run-dir)",
    )
    parser.add_argument(
        "--focus-profile",
        default=None,
        help="Profile to evaluate (default: latest combined momentum profile)",
    )
    parser.add_argument(
        "--phase-run-root",
        type=Path,
        action="append",
        default=None,
        help="Root to scan for phase 25–35 runs (repeatable)",
    )
    args = parser.parse_args()

    run_dir = args.run_dir if args.run_dir.is_absolute() else (repo_root / args.run_dir)
    if not run_dir.is_dir():
        print(f"run-dir not found: {run_dir}", file=sys.stderr)
        return 2

    roots: list[Path] = []
    for pr in args.phase_run_root or []:
        p = pr if pr.is_absolute() else (repo_root / pr)
        roots.append(p.resolve())
    if not roots:
        roots.append(run_dir.parent.resolve())
        roots.append((native_root / "results" / "research" / "logic_lab").resolve())

    log = logging.getLogger("kabu_native.run_research_exit_criteria")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out = run_research_exit_analysis(
        run_dir.resolve(),
        focus_profile=args.focus_profile,
        phase_run_roots=roots,
        output_dir=(args.output_dir.resolve() if args.output_dir else None),
    )
    log.info("research exit criteria written to %s", out)
    print(f"Results: {out}")
    print("  research_exit_report.json")
    print("  research_exit_report.csv")
    print("  phase_progression_analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
