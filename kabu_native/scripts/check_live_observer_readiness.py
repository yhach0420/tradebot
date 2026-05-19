#!/usr/bin/env python3
"""
Phase 61: Live observer re-trial readiness (q070_cap3 + combined_structural_exit_v1).

Example::
    python kabu_native/scripts/check_live_observer_readiness.py \\
        --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


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
    from small_paper.live_observer_readiness import (
        DEFAULT_PHASE54_SESSION_REL,
        DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
        run_live_observer_readiness,
        write_readiness_report,
    )
    from small_paper.pilot_env import load_pilot_environment, log_pilot_env_status

    parser = argparse.ArgumentParser(description="Phase55 live observer readiness")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot_q070_cap3.yaml",
    )
    parser.add_argument(
        "--reference-session-dir",
        type=Path,
        default=repo_root / DEFAULT_PHASE54_SESSION_REL,
        help="Push-replay dir with Phase53/54 review artifacts",
    )
    parser.add_argument(
        "--structural-session-dir",
        type=Path,
        default=repo_root / DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
        help="Session dir with structural_observer_review.json (Phase60)",
    )
    parser.add_argument("--report-date", default=None)
    parser.add_argument("--skip-kabu", action="store_true")
    parser.add_argument("--skip-safety", action="store_true")
    parser.add_argument("--log-env", action="store_true")
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    ref_dir = args.reference_session_dir
    if not ref_dir.is_absolute():
        ref_dir = repo_root / ref_dir
    struct_dir = args.structural_session_dir
    if not struct_dir.is_absolute():
        struct_dir = repo_root / struct_dir

    from small_paper.config import load_pilot_config

    config = load_pilot_config(cfg_path)
    env_status = load_pilot_environment(
        repo_root=repo_root,
        discord_webhook_env=config.discord_webhook_env,
    )
    if args.log_env:
        log_pilot_env_status(env_status)

    day_key = args.report_date or datetime.now(JST).strftime("%Y%m%d")
    report = run_live_observer_readiness(
        cfg_path,
        repo_root=repo_root,
        day_key=day_key,
        reference_session_dir=ref_dir,
        structural_session_dir=struct_dir,
        skip_kabu=args.skip_kabu,
        skip_safety_bundle=args.skip_safety,
    )
    out = write_readiness_report(report, repo_root=repo_root, day_key=day_key)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {out}", file=sys.stderr)
    print(f"readiness={report.get('readiness')}", file=sys.stderr)
    return 0 if report.get("readiness") else 1


if __name__ == "__main__":
    raise SystemExit(main())
