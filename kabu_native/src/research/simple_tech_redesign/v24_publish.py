"""Write report.json / report.md / audit.xlsx only under v24_board_freshness_semantics_correction_replay/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V24_OUT
from research.simple_tech_redesign.v24_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Funnel",
    "LegacyParity",
    "RecoveredStale",
    "NewFills",
    "ClockSources",
    "Diagnosis",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STRATEGY_STACK_PARITY",
    "SIGNAL_N",
    "BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS",
    "LEGACY_EVALUABLE_PARITY",
    "LEGACY_E4_FILL_PARITY",
    "LEGACY_E4_NONFILL_PARITY",
    "CORRECTED_EXECUTION_EVALUABLE_N",
    "CORRECTED_EXECUTION_UNEVALUABLE_N",
    "RECOVERED_FROM_STALE_N",
    "CORRECTED_E4_FILLED_N",
    "CORRECTED_E4_NONFILLED_N",
    "NEW_FILL_FROM_FORMER_STALE_N",
    "NEW_NONFILL_FROM_FORMER_STALE_N",
    "NEW_FILL_DAY_N",
    "NEW_FILL_SYMBOL_N",
    "FILLS_PER_DAY",
    "FUTURE_BOARD_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "ENTRY_CHANGED",
    "E4_CHANGED",
    "FRESHNESS_THRESHOLD_CHANGED",
    "EXIT_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V24_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V24_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V24_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V24_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V24_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    par = dict(report.get("parity") or {})
    src = dict(req.get("BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        f"STRATEGY_STACK_PARITY = `{req.get('STRATEGY_STACK_PARITY')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}`",
        f"BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS = `{json.dumps(src, ensure_ascii=False, default=str)}`",
        "",
        "## Legacy parity",
        f"- LEGACY_EVALUABLE_PARITY = `{req.get('LEGACY_EVALUABLE_PARITY')}`",
        f"- LEGACY_E4_FILL_PARITY = `{req.get('LEGACY_E4_FILL_PARITY')}`",
        f"- LEGACY_E4_NONFILL_PARITY = `{req.get('LEGACY_E4_NONFILL_PARITY')}`",
        f"- lost_legacy_eval_n = `{par.get('lost_legacy_eval_n')}`",
        f"- lost_legacy_fill_n = `{par.get('lost_legacy_fill_n')}`",
        f"- extra_legacy_fill_n = `{par.get('extra_legacy_fill_n')}`",
        "",
        "## Diagnosis",
        f"- cause: {dict(report.get('diagnosis') or {}).get('cause')}",
        f"- lost_legacy_eval: `{json.dumps(dict(report.get('diagnosis') or {}).get('lost_legacy_eval') or [], ensure_ascii=False, default=str)}`",
        f"- extra_legacy_fill: `{json.dumps(dict(report.get('diagnosis') or {}).get('extra_legacy_fill') or [], ensure_ascii=False, default=str)}`",
        f"- legacy_fill_identity_shift: `{json.dumps(dict(report.get('diagnosis') or {}).get('legacy_fill_identity_shift') or [], ensure_ascii=False, default=str)}`",
        "",
        "## Corrected funnel",
        f"- CORRECTED_EXECUTION_EVALUABLE_N = `{req.get('CORRECTED_EXECUTION_EVALUABLE_N')}`",
        f"- CORRECTED_EXECUTION_UNEVALUABLE_N = `{req.get('CORRECTED_EXECUTION_UNEVALUABLE_N')}`",
        f"- RECOVERED_FROM_STALE_N = `{req.get('RECOVERED_FROM_STALE_N')}`",
        f"- CORRECTED_E4_FILLED_N = `{req.get('CORRECTED_E4_FILLED_N')}`",
        f"- CORRECTED_E4_NONFILLED_N = `{req.get('CORRECTED_E4_NONFILLED_N')}`",
        f"- NEW_FILL_FROM_FORMER_STALE_N = `{req.get('NEW_FILL_FROM_FORMER_STALE_N')}`",
        f"- NEW_NONFILL_FROM_FORMER_STALE_N = `{req.get('NEW_NONFILL_FROM_FORMER_STALE_N')}`",
        f"- NEW_FILL_DAY_N = `{req.get('NEW_FILL_DAY_N')}`",
        f"- NEW_FILL_SYMBOL_N = `{req.get('NEW_FILL_SYMBOL_N')}`",
        f"- FILLS_PER_DAY = `{json.dumps(req.get('FILLS_PER_DAY') or {}, ensure_ascii=False, default=str)}`",
        "",
        "## Causality",
        f"- FUTURE_BOARD_CARRYBACK_N = `{req.get('FUTURE_BOARD_CARRYBACK_N')}`",
        f"- FUTURE_TIMESTAMP_CARRYBACK_N = `{req.get('FUTURE_TIMESTAMP_CARRYBACK_N')}`",
        "",
        "## Locks",
        f"- ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}`",
        f"- E4_CHANGED=`{req.get('E4_CHANGED')}`",
        f"- FRESHNESS_THRESHOLD_CHANGED=`{req.get('FRESHNESS_THRESHOLD_CHANGED')}`",
        f"- EXIT_CHANGED=`{req.get('EXIT_CHANGED')}`",
        f"- SIZING_CHANGED=`{req.get('SIZING_CHANGED')}`",
        f"- TRUE_OOS=`{req.get('TRUE_OOS')}`",
        f"- NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
        "- CurrentPriceTime is not the board execution freshness clock. PRICE_FRESHNESS is stored separately.",
        "- No virtual fill. No future quote carry-back. No FIXED180 PnL selection.",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
