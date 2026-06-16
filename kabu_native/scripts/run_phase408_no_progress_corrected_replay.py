#!/usr/bin/env python3
"""Phase408: No Progress Exit corrected replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent


def _bootstrap() -> None:
    for p in (REPO / "src", PARENT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase408 corrected no progress replay")
    parser.add_argument("--output-dir", type=Path, default=REPO / "results" / "reports")
    args = parser.parse_args()

    _bootstrap()
    from research.phase408_no_progress_corrected_replay import run_phase408_corrected_replay

    result = run_phase408_corrected_replay(repo_root=REPO.resolve(), output_dir=args.output_dir.resolve())
    summary = result["summary"]
    ma = summary.get("mandatory_answers") or {}
    print(f"verdict={summary.get('verdict')}", flush=True)
    headline = str(summary.get("headline") or "")
    print(headline.encode("ascii", errors="replace").decode("ascii"), flush=True)
    print(json.dumps(ma, indent=2, ensure_ascii=False, default=str))
    print(f"report={result.get('report_path')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
