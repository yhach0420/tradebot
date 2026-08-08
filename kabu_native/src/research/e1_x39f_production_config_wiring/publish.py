"""Publish X39F artifacts."""
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
        f"- YAML SHA match: `{report.get('yaml', {}).get('pin_match')}`",
        f"- dangerous_conflicts: `{report.get('dangerous_conflicts')}`",
        f"- demo_push: `{report.get('demo_push', {}).get('pass')}`",
        f"- submit/cancel/live: `{report.get('submit_cancel_live')}`",
        f"- 20260810: `{report.get('opened_20260810')}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(str(name)[:31])
        if first:
            ws.title = str(name)[:31]
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


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]], effective: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_md(out / "report.md", report)
    write_xlsx(out / "audit.xlsx", sheets)
    (out / "V1R_EFFECTIVE_RUNTIME_CONFIG_20260810.json").write_text(
        json.dumps(effective, indent=2, default=str), encoding="utf-8"
    )
