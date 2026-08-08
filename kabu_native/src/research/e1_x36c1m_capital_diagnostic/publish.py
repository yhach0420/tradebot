"""Publish capital diagnostic artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_md(path: Path, report: dict[str, Any]) -> None:
    p = report.get("primary_1m") or {}
    e = p.get("economics") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        "",
        "## 100万円結果",
        f"- starting: `{p.get('initial_cash')}`",
        f"- ending: `{p.get('ending_cash')}`",
        f"- PnL: `{p.get('total_pnl_yen_cash')}`",
        f"- return%: `{p.get('total_return_pct')}`",
        f"- PF: `{e.get('pf')}`",
        f"- positive days: `{e.get('positive_days')}`",
        f"- fills: `{p.get('accepted_fills')}`",
        f"- capital_blocked: `{p.get('capital_blocked')}`",
        f"- max drawdown: `{p.get('max_drawdown_yen')}` / `{p.get('max_drawdown_pct')}`%",
        "",
        f"- vs X36: `{report.get('comparison')}`",
        f"- 285A: `{report.get('symbol_285a_1m')}`",
        f"- sensitivity: `{report.get('sensitivity_summary')}`",
        f"- 20260810 opened: `{report.get('opened_20260810')}`",
        f"- V1R unchanged: `{report.get('v1r_unchanged')}`",
        f"- precommit unchanged: `{report.get('precommit_unchanged')}`",
        f"- tests: `{report.get('tests')}`",
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
