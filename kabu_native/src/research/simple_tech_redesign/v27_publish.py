"""Write report.json / report.md / audit.xlsx only under v27_exit_state_sequence_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V27_OUT
from research.simple_tech_redesign.v27_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "EpisodeSummary",
    "Duration",
    "Recovery",
    "Propagation",
    "SupportTests",
    "CoreVsAdded",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "V26_FILL_IDENTITY_PARITY",
    "TOTAL_RESEARCH_FILL_N",
    "CORE_FILL_N",
    "ADDED_FILL_N",
    "EMA_STRUCTURE_EPISODES",
    "BB_STRUCTURE_EPISODES",
    "ALL_PRIMITIVE_EPISODE_SUMMARY",
    "PATH_TYPE_DURATION_COMPARISON",
    "PATH_TYPE_RECOVERY_COMPARISON",
    "PATH_TYPE_PROPAGATION_COMPARISON",
    "CORE_STATE_SEQUENCE_SUMMARY",
    "ADDED_STATE_SEQUENCE_SUMMARY",
    "PERSISTENCE_SUPPORTED",
    "PROPAGATION_SUPPORTED",
    "NON_RECOVERY_SUPPORTED",
    "SUPPORTED_EXIT_STATE_MECHANISMS",
    "PRIMARY_EXIT_STATE_MECHANISM",
    "EXIT_POLICY_CREATED",
    "ENTRY_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V27_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V27_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V27_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V27_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V27_OUT / "audit.xlsx")


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
        "V26 official verdict frozen: `SIMPLE_TECH_V26_COVERAGE_GAIN_EXIT_SEPARATION_FAILED`. "
        "Coverage architecture `E4_THEN_ASK_CROSS_W5` is a research surface, not a frozen ENTRY execution. "
        "Exact V26 primitives. No bar-count search, no combination EXIT, no time-stop, no PnL policy.",
        "",
        f"V26_FILL_IDENTITY_PARITY = `{req.get('V26_FILL_IDENTITY_PARITY')}`",
        f"TOTAL_RESEARCH_FILL_N = `{req.get('TOTAL_RESEARCH_FILL_N')}`",
        f"CORE_FILL_N = `{req.get('CORE_FILL_N')}`",
        f"ADDED_FILL_N = `{req.get('ADDED_FILL_N')}`",
        "",
        f"PERSISTENCE_SUPPORTED = `{req.get('PERSISTENCE_SUPPORTED')}`",
        f"PROPAGATION_SUPPORTED = `{req.get('PROPAGATION_SUPPORTED')}`",
        f"NON_RECOVERY_SUPPORTED = `{req.get('NON_RECOVERY_SUPPORTED')}`",
        f"SUPPORTED_EXIT_STATE_MECHANISMS = `{req.get('SUPPORTED_EXIT_STATE_MECHANISMS')}`",
        f"PRIMARY_EXIT_STATE_MECHANISM = `{req.get('PRIMARY_EXIT_STATE_MECHANISM')}`",
        "",
        f"EXIT_POLICY_CREATED=`{req.get('EXIT_POLICY_CREATED')}` ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}` "
        f"SIZING_CHANGED=`{req.get('SIZING_CHANGED')}` TRUE_OOS=`{req.get('TRUE_OOS')}` "
        f"NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
