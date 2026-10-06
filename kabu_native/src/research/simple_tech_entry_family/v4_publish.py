"""Write report.json / report.md / audit.xlsx only under v4_volume_quality_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V4_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v4_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Volume_Magnitude",
    "Volume_Persistence",
    "Volume_Direction",
    "Volume_Efficiency",
    "Quartiles",
    "Day_Stability",
    "Good6",
    "Bad23",
    "Failure_Taxonomy",
    "Mechanism",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V4_SPEC_SHA256",
    "V1_PARITY",
    "PRE_VOLUME_N",
    "EXECUTABLE_PRE_VOLUME_N",
    "VQ1_COVERAGE",
    "VQ1_SPEARMAN_180",
    "VQ1_SPEARMAN_300",
    "VQ2_AVAILABLE",
    "VQ2_SPEARMAN_180",
    "VQ2_SPEARMAN_300",
    "VQ3_COVERAGE",
    "VQ3_SPEARMAN_180",
    "VQ3_SPEARMAN_300",
    "VQ4_COVERAGE",
    "VQ4_SPEARMAN_180",
    "VQ4_SPEARMAN_300",
    "VQ1_POS_NEG_DAYS",
    "VQ2_POS_NEG_DAYS",
    "VQ3_POS_NEG_DAYS",
    "VQ4_POS_NEG_DAYS",
    "GOOD6_VQ1",
    "GOOD6_VQ2",
    "GOOD6_VQ3",
    "GOOD6_VQ4",
    "OTHER23_VQ1",
    "OTHER23_VQ2",
    "OTHER23_VQ3",
    "OTHER23_VQ4",
    "SUPPORTED_VOLUME_MECHANISM",
    "PRIMARY_DEFICIENCY",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V4_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V4_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V4_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V4_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V4_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V4_SPEC_SHA256: `{req.get('V4_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"SUPPORTED_VOLUME_MECHANISM: `{req.get('SUPPORTED_VOLUME_MECHANISM')}`",
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
        lines.extend(["", "## Questions (field semantics)", ""])
        lines.append(f"Q6: {qa.get('Q6')}")
        lines.append(f"Q6_AXIS: {qa.get('Q6_AXIS')} (not align_vq)")
        lines.append(f"Q5: {qa.get('Q5')}  # GOOD6-vs-OTHER23 contrast via align_vq")
        gvo = dict(report.get("good_vs_other") or {})
        lines.append(f"align_vq: {gvo.get('align_vq')}  # {gvo.get('align_vq_role')} ; is_supported_mechanism={gvo.get('align_vq_is_supported_mechanism')}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This run does not change ENTRY. No Volume threshold. No C14. No EXIT.",
            "- PRE_VOLUME = V1 TREND+PULLBACK+RCI+PRICE_ACTION (nested s4).",
            "- VQ2 uses existing e1_x14 volume_persistence_300s (10s active fraction).",
            "- 18 AM days are burned development data.",
            "- Field semantics: questions.Q6 is VQ2 D_MULTI_DAY and H_PRE_VOLUME on PRE_VOLUME, not good_vs_other.align_vq.",
            "- Field semantics: good_vs_other.align_vq is the first GOOD6-vs-OTHER23 higher axis (VQ1 first). It is not SUPPORTED_VOLUME_MECHANISM.",
            "- SUPPORTED_VOLUME_MECHANISM remains PERSISTENCE when VQ2 gates pass.",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
