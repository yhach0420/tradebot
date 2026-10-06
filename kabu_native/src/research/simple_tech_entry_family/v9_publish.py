"""Write report.json / report.md / audit.xlsx only under v9_trend_context_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V9_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v8_publish import arm_row
from research.simple_tech_entry_family.v9_spec import ANALYSIS_ID, ARM_ORDER, STATE_ORDER

SHEET_ORDER = (
    "Precommit",
    "Arms",
    "States",
    "Pairwise",
    "TOD",
    "Concentration",
    "Daily",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V9_SPEC_SHA256",
    "T3_PARITY",
    "CROSS_ROLE_SUPPORTED",
    "SLOPE_ROLE_SUPPORTED",
    "JOINT_TREND_ROLE_SUPPORTED",
    "TREND_INTERACTION_SUPPORTED",
    "ENTRY_SIGNAL_EDGE_SUPPORTED",
    "PRIMARY_TREND_INTERPRETATION",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V9_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V9_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V9_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V9_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V9_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    arms = dict(report.get("arms") or {})
    states = dict(report.get("states") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V9_SPEC_SHA256: `{req.get('V9_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Trend component support",
        "",
        f"CROSS_ROLE_SUPPORTED: `{req.get('CROSS_ROLE_SUPPORTED')}`",
        f"SLOPE_ROLE_SUPPORTED: `{req.get('SLOPE_ROLE_SUPPORTED')}`",
        f"JOINT_TREND_ROLE_SUPPORTED: `{req.get('JOINT_TREND_ROLE_SUPPORTED')}`",
        f"TREND_INTERACTION_SUPPORTED: `{req.get('TREND_INTERACTION_SUPPORTED')}`",
        f"ENTRY_SIGNAL_EDGE_SUPPORTED: `{req.get('ENTRY_SIGNAL_EDGE_SUPPORTED')}`",
        f"PRIMARY_TREND_INTERPRETATION: {req.get('PRIMARY_TREND_INTERPRETATION')}",
        "",
        "## Arms (base = Pullback AND RCI AND Board, TF1)",
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
    lines.extend(["", "## 2x2 states (same base)", ""])
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
            "- Trend decomposition only. No EMA period/slope-threshold change. No PA/Volume return.",
            "- RCI and Board stay frozen. Time-of-day is diagnostic only.",
            "- 18 AM days burned. TRUE_OOS=false. Not a Runtime candidate.",
            "",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
