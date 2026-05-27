#!/usr/bin/env python3
"""
Phase 164: fade hybrid refinement review runner (review only).

Example::
    python kabu_native/scripts/run_phase164_fade_hybrid_refinement_review.py
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


def discover_recent_sessions(small_paper: Path, *, limit: int = 7) -> list[Path]:
    found: list[tuple[float, Path]] = []
    for trades_path in small_paper.glob("**/structural_trades.csv"):
        if not (trades_path.parent / "structural_events.csv").is_file():
            continue
        found.append((trades_path.stat().st_mtime, trades_path.parent))
    found.sort(key=lambda x: x[0], reverse=True)
    out: list[Path] = []
    seen: set[str] = set()
    for _, p in found:
        k = str(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
        if len(out) >= limit:
            break
    return sorted(out, key=lambda p: str(p))


def main() -> int:
    _repo, native = _bootstrap()
    from research.phase164_fade_hybrid_refinement_review import (
        analyze_phase164,
        write_phase164_outputs,
    )
    from small_paper.config import load_pilot_config

    # Keep same baseline config as Phase161/163 for gain_161 extraction.
    cfg_path = native / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
    reports_dir = native / "results" / "reports"
    docs_dir = native / "docs"
    small_paper = native / "results" / "small_paper"
    cap5_csv = reports_dir / "phase158_cap5_only_trades.csv"
    p162_details = reports_dir / "phase162_fade_hybrid_trade_details.csv"

    if not p162_details.is_file():
        print(
            json.dumps(
                {
                    "error": "phase162_fade_hybrid_trade_details.csv missing",
                    "hint": "run run_phase162_fade_hybrid_shadow_review.py first",
                },
                ensure_ascii=True,
            )
        )
        return 2

    session_dirs = discover_recent_sessions(small_paper, limit=7)
    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 2

    cfg = load_pilot_config(cfg_path)
    result = analyze_phase164(
        session_dirs,
        pilot_config=cfg,
        phase162_trade_details_csv=p162_details,
        cap5_csv=cap5_csv if cap5_csv.is_file() else None,
    )
    outputs = write_phase164_outputs(result, reports_dir=reports_dir, docs_dir=docs_dir)

    report = {k: v for k, v in result.items() if k not in ("scenario_rows",)}
    report["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    report["outputs"] = outputs
    Path(outputs["json"]).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "verdict": result.get("verdict"),
                "session_count": result.get("session_count"),
                "phase161_improved_trade_count": result.get("phase161_improved_trade_count"),
                "outputs": outputs,
            },
            ensure_ascii=True,
            indent=2,
        )
    )

    valid = {
        "second_fade_disable_promising",
        "second_fade_strict_promising",
        "overlap_protection_needed",
        "breakdown_only_promising",
        "no_replay_robust_improvement",
    }
    return 0 if result.get("verdict") in valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

