"""Write report.json / report.md / audit.xlsx only under v22_sizing_execution_capacity_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_strategy.isolation import V22_OUT
from research.simple_tech_strategy.v22_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Capability",
    "EntryDist",
    "ExitDist",
    "RoundtripDist",
    "Counts",
    "Eligible",
    "Fills",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STRATEGY_STACK_PARITY",
    "E4_100SHARE_FILL_PARITY",
    "ENTRY_SIZE_REPLAY_CAPABILITY",
    "EXIT_SIZE_REPLAY_CAPABILITY",
    "ENTRY_CAPACITY_DISTRIBUTION",
    "EXIT_CAPACITY_DISTRIBUTION",
    "ROUNDTRIP_CAPACITY_DISTRIBUTION",
    "CAPACITY_EQ_100_N",
    "CAPACITY_GE_200_N",
    "CAPACITY_GE_300_N",
    "CAPACITY_GE_500_N",
    "CAPACITY_GE_1000_N",
    "NORMALIZED_1M_EXECUTION_FEASIBLE_N",
    "POSITION_SIZING_SPEC_FROZEN",
    "TRUE_OOS",
    "FORWARD_OOS_ELIGIBLE",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V22_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V22_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V22_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V22_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        rows = sheets.get(name) or [{"empty": True}]
        if first:
            ws = wb.active
            ws.title = name[:31]
            first = False
        else:
            ws = wb.create_sheet(name[:31])
        _sheet(ws, rows)
    wb.save(V22_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    ed = dict(req.get("ENTRY_CAPACITY_DISTRIBUTION") or {})
    xd = dict(req.get("EXIT_CAPACITY_DISTRIBUTION") or {})
    rd = dict(req.get("ROUNDTRIP_CAPACITY_DISTRIBUTION") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            f"CASE: `{req.get('CASE')}`",
            "",
            f"STRATEGY_STACK_PARITY: `{req.get('STRATEGY_STACK_PARITY')}`",
            f"E4_100SHARE_FILL_PARITY: `{req.get('E4_100SHARE_FILL_PARITY')}`",
            f"ENTRY_SIZE_REPLAY_CAPABILITY: `{req.get('ENTRY_SIZE_REPLAY_CAPABILITY')}`",
            f"EXIT_SIZE_REPLAY_CAPABILITY: `{req.get('EXIT_SIZE_REPLAY_CAPABILITY')}`",
            "",
            "ENTRY_CAPACITY_DISTRIBUTION:",
            f"- min/p25/median/p75/max: `{ed.get('min')}` / `{ed.get('p25')}` / `{ed.get('median')}` / `{ed.get('p75')}` / `{ed.get('max')}`",
            "EXIT_CAPACITY_DISTRIBUTION:",
            f"- min/p25/median/p75/max: `{xd.get('min')}` / `{xd.get('p25')}` / `{xd.get('median')}` / `{xd.get('p75')}` / `{xd.get('max')}`",
            "ROUNDTRIP_CAPACITY_DISTRIBUTION:",
            f"- min/p25/median/p75/max: `{rd.get('min')}` / `{rd.get('p25')}` / `{rd.get('median')}` / `{rd.get('p75')}` / `{rd.get('max')}`",
            "",
            f"CAPACITY_EQ_100_N: `{req.get('CAPACITY_EQ_100_N')}`",
            f"CAPACITY_GE_200_N: `{req.get('CAPACITY_GE_200_N')}`",
            f"CAPACITY_GE_300_N: `{req.get('CAPACITY_GE_300_N')}`",
            f"CAPACITY_GE_500_N: `{req.get('CAPACITY_GE_500_N')}`",
            f"CAPACITY_GE_1000_N: `{req.get('CAPACITY_GE_1000_N')}`",
            f"NORMALIZED_1M_EXECUTION_FEASIBLE_N: `{req.get('NORMALIZED_1M_EXECUTION_FEASIBLE_N')}` (diagnostic only)",
            "",
            f"POSITION_SIZING_SPEC_FROZEN: `{req.get('POSITION_SIZING_SPEC_FROZEN')}`",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  FORWARD_OOS_ELIGIBLE: `{req.get('FORWARD_OOS_ELIGIBLE')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
