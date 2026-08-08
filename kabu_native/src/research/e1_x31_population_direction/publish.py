"""Publish X31 artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- population case: `{report.get('population_case')}`",
        f"- Phase B eligible: `{report.get('short_phase_b_eligible')}`",
        "",
        "## Candidate vs Controls",
        f"- candidate ret300/600: `{report.get('candidate_ret300')}` / `{report.get('candidate_ret600')}`",
        f"- same-symbol control: `{report.get('same_symbol_control_ret300')}` / `{report.get('same_symbol_control_ret600')}`",
        f"- market/time control: `{report.get('market_time_control_ret300')}` / `{report.get('market_time_control_ret600')}`",
        f"- candidate-control delta: `{report.get('candidate_minus_control_300')}` / `{report.get('candidate_minus_control_600')}`",
        "",
        "## Robustness",
        f"- negative support days: `{report.get('negative_support_days')}`",
        f"- LOSO: `{report.get('loso')}`",
        f"- time-of-day: `{report.get('time_of_day_tag')}`",
        f"- late-chase: `{report.get('late_chase_tag')}`",
        "",
        "## SHORT",
        f"- baseline: `{report.get('short_baseline')}`",
        f"- nested CV: `{report.get('short_nested_cv')}`",
        f"- SHORT signal found: `{report.get('short_signal_found')}`",
        "",
        f"- 20260810 opened: `{report.get('opened_20260810')}`",
        f"- tests: `{report.get('tests')}`",
        f"- A/B: `{report.get('ab_determinism')}`",
        f"- submit/cancel/live: `{report.get('safety', {}).get('submit_cancel_live')}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(name[:31])
        if first:
            ws.title = name[:31]
            first = False
        if not rows:
            ws.append(["empty"])
            continue
        hdr = list(rows[0].keys())
        ws.append(hdr)
        for r in rows:
            ws.append([
                json.dumps(r.get(h), default=str)[:32000]
                if isinstance(r.get(h), (dict, list)) else r.get(h)
                for h in hdr
            ])
    wb.save(path)


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_md(out / "report.md", report)
    write_xlsx(out / "audit.xlsx", sheets)
