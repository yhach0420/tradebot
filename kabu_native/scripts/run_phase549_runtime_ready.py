#!/usr/bin/env python3
"""Phase549: verify V6+E4 cluster guard runtime readiness."""

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
from small_paper.entry_cluster_guard import (  # noqa: E402
    PHASE549_RUNTIME_VERDICT,
    PHASE552_SMOKE_VERDICT,
    build_entry_cluster_guard_state,
    config_from_pilot,
    validate_entry_cluster_guard_model,
)
from small_paper.entry_cluster_classifier import resolve_entry_cluster_guard_model_path  # noqa: E402
from small_paper.production_startup_smoke_test import run_production_startup_smoke_test  # noqa: E402
from small_paper.live_pipeline_preflight import default_config_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase549 cluster guard runtime ready check")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--skip-unit-tests", action="store_true")
    args = parser.parse_args()

    cfg_path = args.config or default_config_path(REPO)
    if not cfg_path.is_absolute():
        cfg_path = REPO / cfg_path

    if not args.skip_unit_tests:
        from tests.test_phase549_entry_cluster_guard_runtime import (  # noqa: E402
            TestEntryClusterGuardConfig,
            TestEntryClusterGuardCore,
            TestEntryClusterGuardExposureGate,
            TestEntryClusterGuardOrNonImpact,
            TestEntryClusterGuardSummaryAndDiscord,
            TestPhase549Verdict,
            TestPhase552ModelPathResolution,
        )

        suite = unittest.TestSuite(
            [
                unittest.defaultTestLoader.loadTestsFromTestCase(TestEntryClusterGuardCore),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestEntryClusterGuardExposureGate),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestEntryClusterGuardConfig),
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    TestEntryClusterGuardSummaryAndDiscord
                ),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestEntryClusterGuardOrNonImpact),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestPhase552ModelPathResolution),
                unittest.defaultTestLoader.loadTestsFromTestCase(TestPhase549Verdict),
            ]
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            print(
                json.dumps(
                    {"verdict": "phase549_runtime_ready_failed", "stage": "unit_tests"},
                    ensure_ascii=False,
                )
            )
            return 1

    errors: list[str] = []
    config = load_pilot_config(cfg_path)
    guard_cfg = config_from_pilot(config)
    if not guard_cfg.enabled:
        errors.append("entry_cluster_guard_enabled is false in production YAML")
    if not guard_cfg.exception_enabled:
        errors.append("entry_cluster_guard_exception_enabled is false")
    if abs(guard_cfg.liquidity_burst_threshold - 0.052267) > 1e-9:
        errors.append(
            f"liquidity_burst_threshold expected 0.052267, got {guard_cfg.liquidity_burst_threshold}"
        )
    if guard_cfg.reject_clusters != frozenset({5}):
        errors.append(f"reject_clusters expected {{5}}, got {sorted(guard_cfg.reject_clusters)}")
    if guard_cfg.reject_csubs != frozenset({0, 2, 3, 5}):
        errors.append(f"reject_csubs expected {{0,2,3,5}}, got {sorted(guard_cfg.reject_csubs)}")

    guard = build_entry_cluster_guard_state(config, repo_root=KABU)
    if guard is None:
        errors.append("build_entry_cluster_guard_state returned None (kabu_native repo_root)")

    prod_guard, prod_errors = validate_entry_cluster_guard_model(config, repo_root=REPO)
    errors.extend(prod_errors)
    if prod_guard is None and guard_cfg.enabled:
        errors.append("validate_entry_cluster_guard_model failed for production repo_root")

    smoke = run_production_startup_smoke_test(repo_root=REPO)
    if not smoke.ready:
        errors.extend([f"startup_smoke_test: {e}" for e in smoke.errors])
    elif smoke.verdict != PHASE552_SMOKE_VERDICT:
        errors.append(f"unexpected smoke verdict: {smoke.verdict}")

    try:
        model_path = resolve_entry_cluster_guard_model_path(repo_root=REPO)
    except FileNotFoundError as exc:
        errors.append(str(exc))
        model_path = KABU / "configs" / "entry_cluster_guard_model.json"
    if not model_path.is_file():
        errors.append(f"missing model: {model_path}")

    gate = config.make_exposure_gate(repo_root=REPO)
    if getattr(gate, "entry_cluster_guard", None) is None:
        errors.append("ExposureGate.entry_cluster_guard is None (production repo_root)")

    gate_kabu = config.make_exposure_gate(repo_root=KABU)
    if getattr(gate_kabu, "entry_cluster_guard", None) is None:
        errors.append("ExposureGate.entry_cluster_guard is None (kabu_native repo_root)")

    if guard is not None:
        summary = guard.summary_fields()
        for key in (
            "cluster_guard_reject_count",
            "cluster_guard_exception_count",
            "cluster_guard_rejected_pnl",
            "cluster_guard_exception_pnl",
            "cluster_guard_exception_win_rate",
            "cluster_guard_exception_pf",
            "cluster_guard_exception_big_winner",
            "cluster_guard_exception_mfe0",
            "cluster_guard_blocked_cluster_counts",
        ):
            if key not in summary:
                errors.append(f"summary missing key: {key}")

    if config.or_overlay_enabled:
        or_gate = config.make_exposure_gate(repo_root=REPO)
        if getattr(or_gate, "entry_cluster_guard", None) is None:
            errors.append("OR config path lost cluster guard on gate (unexpected)")

    verdict = PHASE552_SMOKE_VERDICT if not errors else "phase549_runtime_ready_failed"
    out = {
        "verdict": verdict,
        "ready": not errors,
        "config_path": str(cfg_path),
        "model_path": str(model_path) if model_path.is_file() else None,
        "production_repo_root": str(REPO),
        "startup_smoke_test": smoke.to_dict(),
        "errors": errors,
        "cluster_guard_config": {
            "enabled": guard_cfg.enabled,
            "exception_enabled": guard_cfg.exception_enabled,
            "liquidity_burst_threshold": guard_cfg.liquidity_burst_threshold,
            "reject_clusters": sorted(guard_cfg.reject_clusters),
            "reject_csubs": sorted(guard_cfg.reject_csubs),
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
