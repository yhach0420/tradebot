"""Write report.json / report.md / audit.xlsx only under v28_fallback_3m_ema_persistence_exit/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V28_OUT
from research.simple_tech_redesign.v28_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "ExitCounts",
    "PathRates",
    "AddedControl",
    "AddedTreatment",
    "Deltas",
    "CoreParity",
    "Combined232",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "V27_FILL_IDENTITY_PARITY",
    "CORE_FILL_N",
    "ADDED_FILL_N",
    "PERSISTENCE_K",
    "TECH_EXIT_N",
    "SESSION_CLOSE_EXIT_N",
    "TECH_EXIT_DAY_N",
    "TECH_EXIT_SYMBOL_N",
    "PATH_TYPE_EXIT_RATES",
    "ADDED_CONTROL_ECONOMICS",
    "ADDED_TREATMENT_ECONOMICS",
    "DELTA_TOTAL_PNL",
    "DELTA_PF",
    "DELTA_MAX_DD",
    "DELTA_EX_BEST_DAY",
    "DELTA_EX_TOP3_DAY",
    "DELTA_DROP_TOP_SYMBOL",
    "CORE_PNL_PARITY",
    "COMBINED_232_ECONOMICS",
    "ENTRY_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V28_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V28_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V28_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V28_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V28_OUT / "audit.xlsx")


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
        "V27 official `SIMPLE_TECH_V27_EXIT_STATE_SEQUENCE_MECHANISM_FOUND` frozen. "
        "V26 official CASE B frozen. Protective EXIT applies to ADDED 180 only. CORE 52 is session-close control. "
        "PERSISTENCE_K=6 precommitted. No K search. No 5m/1m BB comparison.",
        "",
        f"V27_FILL_IDENTITY_PARITY = `{req.get('V27_FILL_IDENTITY_PARITY')}`",
        f"CORE_FILL_N = `{req.get('CORE_FILL_N')}` ADDED_FILL_N = `{req.get('ADDED_FILL_N')}`",
        f"PERSISTENCE_K = `{req.get('PERSISTENCE_K')}`",
        f"TECH_EXIT_N = `{req.get('TECH_EXIT_N')}` SESSION_CLOSE_EXIT_N = `{req.get('SESSION_CLOSE_EXIT_N')}`",
        f"TECH_EXIT_DAY_N = `{req.get('TECH_EXIT_DAY_N')}` TECH_EXIT_SYMBOL_N = `{req.get('TECH_EXIT_SYMBOL_N')}`",
        "",
        f"DELTA_TOTAL_PNL = `{req.get('DELTA_TOTAL_PNL')}` DELTA_PF = `{req.get('DELTA_PF')}` "
        f"DELTA_MAX_DD = `{req.get('DELTA_MAX_DD')}`",
        f"DELTA_EX_BEST_DAY = `{req.get('DELTA_EX_BEST_DAY')}` DELTA_EX_TOP3_DAY = `{req.get('DELTA_EX_TOP3_DAY')}` "
        f"DELTA_DROP_TOP_SYMBOL = `{req.get('DELTA_DROP_TOP_SYMBOL')}`",
        f"CORE_PNL_PARITY = `{req.get('CORE_PNL_PARITY')}`",
        "",
        f"ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}` SIZING_CHANGED=`{req.get('SIZING_CHANGED')}` "
        f"TRUE_OOS=`{req.get('TRUE_OOS')}` NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
