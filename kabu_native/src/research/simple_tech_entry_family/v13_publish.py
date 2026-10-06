"""Write report.json / report.md / audit.xlsx only under v13_entry_execution_structure_verification/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V13_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v13_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "V12_Preserved",
    "Identity",
    "Replay_Identity",
    "E4_Fills",
    "Causal_Audit",
    "Numeric_Parity",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "V12_OFFICIAL_VERDICT_PRESERVED",
    "V12_SELECTION_SEMANTICS_AMBIGUOUS",
    "SIGNAL_PARITY",
    "ELIGIBLE_PARITY",
    "E4_FILL_PARITY",
    "SIGNAL_SET_HASH",
    "ELIGIBLE_SET_HASH",
    "E4_FILL_SET_HASH",
    "CAUSAL_FILL_AUDIT_PASS",
    "HORIZON_SEMANTICS_PASS",
    "UNFILLED_ZERO_PASS",
    "E4_NUMERIC_PARITY",
    "REPORTING_SEMANTICS_PASS",
    "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT",
    "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT",
    "DEVELOPMENT_ENTRY_STACK",
    "TRUE_OOS",
    "ENTRY_CERTIFIED",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V13_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V13_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V13_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V13_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V13_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"V12_OFFICIAL_VERDICT_PRESERVED: `{req.get('V12_OFFICIAL_VERDICT_PRESERVED')}`",
        f"V12_SELECTION_SEMANTICS_AMBIGUOUS: `{req.get('V12_SELECTION_SEMANTICS_AMBIGUOUS')}`",
        "",
        "## Identity",
        "",
        f"SIGNAL_PARITY: `{req.get('SIGNAL_PARITY')}`  hash=`{req.get('SIGNAL_SET_HASH')}`",
        f"ELIGIBLE_PARITY: `{req.get('ELIGIBLE_PARITY')}`  hash=`{req.get('ELIGIBLE_SET_HASH')}`",
        f"E4_FILL_PARITY: `{req.get('E4_FILL_PARITY')}`  hash=`{req.get('E4_FILL_SET_HASH')}`",
        "",
        "## Structure",
        "",
        f"CAUSAL_FILL_AUDIT_PASS: `{req.get('CAUSAL_FILL_AUDIT_PASS')}`",
        f"HORIZON_SEMANTICS_PASS: `{req.get('HORIZON_SEMANTICS_PASS')}`",
        f"UNFILLED_ZERO_PASS: `{req.get('UNFILLED_ZERO_PASS')}`",
        f"E4_NUMERIC_PARITY: `{req.get('E4_NUMERIC_PARITY')}`",
        f"REPORTING_SEMANTICS_PASS: `{req.get('REPORTING_SEMANTICS_PASS')}`",
        "",
        "## Development freeze",
        "",
        f"ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT: `{req.get('ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT')}`",
        f"ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT: `{req.get('ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT')}`",
        f"DEVELOPMENT_ENTRY_STACK: `{req.get('DEVELOPMENT_ENTRY_STACK')}`",
        f"ENTRY_CERTIFIED: `{req.get('ENTRY_CERTIFIED')}`",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "- Challenger is E4_INSIDE1_W5. V12 selected policy remains E1_BID_W5.",
        "- V12 CASE B / EDGE_REPAIRED=false is not retroactively changed to CASE A.",
        "- Canonical fill ASK_CROSS_CONSERVATIVE. No EXIT.",
        "",
        f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
        f"NEXT: {req.get('NEXT')}",
        "",
    ]
    return "\n".join(lines) + "\n"
