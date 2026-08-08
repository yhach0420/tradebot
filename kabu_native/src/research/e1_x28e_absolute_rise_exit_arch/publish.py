"""Publish X28E artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_md(path: Path, report: dict[str, Any]) -> None:
    e = report.get("entry_only") or {}
    rg = report.get("regime") or {}
    pb = report.get("pbv2") or {}
    ex = report.get("exit_architecture") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- recommended architecture: `{report.get('recommended_architecture')}`",
        "",
        "## ENTRY-only",
        f"- Specific: `{e.get('specific')}`",
        f"- Family: `{e.get('family')}`",
        "",
        "## Regime",
        f"- conclusion: `{rg.get('conclusion')}`",
        f"- selected: `{rg.get('selected_regime')}`",
        "",
        "## PBv2",
        f"- manifest SHA: `{pb.get('manifest_sha256')}`",
        f"- parity: `{pb.get('parity_status')}`",
        "",
        "## EXIT architecture",
        f"- `{json.dumps(ex, default=str)[:4000]}`",
        "",
        "## X29",
        f"- V2 status: `{report.get('x29_v2_status')}`",
        f"- V3 required: `{report.get('x29_v3_required')}`",
        f"- 20260810 opened: `{report.get('opened_20260810')}`",
        "",
        "## Safety",
        f"- submit/cancel/live: `{report.get('safety', {}).get('submit_cancel_live')}`",
        f"- tests: `{report.get('tests')}`",
        f"- A/B: `{report.get('ab_determinism')}`",
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
                json.dumps(r.get(h), default=str)[:32000] if isinstance(r.get(h), (dict, list)) else r.get(h)
                for h in hdr
            ])
    wb.save(path)


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_md(out / "report.md", report)
    write_xlsx(out / "audit.xlsx", sheets)
