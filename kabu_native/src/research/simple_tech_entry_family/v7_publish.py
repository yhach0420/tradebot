"""Write report.json / report.md / audit.xlsx only under v7_timeframe_role_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.isolation import V7_OUT
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_entry_family.v7_spec import ALL_ROLES, ANALYSIS_ID, TF_IDS

SHEET_ORDER = (
    "Precommit",
    "Population",
    "Bar_Alignment",
    "Trend",
    "Pullback",
    "RCI",
    "Price_Action",
    "Volume",
    "Volume_Nested",
    "Preferred_Scale",
    "Good6",
    "Board_Audit",
    "Day_Stability",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "PARENT_SPEC_SHA256",
    "V7_SPEC_SHA256",
    "V1_PARITY",
    "TREND_PREFERRED_SCALE",
    "PULLBACK_PREFERRED_SCALE",
    "RCI_PREFERRED_SCALE",
    "PRICE_ACTION_PREFERRED_SCALE",
    "VOLUME_PREFERRED_SCALE",
    "ROLE_SCALE_CONFLICT",
    "MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)

ROLE_SHEET = {
    "TREND": "Trend",
    "PULLBACK": "Pullback",
    "RCI": "RCI",
    "PRICE_ACTION": "Price_Action",
    "VOLUME": "Volume",
    "VOLUME_NESTED": "Volume_Nested",
}


def _fmt_pack(tf: str, role: str, st: dict[str, Any]) -> dict[str, Any]:
    p = st.get("PASS") or {}
    f = st.get("FAIL") or {}
    return {
        "tf": tf,
        "role": role,
        "PASS_N": st.get("PASS_N"),
        "FAIL_N": st.get("FAIL_N"),
        "EXE_PASS_N": st.get("EXECUTABLE_PASS_N"),
        "EXE_FAIL_N": st.get("EXECUTABLE_FAIL_N"),
        "PASS_60_MEAN": p.get("MARKOUT_60_MEAN"),
        "PASS_60_MEDIAN": p.get("MARKOUT_60_MEDIAN"),
        "PASS_60_POS": p.get("POSITIVE_RATE_60"),
        "FAIL_60_MEAN": f.get("MARKOUT_60_MEAN"),
        "FAIL_60_MEDIAN": f.get("MARKOUT_60_MEDIAN"),
        "FAIL_60_POS": f.get("POSITIVE_RATE_60"),
        "PASS_180_MEAN": p.get("MARKOUT_180_MEAN"),
        "PASS_180_MEDIAN": p.get("MARKOUT_180_MEDIAN"),
        "PASS_180_POS": p.get("POSITIVE_RATE_180"),
        "FAIL_180_MEAN": f.get("MARKOUT_180_MEAN"),
        "FAIL_180_MEDIAN": f.get("MARKOUT_180_MEDIAN"),
        "FAIL_180_POS": f.get("POSITIVE_RATE_180"),
        "PASS_300_MEAN": p.get("MARKOUT_300_MEAN"),
        "PASS_300_MEDIAN": p.get("MARKOUT_300_MEDIAN"),
        "PASS_300_POS": p.get("POSITIVE_RATE_300"),
        "FAIL_300_MEAN": f.get("MARKOUT_300_MEAN"),
        "FAIL_300_MEDIAN": f.get("MARKOUT_300_MEDIAN"),
        "FAIL_300_POS": f.get("POSITIVE_RATE_300"),
        "PASS_MFE_MEDIAN": p.get("MFE_MEDIAN"),
        "PASS_MAE_MEDIAN": p.get("MAE_MEDIAN"),
        "FAIL_MFE_MEDIAN": f.get("MFE_MEDIAN"),
        "FAIL_MAE_MEDIAN": f.get("MAE_MEDIAN"),
        "EFFECT_180": st.get("EFFECT_180"),
        "EFFECT_300": st.get("EFFECT_300"),
        "DAY_POS": st.get("DAY_POS"),
        "DAY_NEG": st.get("DAY_NEG"),
        "MULTI_DAY": st.get("MULTI_DAY"),
        "EX_BEST_STILL_IMPROVES": st.get("EX_BEST_STILL_IMPROVES"),
        "DROP_TOP_STILL_IMPROVES": st.get("DROP_TOP_STILL_IMPROVES"),
        "COVERAGE_OK": st.get("COVERAGE_OK"),
        "ROLE_SUPPORTED": st.get("ROLE_SUPPORTED"),
        "PA_VS_PRE_180": st.get("PA_VS_PRE_180"),
        "PA_VS_PRE_300": st.get("PA_VS_PRE_300"),
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V7_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V7_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V7_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V7_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V7_OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"PARENT_SPEC_SHA256: `{req.get('PARENT_SPEC_SHA256')}`",
        f"V7_SPEC_SHA256: `{req.get('V7_SPEC_SHA256')}`",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        f"V1_PARITY: `{req.get('V1_PARITY')}`",
        "",
        "## Preferred scale (per role, not a single winner)",
        "",
        f"TREND_PREFERRED_SCALE: `{req.get('TREND_PREFERRED_SCALE')}`",
        f"PULLBACK_PREFERRED_SCALE: `{req.get('PULLBACK_PREFERRED_SCALE')}`",
        f"RCI_PREFERRED_SCALE: `{req.get('RCI_PREFERRED_SCALE')}`",
        f"PRICE_ACTION_PREFERRED_SCALE: `{req.get('PRICE_ACTION_PREFERRED_SCALE')}`",
        f"VOLUME_PREFERRED_SCALE: `{req.get('VOLUME_PREFERRED_SCALE')}`",
        f"ROLE_SCALE_CONFLICT: `{req.get('ROLE_SCALE_CONFLICT')}`",
        f"MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED: `{req.get('MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED')}`",
        "",
        "## Role × timeframe (Ask→Bid 60/180/300 sec, horizons not rescaled)",
        "",
    ]
    roles = dict(report.get("roles") or {})
    for role in list(ALL_ROLES) + ["VOLUME_NESTED"]:
        lines.append(f"### {role}")
        lines.append("")
        for tf in TF_IDS:
            st = ((roles.get(role) or {}).get(tf) or {})
            p = st.get("PASS") or {}
            f = st.get("FAIL") or {}
            lines.append(
                f"- {tf}: PASS/FAIL N={st.get('PASS_N')}/{st.get('FAIL_N')} "
                f"exe={st.get('EXECUTABLE_PASS_N')}/{st.get('EXECUTABLE_FAIL_N')} "
                f"mean60={p.get('MARKOUT_60_MEAN')}/{f.get('MARKOUT_60_MEAN')} "
                f"mean180={p.get('MARKOUT_180_MEAN')}/{f.get('MARKOUT_180_MEAN')} "
                f"mean300={p.get('MARKOUT_300_MEAN')}/{f.get('MARKOUT_300_MEAN')} "
                f"pos180={p.get('POSITIVE_RATE_180')}/{f.get('POSITIVE_RATE_180')} "
                f"day={st.get('DAY_POS')}/{st.get('DAY_NEG')} "
                f"ex-best={st.get('EX_BEST_STILL_IMPROVES')} drop-top={st.get('DROP_TOP_STILL_IMPROVES')} "
                f"supported={st.get('ROLE_SUPPORTED')}"
            )
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Native timeframe comparison: EMA9/EMA21/BB20/RCI9 on each TF's own bars. Not equal calendar lookback.",
            "- No mixed-TF strategy this run. No period retune. No C14. No EXIT. No Persistence hard gate.",
            "- Board is event-level support/veto at decision time and is not a TF role.",
            "- volume_persistence_300s is a TF-clock diagnostic only.",
            "- GOOD6 is diagnostic only and is not a gate.",
            "- 18 AM days are burned development. TRUE_OOS=false. Selected scales are not Runtime candidates.",
            "",
            f"NON_INTERFERENCE_PASS: {req.get('NON_INTERFERENCE_PASS')}",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
