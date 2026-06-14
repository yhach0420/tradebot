#!/usr/bin/env python3
"""Phase390: System Source of Truth v3 expansion — regenerate development history from audit CSV."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_full_system_development_history import generate  # noqa: E402


def main() -> int:
    out = generate(refresh_audit=True)
    print(f"phase390 complete: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
