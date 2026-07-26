#!/usr/bin/env python3
"""EEC_v3 Adaptive Noise Band & Hysteresis runner (offline EC2 diagnostic)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.eec_noise_hysteresis.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline()
    c = payload.get("completion") or {}
    print("=== COMPLETION ===")
    for k, v in c.items():
        print(f"{k}: {v}")
    print(f"out={payload.get('out_dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
