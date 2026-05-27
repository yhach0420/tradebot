#!/usr/bin/env python3
"""
Phase 156: Intraday refresh (10:00 / 14:30) + cap=5 design review (shadow / what-if only).

Example::
    python kabu_native/scripts/run_phase156_intraday_refresh_cap5_review.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def main() -> int:
    repo_root, native_root = _bootstrap()
    from research.mfe_mae_exit_review import discover_sessions
    from research.phase156_intraday_refresh_cap5_review import (
        MIN_SESSIONS,
        analyze_phase156,
        write_phase156_outputs,
    )
    from small_paper.config import load_pilot_config

    cfg_path = (
        native_root / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
    )
    reports_dir = native_root / "results" / "reports"
    docs_dir = native_root / "docs"
    small_paper = native_root / "results" / "small_paper"

    all_dirs = discover_sessions(small_paper, max_sessions=20)
    session_dirs = [
        p
        for p in all_dirs
        if "push_replay" not in str(p).lower()
        and ("live_full_session" in p.name or "live_session" in p.name)
    ]
    if len(session_dirs) < MIN_SESSIONS:
        session_dirs = [
            p for p in all_dirs if "push_replay" not in str(p).lower()
        ][:8]

    if not session_dirs:
        print(json.dumps({"error": "no sessions with structural_trades.csv"}, ensure_ascii=True))
        return 2

    config = load_pilot_config(cfg_path)
    result = analyze_phase156(session_dirs, pilot_config=config)
    outputs = write_phase156_outputs(result, reports_dir=reports_dir, docs_dir=docs_dir)

    design = result["design"]
    design["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    design["sessions_analyzed"] = [str(p) for p in session_dirs]
    design["outputs"] = outputs
    (reports_dir / "phase156_intraday_refresh_cap5_design.json").write_text(
        json.dumps(design, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "verdict_notes": result["verdict_notes"],
                "session_count": design["session_count"],
                "cap_aggregate": design["cap_aggregate"],
                "scenarios": design["scenarios"],
                "outputs": outputs,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    ok = result["verdict"] in (
        "refresh_cap5_shadow_ready",
        "cap5_promising_refresh_not_needed",
        "refresh_promising_cap3_enough",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
