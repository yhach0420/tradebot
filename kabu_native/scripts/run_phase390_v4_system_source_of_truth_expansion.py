#!/usr/bin/env python3
"""Phase390-v4: System Source of Truth expansion — audit CSV → history MD → report."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_full_system_development_history import generate  # noqa: E402


def main() -> int:
    out, old_lines, new_lines = generate(refresh_audit=True)
    print(f"phase390-v4 complete: {out}")
    print(f"line delta: {new_lines - old_lines:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
