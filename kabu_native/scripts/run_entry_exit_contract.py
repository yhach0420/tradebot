#!/usr/bin/env python3
"""Offline Entry–Exit Contract Strategy Study runner."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.entry_exit_contract.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline()
    c = payload.get("completion") or {}
    print("=== COMPLETION ===")
    for k in sorted(c.keys(), key=lambda x: (len(str(x).split("_")[0]), x)):
        print(f"{k}: {c[k]}")
    print(f"out={payload.get('out_dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
