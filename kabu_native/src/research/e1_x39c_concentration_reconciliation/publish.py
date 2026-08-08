"""Publish X39C artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_md(path: Path, report: dict[str, Any]) -> None:
    conc = report.get("concentration") or {}
    d1 = report.get("d1") or {}
    d2 = report.get("d2") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- top symbol: `{conc.get('top_contributor')}` share=`{conc.get('max_symbol_contrib_share')}`",
        f"- D1 pnl: `{d1.get('remaining_total_pnl_yen')}` pf=`{d1.get('pf')}` pos=`{d1.get('positive_days')}`",
        f"- D2 pnl: `{d2.get('total_pnl_yen')}` pf=`{d2.get('pf')}` pos=`{d2.get('positive_days')}`",
        f"- Universe binding: `{report.get('universe_binding')}`",
        f"- 20260810: `{report.get('opened_20260810')}`",
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
