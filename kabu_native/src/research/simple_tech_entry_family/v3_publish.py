"""Write report.json / report.md / audit.xlsx only under v3_exit_neutral/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V3_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v3_spec import ANALYSIS_ID, STRATEGY_ID

SHEET_ORDER = (
    "Precommit",
    "V1_Parity",
    "Signals",
    "Good6",
    "Passive_Split",
    "Daily",
    "Concentration",
    "Component_RCA",
    "Gates",
    "Non_Interference",
    "Integrity",
)

REQUIRED_KEYS = (
    "STRATEGY_ID",
    "PARENT_SPEC_SHA256",
    "V3_SPEC_SHA256",
    "V1_PARITY",
    "SIGNAL_N",
    "EXECUTABLE_SIGNAL_N",
    "MARKOUT_30_MEAN",
    "MARKOUT_30_MEDIAN",
    "MARKOUT_30_POS_RATE",
    "MARKOUT_60_MEAN",
    "MARKOUT_60_MEDIAN",
    "MARKOUT_60_POS_RATE",
    "MARKOUT_180_MEAN",
    "MARKOUT_180_MEDIAN",
    "MARKOUT_180_POS_RATE",
    "MARKOUT_300_MEAN",
    "MARKOUT_300_MEDIAN",
    "MARKOUT_300_POS_RATE",
    "COST_RECOVERY_RATE_300",
    "MFE_MEDIAN",
    "MAE_MEDIAN",
    "PASSIVE_FILLED_MARKOUT_180",
    "PASSIVE_NONFILLED_MARKOUT_180",
    "PASSIVE_FILLED_MARKOUT_300",
    "PASSIVE_NONFILLED_MARKOUT_300",
    "POSITIVE_DAY_N_180",
    "NEGATIVE_DAY_N_180",
    "POSITIVE_DAY_N_300",
    "NEGATIVE_DAY_N_300",
    "EX_BEST_DAY_MARKOUT_180",
    "EX_BEST_DAY_MARKOUT_300",
    "BEST_DAY_CONTRIBUTION_180",
    "TOP3_DAY_CONTRIBUTION_180",
    "TOP_SYMBOL_CONTRIBUTION_180",
    "GOOD_UPMOVE_6_ASK_EDGE",
    "ENTRY_SIGNAL_EDGE_SUPPORTED",
    "PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED",
    "PRIMARY_DEFICIENCY_AFTER_V3",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V3_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V3_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V3_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V3_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V3_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"STRATEGY_ID: `{STRATEGY_ID}`",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V3_SPEC_SHA256: `{req.get('V3_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"ENTRY_SIGNAL_EDGE_SUPPORTED: `{req.get('ENTRY_SIGNAL_EDGE_SUPPORTED')}`",
        f"PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED: `{req.get('PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED')}`",
        f"PRIMARY_DEFICIENCY_AFTER_V3: `{req.get('PRIMARY_DEFICIENCY_AFTER_V3')}`",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Required output",
        "",
    ]
    for k in REQUIRED_KEYS:
        lines.append(f"{k}: {req.get(k)}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Exit-neutral study. C14 / 600 / 750 / realized PnL were not used for selection or gates.",
            "- Horizons 30/60/180/300 are measurement marks, not sell rules. Best-horizon selection is forbidden.",
            "- Ask Runtime adoption is forbidden in this run. Simple Tech EXIT is not implemented.",
            "- 18 AM days are burned development data. Not CERTIFIED / Runtime candidate.",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
