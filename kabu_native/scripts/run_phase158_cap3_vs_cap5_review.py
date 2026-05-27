#!/usr/bin/env python3
"""
Phase 158: cap3 vs cap5 review (price-risk + entry guard context, shadow only).

Example::
    python kabu_native/scripts/run_phase158_cap3_vs_cap5_review.py
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
    from research.phase158_cap3_vs_cap5_review import analyze_phase158, write_phase158_outputs
    from small_paper.config import load_pilot_config

    cfg_path = (
        native_root / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
    )
    reports_dir = native_root / "results" / "reports"
    small_paper = native_root / "results" / "small_paper"

    all_dirs = discover_sessions(small_paper, max_sessions=20)
    session_dirs = [
        p
        for p in all_dirs
        if "push_replay" not in str(p).lower()
        and ("live_full_session" in p.name or "live_session" in p.name)
    ]
    if len(session_dirs) < 4:
        session_dirs = [p for p in all_dirs if "push_replay" not in str(p).lower()][:8]

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 2

    config = load_pilot_config(cfg_path)
    result = analyze_phase158(session_dirs, pilot_config=config)
    outputs = write_phase158_outputs(result, reports_dir=reports_dir)

    report = {k: v for k, v in result.items() if not k.endswith("_rows")}
    report["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    report["sessions_analyzed"] = [str(p) for p in session_dirs]
    report["outputs"] = outputs
    (reports_dir / "phase158_cap3_vs_cap5_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "evaluation_layers": result.get("evaluation_layers"),
                "cap_evaluation": result.get("cap_evaluation"),
                "exit_evaluation": result.get("exit_evaluation"),
                "outputs": outputs,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if result["verdict"] in ("cap5_promising", "cap3_preferred") else 1


if __name__ == "__main__":
    raise SystemExit(main())
