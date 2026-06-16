#!/usr/bin/env python3
"""Phase411: same_symbol_open_reentry_reject forward shadow."""

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
    parser = argparse.ArgumentParser(description="Phase411 same-symbol reentry shadow")
    parser.add_argument("--day", type=str, default=None)
    parser.add_argument("--output-dir", type=Path, default=REPO / "results" / "reports")
    args = parser.parse_args()

    _bootstrap()
    from research.phase411_same_symbol_reentry_shadow import SameSymbolReentryShadowLogger

    repo_root = REPO
    job = SameSymbolReentryShadowLogger(repo_root=repo_root, reports_dir=args.output_dir.resolve())
    result = job.run(day=args.day)
    paths = job.write_outputs(result)
    fwd = result.get("forward_summary") or {}
    print(f"status={result.get('last_run', {}).get('status')}", flush=True)
    print(f"baseline_trades={fwd.get('baseline_trade_count')} shadow_trades={fwd.get('shadow_trade_count')}", flush=True)
    print(f"delta_pnl={fwd.get('delta_pnl_yen_100')} verdict={fwd.get('verdict')}", flush=True)
    print(json.dumps(fwd, indent=2, ensure_ascii=False, default=str))
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
