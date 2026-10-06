"""Publish exactly 3 artifacts: report.json, report.md, audit.xlsx."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.isolation import OUT
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "identity",
    "source_pin",
    "activation_alias",
    "simple_tech",
    "p0_reuse",
    "p1_reuse",
    "inventory",
    "spot",
    "cohorts",
    "robustness",
    "answers",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    pin = dict(report.get("source_pin") or {})
    alias = dict(report.get("activation_alias") or {})
    ident = dict(report.get("identity") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "EVALUATION_TARGET": report.get("EVALUATION_TARGET"),
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "PIN_MODE": pin.get("PIN_MODE"),
            "FULL_N": (report.get("inventory_summary") or {}).get("FULL_N"),
            "SIZING_RESEARCH_ALLOWED": dec.get("SIZING_RESEARCH_ALLOWED"),
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": dec.get("NEW_ENTRY_FAMILY_DESIGN_ALLOWED"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
        }
    )
    sheets["identity"] = kv_rows(ident)
    sheets["source_pin"] = list(pin.get("rows") or []) or kv_rows(pin)
    roles = dict(alias.get("roles") or {})
    sheets["activation_alias"] = [
        {"name": k, "sha": (v or {}).get("sha"), "role": (v or {}).get("role"), "source": (v or {}).get("source")}
        for k, v in roles.items()
    ] or kv_rows(alias)
    sheets["simple_tech"] = kv_rows(dict(report.get("simple_tech_closure") or {}))
    p0 = dict(report.get("p0_reuse") or {})
    sheets["p0_reuse"] = list(p0.get("P0_4_PER_DAY") or []) or kv_rows(p0)
    p1 = dict(report.get("p1_reuse") or {})
    sheets["p1_reuse"] = list(p1.get("sha_rows") or []) or kv_rows({k: p1.get(k) for k in p1 if k not in {"trades", "reference_trades", "all_daily"}})
    sheets["inventory"] = list(report.get("inventory") or []) or [{"empty": True}]
    sheets["spot"] = kv_rows(dict(report.get("spot") or {}))
    coh = dict(report.get("cohorts") or {})
    sheets["cohorts"] = [
        {"cohort": k, **{kk: vv for kk, vv in dict(v).items() if kk not in {"daily_pnl", "day_list"}}}
        for k, v in coh.items()
    ] or [{"empty": True}]
    sheets["robustness"] = kv_rows(dict((report.get("extension") or {}).get("robustness") or {}))
    ans = dict(report.get("answers") or {})
    sheets["answers"] = [{"n": k, "answer": ans[k]} for k in [str(i) for i in range(1, 69)] if k in ans]
    sheets["decision"] = kv_rows(dec)
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    for name in SHEET_ORDER:
        if not sheets.get(name):
            sheets[name] = [{"empty": True}]
    return sheets


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    pin = dict(report.get("source_pin") or {})
    inv = dict(report.get("inventory_summary") or {})
    ans = dict(report.get("answers") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{dec.get('VERDICT')}** CASE {dec.get('CASE')}",
        "",
        f"EVALUATION_TARGET={report.get('EVALUATION_TARGET')}",
        f"PIN_MODE={pin.get('PIN_MODE')}",
        f"FULL_N={inv.get('FULL_N')} PARTIAL_N={inv.get('PARTIAL_N')} UNIVERSE_UNRESOLVED_N={inv.get('UNIVERSE_UNRESOLVED_N')}",
        f"SIZING_RESEARCH_ALLOWED={_n(dec.get('SIZING_RESEARCH_ALLOWED'))}",
        f"NEW_ENTRY_FAMILY_DESIGN_ALLOWED={_n(dec.get('NEW_ENTRY_FAMILY_DESIGN_ALLOWED'))}",
        f"V1R_RESEARCH_PRIORITY_MAINTAINED={_n(dec.get('V1R_RESEARCH_PRIORITY_MAINTAINED'))}",
        "TRUE_OOS=false CERTIFIED=false FUTURE_DATA_USED=false",
        "submit/cancel/live=0/0/0",
        "",
        "## Inventory",
    ]
    for r in list(report.get("inventory") or []):
        lines.append(
            f"- {r.get('date')} class={r.get('capture_class')} uni={r.get('UNIVERSE_SOURCE')} "
            f"n={r.get('UNIVERSE_N')} same_day={r.get('SAME_DAY')} eligible={r.get('replay_eligible')} "
            f"am={r.get('am_coverage')} pm={r.get('pm_coverage')} dropped={r.get('dropped_event_count')}"
        )
    lines.extend(["", "## Required answers", ""])
    for k in [str(i) for i in range(1, 69)]:
        if k in ans:
            lines.append(f"{k}. {ans[k]}")
    lines.extend(["", "STOP.", ""])
    return "\n".join(lines)
