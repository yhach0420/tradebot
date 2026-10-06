"""Publish exactly 3 artifacts for E4 no-adverse-price fallback V1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_spec import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    NARRATIVE_CORRECTION,
)

SHEET_ORDER = (
    "summary",
    "identity",
    "source",
    "closeout",
    "groups",
    "price_degrade",
    "portfolio",
    "coverage",
    "concentration",
    "days",
    "unconstrained",
    "answers",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "CANDIDATE_FROZEN": dec.get("CANDIDATE_FROZEN"),
            "NARRATIVE_CORRECTION": NARRATIVE_CORRECTION,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        }
    )
    sheets["identity"] = kv_rows({"DEV": dev.get("identity"), "BURNED_STRESS": fwd.get("identity")})
    sheets["source"] = kv_rows(dict(report.get("source") or {}))
    sheets["closeout"] = kv_rows(dict(report.get("p_primitive_closeout") or {}))
    grow = []
    for cohort, pack in (("DEVELOPMENT", dev), ("BURNED_STRESS", fwd)):
        for name, d in dict(pack.get("groups") or {}).items():
            grow.append({"cohort": cohort, "group": name, **{k: v for k, v in dict(d).items() if k != "trades"}})
    sheets["groups"] = grow
    deg = []
    for cohort, pack in (("DEVELOPMENT", dev), ("BURNED_STRESS", fwd)):
        body = dict(pack.get("price_degradation") or {})
        deg.append({"cohort": cohort, **{k: v for k, v in body.items() if k != "rows"}})
        for r in list(body.get("rows") or []):
            deg.append({"cohort": cohort, **dict(r)})
    sheets["price_degrade"] = deg
    port = []
    for cohort, pack in (("DEVELOPMENT", dev), ("BURNED_STRESS", fwd)):
        for arm in ("control", "treatment", "core_only"):
            port.append({"cohort": cohort, "arm": arm, **dict(pack.get(arm) or {})})
    sheets["portfolio"] = port
    sheets["coverage"] = kv_rows(
        {
            "DEV": {
                "CONTROL_FILL_N": dev.get("CONTROL_FILL_N"),
                "TREATMENT_FILL_N": dev.get("TREATMENT_FILL_N"),
                "FILL_DELTA": dev.get("FILL_DELTA"),
                "ADDED_RETENTION": dev.get("ADDED_RETENTION"),
                "CORE_ONLY": dev.get("CORE_ONLY_CAUSAL_FILL_N"),
                "COVERAGE_COLLAPSED": dev.get("COVERAGE_COLLAPSED"),
            },
            "BURNED": {
                "CONTROL_FILL_N": fwd.get("CONTROL_FILL_N"),
                "TREATMENT_FILL_N": fwd.get("TREATMENT_FILL_N"),
                "FILL_DELTA": fwd.get("FILL_DELTA"),
                "ADDED_RETENTION": fwd.get("ADDED_RETENTION"),
                "COVERAGE_COLLAPSED": fwd.get("COVERAGE_COLLAPSED"),
            },
        }
    )
    sheets["concentration"] = kv_rows({"DEV": dev.get("concentration"), "BURNED_STRESS": fwd.get("concentration")})
    sheets["days"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("days") or [])] + [
        {"cohort": "BURNED_STRESS", **r} for r in list(fwd.get("days") or [])
    ]
    sheets["unconstrained"] = list(dev.get("unconstrained") or []) + [
        {"cohort": "BURNED_STRESS", **r} for r in list(fwd.get("unconstrained") or [])
    ]
    ans = dict(report.get("answers") or {})
    sheets["answers"] = [{"n": k, "answer": ans[k]} for k in sorted(ans, key=lambda x: int(x) if str(x).isdigit() else 999)]
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    for name in SHEET_ORDER:
        if not sheets.get(name):
            sheets[name] = [{"empty": True}]
    return sheets


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    src = dict(report.get("source") or {})
    answers = dict(report.get("answers") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**Candidate:** `{CANDIDATE_ID}`",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**CANDIDATE_FROZEN:** `{dec.get('CANDIDATE_FROZEN')}`",
        "",
        "## NARRATIVE_CORRECTION",
        NARRATIVE_CORRECTION,
        "",
        "## Exact Control execution",
        f"- SOURCE_FILE: `{src.get('SOURCE_FILE')}`",
        f"- SOURCE_FUNCTION: `{src.get('SOURCE_FUNCTION')}`",
        f"- SOURCE_SHA: `{src.get('SOURCE_SHA')}`",
        f"- E4 limit: `{src.get('EXACT_E4_LIMIT_PRICE')}`",
        f"- W5 Ask: `{src.get('EXACT_ASK1_FRESHNESS')}`",
        "",
        "## Control identity",
        f"- DEVELOPMENT: fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        f"- BURNED_STRESS: fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "",
        "## Coverage / portfolio",
        f"- fill Control/Treatment={dev.get('CONTROL_FILL_N')}/{dev.get('TREATMENT_FILL_N')} delta={dev.get('FILL_DELTA')} ADDED retention={_n(dev.get('ADDED_RETENTION'))} collapsed={dev.get('COVERAGE_COLLAPSED')}",
        f"- CORE-only causal fill_n={dev.get('CORE_ONLY_CAUSAL_FILL_N')}",
        f"- DEV PnL Control/Treatment={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))} delta={_n(dev.get('TOTAL_CAUSAL_DELTA'))}",
        f"- DEV PF={(dev.get('control') or {}).get('PF')}/{(dev.get('treatment') or {}).get('PF')}",
        f"- DEV MaxDD={_n((dev.get('control') or {}).get('max_drawdown'))}/{_n((dev.get('treatment') or {}).get('max_drawdown'))}",
        "",
        "## Required answers",
    ]
    for k in sorted(answers, key=lambda x: int(x) if str(x).isdigit() else 999):
        lines.append(f"{k}. {answers[k]}")
    lines += ["", "STOP.", ""]
    return "\n".join(lines)
