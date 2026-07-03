#!/usr/bin/env python3
"""Phase620 parallel disk-safe (4 workers, focus days 624-701)."""

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
    parser = argparse.ArgumentParser(description="Phase620 parallel disk-safe")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--force-disk", action="store_true", help="Start even if disk above 74 percent")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    if args.cleanup_only:
        from research.phase620_parallel_disk_safe import preflight_cleanup
        from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

        kabu = resolve_kabu_root(REPO)
        r = preflight_cleanup(kabu, resolve_reports_dir(kabu))
        print(json.dumps(r, indent=2))
        return 0 if r.get("can_start") else 1

    if args.aggregate_only:
        from research.phase620_parallel_disk_safe import aggregate_final

        report = aggregate_final(REPO)
        print(f"verdict={report.get('verdict')}")
        print(json.dumps(report.get("mandatory_answers"), ensure_ascii=False, indent=2))
        return 0

    from research.phase620_parallel_disk_safe import run_parallel

    report = run_parallel(REPO, resume=not args.no_resume, force_disk=args.force_disk)
    print(f"verdict={report.get('verdict')}")
    print(f"disk_pct={report.get('disk_used_pct_final')}")
    print(json.dumps(report.get("mandatory_answers"), ensure_ascii=False, indent=2))
    return 0 if report.get("verdict") == "phase620_parallel_disk_safe_done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
