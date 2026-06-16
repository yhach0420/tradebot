#!/usr/bin/env python3
"""Phase395: Runtime Position-CAP alignment — investigation, shadow, proposals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent


def _bootstrap() -> None:
    for p in (REPO / "src", PARENT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase395 position-CAP alignment study")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args()

    _bootstrap()
    from research.phase395_position_cap_alignment import run_phase395

    result = run_phase395(args.repo_root.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
