"""Write report.json / report.md / audit.xlsx only under v25_corrected_execution_baseline_reconciliation/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V25_OUT
from research.simple_tech_redesign.v25_spec import ANALYSIS_ID, SPECIAL_CASES

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Funnel",
    "SpecialCases",
    "ClockAudit",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STRATEGY_RULE_PARITY",
    "SIGNAL_N",
    "CORRECTED_EXECUTION_EVALUABLE_N",
    "CORRECTED_EXECUTION_UNEVALUABLE_N",
    "CORRECTED_E4_FILLED_N",
    "CORRECTED_E4_NONFILLED_N",
    "CORRECTED_FILL_HASH_PARITY",
    "SPECIAL_CASE_5985",
    "SPECIAL_CASE_3696",
    "SPECIAL_CASE_6890",
    "SPECIAL_CASE_6703",
    "SPECIAL_CASE_4401",
    "SPECIAL_CASE_581A",
    "BOARD_CLOCK_CAUSALITY_PASS",
    "FUTURE_BOARD_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "ENTRY_CHANGED",
    "E4_CHANGED",
    "FRESHNESS_THRESHOLD_CHANGED",
    "TRUE_OOS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V25_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V25_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V25_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V25_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V25_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    specs = [str(x["id"]) for x in SPECIAL_CASES]
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "V24 official verdict frozen: `SIMPLE_TECH_V24_LEGACY_PARITY_FAILED`. Legacy 126/38/88 identity is not a V25 gate.",
        "",
        f"STRATEGY_RULE_PARITY = `{req.get('STRATEGY_RULE_PARITY')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}`",
        f"CORRECTED_EXECUTION_EVALUABLE_N = `{req.get('CORRECTED_EXECUTION_EVALUABLE_N')}`",
        f"CORRECTED_EXECUTION_UNEVALUABLE_N = `{req.get('CORRECTED_EXECUTION_UNEVALUABLE_N')}`",
        f"CORRECTED_E4_FILLED_N = `{req.get('CORRECTED_E4_FILLED_N')}`",
        f"CORRECTED_E4_NONFILLED_N = `{req.get('CORRECTED_E4_NONFILLED_N')}`",
        f"CORRECTED_FILL_HASH_PARITY = `{req.get('CORRECTED_FILL_HASH_PARITY')}`",
        "",
        "## Special cases",
        *[f"- {k} explained=`{dict(req.get(k) or {}).get('explained')}` reason=`{dict(req.get(k) or {}).get('reason')}`" for k in specs],
        "",
        "## Causality",
        f"- BOARD_CLOCK_CAUSALITY_PASS = `{req.get('BOARD_CLOCK_CAUSALITY_PASS')}`",
        f"- FUTURE_BOARD_CARRYBACK_N = `{req.get('FUTURE_BOARD_CARRYBACK_N')}`",
        f"- FUTURE_TIMESTAMP_CARRYBACK_N = `{req.get('FUTURE_TIMESTAMP_CARRYBACK_N')}`",
        f"- FUTURE_QUOTE_CARRYBACK_N = `{req.get('FUTURE_QUOTE_CARRYBACK_N')}`",
        "",
        "## Locks",
        f"- ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}`",
        f"- E4_CHANGED=`{req.get('E4_CHANGED')}`",
        f"- FRESHNESS_THRESHOLD_CHANGED=`{req.get('FRESHNESS_THRESHOLD_CHANGED')}`",
        f"- TRUE_OOS=`{req.get('TRUE_OOS')}`",
        "- No PnL accept gate. No FIXED180. CurrentPriceTime is PRICE_FRESHNESS only.",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
