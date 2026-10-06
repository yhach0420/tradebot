"""Write report.json / report.md / audit.xlsx only under v26_joint_coverage_technical_exit_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V26_OUT
from research.simple_tech_redesign.v26_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Coverage",
    "PathTypes",
    "ExitPrimitives",
    "Fills",
    "Daily",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "V25_BASELINE_PARITY",
    "SIGNAL_N",
    "EXECUTION_EVALUABLE_N",
    "CORE_E4_FILL_N",
    "ASK_FALLBACK_ELIGIBLE_N",
    "ASK_FALLBACK_FILL_N",
    "TOTAL_FILL_N",
    "ADDED_FILL_N",
    "FILLS_PER_DAY",
    "CORE_PATH_SUMMARY",
    "ADDED_PATH_SUMMARY",
    "1M_EXIT_PRIMITIVES",
    "3M_EXIT_PRIMITIVES",
    "5M_EXIT_PRIMITIVES",
    "GOOD_CONTINUATION_N",
    "EARLY_FAILURE_N",
    "DIP_THEN_RECOVERY_N",
    "PROFIT_THEN_FAILURE_N",
    "SUPPORTED_EXIT_PRIMITIVES",
    "PRIMARY_EXIT_PRIMITIVE",
    "CORE_WINNER_PRESERVATION",
    "JOINT_COVERAGE_EXIT_MECHANISM_FOUND",
    "ENTRY_SIGNAL_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V26_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V26_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V26_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V26_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V26_OUT / "audit.xlsx")


def _brief_path(p: dict[str, Any] | None) -> str:
    p = dict(p or {})
    return (
        f"N={p.get('N')} days={p.get('DISTINCT_DAYS')} symbols={p.get('DISTINCT_SYMBOLS')} "
        f"types={p.get('PATH_TYPE_N')} MFE_med={p.get('MFE_MEDIAN')} MAE_med={p.get('MAE_MEDIAN')} "
        f"MFE_pos_frac={p.get('MFE_POS_FRAC')}"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "V25 baseline frozen. ENTRY/Trend/Pullback/RCI/E4 unchanged. Coverage architecture = `E4_THEN_ASK_CROSS_W5`. "
        "No chase, no wait extension, no time-stop EXIT, no FIXED180, no PnL accept gate.",
        "",
        f"V25_BASELINE_PARITY = `{req.get('V25_BASELINE_PARITY')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}`",
        f"EXECUTION_EVALUABLE_N = `{req.get('EXECUTION_EVALUABLE_N')}`",
        f"CORE_E4_FILL_N = `{req.get('CORE_E4_FILL_N')}`",
        f"ASK_FALLBACK_ELIGIBLE_N = `{req.get('ASK_FALLBACK_ELIGIBLE_N')}`",
        f"ASK_FALLBACK_FILL_N = `{req.get('ASK_FALLBACK_FILL_N')}`",
        f"TOTAL_FILL_N = `{req.get('TOTAL_FILL_N')}`",
        f"ADDED_FILL_N = `{req.get('ADDED_FILL_N')}`",
        f"FILLS_PER_DAY = `{req.get('FILLS_PER_DAY')}`",
        "",
        "## Path summaries",
        f"- CORE: `{_brief_path(dict(req.get('CORE_PATH_SUMMARY') or {}))}`",
        f"- ADDED: `{_brief_path(dict(req.get('ADDED_PATH_SUMMARY') or {}))}`",
        f"- GOOD_CONTINUATION_N=`{req.get('GOOD_CONTINUATION_N')}` EARLY_FAILURE_N=`{req.get('EARLY_FAILURE_N')}` "
        f"DIP_THEN_RECOVERY_N=`{req.get('DIP_THEN_RECOVERY_N')}` PROFIT_THEN_FAILURE_N=`{req.get('PROFIT_THEN_FAILURE_N')}`",
        "",
        "## EXIT primitives",
        f"- SUPPORTED_EXIT_PRIMITIVES = `{req.get('SUPPORTED_EXIT_PRIMITIVES')}`",
        f"- PRIMARY_EXIT_PRIMITIVE = `{req.get('PRIMARY_EXIT_PRIMITIVE')}`",
        f"- CORE_WINNER_PRESERVATION = `{req.get('CORE_WINNER_PRESERVATION')}`",
        f"- JOINT_COVERAGE_EXIT_MECHANISM_FOUND = `{req.get('JOINT_COVERAGE_EXIT_MECHANISM_FOUND')}`",
        "",
        "## Locks",
        f"- ENTRY_SIGNAL_CHANGED=`{req.get('ENTRY_SIGNAL_CHANGED')}`",
        f"- TRUE_OOS=`{req.get('TRUE_OOS')}`",
        f"- NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
