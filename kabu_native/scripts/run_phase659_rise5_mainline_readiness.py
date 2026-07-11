#!/usr/bin/env python3
"""Phase659 — Rise5 mainline promotion readiness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

KABU = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    for p in (KABU / "src", KABU.parent):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.phase659_rise5_mainline_readiness import run_phase659

    result = run_phase659(repo_root=KABU)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"report={result.get('output_paths', {}).get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
