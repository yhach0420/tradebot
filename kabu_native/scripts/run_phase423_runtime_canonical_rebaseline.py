#!/usr/bin/env python3
"""Phase423 canonical runtime rebaseline (post-Phase421)."""

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
    from research.phase423_runtime_canonical_rebaseline import Phase423Job
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(REPO)
    job = Phase423Job(repo_root=REPO, reports_dir=reports)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("summary") or {}
    metrics = summary.get("metrics") or {}
    print(f"verdict={summary.get('verdict')}", flush=True)
    print(
        json.dumps(
            {
                "accepted": metrics.get("accepted_count"),
                "rejected": metrics.get("rejected_count"),
                "pf": metrics.get("profit_factor"),
                "pnl_yen": metrics.get("total_pnl_yen"),
                "maxdd_yen": metrics.get("max_drawdown_yen"),
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )
    print(f"summary={paths.get('summary')}", flush=True)
    print(f"daily={paths.get('daily')}", flush=True)
    print(f"trades={paths.get('trades')}", flush=True)
    print(f"comparison={paths.get('comparison')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0 if summary.get("verdict") == "canonical_baseline_established" else 1


if __name__ == "__main__":
    raise SystemExit(main())
