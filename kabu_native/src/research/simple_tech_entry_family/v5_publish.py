"""Write report.json / report.md / audit.xlsx only under v5_reversal_quality_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V5_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v5_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Cross_Audit",
    "RQ1_Level",
    "RQ2_Delta",
    "RQ3_MultiBar",
    "RQ4_Recovery",
    "Quartiles",
    "Day_Stability",
    "Good6",
    "Other23",
    "Failure_Taxonomy",
    "Mechanism",
    "Cross_Pass",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V5_SPEC_SHA256",
    "V1_PARITY",
    "PRE_REVERSAL_N",
    "RCI_CROSS_PASS_N",
    "EXECUTABLE_PRE_REVERSAL_N",
    "RQ1_SPEARMAN_180",
    "RQ1_SPEARMAN_300",
    "RQ2_SPEARMAN_180",
    "RQ2_SPEARMAN_300",
    "RQ3_SPEARMAN_180",
    "RQ3_SPEARMAN_300",
    "RQ4_SPEARMAN_180",
    "RQ4_SPEARMAN_300",
    "RQ1_POS_NEG_DAYS",
    "RQ2_POS_NEG_DAYS",
    "RQ3_POS_NEG_DAYS",
    "RQ4_POS_NEG_DAYS",
    "RQ1_EX_BEST",
    "RQ2_EX_BEST",
    "RQ3_EX_BEST",
    "RQ4_EX_BEST",
    "RQ1_DROP_TOP_SYMBOL",
    "RQ2_DROP_TOP_SYMBOL",
    "RQ3_DROP_TOP_SYMBOL",
    "RQ4_DROP_TOP_SYMBOL",
    "GOOD6_RQ1",
    "GOOD6_RQ2",
    "GOOD6_RQ3",
    "GOOD6_RQ4",
    "OTHER23_RQ1",
    "OTHER23_RQ2",
    "OTHER23_RQ3",
    "OTHER23_RQ4",
    "SUPPORTED_REVERSAL_MECHANISM",
    "PRIMARY_DEFICIENCY",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V5_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V5_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V5_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V5_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V5_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V5_SPEC_SHA256: `{req.get('V5_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"SUPPORTED_REVERSAL_MECHANISM: `{req.get('SUPPORTED_REVERSAL_MECHANISM')}`",
        f"PRIMARY_DEFICIENCY: `{req.get('PRIMARY_DEFICIENCY')}`",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Required output",
        "",
    ]
    for k in REQUIRED_KEYS:
        lines.append(f"{k}: {req.get(k)}")
    qa = dict(report.get("questions") or {})
    if qa:
        lines.extend(["", "## Questions", ""])
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"):
            lines.append(f"{q}: {qa.get(q)}")
        lines.append(f"Q6_AXIS: {qa.get('Q6_AXIS')} (not align_rq)")
    gvo = dict(report.get("good_vs_other") or {})
    if gvo:
        lines.extend(
            [
                "",
                f"align_rq: {gvo.get('align_rq')}  # {gvo.get('align_rq_role')} ; is_supported_mechanism={gvo.get('align_rq_is_supported_mechanism')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This run does not change ENTRY. No RCI threshold. No C14. No EXIT.",
            "- PRE_REVERSAL = V1 TREND+PULLBACK (nested s2). Price Action/Volume/Board are not population filters.",
            "- Persistence was not added. Volume persistence remains a prior research finding only.",
            "- Taxonomy labels are descriptive current-state tags, not ENTRY rules.",
            "- 18 AM days are burned development data.",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
