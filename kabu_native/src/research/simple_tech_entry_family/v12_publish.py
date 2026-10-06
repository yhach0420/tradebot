"""Write report.json / report.md / audit.xlsx only under v12_entry_execution/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V12_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v12_spec import ANALYSIS_ID, CONTROL_ARM, POLICY_ARMS

SHEET_ORDER = (
    "Precommit",
    "E0_Parity",
    "Policies",
    "Conditional",
    "Unconditional",
    "Missed_Opportunity",
    "Robustness",
    "Wait_Band",
    "Daily",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V11_SPEC_SHA256",
    "V12_SPEC_SHA256",
    "ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH",
    "E0_PARITY",
    "E0_FULL_60",
    "E0_FULL_180",
    "E0_FULL_300",
    "PASSIVE_BID_MECHANISM_SUPPORTED",
    "INSIDE1_MECHANISM_SUPPORTED",
    "ENTRY_EXECUTION_MECHANISM_SUPPORTED",
    "ENTRY_EXECUTION_EDGE_REPAIRED",
    "SELECTED_EXECUTION_POLICY",
    "PRIMARY_EXECUTION_DEFICIENCY",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)

POLICY_IDS = (CONTROL_ARM,) + tuple(a[0] for a in POLICY_ARMS)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V12_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V12_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V12_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V12_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V12_OUT / "audit.xlsx")


def policy_sheet_row(p: dict[str, Any]) -> dict[str, Any]:
    cond = p.get("CONDITIONAL_FILLED_MARKOUT") or {}
    un = p.get("UNCONDITIONAL_POLICY_MARKOUT") or {}
    cb = p.get("CONDITIONAL_FILL_TO_BID") or {}
    ub = p.get("UNCONDITIONAL_FILL_TO_BID") or {}
    ft = p.get("FILL_TIME_MARKOUT_DIAG") or {}
    fg = p.get("FILLED_GROSS_MID") or {}
    ug = p.get("UNFILLED_GROSS_MID") or {}
    vs = p.get("VS_E0") or {}
    return {
        "ARM_ID": p.get("ARM_ID"),
        "FAMILY": p.get("FAMILY"),
        "SIGNAL_N": p.get("SIGNAL_N"),
        "ELIGIBLE_N": p.get("ELIGIBLE_N"),
        "FILLED_N": p.get("FILLED_N"),
        "UNFILLED_N": p.get("UNFILLED_N"),
        "FILL_RATE": p.get("FILL_RATE"),
        "WAIT_MEAN": p.get("WAIT_TO_FILL_MEAN"),
        "WAIT_MEDIAN": p.get("WAIT_TO_FILL_MEDIAN"),
        "WAIT_P75": p.get("WAIT_TO_FILL_P75"),
        "WAIT_MAX": p.get("WAIT_TO_FILL_MAX"),
        "DELAY_BUDGET_SEC": p.get("DELAY_BUDGET_SEC"),
        "IMPROVE_VS_ASK0_BPS": p.get("ENTRY_PRICE_IMPROVEMENT_VS_ASK0_BPS"),
        "ENTRY_VS_MID0_BPS": p.get("ENTRY_PRICE_VS_MID0_BPS"),
        "COND_MID_60": cond.get("60"),
        "COND_MID_180": cond.get("180"),
        "COND_MID_300": cond.get("300"),
        "UNCOND_MID_60": un.get("60"),
        "UNCOND_MID_180": un.get("180"),
        "UNCOND_MID_300": un.get("300"),
        "COND_BID_60": cb.get("60"),
        "COND_BID_180": cb.get("180"),
        "COND_BID_300": cb.get("300"),
        "UNCOND_BID_60": ub.get("60"),
        "UNCOND_BID_180": ub.get("180"),
        "UNCOND_BID_300": ub.get("300"),
        "FT_MID_60": ft.get("60"),
        "FT_MID_180": ft.get("180"),
        "FT_MID_300": ft.get("300"),
        "FILLED_GROSS_60": fg.get("60"),
        "FILLED_GROSS_180": fg.get("180"),
        "FILLED_GROSS_300": fg.get("300"),
        "UNFILLED_GROSS_60": ug.get("60"),
        "UNFILLED_GROSS_180": ug.get("180"),
        "UNFILLED_GROSS_300": ug.get("300"),
        "POS_DAY_180": p.get("POSITIVE_DAY_N_180"),
        "NEG_DAY_180": p.get("NEGATIVE_DAY_N_180"),
        "POS_DAY_300": p.get("POSITIVE_DAY_N_300"),
        "NEG_DAY_300": p.get("NEGATIVE_DAY_N_300"),
        "EX_BEST_180": p.get("EX_BEST_180"),
        "EX_BEST_300": p.get("EX_BEST_300"),
        "EX_TOP3_180": p.get("EX_TOP3_180"),
        "EX_TOP3_300": p.get("EX_TOP3_300"),
        "DROP_TOP_180": p.get("DROP_TOP_SYMBOL_180"),
        "DROP_TOP_300": p.get("DROP_TOP_SYMBOL_300"),
        "DROP_TOP3_180": p.get("DROP_TOP3_180"),
        "DROP_TOP3_300": p.get("DROP_TOP3_300"),
        "VS_E0_180": vs.get("EFFECT_180"),
        "VS_E0_300": vs.get("EFFECT_300"),
        "MULTI_DAY": vs.get("D_MULTI_DAY"),
        "ADVERSE": p.get("EXTREME_ADVERSE_SELECTION"),
        "COVERAGE_OK": p.get("COVERAGE_OK"),
        "EVIDENCE_LIMITED": p.get("EVIDENCE_LIMITED"),
        "MECHANISM_MEMBER": p.get("MECHANISM_MEMBER"),
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    policies = dict(report.get("policies") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V11_SPEC_SHA256: `{req.get('V11_SPEC_SHA256')}`",
        f"V12_SPEC_SHA256: `{req.get('V12_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        f"ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH: `{req.get('ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH')}`",
        "",
        "## E0 parity (Ask0 -> BidH, 126 executable)",
        "",
        f"E0_PARITY: `{req.get('E0_PARITY')}`",
        f"60={_fmt(req.get('E0_FULL_60'))}  180={_fmt(req.get('E0_FULL_180'))}  300={_fmt(req.get('E0_FULL_300'))}",
        "",
        "## Policies",
        "",
    ]
    for aid in POLICY_IDS:
        p = policies.get(aid) or {}
        lines.append(
            f"### {aid}  family={p.get('FAMILY')} eligible={p.get('ELIGIBLE_N')} "
            f"filled={p.get('FILLED_N')} unfilled={p.get('UNFILLED_N')} fill_rate={_fmt(p.get('FILL_RATE'))}"
        )
        lines.append(
            f"- wait mean/median/p75/max={_fmt(p.get('WAIT_TO_FILL_MEAN'))}/"
            f"{_fmt(p.get('WAIT_TO_FILL_MEDIAN'))}/{_fmt(p.get('WAIT_TO_FILL_P75'))}/{_fmt(p.get('WAIT_TO_FILL_MAX'))} "
            f"budget={p.get('DELAY_BUDGET_SEC')}"
        )
        lines.append(
            f"- improve_vs_ask0={_fmt(p.get('ENTRY_PRICE_IMPROVEMENT_VS_ASK0_BPS'))} "
            f"entry_vs_mid0={_fmt(p.get('ENTRY_PRICE_VS_MID0_BPS'))}"
        )
        cond = p.get("CONDITIONAL_FILLED_MARKOUT") or {}
        un = p.get("UNCONDITIONAL_POLICY_MARKOUT") or {}
        ub = p.get("UNCONDITIONAL_FILL_TO_BID") or {}
        lines.append(
            f"- COND FILL->MID 60/180/300={_fmt(cond.get('60'))}/{_fmt(cond.get('180'))}/{_fmt(cond.get('300'))}"
        )
        lines.append(
            f"- UNCOND FILL->MID 60/180/300={_fmt(un.get('60'))}/{_fmt(un.get('180'))}/{_fmt(un.get('300'))}"
        )
        lines.append(
            f"- UNCOND FILL->BID 60/180/300={_fmt(ub.get('60'))}/{_fmt(ub.get('180'))}/{_fmt(ub.get('300'))}"
        )
        fg = p.get("FILLED_GROSS_MID") or {}
        ug = p.get("UNFILLED_GROSS_MID") or {}
        lines.append(
            f"- FILLED_GROSS_MID 60/180/300={_fmt(fg.get('60'))}/{_fmt(fg.get('180'))}/{_fmt(fg.get('300'))}"
        )
        lines.append(
            f"- UNFILLED_GROSS_MID 60/180/300={_fmt(ug.get('60'))}/{_fmt(ug.get('180'))}/{_fmt(ug.get('300'))}"
        )
        lines.append(
            f"- days180 +/−={p.get('POSITIVE_DAY_N_180')}/{p.get('NEGATIVE_DAY_N_180')} "
            f"ex-best180/300={_fmt(p.get('EX_BEST_180'))}/{_fmt(p.get('EX_BEST_300'))} "
            f"drop-top180/300={_fmt(p.get('DROP_TOP_SYMBOL_180'))}/{_fmt(p.get('DROP_TOP_SYMBOL_300'))} "
            f"drop-top3 180/300={_fmt(p.get('DROP_TOP3_180'))}/{_fmt(p.get('DROP_TOP3_300'))}"
        )
        lines.append(
            f"- coverage_ok={p.get('COVERAGE_OK')} evidence_limited={p.get('EVIDENCE_LIMITED')} "
            f"adverse={p.get('EXTREME_ADVERSE_SELECTION')} mechanism_member={p.get('MECHANISM_MEMBER')}"
        )
        lines.append("")
    lines.extend(
        [
            "## Mechanism / selection",
            "",
            f"PASSIVE_BID_MECHANISM_SUPPORTED: `{req.get('PASSIVE_BID_MECHANISM_SUPPORTED')}`",
            f"INSIDE1_MECHANISM_SUPPORTED: `{req.get('INSIDE1_MECHANISM_SUPPORTED')}`",
            f"ENTRY_EXECUTION_MECHANISM_SUPPORTED: `{req.get('ENTRY_EXECUTION_MECHANISM_SUPPORTED')}`",
            f"ENTRY_EXECUTION_EDGE_REPAIRED: `{req.get('ENTRY_EXECUTION_EDGE_REPAIRED')}`",
            f"SELECTED_EXECUTION_POLICY: `{req.get('SELECTED_EXECUTION_POLICY')}`",
            f"PRIMARY_EXECUTION_DEFICIENCY: `{req.get('PRIMARY_EXECUTION_DEFICIENCY')}`",
            "",
            "## Notes",
            "",
            "- Frozen B1: TF1 AND T3 AND V1 Pullback AND RCI9 -80 cross. No Board/PA/Volume/Persistence/PQ3.",
            "- Canonical fill: ASK_CROSS_CONSERVATIVE. No queue. No touch-fill. No reprice. No fallback market.",
            "- PRIMARY: FILL_PRICE -> future MID at signal t0 + 60/180/300. Unfilled contribute 0 on the 126.",
            "- SECONDARY FILL->BID is diagnostic executable markout, not an EXIT.",
            "- 18 AM days burned. TRUE_OOS=false. Not a Runtime candidate.",
            "",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
