#!/usr/bin/env python3
"""Phase600: Full push replay runtime equivalence audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase600 full replay runtime equivalence audit")
    parser.add_argument("--workers", type=int, default=4, help="Parallel replay workers")
    parser.add_argument("--chunk-size", type=int, default=50000, help="Checkpoint every N push rows")
    parser.add_argument("--max-rows", type=int, default=None, help="Limit push rows per replay")
    parser.add_argument(
        "--skip-replay",
        action="store_true",
        help="Skip replay; use existing checkpoints + live session data",
    )
    args = parser.parse_args()

    from research.phase600_full_replay_runtime_equivalence_audit import run_phase600

    result = run_phase600(
        REPO,
        workers=args.workers,
        chunk_size=args.chunk_size,
        max_rows=args.max_rows,
        skip_replay=args.skip_replay,
    )
    print(f"verdict={result.get('verdict')}", flush=True)
    ma = result.get("mandatory_answers") or {}
    print(f"classification={ma.get('verdict_class')}", flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    print(json.dumps(ma, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
