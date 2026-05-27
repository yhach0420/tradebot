#!/usr/bin/env python3
"""
Phase 159: overlap_replaced_review validity review (all sessions).

Example::
    python kabu_native/scripts/run_phase159_overlap_review.py
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


def discover_all_sessions(small_paper: Path) -> list[Path]:
    found: list[Path] = []
    for trades_path in small_paper.glob("**/structural_trades.csv"):
        if trades_path.parent.is_dir():
            found.append(trades_path.parent)
    found.sort(key=lambda p: str(p))
    return found


def main() -> int:
    repo, native = _bootstrap()
    from research.phase159_overlap_review import analyze_phase159, write_phase159_outputs
    from small_paper.config import load_pilot_config

    cfg_path = (
        native / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
    )
    reports_dir = native / "results" / "reports"
    docs_dir = native / "docs"
    small_paper = native / "results" / "small_paper"
    cap5_csv = reports_dir / "phase158_cap5_only_trades.csv"

    session_dirs = discover_all_sessions(small_paper)
    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 2

    config = load_pilot_config(cfg_path)
    result = analyze_phase159(
        session_dirs,
        pilot_config=config,
        cap5_only_csv=cap5_csv if cap5_csv.is_file() else None,
    )
    outputs = write_phase159_outputs(result, reports_dir=reports_dir, docs_dir=docs_dir)

    report = {k: v for k, v in result.items() if k not in ("overlap_events", "pair_comparison", "worst50")}
    report["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    report["session_count"] = len(session_dirs)
    report["outputs"] = outputs
    (reports_dir / "phase159_overlap_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "overlap_summary": result.get("overlap_summary"),
                "outputs": outputs,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if result["verdict"] in ("overlap_harmful", "overlap_mixed", "overlap_helpful") else 1


if __name__ == "__main__":
    raise SystemExit(main())
