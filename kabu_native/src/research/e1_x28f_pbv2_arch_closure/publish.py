"""Publish X28F artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]], mapping: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out / "pbv2_exit_reason_mapping_v1.json").write_text(
        json.dumps(mapping, indent=2, default=str), encoding="utf-8",
    )
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- PBv2 parity: `{report.get('pbv2_parity', {}).get('status')}`",
        f"- selected: `{report.get('selected_architecture')}`",
        f"- X29 V2: `{report.get('x29_v2_status')}` V3 required: `{report.get('x29_v3_required')}`",
        f"- 20260810 opened: `{report.get('opened_20260810')}`",
        f"- tests: `{report.get('tests')}`",
        f"- submit/cancel/live: `{(report.get('safety') or {}).get('submit_cancel_live')}`",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
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
                json.dumps(r.get(h), default=str)[:32000] if isinstance(r.get(h), (dict, list)) else r.get(h)
                for h in hdr
            ])
    wb.save(out / "audit.xlsx")
