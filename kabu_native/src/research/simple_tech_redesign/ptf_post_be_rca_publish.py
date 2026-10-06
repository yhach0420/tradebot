"""Publish PTF post-BE RCA artifacts."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PTF_POST_BE_RCA_OUT
from research.simple_tech_redesign.ptf_post_be_rca_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "integrity",
    "decision",
    "class_counts",
    "comparisons",
    "sequences",
    "concentration",
    "loo",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PTF_POST_BE_RCA_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in PTF_POST_BE_RCA_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PTF_POST_BE_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PTF_POST_BE_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PTF_POST_BE_RCA_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "analysis_id": ANALYSIS_ID,
            "verdict": dec.get("VERDICT"),
            "case": dec.get("CASE"),
            "primary_next_mechanism": dec.get("PRIMARY_NEXT_MECHANISM"),
            "primary_next_exit_target": dec.get("PRIMARY_NEXT_EXIT_TARGET"),
        }
    )
    sheets["integrity"] = kv_rows(dict(report.get("integrity") or {}))
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    sheets["class_counts"] = [
        {"cohort": "DEVELOPMENT", **dict(dev.get("class_counts") or {})},
        {"cohort": "FORWARD_BURNED", **dict(fwd.get("class_counts") or {})},
    ]
    cmp_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for cmp_name in ("ptf_vs_good", "ptf_vs_dip", "ptf_vs_p_early"):
            cm = dict(pack.get(cmp_name) or {})
            for mk, mv in dict(cm.get("metrics") or {}).items():
                cmp_rows.append({"cohort": cohort, "comparison": cmp_name, "metric": mk, **dict(mv)})
    sheets["comparisons"] = cmp_rows
    sheets["sequences"] = list(report.get("sequence_rows") or [])
    conc = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        conc.append({"cohort": cohort, **dict(pack.get("ptf_concentration") or {})})
    sheets["concentration"] = conc
    loo_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        lo = dict(pack.get("loo") or {})
        for scope in ("full", "leave_one_top_day", "leave_one_top_symbol"):
            loo_rows.append({"cohort": cohort, "scope": scope, **dict(lo.get(scope) or {})})
    sheets["loo"] = loo_rows
    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        "## Verdict",
        f"- CASE: {dec.get('CASE')}",
        f"- VERDICT: {dec.get('VERDICT')}",
        f"- PRIMARY_NEXT_MECHANISM: {dec.get('PRIMARY_NEXT_MECHANISM')}",
        f"- PRIMARY_NEXT_EXIT_TARGET: {dec.get('PRIMARY_NEXT_EXIT_TARGET')}",
        f"- NEXT: {dec.get('NEXT')}",
        "",
        "## BE population",
        f"- DEV BE: {req.get('DEV_BE_N')}",
        f"- FWD BE: {req.get('FWD_BE_N')}",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
