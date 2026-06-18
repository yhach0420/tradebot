#!/usr/bin/env python3
"""Phase424 Phase273/274 equity curve consistency audit."""

from __future__ import annotations

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
    from research.phase273_live_config_forward_shadow_logger import LiveConfigForwardShadowLogger
    from research.phase274_live_config_auto_transition_shadow import LiveConfigAutoTransitionShadow
    from research.phase424_equity_curve_consistency_audit import Phase424Job
    from research.structural_trade_normalize import resolve_reports_dir

    job = Phase424Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)

    # Refresh Phase273/274 outputs with canonical trade stream
    reports = resolve_reports_dir(REPO)
    p273 = LiveConfigForwardShadowLogger(repo_root=REPO, reports_dir=reports).run(day="20260617")
    LiveConfigForwardShadowLogger(repo_root=REPO, reports_dir=reports).write_outputs(p273)
    p274 = LiveConfigAutoTransitionShadow(repo_root=REPO, reports_dir=reports).run(day="20260617")
    LiveConfigAutoTransitionShadow(repo_root=REPO, reports_dir=reports).write_outputs(p274)

    audit = result.get("audit") or {}
    summary = result.get("summary") or {}
    print(f"verdict={audit.get('verdict')}", flush=True)
    print(json.dumps(summary.get("equity_milestones") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"audit={paths.get('audit')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0 if audit.get("verdict") == "bug_fixed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
