#!/usr/bin/env python3
"""Phase509 — T15/T13 signal definition audit runner."""

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
    parser = argparse.ArgumentParser(description="Phase509 T15/T13 signal definition audit")
    parser.parse_args()

    from research.phase509_t15_t13_signal_audit import run_and_write

    result = run_and_write(repo_root=REPO)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
