#!/usr/bin/env python3
"""
Phase 54: TAKE / HOLD / EXIT runtime review (observer-only what-if).

Example::
    python kabu_native/scripts/review_runtime_exit.py \\
        --session-dir kabu_native/results/small_paper/20260518/push_replay_220451 \\
        --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml
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
    from research.runtime_exit_review import build_and_write_runtime_exit_review
    from small_paper.config import load_pilot_config

    parser = argparse.ArgumentParser(description="Phase54 TAKE/HOLD/EXIT runtime review")
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot_q070_cap3.yaml",
    )
    parser.add_argument("--poll-interval-sec", type=float, default=None)
    args = parser.parse_args()

    session_dir = args.session_dir
    if not session_dir.is_absolute():
        session_dir = repo_root / session_dir
    if not session_dir.is_dir():
        print(f"session dir not found: {session_dir}", file=sys.stderr)
        return 2

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    config = load_pilot_config(cfg_path)

    review = build_and_write_runtime_exit_review(
        session_dir,
        pilot_config=config,
        poll_interval_sec=args.poll_interval_sec,
    )
    print(json.dumps(review, ensure_ascii=False, indent=2, default=str))
    rec = review.get("recommendation", {}).get("recommend_runtime_fix")
    print(f"\nRecommend runtime fix: {rec}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
