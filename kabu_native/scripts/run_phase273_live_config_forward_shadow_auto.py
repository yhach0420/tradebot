#!/usr/bin/env python3
"""Phase273 auto-run after paper session (manual trigger)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase273 live config forward shadow auto")
    parser.add_argument("--day", type=str, default=None)
    parser.add_argument("--reports-dir", type=Path, default=REPO / "kabu_native" / "results" / "reports")
    args = parser.parse_args()

    _bootstrap()
    from small_paper.live_config_forward_shadow_auto import run_live_config_forward_shadow_auto

    block = run_live_config_forward_shadow_auto(
        repo_root=REPO,
        day=args.day,
        reports_dir=args.reports_dir,
    )
    print(block, flush=True)
    return 0 if block.get("status") != "warning" else 1


if __name__ == "__main__":
    raise SystemExit(main())
