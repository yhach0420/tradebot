#!/usr/bin/env python3
"""Phase410 duplicate re-entry audit."""

from __future__ import annotations

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
    _bootstrap()
    from research.phase410_duplicate_reentry_audit import run_phase410_audit

    repo_root = REPO
    result = run_phase410_audit(repo_root=repo_root, output_dir=REPO / "results" / "reports")
    print(f"verdict={result['summary'].get('verdict')}", flush=True)
    print(json.dumps(result["summary"].get("mandatory_answers"), indent=2, ensure_ascii=False, default=str))
    print(f"report={result.get('report_path')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
