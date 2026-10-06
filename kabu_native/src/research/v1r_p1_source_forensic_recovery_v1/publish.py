"""Publish report.json / report.md / audit.xlsx only. recovered_source/ only if byte-exact."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.v1r_p1_source_forensic_recovery_v1.isolation import OUT
from research.v1r_p1_source_forensic_recovery_v1.spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "existence",
    "searches",
    "semantic",
    "calibration",
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
    keep = {"report.json", "report.md", "audit.xlsx"}
    for p in OUT.iterdir():
        if p.is_file() and p.name not in keep:
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
    searches = dict(report.get("searches") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "RECOVERY_MODE": dec.get("RECOVERY_MODE"),
            "BYTE_EXACT": (report.get("recovery") or {}).get("BYTE_EXACT_RECOVERED"),
            "BEHAVIORAL_EQUIVALENCE": (report.get("calibration") or {}).get("BEHAVIORAL_EQUIVALENCE"),
            "material_n": (report.get("semantic_diff") or {}).get("material_n"),
        }
    )
    sheets["existence"] = kv_rows(dict(report.get("existence_proof") or {}))
    search_rows = []
    for k, v in searches.items():
        search_rows.append(
            {
                "source": k,
                "searched": (v or {}).get("searched"),
                "candidate_n": (v or {}).get("candidate_n"),
                "hash_match_n": (v or {}).get("hash_match_n"),
                "evidence": (v or {}).get("evidence"),
            }
        )
    sheets["searches"] = search_rows or [{"empty": True}]
    sheets["semantic"] = list((report.get("semantic_diff") or {}).get("diffs") or []) or [{"empty": True}]
    sheets["calibration"] = list((report.get("calibration") or {}).get("rows") or []) or [{"empty": True}]
    ans = dict(report.get("answers") or {})
    sheets["answers"] = [{"n": k, "answer": ans[k]} for k in [str(i) for i in range(1, 39)] if k in ans]
    sheets["decision"] = kv_rows(dec)
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    for name in SHEET_ORDER:
        if not sheets.get(name):
            sheets[name] = [{"empty": True}]
    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    ans = dict(report.get("answers") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{dec.get('VERDICT')}** CASE {dec.get('CASE')}",
        "",
        f"RECOVERY_MODE={dec.get('RECOVERY_MODE')}",
        f"BYTE_IDENTICAL={dec.get('BYTE_IDENTICAL')}",
        f"BEHAVIORALLY_IDENTICAL_ON_P1_FULL14={dec.get('BEHAVIORALLY_IDENTICAL_ON_P1_FULL14')}",
        f"extension_replay_allowed={dec.get('extension_replay_allowed')}",
        f"NEW_ENTRY_FAMILY_DESIGN_ALLOWED={dec.get('NEW_ENTRY_FAMILY_DESIGN_ALLOWED')}",
        "submit/cancel/live=0/0/0",
        "",
        "## Required answers",
        "",
    ]
    for k in [str(i) for i in range(1, 39)]:
        if k in ans:
            lines.append(f"{k}. {ans[k]}")
    lines.extend(["", "STOP.", ""])
    return "\n".join(lines)
