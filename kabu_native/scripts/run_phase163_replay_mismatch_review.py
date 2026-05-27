#!/usr/bin/env python3
"""
Phase 163: Phase161 vs Phase162 G hybrid PF mismatch root-cause review.

Example::
    python kabu_native/scripts/run_phase163_replay_mismatch_review.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return native


def discover_sessions(small_paper: Path, *, limit: int = 7) -> list[Path]:
    found: list[tuple[float, Path]] = []
    for trades_path in small_paper.glob("**/structural_trades.csv"):
        if not (trades_path.parent / "structural_events.csv").is_file():
            continue
        found.append((trades_path.stat().st_mtime, trades_path.parent))
    found.sort(key=lambda x: x[0], reverse=True)
    out: list[Path] = []
    seen: set[str] = set()
    for _, p in found:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return sorted(out, key=lambda p: str(p))


def main() -> int:
    native = _bootstrap()
    from research.phase163_replay_mismatch_review import (
        analyze_phase163,
        write_phase163_outputs,
    )
    from small_paper.config import load_pilot_config

    cfg_path = native / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
    reports_dir = native / "results" / "reports"
    small_paper = native / "results" / "small_paper"
    p162_csv = reports_dir / "phase162_fade_hybrid_trade_details.csv"
    cap5_csv = reports_dir / "phase158_cap5_only_trades.csv"

    session_dirs = discover_sessions(small_paper)
    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 2
    if not p162_csv.is_file():
        print(
            json.dumps(
                {
                    "error": "phase162 trade details missing",
                    "hint": "run run_phase162_fade_hybrid_shadow_review.py first",
                },
                ensure_ascii=True,
            )
        )
        return 2

    config = load_pilot_config(cfg_path)
    result = analyze_phase163(
        session_dirs,
        pilot_config=config,
        phase162_details_csv=p162_csv,
        cap5_csv=cap5_csv if cap5_csv.is_file() else None,
    )
    outputs = write_phase163_outputs(result, reports_dir=reports_dir)

    report = {k: v for k, v in result.items() if k != "trade_details"}
    report["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    report["outputs"] = outputs
    Path(outputs["json"]).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "verdict": result.get("verdict"),
                "phase161_g_improved_count": result.get("phase161_g_improved_count"),
                "improvement_lost_count": result.get("improvement_lost_count"),
                "total_gain_lost": result.get("total_gain_lost"),
                "outputs": outputs,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    valid = {
        "replay_model_wrong",
        "session_close_dominant",
        "overlap_dominant",
        "quality_decay_dominant",
        "mixed",
    }
    return 0 if result.get("verdict") in valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
