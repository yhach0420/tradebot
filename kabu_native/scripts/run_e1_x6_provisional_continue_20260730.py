#!/usr/bin/env python
"""E1_X6 research helper — fixture tests by default; full replay gated.

Default: run fixture contract tests only (safe during 7/31 Capture).
Full replay requires --allow-full-replay AFTER 7/31 PM seal.
Does NOT modify current run artifacts unless full publish is explicitly allowed.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    import argparse

    repo = Path(__file__).resolve().parents[2]
    native = Path(__file__).resolve().parents[1]
    src = native / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture-tests",
        action="store_true",
        default=True,
        help="Run small fixed fixture tests (default; Capture-safe)",
    )
    parser.add_argument(
        "--allow-full-replay",
        action="store_true",
        default=False,
        help="AFTER 7/31 PM seal only: run full provisional/final pipeline",
    )
    parser.add_argument("--resume-run-id", default="")
    args = parser.parse_args()

    from research.e1_x6_provisional.util import progress

    if args.allow_full_replay:
        from research.e1_x6_provisional.pipeline import run_provisional_pipeline

        kwargs = {"allow_full_replay": True}
        if args.resume_run_id:
            kwargs["resume_run_id"] = args.resume_run_id
        report = run_provisional_pipeline(**kwargs)
        print("==== FULL PIPELINE ====")
        print("run_id:", report.get("provisional_run_id"))
        print("status:", report.get("status"))
        print("blockers:", report.get("blockers"))
        return 0 if not report.get("blockers") else 2

    # Default path: fixture tests only
    progress("runner: fixture-tests only (full replay blocked during Capture)")
    import pytest

    test_path = native / "tests" / "test_e1_x6_research_builder_contracts.py"
    rc = pytest.main(["-q", str(test_path)])
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
