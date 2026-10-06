"""Write report.json / report.md / audit.xlsx only under v4_persistence_rule/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V4_PR_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v4_persistence_spec import ANALYSIS_ID, ARM_ORDER

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Arms",
    "Daily",
    "Symbols",
    "Band_Stability",
    "Gates",
    "Signals",
    "Integrity",
    "Non_Interference",
)

ARM_SUMMARY_KEYS = (
    "ARM_ID",
    "PRE_VOLUME_N",
    "VOLUME_PASS_N",
    "BOARD_PASS_N",
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
    "MFE_MEAN",
    "MFE_MEDIAN",
    "MAE_MEAN",
    "MAE_MEDIAN",
    "COST_RECOVERY_RATE_60",
    "COST_RECOVERY_RATE_180",
    "COST_RECOVERY_RATE_300",
    "COST_RECOVERY_60",
    "COST_RECOVERY_180",
    "COST_RECOVERY_300",
    "SIGNAL_DAY_N_180",
    "POSITIVE_DAY_N_180",
    "NEGATIVE_DAY_N_180",
    "ZERO_DAY_N_180",
    "DAY_MEDIAN_MARKOUT_180",
    "BEST_DAY_180",
    "EX_BEST_DAY_MARKOUT_180",
    "EX_TOP3_DAY_MARKOUT_180",
    "SIGNAL_DAY_N_300",
    "POSITIVE_DAY_N_300",
    "NEGATIVE_DAY_N_300",
    "ZERO_DAY_N_300",
    "DAY_MEDIAN_MARKOUT_300",
    "BEST_DAY_300",
    "EX_BEST_DAY_MARKOUT_300",
    "EX_TOP3_DAY_MARKOUT_300",
    "TOP_SYMBOL",
    "TOP_SYMBOL_SHARE",
    "TOP3_SYMBOL_SHARE",
    "DROP_TOP_SYMBOL_MARKOUT_180",
    "DROP_TOP_SYMBOL_MARKOUT_300",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V4_RCA_SPEC_SHA256",
    "V4_PR_SPEC_SHA256",
    "V1_PARITY",
    "CONTROL_PARITY_V3",
    "PRE_VOLUME_N",
    "CONTROL_M15",
    "P60",
    "P80",
    "P90",
    "BAND_STABILITY",
    "PERSISTENCE_RULE_MECHANISM_SUPPORTED",
    "ENTRY_SIGNAL_EDGE_REPAIRED",
    "SELECTED_PERSISTENCE_BAND",
    "PRIMARY_DEFICIENCY_AFTER_V4",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def arm_summary(arm: dict[str, Any]) -> dict[str, Any]:
    return {k: arm.get(k) for k in ARM_SUMMARY_KEYS}


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V4_PR_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V4_PR_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V4_PR_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V4_PR_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V4_PR_OUT / "audit.xlsx")


def _arm_block(name: str, arm: dict[str, Any]) -> list[str]:
    return [
        f"{name}:",
        f" signal_n={arm.get('EXECUTABLE_SIGNAL_N')} volume_pass={arm.get('VOLUME_PASS_N')} board_pass={arm.get('BOARD_PASS_N')}",
        f" markout60 mean/median/pos={arm.get('MARKOUT_60_MEAN')} / {arm.get('MARKOUT_60_MEDIAN')} / {arm.get('MARKOUT_60_POS_RATE')}",
        f" markout180 mean/median/pos={arm.get('MARKOUT_180_MEAN')} / {arm.get('MARKOUT_180_MEDIAN')} / {arm.get('MARKOUT_180_POS_RATE')}",
        f" markout300 mean/median/pos={arm.get('MARKOUT_300_MEAN')} / {arm.get('MARKOUT_300_MEDIAN')} / {arm.get('MARKOUT_300_POS_RATE')}",
        f" pos_neg_days180={arm.get('POSITIVE_DAY_N_180')}/{arm.get('NEGATIVE_DAY_N_180')}",
        f" pos_neg_days300={arm.get('POSITIVE_DAY_N_300')}/{arm.get('NEGATIVE_DAY_N_300')}",
        f" ex_best180={arm.get('EX_BEST_DAY_MARKOUT_180')} ex_best300={arm.get('EX_BEST_DAY_MARKOUT_300')}",
        "",
    ]


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V4_RCA_SPEC_SHA256: `{req.get('V4_RCA_SPEC_SHA256')}`",
        f"V4_PR_SPEC_SHA256: `{req.get('V4_PR_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"PERSISTENCE_RULE_MECHANISM_SUPPORTED: `{req.get('PERSISTENCE_RULE_MECHANISM_SUPPORTED')}`",
        f"ENTRY_SIGNAL_EDGE_REPAIRED: `{req.get('ENTRY_SIGNAL_EDGE_REPAIRED')}`",
        f"SELECTED_PERSISTENCE_BAND: `{req.get('SELECTED_PERSISTENCE_BAND')}`",
        f"PRIMARY_DEFICIENCY_AFTER_V4: `{req.get('PRIMARY_DEFICIENCY_AFTER_V4')}`",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Required output",
        "",
        f"PRE_VOLUME_N: {req.get('PRE_VOLUME_N')}",
        "",
    ]
    for name in ARM_ORDER:
        arm = dict(req.get(name) or {})
        lines.extend(_arm_block(name, arm))
    lines.extend(
        [
            f"BAND_STABILITY: {req.get('BAND_STABILITY')}",
            "",
            f"PERSISTENCE_RULE_MECHANISM_SUPPORTED: {req.get('PERSISTENCE_RULE_MECHANISM_SUPPORTED')}",
            f"ENTRY_SIGNAL_EDGE_REPAIRED: {req.get('ENTRY_SIGNAL_EDGE_REPAIRED')}",
            f"SELECTED_PERSISTENCE_BAND: {req.get('SELECTED_PERSISTENCE_BAND')}",
            f"PRIMARY_DEFICIENCY_AFTER_V4: {req.get('PRIMARY_DEFICIENCY_AFTER_V4')}",
            f"TRUE_OOS: {req.get('TRUE_OOS')}",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"VERDICT: {req.get('VERDICT')}",
            f"NEXT: {req.get('NEXT')}",
            "",
            "## Notes",
            "",
            "- Volume magnitude gate is replaced 1:1. AND with 1.5x is forbidden.",
            "- Bands P60/P80/P90 were precommitted from 30-step participation semantics. No extra cuts.",
            "- Exit-neutral Ask/Bid markouts only. No C14. No Simple Tech EXIT. TRUE_OOS=false.",
            "- 18 AM days are burned development data. Not CERTIFIED / Runtime candidate.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
