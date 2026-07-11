#!/usr/bin/env python3
"""Phase658 — Full-period shadow revalidation on Phase634 universe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase658 full-period shadow revalidation")
    parser.add_argument(
        "--skip-slow",
        action="store_true",
        help="Skip phase655 horizon enrichment (counterfactual replay)",
    )
    args = parser.parse_args()

    from research.phase658_full_period_shadow_revalidation import run_phase658

    result = run_phase658(repo_root=KABU, skip_slow=args.skip_slow)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    paths = result.get("output_paths") or {}
    if paths.get("report"):
        print(f"report={paths['report']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
