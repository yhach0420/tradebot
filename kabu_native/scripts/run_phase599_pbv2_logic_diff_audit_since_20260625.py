#!/usr/bin/env python3
"""Phase599: PBv2 logic diff audit since 20260625."""

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
    parser = argparse.ArgumentParser(description="Phase599 PBv2 logic diff audit")
    parser.add_argument("--skip-replay", action="store_true", help="Skip push replay (git/config only)")
    parser.add_argument("--max-push-rows", type=int, default=None, help="Limit push rows for replay")
    args = parser.parse_args()

    from research.phase599_pbv2_logic_diff_audit_since_20260625 import run_phase599

    result = run_phase599(
        REPO,
        skip_replay=args.skip_replay,
        max_push_rows=args.max_push_rows,
    )
    print(f"verdict={result.get('verdict')}", flush=True)
    print(f"classification={result.get('verdict_class')}", flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
