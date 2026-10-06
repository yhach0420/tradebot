"""Publish proven failure actionability gate artifacts."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PROVEN_FAILURE_ACTIONABILITY_OUT
from research.simple_tech_redesign.proven_failure_actionability_gate_spec import ANALYSIS_ID

SHEET_ORDER = ("summary", "integrity", "decision", "event_rates", "timing", "crossing", "overlay", "sequences", "loo")


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PROVEN_FAILURE_ACTIONABILITY_OUT.mkdir(parents=True, exist_ok=True)
    for p in PROVEN_FAILURE_ACTIONABILITY_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PROVEN_FAILURE_ACTIONABILITY_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PROVEN_FAILURE_ACTIONABILITY_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PROVEN_FAILURE_ACTIONABILITY_OUT / "audit.xlsx")


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
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    rate_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            rate_rows.append({"cohort": cohort, "pool": pool, **dict(ep)})
    sheets["event_rates"] = rate_rows
    timing_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED", "P_PROFIT_THEN_FAILURE", "P_EARLY_AFTER_BE", "PROTECTED_DIP", "PROTECTED_GOOD"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            timing_rows.append(
                {
                    "cohort": cohort,
                    "pool": pool,
                    "median_be_to_first_below_sec": ep.get("median_be_to_first_below_sec"),
                    "median_first_below_to_reclaim_sec": ep.get("median_first_below_to_reclaim_sec"),
                    "median_reclaim_to_second_below_sec": ep.get("median_reclaim_to_second_below_sec"),
                }
            )
    sheets["timing"] = timing_rows
    cross_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            cross_rows.append(
                {
                    "cohort": cohort,
                    "pool": pool,
                    "median_above_to_below_crossing_n": ep.get("median_above_to_below_crossing_n"),
                    "median_below_to_above_crossing_n": ep.get("median_below_to_above_crossing_n"),
                    "micro_jitter_warn_n": ep.get("micro_jitter_warn_n"),
                }
            )
    sheets["crossing"] = cross_rows
    overlay_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            overlay_rows.append({"cohort": cohort, "pool": pool, "scope": "fired", **dict(ep.get("overlay_fired") or {})})
            overlay_rows.append({"cohort": cohort, "pool": pool, "scope": "not_fired", **dict(ep.get("overlay_not_fired") or {})})
    sheets["overlay"] = overlay_rows
    sheets["sequences"] = list(report.get("sequence_rows") or [])
    loo_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for fam in ("PTF", "P_EARLY"):
            for scope, val in dict(pack.get("loo", {}).get(fam) or {}).items():
                loo_rows.append({"cohort": cohort, "family": fam, "scope": scope, "second_below_rate": val})
    sheets["loo"] = loo_rows
    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"- VERDICT: {dec.get('VERDICT')}",
        f"- CASE: {dec.get('CASE')}",
        f"- PRIMARY_NEXT_MECHANISM: {dec.get('PRIMARY_NEXT_MECHANISM')}",
        f"- DEV BE: {req.get('DEV_BE_N')} FWD BE: {req.get('FWD_BE_N')}",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
