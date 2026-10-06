"""Write report.json / report.md / audit.xlsx only under v6_trend_pullback_stage_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V6_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v6_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Trend_Stage",
    "Pullback_Stage",
    "TQ1",
    "TQ2",
    "TQ3",
    "TQ4",
    "PQ1",
    "PQ2",
    "PQ3",
    "PQ4",
    "Quartiles",
    "Day_Stability",
    "Good6",
    "Other23",
    "Mechanism",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V6_SPEC_SHA256",
    "V1_PARITY",
    "TREND_PASS_N",
    "TREND_FAIL_N",
    "TREND_PASS_MARKOUT_180",
    "TREND_PASS_MARKOUT_300",
    "TREND_FAIL_MARKOUT_180",
    "TREND_FAIL_MARKOUT_300",
    "PULLBACK_PASS_N",
    "PULLBACK_FAIL_N",
    "PULLBACK_PASS_MARKOUT_180",
    "PULLBACK_PASS_MARKOUT_300",
    "PULLBACK_FAIL_MARKOUT_180",
    "PULLBACK_FAIL_MARKOUT_300",
    "TQ1_SPEARMAN_180",
    "TQ1_SPEARMAN_300",
    "TQ2_SPEARMAN_180",
    "TQ2_SPEARMAN_300",
    "TQ3_SPEARMAN_180",
    "TQ3_SPEARMAN_300",
    "TQ4_SPEARMAN_180",
    "TQ4_SPEARMAN_300",
    "TQ1_POS_NEG_DAYS",
    "TQ2_POS_NEG_DAYS",
    "TQ3_POS_NEG_DAYS",
    "TQ4_POS_NEG_DAYS",
    "TQ1_EX_BEST",
    "TQ2_EX_BEST",
    "TQ3_EX_BEST",
    "TQ4_EX_BEST",
    "TQ1_DROP_TOP_SYMBOL",
    "TQ2_DROP_TOP_SYMBOL",
    "TQ3_DROP_TOP_SYMBOL",
    "TQ4_DROP_TOP_SYMBOL",
    "PQ1_SPEARMAN_180",
    "PQ1_SPEARMAN_300",
    "PQ2_SPEARMAN_180",
    "PQ2_SPEARMAN_300",
    "PQ3_SPEARMAN_180",
    "PQ3_SPEARMAN_300",
    "PQ4_SPEARMAN_180",
    "PQ4_SPEARMAN_300",
    "PQ1_POS_NEG_DAYS",
    "PQ2_POS_NEG_DAYS",
    "PQ3_POS_NEG_DAYS",
    "PQ4_POS_NEG_DAYS",
    "PQ1_EX_BEST",
    "PQ2_EX_BEST",
    "PQ3_EX_BEST",
    "PQ4_EX_BEST",
    "PQ1_DROP_TOP_SYMBOL",
    "PQ2_DROP_TOP_SYMBOL",
    "PQ3_DROP_TOP_SYMBOL",
    "PQ4_DROP_TOP_SYMBOL",
    "SUPPORTED_TREND_MECHANISM",
    "SUPPORTED_PULLBACK_MECHANISM",
    "NEXT_COMPONENT",
    "PRIMARY_DEFICIENCY",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V6_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V6_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V6_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V6_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V6_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V6_SPEC_SHA256: `{req.get('V6_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"NEXT_COMPONENT: `{req.get('NEXT_COMPONENT')}`",
        f"SUPPORTED_TREND_MECHANISM: `{req.get('SUPPORTED_TREND_MECHANISM')}`",
        f"SUPPORTED_PULLBACK_MECHANISM: `{req.get('SUPPORTED_PULLBACK_MECHANISM')}`",
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
        lines.append(f"Q6_ALIGN_TQ: {qa.get('Q6_ALIGN_TQ')}")
        lines.append(f"Q6_ALIGN_PQ: {qa.get('Q6_ALIGN_PQ')}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This run does not change ENTRY. No EMA 9/21 retune. No BB20/2sigma retune. No C14. No EXIT.",
            "- PRE_TREND = all warmup-valid evaluable V1 nested s0 rows. PRE_PULLBACK = TREND_PASS only.",
            "- Markout means are Ask→Bid on the executable subset. Headcounts are full nested N.",
            "- TQ2/TQ3 are signed 3-bar slope bps, not absolute magnitude.",
            "- PQ1/PQ2/PQ4 intended direction is lower-better. PQ3 is higher-better.",
            "- GOOD6 is diagnostic only (n=6 post-hoc subset) and is not a mechanism gate.",
            "- Persistence was not added. RCI was not changed.",
            "- 18 AM days are burned development data. TRUE_OOS=false.",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
