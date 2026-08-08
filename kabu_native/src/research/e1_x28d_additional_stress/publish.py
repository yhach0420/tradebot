"""Publish X28D artifacts (no mass CSV)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    s = report.get("specific_cohort") or {}
    f = report.get("family_cohort") or {}
    stop = report.get("stop_risk_view") or {}
    dep = report.get("dependency") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- role: `{report.get('role')}`",
        f"- stress days: `{report.get('stress_days')}`",
        "",
        "## Data sufficiency",
        f"- 20260805: `{report.get('data_sufficiency', {}).get('20260805')}`",
        f"- 20260806: `{report.get('data_sufficiency', {}).get('20260806')}`",
        f"- 20260807: `{report.get('data_sufficiency', {}).get('20260807')}`",
        "",
        "## Old X29 precommit",
        f"- status: `{report.get('old_x29_status')}`",
        f"- sha: `{report.get('old_x29_precommit_sha')}`",
        "",
        "## Specific 49",
        f"- stress status: `{s.get('stress_status')}`",
        f"- avg/median return: `{s.get('avg_return_bps')}` / `{s.get('median_return_bps')}`",
        f"- PF: `{s.get('profit_factor')}`",
        f"- ENTRY delta (cand-med): `{s.get('median_entry_delta')}`",
        f"- personalization delta (cand-med): `{s.get('median_personalization_delta')}`",
        f"- same/reversed/insufficient: `{s.get('direction_counts')}`",
        "",
        "## Family 118",
        f"- stress status: `{f.get('stress_status')}`",
        f"- family return avg/med: `{f.get('avg_return_bps')}` / `{f.get('median_return_bps')}`",
        f"- PF: `{f.get('profit_factor')}`",
        f"- ENTRY delta: `{f.get('median_entry_delta')}`",
        f"- family-minus-specific: `{f.get('median_family_minus_specific')}`",
        "",
        "## Stop risk",
        f"- NORMAL: `{stop.get('NORMAL_STOP')}`",
        f"- WIDE: `{stop.get('WIDE_STOP')}`",
        f"- VERY_WIDE: `{stop.get('VERY_WIDE_STOP')}`",
        f"- alert: `{report.get('wide_stop_alert')}`",
        "",
        "## Dependency",
        f"- max day: `{dep.get('max_day_contribution_share')}`",
        f"- max symbol: `{dep.get('max_symbol_contribution_share')}`",
        f"- 285A/2354/4052: `{dep.get('focus_symbols')}`",
        "",
        "## Program decision",
        f"- `{report.get('program_decision')}`",
        f"- X29 V2 required: `{report.get('x29_v2_required')}`",
        f"- X29 V2 sha: `{report.get('x29_v2_precommit_sha')}`",
        "",
        "## Safety",
        f"- submit/cancel/live: `{report.get('safety', {}).get('submit_cancel_live')}`",
        f"- A/B: `{report.get('ab_determinism')}`",
        f"- tests: `{report.get('tests', {}).get('passed')}/{report.get('tests', {}).get('total')}`",
        "",
        "## Note",
        "- 20260810 market data NOT opened",
        "- prospective observer NOT started",
        "- no prospective evidence consumed",
        "- no candidate selection change / no parameter retune",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_audit_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
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
            ws.append([_cell(r.get(h)) for h in hdr])
    wb.save(path)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)[:32000]
    if isinstance(v, float) and (v != v):
        return None
    return v


def publish(out_dir: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )
    write_report_md(out_dir / "report.md", report)
    write_audit_xlsx(out_dir / "audit.xlsx", sheets)
