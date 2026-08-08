"""Publish X39D artifacts."""
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
        f"- V1R: `{report.get('v1r_sha')}`",
        f"- model: `{report.get('model_artifact_sha')}`",
        f"- universe binding: `{report.get('universe_binding_sha')}`",
        f"- precommit U1: `{report.get('prospective_precommit_sha')}`",
        f"- activation: `{report.get('activation_manifest_sha')}`",
        f"- Primary: `{report.get('primary')}`",
        f"- PBV2: `{report.get('pbv2')}`",
        f"- 1M: `{report.get('capital_1m')}`",
        f"- 20260810: `{report.get('opened_20260810')}`",
        f"- observer: `{report.get('prospective_observer')}`",
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
