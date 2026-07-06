#!/usr/bin/env python3
"""Phase633 — combo_soft robustness decomposition runner."""

from __future__ import annotations

import sys
from pathlib import Path

KABU = Path(__file__).resolve().parents[1]
REPO = KABU.parent


def main() -> int:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    from research.phase633_combo_soft_robustness import main as _main

    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
