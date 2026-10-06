"""Write report.json / report.md / audit.xlsx only under v8_architecture_role_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V8_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v8_spec import ANALYSIS_ID, ARM_ORDER

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Arms",
    "Pairwise",
    "A4_Diagnostics",
    "Daily",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V8_SPEC_SHA256",
    "V1_PARITY",
    "PRICE_ACTION_HARD_ROLE_SUPPORTED",
    "VOLUME_HARD_ROLE_SUPPORTED",
    "TREND_HARD_ROLE_SUPPORTED",
    "RCI_CONFIRM_ROLE_SUPPORTED",
    "BOARD_VETO_ROLE_SUPPORTED",
    "CORE_ENTRY_EDGE_SUPPORTED",
    "PRIMARY_ARCHITECTURE_DEFICIENCY",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)

ARM_PUB_KEYS = (
    "ARM_ID",
    "SIGNAL_N",
    "EXECUTABLE_SIGNAL_N",
    "MARKOUT60_MEAN",
    "MARKOUT60_MEDIAN",
    "MARKOUT60_POS_RATE",
    "MARKOUT180_MEAN",
    "MARKOUT180_MEDIAN",
    "MARKOUT180_POS_RATE",
    "MARKOUT300_MEAN",
    "MARKOUT300_MEDIAN",
    "MARKOUT300_POS_RATE",
    "MFE_MEAN",
    "MFE_MEDIAN",
    "MAE_MEAN",
    "MAE_MEDIAN",
    "COST_RECOVERY_300",
    "POSITIVE_DAY_N_180",
    "NEGATIVE_DAY_N_180",
    "POSITIVE_DAY_N_300",
    "NEGATIVE_DAY_N_300",
    "EX_BEST_180",
    "EX_BEST_300",
    "EX_TOP3_180",
    "EX_TOP3_300",
    "DROP_TOP_SYMBOL_180",
    "DROP_TOP_SYMBOL_300",
)


def arm_row(arm: dict[str, Any]) -> dict[str, Any]:
    return {k: arm.get(k) for k in ARM_PUB_KEYS}


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V8_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V8_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V8_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V8_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V8_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    arms = dict(report.get("arms") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V8_SPEC_SHA256: `{req.get('V8_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Role decisions",
        "",
        f"PRICE_ACTION_HARD_ROLE_SUPPORTED: `{req.get('PRICE_ACTION_HARD_ROLE_SUPPORTED')}`",
        f"VOLUME_HARD_ROLE_SUPPORTED: `{req.get('VOLUME_HARD_ROLE_SUPPORTED')}`",
        f"TREND_HARD_ROLE_SUPPORTED: `{req.get('TREND_HARD_ROLE_SUPPORTED')}`",
        f"RCI_CONFIRM_ROLE_SUPPORTED: `{req.get('RCI_CONFIRM_ROLE_SUPPORTED')}`",
        f"BOARD_VETO_ROLE_SUPPORTED: `{req.get('BOARD_VETO_ROLE_SUPPORTED')}`",
        f"CORE_ENTRY_EDGE_SUPPORTED: `{req.get('CORE_ENTRY_EDGE_SUPPORTED')}`",
        f"PRIMARY_ARCHITECTURE_DEFICIENCY: `{req.get('PRIMARY_ARCHITECTURE_DEFICIENCY')}`",
        "",
        "## Arms (Ask->Bid 60/180/300, TF1, exit-neutral)",
        "",
    ]
    for aid in ARM_ORDER:
        a = arms.get(aid) or {}
        lines.append(
            f"### {aid}",
        )
        lines.append(
            f"SIGNAL_N={a.get('SIGNAL_N')} EXECUTABLE_SIGNAL_N={a.get('EXECUTABLE_SIGNAL_N')} "
            f"mean60/180/300={a.get('MARKOUT60_MEAN')}/{a.get('MARKOUT180_MEAN')}/{a.get('MARKOUT300_MEAN')} "
            f"median180/300={a.get('MARKOUT180_MEDIAN')}/{a.get('MARKOUT300_MEDIAN')} "
            f"pos180={a.get('MARKOUT180_POS_RATE')} day180={a.get('POSITIVE_DAY_N_180')}/{a.get('NEGATIVE_DAY_N_180')} "
            f"ex-best180/300={a.get('EX_BEST_180')}/{a.get('EX_BEST_300')} "
            f"cost300={a.get('COST_RECOVERY_300')}"
        )
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Hard-gate ablation only. No EMA/BB/RCI/PA/Volume/Board definition change.",
            "- Pullback was not removed. No mixed TF. No extra arms. No PQ3/Persistence gate.",
            "- CORE_ENTRY_EDGE is absolute, not relative improvement vs A0.",
            "- 18 AM days are burned development. TRUE_OOS=false. Not a Runtime candidate.",
            "",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
