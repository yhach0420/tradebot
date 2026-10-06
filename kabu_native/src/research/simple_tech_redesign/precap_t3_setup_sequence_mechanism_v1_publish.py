"""Publish 3 artifacts for T3 P2 pullback-touch-age sequence V1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRECAP_T3_SETUP_SEQUENCE_V1_OUT
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "identity",
    "sequence",
    "daily_rho",
    "age_econ",
    "qualify",
    "diagnostics",
    "capture",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in PRECAP_T3_SETUP_SEQUENCE_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRECAP_T3_SETUP_SEQUENCE_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRECAP_T3_SETUP_SEQUENCE_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        rows = sheets.get(name) or [{"empty": True}]
        clean = [{k: _cell(v) for k, v in dict(r).items()} for r in rows]
        if first:
            ws = wb.active
            ws.title = name[:31]
            first = False
        else:
            ws = wb.create_sheet(name[:31])
        _sheet(ws, clean)
    wb.save(PRECAP_T3_SETUP_SEQUENCE_V1_OUT / "audit.xlsx")


def _daily_line(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "evaluable_day_n": d.get("evaluable_day_n"),
        "positive_rho_day_n": d.get("positive_rho_day_n"),
        "negative_rho_day_n": d.get("negative_rho_day_n"),
        "zero_rho_day_n": d.get("zero_rho_day_n"),
        "median_daily_rho": d.get("median_daily_rho"),
        "IQR_daily_rho": d.get("IQR_daily_rho"),
        "direction": d.get("direction"),
        "unevaluable": d.get("unevaluable"),
    }


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    daily = dict(ev.get("daily") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "PRIMARY_FIELD": "P2_TOUCH_AGE_BARS",
            "PRIMARY_NEXT_MECHANISM": dec.get("PRIMARY_NEXT_MECHANISM"),
            "qualify": dec.get("qualify"),
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": "20260902",
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "TRUE_OOS": False,
        }
    )
    sheets["identity"] = kv_rows({"pool": ev.get("pool_identity"), "AB": ev.get("identity_AB_as_development"), "C": ev.get("identity_C_as_forward_burned")})
    sheets["sequence"] = kv_rows(dict(ev.get("sequence_integrity") or {}))
    daily_rows = []
    for bk, pack in daily.items():
        for row in list(pack.get("daily") or []):
            daily_rows.append({"block": bk, **row})
    sheets["daily_rho"] = daily_rows or [{"empty": True}]
    econ_rows = []
    for bk, ages in dict(ev.get("economics") or {}).items():
        for age, pack in dict(ages).items():
            econ_rows.append({"block": bk, "age": age, **dict(pack)})
    sheets["age_econ"] = econ_rows or [{"empty": True}]
    sheets["qualify"] = kv_rows(dict(ev.get("qualify_gates") or {}))
    sheets["diagnostics"] = kv_rows(
        {
            "age_distribution": ev.get("age_distribution"),
            "no_fill_age": ev.get("no_fill_age_distribution"),
            "touch_count": ev.get("touch_count_diagnostic"),
            "geometry": ev.get("geometry_diagnostic"),
            "concentration": ev.get("concentration"),
            "arrival": ev.get("arrival_strata"),
            "admitted": ev.get("admitted"),
            "cap_blocked": ev.get("cap_blocked"),
            "core": ev.get("core"),
            "added": ev.get("added"),
        }
    )
    sheets["capture"] = kv_rows(dict(report.get("capture_reporting_state") or {}))
    sheets["decision"] = kv_rows(dec)
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    ans = dict(report.get("answers") or {})
    daily = dict(ev.get("daily") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        "**PRIMARY:** `P2_TOUCH_AGE_BARS` · hypothesis `OLDER_PULLBACK_IS_WORSE` (not flipped)",
        f"**PRIMARY_NEXT_MECHANISM:** `{dec.get('PRIMARY_NEXT_MECHANISM')}`",
        "**ABSOLUTE_FEATURE_FAMILY_CLOSED:** true",
        "**TRUE_OOS:** false · **PROSPECTIVE_HARVEST_SUSPENDED:** true",
        "",
        "## Daily Spearman",
    ]
    for bk in ("BLOCK_A_DISCOVERY", "BLOCK_B_INTERNAL_STABILITY", "BLOCK_C_BURNED_STRESS"):
        lines.append(f"- {bk}: {_daily_line(dict(daily.get(bk) or {}))}")
    lines.extend(["", "## QUALIFY gates", str(ev.get("qualify_gates")), "", "## Mandatory answers"])
    for i in range(1, 31):
        lines.append(f"{i}. {ans.get(str(i), '')}")
    lines.extend(["", f"NEXT: {dec.get('NEXT')}", "", "STOP.", ""])
    return "\n".join(lines)
