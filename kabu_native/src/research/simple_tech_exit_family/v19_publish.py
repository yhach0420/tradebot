"""Write report.json / report.md / audit.xlsx only under v19_exit_structure_verification/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_exit_family.isolation import V19_OUT
from research.simple_tech_exit_family.v18_publish import flatten_trade
from research.simple_tech_exit_family.v19_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Hashes",
    "Parity",
    "Causal",
    "Latency",
    "Trades",
    "Mismatch",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "ENTRY_STACK_PARITY",
    "E4_FILL_SET_HASH_PARITY",
    "EXIT_POLICY_PARITY",
    "EXIT_INPUT_FILL_SET_HASH",
    "SCHEDULED_EXIT_SET_HASH",
    "ACTUAL_EXIT_SET_HASH",
    "EXIT_TRADE_BY_TRADE_PARITY",
    "EXIT_MISMATCH_N",
    "CAUSAL_EXIT_AUDIT_PASS",
    "V18_NUMERIC_PARITY",
    "V18_LATENCY_PARITY",
    "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT",
    "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT",
    "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT",
    "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT",
    "DEVELOPMENT_STRATEGY_STACK",
    "TRUE_OOS",
    "STRATEGY_CERTIFIED",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V19_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V19_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V19_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V19_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V19_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            "",
            f"ENTRY_STACK_PARITY: `{req.get('ENTRY_STACK_PARITY')}`",
            f"E4_FILL_SET_HASH_PARITY: `{req.get('E4_FILL_SET_HASH_PARITY')}`",
            f"EXIT_POLICY_PARITY: `{req.get('EXIT_POLICY_PARITY')}`",
            "",
            f"EXIT_INPUT_FILL_SET_HASH: `{req.get('EXIT_INPUT_FILL_SET_HASH')}`",
            f"SCHEDULED_EXIT_SET_HASH: `{req.get('SCHEDULED_EXIT_SET_HASH')}`",
            f"ACTUAL_EXIT_SET_HASH: `{req.get('ACTUAL_EXIT_SET_HASH')}`",
            "",
            f"EXIT_TRADE_BY_TRADE_PARITY: `{req.get('EXIT_TRADE_BY_TRADE_PARITY')}`  EXIT_MISMATCH_N=`{req.get('EXIT_MISMATCH_N')}`",
            f"CAUSAL_EXIT_AUDIT_PASS: `{req.get('CAUSAL_EXIT_AUDIT_PASS')}`",
            f"V18_NUMERIC_PARITY: `{req.get('V18_NUMERIC_PARITY')}`  V18_LATENCY_PARITY: `{req.get('V18_LATENCY_PARITY')}`",
            "",
            f"ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT: `{req.get('ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT')}`",
            f"ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT: `{req.get('ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT')}`",
            f"EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT: `{req.get('EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT')}`",
            f"EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT: `{req.get('EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT')}`",
            f"DEVELOPMENT_STRATEGY_STACK: `{req.get('DEVELOPMENT_STRATEGY_STACK')}`",
            "",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  STRATEGY_CERTIFIED: `{req.get('STRATEGY_CERTIFIED')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
