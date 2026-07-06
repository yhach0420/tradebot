#!/usr/bin/env python3
"""Phase652: Shadow Registry and Dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase652_shadow_registry import main as _main

    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
