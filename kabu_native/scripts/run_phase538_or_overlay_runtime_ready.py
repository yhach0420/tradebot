#!/usr/bin/env python3
"""Phase538: verify OR overlay runtime readiness (unit tests + config + split CAP)."""

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

from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.live_pipeline_preflight import default_config_path  # noqa: E402
from small_paper.or_overlay_entry import (  # noqa: E402
    PHASE538_RUNTIME_VERDICT,
    build_or_overlay_state,
    config_from_pilot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase538 OR overlay runtime ready check")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--skip-unit-tests", action="store_true")
    args = parser.parse_args()

    cfg_path = args.config or default_config_path(REPO)
    if not cfg_path.is_absolute():
        cfg_path = REPO / cfg_path

    if not args.skip_unit_tests:
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
        if not result.wasSuccessful():
            print(json.dumps({"verdict": "phase538_or_overlay_runtime_failed", "stage": "unit_tests"}))
            return 1

    errors: list[str] = []
    config = load_pilot_config(cfg_path)
    ocfg = config_from_pilot(config)
    if not ocfg.enabled:
        errors.append("or_overlay_enabled is false in production YAML")
    if ocfg.cap_pbv2 != 4:
        errors.append(f"cap_pbv2 expected 4, got {ocfg.cap_pbv2}")
    if ocfg.cap_or != 1:
        errors.append(f"cap_or expected 1, got {ocfg.cap_or}")
    if int(config.max_concurrent_positions) != 5:
        errors.append(
            f"max_concurrent_positions expected 5 (split 4+1), got {config.max_concurrent_positions}"
        )
    if build_or_overlay_state(config) is None:
        errors.append("build_or_overlay_state returned None")

    empty_summary = build_or_overlay_state(config).summary_fields(events=[], observer=None)
    for key in (
        "or_entry_count",
        "or_exit_count",
        "or_active_positions",
        "or_realized_pnl",
        "or_unrealized_pnl",
        "or_win_rate",
        "or_pf",
        "or_blocked_count",
        "or_cap_full_count",
        "pbv2_count",
        "or_count",
        "or_pool_utilization",
    ):
        if key not in empty_summary:
            errors.append(f"summary missing key: {key}")

    verdict = PHASE538_RUNTIME_VERDICT if not errors else "phase538_or_overlay_runtime_failed"
    out = {
        "verdict": verdict,
        "ready": not errors,
        "config_path": str(cfg_path),
        "errors": errors,
        "or_overlay_config": {
            "enabled": ocfg.enabled,
            "cap_pbv2": ocfg.cap_pbv2,
            "cap_or": ocfg.cap_or,
            "max_concurrent_positions": config.max_concurrent_positions,
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
