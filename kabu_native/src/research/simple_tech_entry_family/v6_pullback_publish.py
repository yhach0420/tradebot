"""Write report.json / report.md / audit.xlsx only under v6_pullback_rule/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V6_PR_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v6_pullback_spec import ANALYSIS_ID, ARM_ORDER

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Stage_Arms",
    "Final_Arms",
    "Daily",
    "Symbols",
    "Band_Stability",
    "Gates",
    "Signals",
    "Integrity",
    "Non_Interference",
)

STAGE_KEYS = (
    "ARM_ID",
    "PULLBACK_PASS_N",
    "STAGE_EXECUTABLE_N",
    "STAGE_MARKOUT_60_MEAN",
    "STAGE_MARKOUT_60_MEDIAN",
    "STAGE_MARKOUT_60_POS_RATE",
    "STAGE_MARKOUT_180_MEAN",
    "STAGE_MARKOUT_180_MEDIAN",
    "STAGE_MARKOUT_180_POS_RATE",
    "STAGE_MARKOUT_300_MEAN",
    "STAGE_MARKOUT_300_MEDIAN",
    "STAGE_MARKOUT_300_POS_RATE",
    "STAGE_MFE_MEAN",
    "STAGE_MFE_MEDIAN",
    "STAGE_MAE_MEAN",
    "STAGE_MAE_MEDIAN",
    "STAGE_COST_RECOVERY_RATE_60",
    "STAGE_COST_RECOVERY_RATE_180",
    "STAGE_COST_RECOVERY_RATE_300",
    "STAGE_POSITIVE_DAY_N_180",
    "STAGE_NEGATIVE_DAY_N_180",
    "STAGE_POSITIVE_DAY_N_300",
    "STAGE_NEGATIVE_DAY_N_300",
    "STAGE_EX_BEST_DAY_MARKOUT_180",
    "STAGE_EX_BEST_DAY_MARKOUT_300",
    "STAGE_EX_TOP3_DAY_MARKOUT_180",
    "STAGE_EX_TOP3_DAY_MARKOUT_300",
    "STAGE_TOP_SYMBOL",
    "STAGE_DROP_TOP_SYMBOL_MARKOUT_180",
    "STAGE_DROP_TOP_SYMBOL_MARKOUT_300",
)

FINAL_KEYS = (
    "ARM_ID",
    "RCI_PASS_N",
    "PRICE_ACTION_PASS_N",
    "VOLUME_PASS_N",
    "BOARD_PASS_N",
    "EXECUTABLE_SIGNAL_N",
    "FINAL_MARKOUT_60_MEAN",
    "FINAL_MARKOUT_60_MEDIAN",
    "FINAL_MARKOUT_60_POS_RATE",
    "FINAL_MARKOUT_180_MEAN",
    "FINAL_MARKOUT_180_MEDIAN",
    "FINAL_MARKOUT_180_POS_RATE",
    "FINAL_MARKOUT_300_MEAN",
    "FINAL_MARKOUT_300_MEDIAN",
    "FINAL_MARKOUT_300_POS_RATE",
    "FINAL_POSITIVE_DAY_N_180",
    "FINAL_NEGATIVE_DAY_N_180",
    "FINAL_POSITIVE_DAY_N_300",
    "FINAL_NEGATIVE_DAY_N_300",
    "FINAL_EX_BEST_DAY_MARKOUT_180",
    "FINAL_EX_BEST_DAY_MARKOUT_300",
    "FINAL_DROP_TOP_SYMBOL_MARKOUT_180",
    "FINAL_DROP_TOP_SYMBOL_MARKOUT_300",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V6_RCA_SPEC_SHA256",
    "V6_PR_SPEC_SHA256",
    "V1_PARITY",
    "CONTROL_PARITY_V6_STAGE",
    "CONTROL_PARITY_V3_FINAL",
    "PRE_PULLBACK_N",
    "CONTROL_P0",
    "D10",
    "D20",
    "D30",
    "BAND_STABILITY",
    "PULLBACK_DEPTH_MECHANISM_SUPPORTED",
    "ENTRY_SIGNAL_EDGE_REPAIRED",
    "SELECTED_PULLBACK_RULE",
    "PRIMARY_DEFICIENCY_AFTER_V6",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def arm_summary(arm: dict[str, Any]) -> dict[str, Any]:
    keys = ("ARM_ID", "PRE_PULLBACK_N") + STAGE_KEYS[1:] + FINAL_KEYS[1:]
    return {k: arm.get(k) for k in keys}


def _pub_stage(arm: dict[str, Any]) -> dict[str, Any]:
    return {k: arm.get(k) for k in STAGE_KEYS}


def _pub_final(arm: dict[str, Any]) -> dict[str, Any]:
    return {k: arm.get(k) for k in FINAL_KEYS}


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V6_PR_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V6_PR_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V6_PR_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V6_PR_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V6_PR_OUT / "audit.xlsx")


def _arm_block(name: str, arm: dict[str, Any]) -> list[str]:
    return [
        f"{name}:",
        f" stage_n={arm.get('PULLBACK_PASS_N')} stage_exe={arm.get('STAGE_EXECUTABLE_N')} final_signal_n={arm.get('EXECUTABLE_SIGNAL_N')}",
        f" stage 60 mean/median/pos={arm.get('STAGE_MARKOUT_60_MEAN')} / {arm.get('STAGE_MARKOUT_60_MEDIAN')} / {arm.get('STAGE_MARKOUT_60_POS_RATE')}",
        f" stage 180 mean/median/pos={arm.get('STAGE_MARKOUT_180_MEAN')} / {arm.get('STAGE_MARKOUT_180_MEDIAN')} / {arm.get('STAGE_MARKOUT_180_POS_RATE')}",
        f" stage 300 mean/median/pos={arm.get('STAGE_MARKOUT_300_MEAN')} / {arm.get('STAGE_MARKOUT_300_MEDIAN')} / {arm.get('STAGE_MARKOUT_300_POS_RATE')}",
        f" stage day180={arm.get('STAGE_POSITIVE_DAY_N_180')}/{arm.get('STAGE_NEGATIVE_DAY_N_180')} day300={arm.get('STAGE_POSITIVE_DAY_N_300')}/{arm.get('STAGE_NEGATIVE_DAY_N_300')}",
        f" stage ex_best180/300={arm.get('STAGE_EX_BEST_DAY_MARKOUT_180')} / {arm.get('STAGE_EX_BEST_DAY_MARKOUT_300')}",
        f" final rci/pa/vol/board/exe={arm.get('RCI_PASS_N')}/{arm.get('PRICE_ACTION_PASS_N')}/{arm.get('VOLUME_PASS_N')}/{arm.get('BOARD_PASS_N')}/{arm.get('EXECUTABLE_SIGNAL_N')}",
        f" final 60/180/300 mean={arm.get('FINAL_MARKOUT_60_MEAN')} / {arm.get('FINAL_MARKOUT_180_MEAN')} / {arm.get('FINAL_MARKOUT_300_MEAN')}",
        f" final 180/300 median={arm.get('FINAL_MARKOUT_180_MEDIAN')} / {arm.get('FINAL_MARKOUT_300_MEDIAN')}",
        f" final day180={arm.get('FINAL_POSITIVE_DAY_N_180')}/{arm.get('FINAL_NEGATIVE_DAY_N_180')} day300={arm.get('FINAL_POSITIVE_DAY_N_300')}/{arm.get('FINAL_NEGATIVE_DAY_N_300')}",
        f" final ex_best180/300={arm.get('FINAL_EX_BEST_DAY_MARKOUT_180')} / {arm.get('FINAL_EX_BEST_DAY_MARKOUT_300')}",
        "",
    ]


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V6_RCA_SPEC_SHA256: `{req.get('V6_RCA_SPEC_SHA256')}`",
        f"V6_PR_SPEC_SHA256: `{req.get('V6_PR_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"PULLBACK_DEPTH_MECHANISM_SUPPORTED: `{req.get('PULLBACK_DEPTH_MECHANISM_SUPPORTED')}`",
        f"ENTRY_SIGNAL_EDGE_REPAIRED: `{req.get('ENTRY_SIGNAL_EDGE_REPAIRED')}`",
        f"SELECTED_PULLBACK_RULE: `{req.get('SELECTED_PULLBACK_RULE')}`",
        f"PRIMARY_DEFICIENCY_AFTER_V6: `{req.get('PRIMARY_DEFICIENCY_AFTER_V6')}`",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Required output",
        "",
        f"PRE_PULLBACK_N: {req.get('PRE_PULLBACK_N')}",
        "",
    ]
    for name in ARM_ORDER:
        arm = dict(req.get(name) or {})
        lines.extend(_arm_block(name, arm))
    lines.extend(
        [
            f"BAND_STABILITY: {req.get('BAND_STABILITY')}",
            "",
            f"PULLBACK_DEPTH_MECHANISM_SUPPORTED: {req.get('PULLBACK_DEPTH_MECHANISM_SUPPORTED')}",
            f"ENTRY_SIGNAL_EDGE_REPAIRED: {req.get('ENTRY_SIGNAL_EDGE_REPAIRED')}",
            f"SELECTED_PULLBACK_RULE: {req.get('SELECTED_PULLBACK_RULE')}",
            f"PRIMARY_DEFICIENCY_AFTER_V6: {req.get('PRIMARY_DEFICIENCY_AFTER_V6')}",
            f"TRUE_OOS: {req.get('TRUE_OOS')}",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"VERDICT: {req.get('VERDICT')}",
            f"NEXT: {req.get('NEXT')}",
            "",
            "## Notes",
            "",
            "- CONTROL_P0 is frozen V1 pullback. D10/D20/D30 replace Low<=EMA9 with PQ1 depth. Close>=BB_LOWER retained.",
            "- EMA 9/21 and BB20/2sigma are unchanged. PQ2/PQ3/PQ4 were not tested. Persistence was not added.",
            "- Mechanism gate is stage-local (TREND_PASS). Entry-edge gate is final-signal.",
            "- Exit-neutral Ask/Bid markouts only. No C14. No Simple Tech EXIT. TRUE_OOS=false.",
            "- 18 AM days are burned development data. Not CERTIFIED / Runtime candidate.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
