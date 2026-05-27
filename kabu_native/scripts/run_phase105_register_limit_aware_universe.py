#!/usr/bin/env python3
"""Phase 105: register-limit-aware dynamic universe (shadow / dry-run only)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script = Path(__file__).resolve()
    build = script.parent / "build_dynamic_universe.py"
    args = [sys.executable, str(build), "--board-mode", "none"]
    if len(sys.argv) > 1:
        args.extend(sys.argv[1:])
    return subprocess.call(args)


if __name__ == "__main__":
    raise SystemExit(main())
