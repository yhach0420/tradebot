"""Publish exactly 3 artifacts for SIMPLE_TECH_STRATEGY_LEVEL_ENTRY_REBASE_V1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_spec import ANALYSIS_ID, ELIGIBLE_ENTRY_IDS

SHEET_ORDER = (
    "summary",
    "inventory",
    "excluded",
    "candidates",
    "gates",
    "reuse",
    "selection",
    "burned",
    "occupancy",
    "answers",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    reuse = dict(report.get("reuse") or {})
    sel = dict(report.get("selection") or {})
    occ = dict(report.get("occupancy") or {})
    burned = dict(report.get("burned") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "ENTRY_BASE_CANDIDATE_FROZEN": dec.get("ENTRY_BASE_CANDIDATE_FROZEN"),
            "CURRENT_T3_STACK_CLOSED": dec.get("CURRENT_T3_STACK_CLOSED"),
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": dec.get("CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED"),
            "REUSED_RULE_N": reuse.get("REUSED_RULE_N"),
            "RECOMPUTED_RULE_N": reuse.get("RECOMPUTED_RULE_N"),
            "QUALIFIED_N": sel.get("n"),
            "SELECTED_ENTRY_ID": sel.get("SELECTED_ENTRY_ID"),
            "PNL_USED_FOR_SELECTION": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        }
    )
    inv = list(report.get("inventory") or [])
    sheets["inventory"] = inv or [{"empty": True}]
    sheets["excluded"] = [r for r in inv if not bool(r.get("eligible"))] or [{"empty": True}]
    cand_rows = []
    for r in list(report.get("candidates") or []):
        m = dict(r.get("metrics") or {})
        cand_rows.append(
            {
                "ENTRY_ID": r.get("ENTRY_ID"),
                "qualified": r.get("qualified"),
                "SIGNAL_N": m.get("SIGNAL_N"),
                "EXECUTION_EVALUABLE_N": m.get("EXECUTION_EVALUABLE_N"),
                "MEAN_60": m.get("MEAN_60"),
                "MEDIAN_60": m.get("MEDIAN_60"),
                "MEAN_180": m.get("MEAN_180"),
                "MEDIAN_180": m.get("MEDIAN_180"),
                "MEAN_300": m.get("MEAN_300"),
                "MEDIAN_300": m.get("MEDIAN_300"),
                "POSITIVE_DAY_N_180": m.get("POSITIVE_DAY_N_180"),
                "NEGATIVE_DAY_N_180": m.get("NEGATIVE_DAY_N_180"),
                "POSITIVE_DAY_N_300": m.get("POSITIVE_DAY_N_300"),
                "NEGATIVE_DAY_N_300": m.get("NEGATIVE_DAY_N_300"),
                "EX_BEST_DAY_MEAN_180": m.get("EX_BEST_DAY_MEAN_180"),
                "EX_BEST_DAY_MEAN_300": m.get("EX_BEST_DAY_MEAN_300"),
                "DROP_TOP_SYMBOL_MEAN_180": m.get("DROP_TOP_SYMBOL_MEAN_180"),
                "DROP_TOP_SYMBOL_MEAN_300": m.get("DROP_TOP_SYMBOL_MEAN_300"),
                "TOP_DAY_SHARE": m.get("TOP_DAY_SHARE"),
                "TOP_SYMBOL_SHARE": m.get("TOP_SYMBOL_SHARE"),
                "TOP_SYMBOL_CONTRIBUTION": m.get("TOP_SYMBOL_CONTRIBUTION"),
                "MFE300_MEAN": m.get("MFE300_MEAN"),
                "MAE300_MEAN": m.get("MAE300_MEAN"),
                "REUSE_PRIOR_METRICS": m.get("REUSE_PRIOR_METRICS"),
                "ARTIFACT": m.get("ARTIFACT"),
            }
        )
    sheets["candidates"] = cand_rows or [{"empty": True}]
    gate_rows = []
    for r in list(report.get("candidates") or []):
        g = dict(r.get("gates") or {})
        gate_rows.append({"ENTRY_ID": r.get("ENTRY_ID"), "qualified": r.get("qualified"), **g})
    sheets["gates"] = gate_rows or [{"empty": True}]
    sheets["reuse"] = kv_rows(reuse)
    sheets["selection"] = kv_rows(sel)
    sheets["burned"] = kv_rows(burned)
    sheets["occupancy"] = kv_rows(occ)
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
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    reuse = dict(report.get("reuse") or {})
    sel = dict(report.get("selection") or {})
    ans = dict(report.get("answers") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{dec.get('VERDICT')}** CASE {dec.get('CASE')}",
        "",
        f"ENTRY_BASE_CANDIDATE_FROZEN={dec.get('ENTRY_BASE_CANDIDATE_FROZEN')}",
        f"CURRENT_T3_STACK_CLOSED={dec.get('CURRENT_T3_STACK_CLOSED')}",
        f"CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED={dec.get('CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED')}",
        f"REUSED_RULE_N={reuse.get('REUSED_RULE_N')} RECOMPUTED_RULE_N={reuse.get('RECOMPUTED_RULE_N')}",
        f"QUALIFIED_N={sel.get('n')} SELECTED={sel.get('SELECTED_ENTRY_ID')}",
        "PNL_USED_FOR_SELECTION=false",
        "",
        "## Eligible pool",
        ", ".join(ELIGIBLE_ENTRY_IDS),
        "",
        "## Candidates",
    ]
    for r in list(report.get("candidates") or []):
        m = dict(r.get("metrics") or {})
        lines.append(
            f"- {r.get('ENTRY_ID')} qual={r.get('qualified')} sig={m.get('SIGNAL_N')} exe={m.get('EXECUTION_EVALUABLE_N')} "
            f"mean180={_n(m.get('MEAN_180'))} med180={_n(m.get('MEDIAN_180'))} "
            f"mean300={_n(m.get('MEAN_300'))} med300={_n(m.get('MEDIAN_300'))} "
            f"day180={m.get('POSITIVE_DAY_N_180')}/{m.get('NEGATIVE_DAY_N_180')} "
            f"day300={m.get('POSITIVE_DAY_N_300')}/{m.get('NEGATIVE_DAY_N_300')}"
        )
    lines.extend(["", "## Required answers", ""])
    for k in [str(i) for i in range(1, 51)]:
        if k in ans:
            lines.append(f"{k}. {ans[k]}")
    lines.extend(["", "STOP.", ""])
    return "\n".join(lines)
