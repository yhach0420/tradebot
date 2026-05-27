#!/usr/bin/env python3
"""
Phase 148b: Recover missing session artifacts from raw small-paper live files.

Example::
    python kabu_native/scripts/recover_small_paper_session_finalize.py \\
        --session-dir kabu_native/results/small_paper/20260525/live_session_075733
"""

from __future__ import annotations

import argparse
import json
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
    repo_root, _native = _bootstrap()
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
    from runner.session_finalize_recovery import recover_session_finalize, recovery_verdict

    parser = argparse.ArgumentParser(description="Recover small-paper session finalize artifacts")
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
    parser.add_argument("--skip-structural-review", action="store_true")
    args = parser.parse_args()

    session_dir = args.session_dir
    if not session_dir.is_absolute():
        session_dir = repo_root / session_dir

    try:
        config_rel = str(args.config.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        config_rel = str(args.config)

    result = recover_session_finalize(
        session_dir,
        repo_root=repo_root,
        config_rel=config_rel,
        structural_exit_policy=args.structural_exit_policy,
        poll_interval_sec=args.poll_interval_sec,
        skip_structural_review=args.skip_structural_review,
    )
    verdict = recovery_verdict(result)
    payload = {"verdict": verdict, **result}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if verdict == "am_session_recovered_and_runner_fixed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
