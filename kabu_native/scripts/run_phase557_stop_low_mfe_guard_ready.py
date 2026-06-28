#!/usr/bin/env python3
"""Phase557: verify stop_low_mfe guard (G554_022) runtime readiness."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
SRC = KABU / "src"
for p in (KABU, SRC, REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.live_pipeline_preflight import default_config_path, run_live_pipeline_preflight  # noqa: E402
from small_paper.production_startup_smoke_test import run_production_startup_smoke_test  # noqa: E402
from small_paper.stop_low_mfe_guard import (  # noqa: E402
    PHASE557_RUNTIME_VERDICT,
    build_stop_low_mfe_guard_state,
    config_from_pilot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase557 stop_low_mfe guard runtime ready check")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--skip-unit-tests", action="store_true")
    parser.add_argument(
        "--skip-overlap",
        action="store_true",
        help="Skip Phase557 overlap re-analysis (~5min); use for paper-trade preflight",
    )
    args = parser.parse_args()

    cfg_path = args.config or default_config_path(REPO)
    if not cfg_path.is_absolute():
        cfg_path = REPO / cfg_path

    test_ok = True
    if not args.skip_unit_tests:
        from tests.test_phase557_stop_low_mfe_guard_runtime import (  # noqa: E402
            TestPhase557Verdict,
            TestStopLowMfeGuardConfig,
            TestStopLowMfeGuardCore,
            TestStopLowMfeGuardExposureGate,
            TestStopLowMfeGuardFeature,
            TestStopLowMfeGuardOrNonImpact,
            TestStopLowMfeGuardSession,
            TestStopLowMfeGuardSummaryAndDiscord,
        )

        suite = unittest.TestSuite(
            [
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardFeature),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardCore),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardExposureGate),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardOrNonImpact),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardSummaryAndDiscord),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardConfig),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestStopLowMfeGuardSession),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestPhase557Verdict),
            ]
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        test_ok = result.wasSuccessful()
        if not test_ok:
            print(
                json.dumps(
                    {"verdict": "phase557_runtime_ready_failed", "stage": "unit_tests"},
                    ensure_ascii=False,
                )
            )
            return 1

    errors: list[str] = []
    config = load_pilot_config(cfg_path)
    guard_cfg = config_from_pilot(config)
    if not guard_cfg.enabled:
        errors.append("stop_low_mfe_guard_enabled is false in production YAML")
    if guard_cfg.missing_policy != "pass":
        errors.append(f"stop_low_mfe_guard_missing_policy expected pass, got {guard_cfg.missing_policy}")
    if not guard_cfg.pbv2_only:
        errors.append("stop_low_mfe_guard_pbv2_only expected true")
    if abs(guard_cfg.threshold - 0.009) > 1e-9:
        errors.append(f"stop_low_mfe_guard_threshold expected 0.009, got {guard_cfg.threshold}")

    guard = build_stop_low_mfe_guard_state(config)
    if guard is None:
        errors.append("build_stop_low_mfe_guard_state returned None")

    gate = config.make_exposure_gate(repo_root=REPO)
    if getattr(gate, "stop_low_mfe_guard", None) is None:
        errors.append("ExposureGate.stop_low_mfe_guard is None (production repo_root)")

    gate_kabu = config.make_exposure_gate(repo_root=KABU)
    if getattr(gate_kabu, "stop_low_mfe_guard", None) is None:
        errors.append("ExposureGate.stop_low_mfe_guard is None (kabu_native repo_root)")

    if guard is not None:
        summary = guard.summary_fields()
        for key in (
            "stop_low_mfe_guard_reject_count",
            "stop_low_mfe_guard_missing_count",
            "stop_low_mfe_guard_blocked_loss",
            "stop_low_mfe_guard_blocked_winner",
            "stop_low_mfe_guard_blocked_big_winner",
            "stop_low_mfe_guard_net_shadow",
            "stop_low_mfe_guard_volume_accel_threshold",
        ):
            if key not in summary:
                errors.append(f"summary missing key: {key}")

    smoke = run_production_startup_smoke_test(repo_root=REPO)
    if not smoke.ready:
        errors.extend([f"startup_smoke_test: {e}" for e in smoke.errors])
    elif not smoke.checks.get("stop_low_mfe_guard"):
        errors.append("production startup smoke test missing stop_low_mfe_guard check")

    preflight = run_live_pipeline_preflight(config_path=cfg_path, repo_root=REPO)
    preflight_ok = preflight.ready
    if not preflight_ok:
        errors.extend([f"preflight: {e}" for e in preflight.errors])

    rollback_cfg = load_pilot_config(cfg_path)
    rollback_cfg.stop_low_mfe_guard_enabled = False
    rollback_gate = rollback_cfg.make_exposure_gate(repo_root=REPO)
    if getattr(rollback_gate, "stop_low_mfe_guard", None) is not None:
        errors.append("rollback stop_low_mfe_guard_enabled=false still attaches guard")

    if not args.skip_overlap:
        from research.phase557_stop_low_mfe_guard_runtime_implementation import Phase557Job  # noqa: E402

        job = Phase557Job(repo_root=KABU)
        overlap_result = job.run(
            runtime_ready=len(errors) == 0,
            test_ok=test_ok,
            preflight_ok=preflight_ok,
        )
        paths = job.write_outputs(overlap_result)
    else:
        overlap_result = {
            "runtime_ready": len(errors) == 0 and test_ok and preflight_ok,
            "overlap_mandatory_answers": {"skipped": True},
            "runtime_mandatory_answers": {"skipped": True},
        }
        paths = {}

    ready = len(errors) == 0 and bool(overlap_result.get("runtime_ready"))
    verdict = PHASE557_RUNTIME_VERDICT if ready else "phase557_stop_low_mfe_guard_runtime_pending"
    out = {
        "verdict": verdict,
        "ready": ready,
        "config_path": str(cfg_path),
        "errors": errors,
        "overlap_mandatory_answers": overlap_result.get("overlap_mandatory_answers"),
        "runtime_mandatory_answers": overlap_result.get("runtime_mandatory_answers"),
        "output_paths": paths,
        "smoke_checks": smoke.checks,
        "preflight_verdict": preflight.verdict,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
