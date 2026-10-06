"""Write report.json / report.md / audit.xlsx only under v11_signal_execution_cost_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V11_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v8_publish import arm_row
from research.simple_tech_entry_family.v11_spec import ANALYSIS_ID, MARKOUT_KINDS

SHEET_ORDER = (
    "Precommit",
    "Markouts",
    "Decomposition",
    "Robustness_Mid",
    "TOD",
    "Spread",
    "Daily",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V11_SPEC_SHA256",
    "B1_PARITY",
    "Q1_GROSS_MID_POSITIVE_60_180_300",
    "Q2_EXECUTION_COST_PRIMARY",
    "Q3_ENTRY_CROSS_ALREADY_NEGATIVE",
    "Q4_SIGNAL_ARCHITECTURE_STILL_INSUFFICIENT",
    "Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD",
    "GROSS_MID_EDGE_ROBUST",
    "ENTRY_SIGNAL_EDGE_SUPPORTED",
    "PRIMARY_INTERPRETATION",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V11_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V11_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V11_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V11_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V11_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    fam = dict(report.get("families") or {})
    dec = dict(report.get("decomposition") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V11_SPEC_SHA256: `{req.get('V11_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Questions",
        "",
        f"Q1_GROSS_MID_POSITIVE_60_180_300: `{req.get('Q1_GROSS_MID_POSITIVE_60_180_300')}`",
        f"Q2_EXECUTION_COST_PRIMARY: `{req.get('Q2_EXECUTION_COST_PRIMARY')}`",
        f"Q3_ENTRY_CROSS_ALREADY_NEGATIVE: `{req.get('Q3_ENTRY_CROSS_ALREADY_NEGATIVE')}`",
        f"Q4_SIGNAL_ARCHITECTURE_STILL_INSUFFICIENT: `{req.get('Q4_SIGNAL_ARCHITECTURE_STILL_INSUFFICIENT')}`",
        f"Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD: `{req.get('Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD')}`",
        f"GROSS_MID_EDGE_ROBUST: `{req.get('GROSS_MID_EDGE_ROBUST')}`",
        f"ENTRY_SIGNAL_EDGE_SUPPORTED: `{req.get('ENTRY_SIGNAL_EDGE_SUPPORTED')}`",
        f"PRIMARY_INTERPRETATION: {req.get('PRIMARY_INTERPRETATION')}",
        "",
        "## Three markouts (B1 executable, TF1)",
        "",
    ]
    for kind in MARKOUT_KINDS:
        a = fam.get(kind) or {}
        lines.append(
            f"### {kind}  exe={a.get('EXECUTABLE_SIGNAL_N')} "
            f"mean60/180/300={a.get('MARKOUT60_MEAN')}/{a.get('MARKOUT180_MEAN')}/{a.get('MARKOUT300_MEAN')} "
            f"median180/300={a.get('MARKOUT180_MEDIAN')}/{a.get('MARKOUT300_MEDIAN')} "
            f"day180={a.get('POSITIVE_DAY_N_180')}/{a.get('NEGATIVE_DAY_N_180')}"
        )
    lines.extend(["", "## Cost decomposition (actual quotes, bps)", ""])
    for h in (60, 180, 300):
        d = dec.get(str(h)) or dec.get(h) or {}
        lines.append(
            f"- {h}s: gross={d.get('GROSS_DIRECTIONAL_EDGE')} "
            f"entry_half={d.get('ENTRY_HALF_SPREAD_BURDEN')} "
            f"exit_half={d.get('FUTURE_EXIT_HALF_SPREAD_BURDEN')} "
            f"total_deg={d.get('TOTAL_EXECUTABLE_DEGRADATION')}"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Frozen B1: T3 AND V1 Pullback AND RCI9 -80 cross. No Board hard veto. No PA/Volume/Persistence/PQ3.",
            "- R1B0 is not a gate. Inverse Board rule forbidden. TOD and spread are diagnostic only.",
            "- 18 AM days burned. TRUE_OOS=false. Not a Runtime candidate.",
            "",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
