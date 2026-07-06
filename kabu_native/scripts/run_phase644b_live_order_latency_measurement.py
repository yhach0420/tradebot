#!/usr/bin/env python3
"""Phase644b: aggregate live paper order latency traces."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent
for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase644b_live_order_latency_measurement import Phase644bJob, main as _main

    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
