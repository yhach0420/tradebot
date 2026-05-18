#!/usr/bin/env python3
"""
Phase 49: Performance review for push-replay small paper pilot output.

Example::
    python kabu_native/scripts/review_small_paper_push_replay.py \\
        --session-dir kabu_native/results/small_paper/20260518/push_replay_205219
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
    from research.small_paper_performance_review import build_and_write_review
    from small_paper.config import load_pilot_config

    parser = argparse.ArgumentParser(description="Phase49 push-replay performance review")
    parser.add_argument(
        "--session-dir",
        type=Path,
        required=True,
        help="push_replay_HHMMSS output directory",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot.yaml",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=None,
        help="Observer replay clock step (default: from summary.json)",
    )
    args = parser.parse_args()

    session_dir = args.session_dir
    if not session_dir.is_absolute():
        session_dir = repo_root / session_dir
    if not session_dir.is_dir():
        print(f"session dir not found: {session_dir}", file=sys.stderr)
        return 2

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    config = load_pilot_config(cfg_path) if cfg_path.is_file() else None

    review = build_and_write_review(
        session_dir,
        pilot_config=config,
        poll_interval_sec=args.poll_interval_sec,
    )
    print(json.dumps(review, ensure_ascii=False, indent=2, default=str))
    print(f"\nVerdict: {review.get('verdict', {}).get('verdict')}", file=sys.stderr)
    print(f"Wrote {session_dir / 'small_paper_performance_review.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
