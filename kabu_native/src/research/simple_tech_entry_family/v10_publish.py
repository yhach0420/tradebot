"""Write report.json / report.md / audit.xlsx only under v10_rci_board_role_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V10_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v8_publish import arm_row
from research.simple_tech_entry_family.v10_spec import ANALYSIS_ID, ARM_ORDER, STATE_ORDER

SHEET_ORDER = (
    "Precommit",
    "Arms",
    "States",
    "Pairwise",
    "TOD",
    "Concentration",
    "PQ3_Diagnostic",
    "Daily",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V10_SPEC_SHA256",
    "B2_PARITY",
    "RCI_INCREMENTAL_ROLE_SUPPORTED",
    "BOARD_INCREMENTAL_ROLE_SUPPORTED",
    "RCI_HARD_CONFIRM_HARMFUL",
    "BOARD_VETO_HARMFUL",
    "ENTRY_SIGNAL_EDGE_SUPPORTED",
    "SELECTED_STACK",
    "PRIMARY_INTERPRETATION",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V10_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V10_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V10_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V10_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V10_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    arms = dict(report.get("arms") or {})
    states = dict(report.get("states") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V10_SPEC_SHA256: `{req.get('V10_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Incremental roles (T3 is reference context)",
        "",
        f"RCI_INCREMENTAL_ROLE_SUPPORTED: `{req.get('RCI_INCREMENTAL_ROLE_SUPPORTED')}`",
        f"BOARD_INCREMENTAL_ROLE_SUPPORTED: `{req.get('BOARD_INCREMENTAL_ROLE_SUPPORTED')}`",
        f"RCI_HARD_CONFIRM_HARMFUL: `{req.get('RCI_HARD_CONFIRM_HARMFUL')}`",
        f"BOARD_VETO_HARMFUL: `{req.get('BOARD_VETO_HARMFUL')}`",
        f"ENTRY_SIGNAL_EDGE_SUPPORTED: `{req.get('ENTRY_SIGNAL_EDGE_SUPPORTED')}`",
        f"SELECTED_STACK: `{req.get('SELECTED_STACK')}`",
        f"PRIMARY_INTERPRETATION: {req.get('PRIMARY_INTERPRETATION')}",
        "",
        "## Arms (base = T3 AND Pullback, TF1, no PA, no Volume)",
        "",
    ]
    for aid in ARM_ORDER:
        a = arms.get(aid) or {}
        lines.append(
            f"### {aid}  N={a.get('SIGNAL_N')} exe={a.get('EXECUTABLE_SIGNAL_N')} "
            f"mean180/300={a.get('MARKOUT180_MEAN')}/{a.get('MARKOUT300_MEAN')} "
            f"median180/300={a.get('MARKOUT180_MEDIAN')}/{a.get('MARKOUT300_MEDIAN')} "
            f"day180={a.get('POSITIVE_DAY_N_180')}/{a.get('NEGATIVE_DAY_N_180')}"
        )
    lines.extend(["", "## RCI x Board states (same T3+Pullback base, diagnostic only)", ""])
    for sid in STATE_ORDER:
        a = states.get(sid) or {}
        lines.append(
            f"- {sid}: N={a.get('SIGNAL_N')} exe={a.get('EXECUTABLE_SIGNAL_N')} "
            f"mean180/300={a.get('MARKOUT180_MEAN')}/{a.get('MARKOUT300_MEAN')}"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Nested incremental only: B1 vs B0 = RCI, B2 vs B1 = Board. T3 is reference, CROSS independent necessity unconfirmed.",
            "- Do not reuse V8 A4 vs A5/A6 (those were Trend-off). PQ3 is diagnostic only. No PA/Volume/Persistence restore.",
            "- 18 AM days burned. TRUE_OOS=false. Not a Runtime candidate.",
            "",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
