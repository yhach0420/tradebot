#!/usr/bin/env python3
"""Run Phase505 — 20260623 runtime failure root cause analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from research.phase505_runtime_failure_root_cause import run_phase505  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase505 runtime failure RCA (20260623)")
    parser.add_argument(
        "--kabu-root",
        type=Path,
        default=REPO,
        help="kabu_native root (default: script parent)",
    )
    args = parser.parse_args()
    out = run_phase505(kabu_root=args.kabu_root)
    print(json.dumps({"verdict": out.get("verdict"), "generated_at": out.get("generated_at")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
