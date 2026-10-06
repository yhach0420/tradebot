"""Publish entry thesis invalidation RCA artifacts."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.entry_thesis_invalidation_rca_spec import ANALYSIS_ID, PREDICATE_INVENTORY, T3_SOURCE
from research.simple_tech_redesign.isolation import ENTRY_THESIS_INVALIDATION_RCA_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "predicates",
    "decision",
    "event_rates",
    "first_invalid",
    "churn",
    "sequences",
    "overlay",
    "loo",
    "closed_overlap",
    "trades",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    ENTRY_THESIS_INVALIDATION_RCA_OUT.mkdir(parents=True, exist_ok=True)
    for p in ENTRY_THESIS_INVALIDATION_RCA_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (ENTRY_THESIS_INVALIDATION_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (ENTRY_THESIS_INVALIDATION_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(ENTRY_THESIS_INVALIDATION_RCA_OUT / "audit.xlsx")


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
        }
    )
    sheets["integrity"] = kv_rows(dict(report.get("integrity") or {}))
    sheets["predicates"] = [dict(p) for p in PREDICATE_INVENTORY] + [dict(T3_SOURCE)]
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    rate_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            rate_rows.append({"cohort": cohort, "pool": pool, **dict(ep)})
    sheets["event_rates"] = rate_rows
    first_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            first_rows.append({"cohort": cohort, "pool": pool, **dict(ep.get("first_invalidated_predicate") or {})})
    sheets["first_invalid"] = first_rows
    churn_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            row = {
                "cohort": cohort,
                "pool": pool,
                "median_thesis_valid_to_invalid_n": ep.get("median_thesis_valid_to_invalid_n"),
                "median_thesis_invalid_to_valid_n": ep.get("median_thesis_invalid_to_valid_n"),
            }
            for pid, pc in dict(ep.get("predicate_churn") or {}).items():
                row[f"{pid}_median_v2i"] = pc.get("median_valid_to_invalid_n")
                row[f"{pid}_median_i2v"] = pc.get("median_invalid_to_valid_n")
            churn_rows.append(row)
    sheets["churn"] = churn_rows
    seq_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            seq_rows.append({"cohort": cohort, "pool": pool, **dict(ep.get("sequence_order") or {})})
    sheets["sequences"] = seq_rows
    overlay_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED", "PROTECTED_DIP", "PROTECTED_GOOD"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            overlay_rows.append({"cohort": cohort, "pool": pool, **dict(ep.get("overlay") or {})})
    sheets["overlay"] = overlay_rows
    loo_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        loo_rows.append({"cohort": cohort, **dict(pack.get("loo_multi_invalid") or {})})
    sheets["loo"] = loo_rows
    closed_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        closed_rows.append({"cohort": cohort, **dict(pack.get("closed_mechanism") or {})})
    sheets["closed_overlap"] = closed_rows
    sheets["trades"] = list(report.get("trade_audit") or [])
    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    integ = dict(report.get("integrity") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    dev_fail = dict(dev.get("by_pool", {}).get("FAILURE") or {})
    dev_prot = dict(dev.get("by_pool", {}).get("PROTECTED") or {})
    def _fmt_rate(v: Any) -> str:
        return f"{float(v):.3f}" if v is not None else "n/a"
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        "",
        "## Identity",
        f"- DEV fills={integ.get('dev_fill_n')} CORE/ADDED={integ.get('dev_core_n')}/{integ.get('dev_added_n')} PnL={integ.get('dev_pnl')}",
        f"- FWD fills={integ.get('fwd_fill_n')} CORE/ADDED={integ.get('fwd_core_n')}/{integ.get('fwd_added_n')} PnL={integ.get('fwd_pnl')}",
        f"- Entry predicate parity OK n={integ.get('entry_parity_ok_n')}",
        "",
        "## T3 Predicate Inventory",
    ]
    for p in PREDICATE_INVENTORY:
        lines.append(f"- **{p['id']}** `{p['function']}` — {p['condition']}")
    lines.extend(
        [
            "",
            "## DEV FAILURE vs PROTECTED (multi-invalid)",
            f"- FAILURE multi-invalid rate = {_fmt_rate(dev_fail.get('multi_invalid_rate'))} (n={dev_fail.get('n')})",
            f"- PROTECTED multi-invalid rate = {_fmt_rate(dev_prot.get('multi_invalid_rate'))} (n={dev_prot.get('n')})",
            f"- delta = {_fmt_rate(dev.get('failure_vs_protected_delta', {}).get('multi_invalid_rate'))}",
            "",
            "## FWD directional check",
            f"- FWD delta multi-invalid = {_fmt_rate(fwd.get('failure_vs_protected_delta', {}).get('multi_invalid_rate'))}",
            "",
            f"**PRIMARY_NEXT_MECHANISM:** `{dec.get('PRIMARY_NEXT_MECHANISM')}`",
            f"**NEW_EXIT_RULE:** {dec.get('NEW_EXIT_RULE')}",
            f"**CANDIDATE_FROZEN:** {dec.get('CANDIDATE_FROZEN')}",
            "",
            f"**NEXT:** {dec.get('NEXT')}",
            "",
            "STOP.",
        ]
    )
    return "\n".join(lines) + "\n"
