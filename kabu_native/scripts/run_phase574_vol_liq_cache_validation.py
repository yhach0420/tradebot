#!/usr/bin/env python3
"""Phase574 — Vol/Liq startup cache shadow validation (research only)."""

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
    parser = argparse.ArgumentParser(description="Phase574 vol/liq cache shadow validation")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--recompute-baseline",
        action="store_true",
        help="Ignore baseline snapshots and rerun full prior_vol_liq_scores scan",
    )
    args = parser.parse_args()

    from research.phase574_vol_liq_cache_validation import Phase574Job

    job = Phase574Job(
        repo_root=REPO,
        workers=max(1, args.workers),
        skip_baseline_if_snapshot=not args.recompute_baseline,
    )
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(f"all_pass={result.get('all_pass')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True), flush=True)
    for label, path in paths.items():
        print(f"{label}={path}", flush=True)
    return 0 if result.get("all_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
