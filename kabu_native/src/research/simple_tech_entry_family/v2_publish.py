"""Write report.json / report.md / audit.xlsx only under v2/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V2_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v2_spec import ANALYSIS_ID, STRATEGY_ID

SHEET_ORDER = (
    "Precommit",
    "Data_Manifest",
    "Stage_Parity",
    "V1_V2_Counterfactual",
    "Setups",
    "Signals",
    "Recovery",
    "Trades",
    "Daily",
    "Economics",
    "Mechanism",
    "Deficiency_RCA",
    "Non_Interference",
    "Integrity",
)

REQUIRED_KEYS = (
    "STRATEGY_ID",
    "PARENT_SPEC_SHA256",
    "V2_SPEC_SHA256",
    "V1_SIGNAL_N",
    "V1_FILL_N",
    "V1_FILL_RATE",
    "V1_FILLED_FWD3",
    "V1_NONFILLED_FWD3",
    "V2_SETUP_N",
    "V2_TRIGGER_N",
    "V2_SIGNAL_N",
    "V2_FILL_N",
    "V2_FILL_RATE",
    "V2_FILLED_FWD3",
    "V2_NONFILLED_FWD3",
    "V2_FILL_QUALITY_GAP",
    "GOOD_UPMOVE_RECOVERED_N",
    "GOOD_UPMOVE_STILL_NONFILL_N",
    "NEW_BAD_FILL_N",
    "V2_NET",
    "V2_PF",
    "V2_DD",
    "TRADE_N",
    "WIN_N",
    "LOSS_N",
    "FLAT_N",
    "POSITIVE_DAY_N",
    "NEGATIVE_DAY_N",
    "ZERO_DAY_N",
    "DAILY_MEDIAN",
    "EX_BEST",
    "EX_TOP3",
    "TRIGGER_MECHANISM_SUPPORTED",
    "PRIMARY_DEFICIENCY_AFTER_V2",
    "NON_INTERFERENCE_PASS",
    "TRUE_OOS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V2_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V2_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V2_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V2_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V2_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"STRATEGY_ID: `{STRATEGY_ID}`",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V2_SPEC_SHA256: `{req.get('V2_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRIGGER_MECHANISM_SUPPORTED: `{req.get('TRIGGER_MECHANISM_SUPPORTED')}`",
        f"PRIMARY_DEFICIENCY_AFTER_V2: `{req.get('PRIMARY_DEFICIENCY_AFTER_V2')}`",
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
            "- Only the price-trigger component changed. MA/BB/RCI/Volume/Board/Passive Fill/C14 stay frozen.",
            "- Existing 18-day set is burned development data. Do not treat this as OOS.",
            "- Do not adopt V2 into Runtime. Do not search 2-tick / 5s / 10s confirm delays.",
            "- Runtime/Capture were not stopped, restarted, or written.",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
