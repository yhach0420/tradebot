#!/usr/bin/env python3
"""Phase 145: AM/PM rescreening, limit status, session close review (what-if only)."""

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
SMALL_PAPER = NATIVE / "results" / "small_paper"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"


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


def _build_recommendations_md(result: dict[str, Any]) -> str:
    am = result["am_pm_rescreening"]
    lim = result["limit_status"]
    close = result["session_close"]
    lines = [
        "# Phase 145 — Remaining Issues Review",
        "",
        f"Generated: {result.get('generated_at', '')}",
        "",
        "## Summary",
        "",
        f"- Trade dates: {', '.join(result.get('trade_dates') or [])}",
        f"- Live sessions: {result.get('session_count', 0)}",
        "",
        "| Topic | Verdict |",
        "|-------|---------|",
        f"| AM/PM rescreening | `{am['verdict']}` |",
        f"| Limit up/down | `{lim['verdict']}` |",
        f"| Session close (11:25 / 15:23) | `{close['verdict']}` |",
        "",
        "## 1. AM/PM rescreening",
        "",
        f"**Verdict:** `{am['verdict']}`",
        "",
    ]
    for n in am.get("verdict_notes") or []:
        lines.append(f"- {n}")
    lines.extend(["", "### Notes", "", "- Same Core10 + Dynamic40 / vol_liq logic; no coefficient change.", "- PM uses morning + PM push composite when push data exists.", ""])
    if am.get("rows"):
        r0 = am["rows"][0]
        lines.append(
            f"Example day `{r0.get('trade_date')}`: overlap={r0.get('overlap_count')} "
            f"PM added={r0.get('pm_added_count')} AM removed={r0.get('am_removed_count')} "
            f"churn={r0.get('churn_rate')}"
        )
    lines.extend(
        [
            "",
            "## 2. Limit up / limit down",
            "",
            f"**Verdict:** `{lim['verdict']}`",
            "",
        ]
    )
    for n in lim.get("verdict_notes") or []:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "- Limit prices: JPX tier proxy from previous close (not official kabu limit fields).",
            "- Scenarios compared: warning_only, exclude_limit_up_down, downgrade_near_limit, no_change.",
            "",
            "## 3. Session close",
            "",
            f"**Verdict:** `{close['verdict']}`",
            "",
        ]
    )
    for n in close.get("verdict_notes") or []:
        lines.append(f"- {n}")
    ref = close.get("policy_reference") or {}
    lines.extend(
        [
            "",
            f"- Reference force-close times: AM `{ref.get('morning_force_close')}` / PM `{ref.get('afternoon_force_close')}` (Phase 116).",
            "- Current pilot max hold ~3 min: **zero** positions open at 11:25/15:23.",
            "- Analysis uses counterfactual pnl (entry before boundary vs price at force-close).",
            "",
            "## Constraints (unchanged)",
            "",
            "- Production pilot YAML not modified",
            "- Entry / exit / quality / vol_liq / cap=3 unchanged",
            "- No symbol hard-excludes, no time-band excludes",
            "- Review / what-if only — no implementation in this phase",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 145 remaining issues review")
    parser.add_argument("--trade-date", action="append", default=None)
    parser.add_argument("--no-generate-features", action="store_true")
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.remaining_issues_review import discover_review_days, run_remaining_issues_review

    trade_dates = args.trade_date or discover_review_days(SMALL_PAPER)
    result = run_remaining_issues_review(
        repo_root=ROOT,
        reports_dir=REPORTS,
        small_paper_root=SMALL_PAPER,
        push_root=PUSH_ROOT,
        trade_dates=trade_dates,
        generate_features=not args.no_generate_features,
    )

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    out_json = REPORTS / "phase145_remaining_issues_review.json"
    am_csv = REPORTS / "phase145_am_pm_rescreening_review.csv"
    limit_csv = REPORTS / "phase145_limit_status_review.csv"
    close_csv = REPORTS / "phase145_session_close_review.csv"
    rec_md = REPORTS / "phase145_recommendations.md"

    report: dict[str, Any] = {
        "phase": 145,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "trade_dates": result["trade_dates"],
        "session_count": result["session_count"],
        "verdicts": {
            "am_pm_rescreening": result["am_pm_rescreening"]["verdict"],
            "limit_status": result["limit_status"]["verdict"],
            "session_close": result["session_close"]["verdict"],
        },
        "verdict_options": {
            "am_pm": {
                "A": "am_pm_rescreening_worthwhile",
                "B": "am_pm_rescreening_not_needed",
                "C": "need_intraday_liquidity_data",
            },
            "limit": {
                "A": "limit_exclusion_promising",
                "B": "warning_only_sufficient",
                "C": "need_limit_price_source",
                "D": "limit_signal_noisy",
            },
            "session_close": {
                "A": "session_close_reasonable",
                "B": "session_close_too_early",
                "C": "session_close_too_late",
                "D": "need_more_session_close_data",
            },
        },
        "am_pm_rescreening": {
            "verdict": result["am_pm_rescreening"]["verdict"],
            "verdict_notes": result["am_pm_rescreening"]["verdict_notes"],
            "daily": result["am_pm_rescreening"]["daily"],
        },
        "limit_status": {
            "verdict": result["limit_status"]["verdict"],
            "verdict_notes": result["limit_status"]["verdict_notes"],
            "scenarios": result["limit_status"]["scenarios"],
        },
        "session_close": {
            "verdict": result["session_close"]["verdict"],
            "verdict_notes": result["session_close"]["verdict_notes"],
            "scenarios": result["session_close"]["scenarios"],
            "policy_reference": result["session_close"]["policy_reference"],
        },
        "methodology": {
            "review_only": True,
            "no_pilot_yaml_change": True,
            "no_entry_exit_quality_change": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "am_pm_csv": _rel(am_csv),
            "limit_csv": _rel(limit_csv),
            "session_close_csv": _rel(close_csv),
            "recommendations_md": _rel(rec_md),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(am_csv, result["am_pm_rescreening"]["rows"])
    _write_csv(limit_csv, result["limit_status"]["rows"])
    _write_csv(close_csv, result["session_close"]["rows"])
    rec_md.write_text(_build_recommendations_md({**report, **result}), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdicts": report["verdicts"],
                "am_pm_rows": len(result["am_pm_rescreening"]["rows"]),
                "limit_rows": len(result["limit_status"]["rows"]),
                "session_close_rows": len(result["session_close"]["rows"]),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
