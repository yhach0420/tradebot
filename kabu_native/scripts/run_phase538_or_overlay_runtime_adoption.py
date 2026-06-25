#!/usr/bin/env python3
"""Phase538: OR Overlay runtime adoption verdict script."""

from __future__ import annotations

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

from small_paper.or_overlay_entry import PHASE538_RUNTIME_VERDICT  # noqa: E402


def main() -> int:
    from tests.test_phase538_or_overlay_runtime import (  # noqa: E402
        TestOrOverlayCap,
        TestOrOverlayConfig,
        TestOrOverlayEntry,
        TestPhase538Verdict,
    )

    suite = unittest.TestSuite(
        [
            unittest.defaultTestLoader.loadTestsFromTestCase(TestOrOverlayCap),
            unittest.defaultTestLoader.loadTestsFromTestCase(TestOrOverlayEntry),
            unittest.defaultTestLoader.loadTestsFromTestCase(TestOrOverlayConfig),
            unittest.defaultTestLoader.loadTestsFromTestCase(TestPhase538Verdict),
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "phase": 538,
        "verdict": PHASE538_RUNTIME_VERDICT if result.wasSuccessful() else "phase538_or_overlay_runtime_failed",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "adopted_config": {
            "universe": "Core10 + Dynamic40",
            "cap_pbv2": 4,
            "cap_or": 1,
            "or_definition": "OR Open Strength Overlay (O_R003_OR + OS9)",
            "rollback": "or_overlay_enabled: false",
        },
        "acceptance_review_days": 5,
        "acceptance_checks": [
            "or_entry_count",
            "or_pnl",
            "or_win_rate",
            "or_pf",
            "pbv2_pnl_degradation",
            "cap_collision_count",
            "or_pool_utilization",
        ],
    }
    reports = NATIVE / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out_path = reports / "phase538_or_overlay_runtime_adoption.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Wrote {out_path}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
