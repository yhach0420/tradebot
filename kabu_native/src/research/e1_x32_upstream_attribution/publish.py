"""Publish X32 artifacts."""
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
        f"- primary culprit: `{report.get('primary_culprit_transition')}`",
        f"- recommended next: `{report.get('recommended_next_phase')}`",
        "",
        "## Canonical funnel",
        f"```json\n{json.dumps(report.get('canonical_funnel'), indent=2, default=str)[:5000]}\n```",
        "",
        "## Stage returns",
        f"```json\n{json.dumps(report.get('stage_summaries'), indent=2, default=str)[:4000]}\n```",
        "",
        "## Transitions",
        f"```json\n{json.dumps(report.get('transitions_summary'), indent=2, default=str)[:4000]}\n```",
        "",
        f"- symbol-selection Δ300/600: `{report.get('symbol_selection_delta_300')}` / `{report.get('symbol_selection_delta_600')}`",
        f"- timing Δ300/600: `{report.get('timing_delta_300')}` / `{report.get('timing_delta_600')}`",
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
