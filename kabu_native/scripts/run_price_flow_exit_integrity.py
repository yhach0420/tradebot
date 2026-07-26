#!/usr/bin/env python3
"""Offline Price-Flow EXIT integrity / CAP=5 revalidation runner."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.price_flow_exit_integrity.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline()
    v = payload.get("verdict") or {}
    c = payload.get("completion") or {}
    print("=== COMPLETION ===")
    for k in sorted(c.keys(), key=lambda x: (len(x), x)):
        print(f"{k}: {c[k]}")
    print(f"final={v.get('final')}")
    print(f"out={payload.get('out_dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
