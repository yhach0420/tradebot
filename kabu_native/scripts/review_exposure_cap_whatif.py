#!/usr/bin/env python3
"""
Phase 53: Exposure cap what-if validation (q070 + allowed windows).

Example::
    python kabu_native/scripts/review_exposure_cap_whatif.py \\
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
    from research.exposure_cap_whatif_review import build_and_write_exposure_cap_whatif
    from small_paper.config import load_pilot_config

    parser = argparse.ArgumentParser(description="Phase53 exposure cap what-if")
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot_q070_cap3.yaml",
    )
    parser.add_argument("--min-quality", type=float, default=0.70)
    args = parser.parse_args()

    session_dir = args.session_dir
    if not session_dir.is_absolute():
        session_dir = repo_root / session_dir
    if not session_dir.is_dir():
        print(f"session dir not found: {session_dir}", file=sys.stderr)
        return 2

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    config = load_pilot_config(cfg_path)

    review = build_and_write_exposure_cap_whatif(
        session_dir,
        pilot_config=config,
        min_quality=args.min_quality,
    )
    text = json.dumps(review, ensure_ascii=False, indent=2, default=str)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    rec = review.get("recommendation", {}).get("recommend_cap_candidate")
    guidance = review.get("recommendation", {}).get("live_observer_trial_guidance")
    print(f"\nRecommend cap: {rec}", file=sys.stderr)
    print(f"Guidance: {guidance}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
