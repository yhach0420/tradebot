#!/usr/bin/env python3
"""Phase 147: Shadow pilot readiness review (Core10 + Dynamic40 + AM/PM)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for row in rows:
        for k in row:
            if k not in cols:
                cols.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 147 shadow pilot readiness review")
    parser.add_argument("--trade-date", action="append", default=None)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.shadow_pilot_readiness_review import (
        TARGET_DATES,
        build_operational_checklist_md,
        run_shadow_pilot_readiness_review,
    )

    trade_dates = args.trade_date or list(TARGET_DATES)
    result = run_shadow_pilot_readiness_review(
        repo_root=ROOT,
        reports_dir=REPORTS,
        trade_dates=trade_dates,
    )

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    out_json = REPORTS / "phase147_shadow_pilot_readiness_review.json"
    uni_csv = REPORTS / "phase147_universe_readiness.csv"
    risk_csv = REPORTS / "phase147_risk_register.csv"
    checklist_md = REPORTS / "phase147_operational_checklist.md"

    report: dict[str, Any] = {
        "phase": 147,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "trade_dates": trade_dates,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "shadow_pilot_ready",
            "B": "shadow_pilot_ready_with_cautions",
            "C": "blocker_exists",
            "D": "configuration_incomplete",
        },
        "universe_readiness": result["universe_readiness"],
        "session_readiness": result["session_readiness"],
        "operational_readiness": result["operational_readiness"],
        "limit_policy_readiness": result["limit_policy_readiness"],
        "risk_register_summary": {
            "total": len(result["risk_register"]),
            "critical": sum(1 for r in result["risk_register"] if r["severity"] == "critical"),
            "high": sum(1 for r in result["risk_register"] if r["severity"] == "high"),
            "medium": sum(1 for r in result["risk_register"] if r["severity"] == "medium"),
            "low": sum(1 for r in result["risk_register"] if r["severity"] == "low"),
            "production_blockers": sum(1 for r in result["risk_register"] if r["production_blocker"]),
        },
        "phase146_reference": result.get("phase146_reference"),
        "phase145_reference": result.get("phase145_reference"),
        "pilot_config_snapshot": result.get("pilot_config_snapshot"),
        "shadow_commands": result.get("shadow_commands"),
        "methodology": {
            "review_only": True,
            "no_production_yaml_change": True,
            "no_entry_exit_quality_change": True,
            "order_enabled_must_be_false": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "universe_csv": _rel(uni_csv),
            "risk_csv": _rel(risk_csv),
            "checklist_md": _rel(checklist_md),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(uni_csv, result["universe_daily"])
    _write_csv(risk_csv, result["risk_register"])
    checklist_md.write_text(build_operational_checklist_md(result), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "verdict_notes": result["verdict_notes"],
                "blockers": report["risk_register_summary"]["production_blockers"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
