#!/usr/bin/env python3
"""Canonical path unify entrypoint — delegates solely to clean SoT publish."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))


def main() -> int:
    from scripts.run_e1_x5_sot_clean_publish_20260728 import main as publish_main

    return publish_main()


if __name__ == "__main__":
    raise SystemExit(main())
