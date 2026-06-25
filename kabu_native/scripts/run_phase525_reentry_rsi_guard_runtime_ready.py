#!/usr/bin/env python3
"""Phase525: verify re-entry RSI guard runtime readiness."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "kabu_native"
SRC = NATIVE / "src"
for p in (NATIVE, SRC, REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.live_pipeline_preflight import (  # noqa: E402
    PHASE525_RUNTIME_VERDICT,
    default_config_path,
    run_reentry_rsi_guard_preflight,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase525 re-entry RSI guard runtime ready check")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Pilot YAML (default: production q070 shadow config)",
    )
    parser.add_argument("--skip-unit-tests", action="store_true")
    args = parser.parse_args()

    cfg = args.config or default_config_path(REPO)

    if not args.skip_unit_tests:
        from tests.test_phase525_reentry_rsi_guard import TestReentryRsiGuard

        suite = unittest.TestLoader().loadTestsFromTestCase(TestReentryRsiGuard)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            print(json.dumps({"verdict": "phase525_failed", "stage": "unit_tests"}))
            return 1

    report = run_reentry_rsi_guard_preflight(config_path=cfg, repo_root=REPO)
    out = {
        "verdict": report.verdict,
        "ready": report.ready,
        "config_path": report.config_path,
        "errors": report.errors,
        "cases": [
            {
                "case_id": c.case_id,
                "ok": c.ok,
                "decision_reason": c.decision_reason,
                "rsi14": c.rsi14,
                "uses_float_epoch_timestamps": c.uses_float_epoch_timestamps,
            }
            for c in report.cases
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if report.verdict == PHASE525_RUNTIME_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
