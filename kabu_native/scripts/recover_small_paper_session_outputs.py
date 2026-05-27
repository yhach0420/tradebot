#!/usr/bin/env python3
"""
Phase 148c: Recover missing small-paper session outputs from raw live files.

Example::
    python kabu_native/scripts/recover_small_paper_session_outputs.py \\
        --session-dir kabu_native/results/small_paper/20260525/live_session_075733 \\
        --structural-exit-policy combined_structural_exit_v1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def main() -> int:
    repo_root, native = _bootstrap()
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
    from runner.session_finalize_recovery import (
        phase148c_recovery_verdict,
        recover_session_outputs,
        validate_session_output_counts,
    )

    parser = argparse.ArgumentParser(description="Phase148c recover session outputs")
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=repo_root / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml",
    )
    parser.add_argument(
        "--structural-exit-policy",
        default=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    )
    parser.add_argument("--poll-interval-sec", type=float, default=None)
    parser.add_argument("--force-summary-rebuild", action="store_true")
    parser.add_argument("--force-structural-review", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    session_dir = args.session_dir
    if not session_dir.is_absolute():
        session_dir = repo_root / session_dir

    try:
        config_rel = str(args.config.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        config_rel = str(args.config)

    recovery: dict = {"skipped": True}
    review_cmd_exit = None
    if not args.validate_only:
        review_proc = subprocess.run(
            [
                sys.executable,
                str(native / "scripts" / "review_structural_observer.py"),
                "--session-dir",
                str(session_dir),
                "--config",
                str(args.config if args.config.is_absolute() else repo_root / args.config),
                "--structural-exit-policy",
                args.structural_exit_policy,
            ]
            + (
                ["--poll-interval-sec", str(args.poll_interval_sec)]
                if args.poll_interval_sec is not None
                else []
            ),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        review_cmd_exit = review_proc.returncode

        recovery = recover_session_outputs(
            session_dir,
            repo_root=repo_root,
            config_rel=config_rel,
            structural_exit_policy=args.structural_exit_policy,
            poll_interval_sec=args.poll_interval_sec,
            force_summary_rebuild=args.force_summary_rebuild,
            force_structural_review=args.force_structural_review,
        )
        recovery["review_structural_observer_exit_code"] = review_cmd_exit

    validation = validate_session_output_counts(session_dir)
    verdict = phase148c_recovery_verdict(recovery, validation)

    payload = {
        "verdict": verdict,
        "recovery": recovery,
        "validation": validation,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if verdict == "am_session_outputs_recovered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
