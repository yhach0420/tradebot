"""Publish capital sweep artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)[:32000]
    return v


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- source_identity: `{report.get('source_identity', {}).get('pass')}`",
        f"- capital_levels: `{report.get('capital_levels_n')}`",
        "",
        "## Primary summary",
        "",
    ]
    rows = report.get("summary_table") or []
    if rows:
        headers = list(rows[0].keys())
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for r in rows:
            lines.append("| " + " | ".join(_fmt(r.get(h)) for h in headers) + " |")
    lines += ["", "## Pareto comparison", ""]
    pc = report.get("pareto") or {}
    for k, v in pc.items():
        if k == "incremental":
            continue
        lines.append(f"- {k}: `{v}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.1f}"
        return f"{v:.6g}"
    return str(v)


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
            ws.append([_cell(r.get(h)) for h in hdr])
    wb.save(path)


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_md(out / "report.md", report)
    write_xlsx(out / "audit.xlsx", sheets)
