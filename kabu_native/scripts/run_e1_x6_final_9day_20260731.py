#!/usr/bin/env python
"""Run E1_X6 FINAL 9-day research pipeline (20260721–20260731).

Research-only. Does NOT touch E1_X5 Runtime / Paper / Live / Discord / broker.
Requires allow_full_replay (always True here). Fixture contracts run first (fail-stop).
Plan SoT Version 1.3 (replay lifecycle contract) required.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    native = Path(__file__).resolve().parents[1]
    repo = native.parent
    src = native / "src"
    for p in (str(src), str(repo)):
        if p not in sys.path:
            sys.path.insert(0, p)

    import os
    import tempfile

    os.environ.setdefault("PYTHONPATH", os.pathsep.join([str(src), str(repo)]))

    progress_log = Path(tempfile.gettempdir()) / "e1x6_final_progress.log"
    try:
        progress_log.write_text("", encoding="utf-8")
    except Exception:
        pass

    print("==== E1_X6 FINAL 9-day runner (Plan 1.3) ====")
    print("progress_log:", progress_log)
    print("Running fixture contracts first (fail-stop)...")

    import pytest

    test_path = native / "tests" / "test_e1_x6_research_builder_contracts.py"
    rc = pytest.main(["-q", str(test_path)])
    if rc != 0:
        print("FIXTURE_CONTRACTS_FAILED rc=", rc)
        return int(rc)

    from research.e1_x6_provisional.constants import PLAN_REL
    from research.e1_x6_provisional.pipeline import REQUIRED_PLAN_VERSION, run_final_9day_pipeline
    from research.e1_x6_provisional.util import repo_root, set_progress_mode, sha256_file

    plan_path = repo_root() / PLAN_REL
    plan_sha = sha256_file(plan_path) if plan_path.is_file() else None
    print(f"plan_version_required={REQUIRED_PLAN_VERSION} plan_sha256={plan_sha}")
    print("A/B isolation: sequential run_a then run_b with separate norm caches")

    set_progress_mode("final")
    report = run_final_9day_pipeline(allow_full_replay=True)

    print("==== FINAL PIPELINE RESULT ====")
    print("run_id:", report.get("final_run_id") or report.get("run_id"))
    print("banner:", report.get("banner"))
    print("status:", report.get("status"))
    print("verdict:", report.get("verdict"))
    print("NEXT_PHASE:", report.get("NEXT_PHASE"))
    print("blockers:", report.get("blockers"))
    print("plan:", report.get("plan"))
    print("tests_n:", len(report.get("tests") or []))
    print("published_paths:", report.get("published_paths"))
    print("publish_skipped:", report.get("publish_skipped"))
    print("progress_log:", report.get("progress_log") or progress_log)
    print("temp_work:", report.get("temp_work"))

    if report.get("publish_skipped") or report.get("blockers"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
