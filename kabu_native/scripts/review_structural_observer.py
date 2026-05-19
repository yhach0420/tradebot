#!/usr/bin/env python3
"""
Phase 58/60: structural observer evaluation (observer-only, no orders).

Example (official combined structural EXIT)::
    python kabu_native/scripts/review_structural_observer.py \\
        --session-dir kabu_native/results/small_paper/20260519/live_full_session_081047 \\
        --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \\
        --structural-exit-policy combined_structural_exit_v1
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
    from research.structural_exit_policies import (
        POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        POLICY_STRUCTURAL_OBSERVER_V1,
    )
    from research.structural_observer_review import (
        DEFAULT_OFFICIAL_EXIT_POLICY,
        build_and_write_structural_observer_review,
    )
    from small_paper.config import load_pilot_config

    parser = argparse.ArgumentParser(description="Structural observer review (Phase 58/60)")
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot_q070_cap3.yaml",
    )
    parser.add_argument("--poll-interval-sec", type=float, default=None)
    parser.add_argument(
        "--structural-exit-policy",
        choices=[POLICY_STRUCTURAL_OBSERVER_V1, POLICY_COMBINED_STRUCTURAL_EXIT_V1],
        default=DEFAULT_OFFICIAL_EXIT_POLICY,
        help="Official EXIT policy for PF and verdict (default: structural_observer_v1)",
    )
    args = parser.parse_args()

    session_dir = args.session_dir
    if not session_dir.is_absolute():
        session_dir = repo_root / session_dir
    if not session_dir.is_dir():
        print(f"session dir not found: {session_dir}", file=sys.stderr)
        return 2

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    config = load_pilot_config(cfg_path)

    review = build_and_write_structural_observer_review(
        session_dir,
        pilot_config=config,
        poll_interval_sec=args.poll_interval_sec,
        structural_exit_policy=args.structural_exit_policy,
    )
    print(json.dumps(review, ensure_ascii=False, indent=2, default=str))

    legacy_pf = review.get("legacy_virtual_hold_pf")
    base_pf = (review.get("baseline_structural_observer_v1_metrics") or {}).get("structural_pf")
    comb_pf = (review.get("combined_structural_exit_v1_metrics") or {}).get("structural_pf")
    print(
        f"\nOfficial policy: {review.get('structural_exit_policy')}",
        file=sys.stderr,
    )
    print(
        f"official_verdict={review.get('official_verdict')} "
        f"structural_pf={review.get('structural_pf')} "
        f"baseline_pf={base_pf} combined_pf={comb_pf} legacy_pf={legacy_pf}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
