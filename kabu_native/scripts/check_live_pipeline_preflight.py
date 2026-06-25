#!/usr/bin/env python3
"""
Phase506: Live PUSH pipeline preflight before paper trade start.

Verifies float-epoch price rings traverse ENTRY enrichment + ExposureGate without
the Phase505 total_seconds crash.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()
    from small_paper.live_pipeline_preflight import (
        default_config_path,
        run_live_pipeline_preflight,
    )

    parser = argparse.ArgumentParser(description="Phase506 live ENTRY pipeline preflight")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Pilot YAML (default: trailing_mfe production shadow config)",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args()

    cfg = args.config or default_config_path(repo_root)
    if not cfg.is_absolute():
        cfg = repo_root / cfg

    report = run_live_pipeline_preflight(config_path=cfg, repo_root=repo_root)
    if args.json:
        print(
            json.dumps(
                {
                    "verdict": report.verdict,
                    "ready": report.ready,
                    "config_path": report.config_path,
                    "cases": [
                        {
                            "case_id": c.case_id,
                            "ok": c.ok,
                            "uses_float_epoch_timestamps": c.uses_float_epoch_timestamps,
                            "tick_ts_type": c.tick_ts_type,
                            "rsi14": c.rsi14,
                            "late_chase_flag": c.late_chase_flag,
                            "full_exposure_gate_reached": c.full_exposure_gate_reached,
                            "decision_reason": c.decision_reason,
                            "error": c.error,
                        }
                        for c in report.cases
                    ],
                    "errors": report.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    if report.ready:
        print("[PREFLIGHT] live pipeline ok")
        return 0
    print("[PREFLIGHT] live pipeline failed", file=sys.stderr)
    for err in report.errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
