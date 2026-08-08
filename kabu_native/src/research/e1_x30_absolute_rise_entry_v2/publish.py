"""Publish X30 artifacts."""
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
        f"- population: `{report.get('population_n')}`",
        f"- label prevalence: `{report.get('label_prevalence')}`",
        "",
        "## Nested CV",
        f"- catalog: `{report.get('candidate_semantic_families_generated')}`",
        f"- outer-pass: `{report.get('candidate_families_outer_pass')}`",
        f"- LODO-pass: `{report.get('candidate_families_lodo_pass')}`",
        "",
        "## Best ENTRY families",
        f"```json\n{json.dumps(report.get('best_entry_families'), indent=2, default=str)[:6000]}\n```",
        "",
        "## Old comparison",
        f"- old49: `{report.get('old49_comparison')}`",
        f"- old118: `{report.get('old118_comparison')}`",
        "",
        "## Manifest / EXIT / Prospective",
        f"- ENTRY_V2 manifest created: `{report.get('entry_v2_manifest_created')}`",
        f"- manifest SHA: `{report.get('manifest_sha')}`",
        f"- EXIT research allowed: `{report.get('exit_research_allowed')}`",
        f"- 20260810 opened: `{report.get('opened_20260810')}`",
        "",
        "## Safety",
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
