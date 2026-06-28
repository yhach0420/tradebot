#!/usr/bin/env python3
"""Phase550/552: paper trade readiness — production startup smoke test required."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"


def _bootstrap() -> Path:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return REPO


def main() -> int:
    repo_root = _bootstrap()
    parser = argparse.ArgumentParser(description="Phase550 paper trade readiness")
    parser.add_argument(
        "--exit-policy-shadow",
        default="trailing-mfe",
        choices=["", "fade-hybrid", "fade-breakdown", "trailing-mfe"],
    )
    args = parser.parse_args()

    from small_paper.live_pipeline_preflight import (
        default_config_path,
        run_live_pipeline_preflight,
    )
    from small_paper.production_startup_smoke_test import (
        PHASE552_SMOKE_VERDICT,
        production_run_session_key,
        run_production_startup_smoke_test,
    )
    from small_paper.entry_cluster_guard import validate_entry_cluster_guard_model
    from small_paper.config import load_pilot_config

    errors: list[str] = []
    cfg_path = default_config_path(repo_root)
    if not cfg_path.is_absolute():
        cfg_path = repo_root / cfg_path
    config = load_pilot_config(cfg_path)

    smoke = run_production_startup_smoke_test(
        repo_root=repo_root,
        config_rel=str(cfg_path.relative_to(repo_root)).replace("\\", "/"),
    )
    if not smoke.ready:
        errors.extend(smoke.errors)

    preflight = run_live_pipeline_preflight(config_path=cfg_path, repo_root=repo_root)
    if not preflight.ready:
        errors.extend(preflight.errors)

    _, cg_errors = validate_entry_cluster_guard_model(config, repo_root=repo_root)
    errors.extend(cg_errors)

    gate = config.make_exposure_gate(
        repo_root=repo_root,
        run_session_key=production_run_session_key(day_stamp="smoke_fast"),
    )
    if getattr(config, "entry_cluster_guard_enabled", False):
        if getattr(gate, "entry_cluster_guard", None) is None:
            errors.append("phase550: make_exposure_gate missing entry_cluster_guard at repo_root")

    verdict = PHASE552_SMOKE_VERDICT if not errors else "phase550_paper_trade_readiness_failed"
    out = {
        "verdict": verdict,
        "ready": not errors,
        "production_repo_root": str(repo_root),
        "config_path": str(cfg_path),
        "smoke_test": smoke.to_dict(),
        "preflight_verdict": preflight.verdict,
        "errors": errors,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
