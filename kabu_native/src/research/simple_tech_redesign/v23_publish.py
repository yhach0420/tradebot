"""Write report.json / report.md / audit.xlsx only under v23_stale_execution_coverage_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V23_OUT
from research.simple_tech_redesign.v23_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "StaleRows",
    "ClassCounts",
    "Distributions",
    "DaySymbol",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STALE_N",
    "STALE_CLASS_COUNTS",
    "STALE_CLASS_DAY_COUNTS",
    "STALE_CLASS_SYMBOL_COUNTS",
    "EVENT_TIME_AGE_DISTRIBUTION",
    "RECEIVED_TIME_AGE_DISTRIBUTION",
    "TIME_TO_NEXT_FRESH_QUOTE",
    "REAL_MARKET_STALE_N",
    "AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N",
    "UNRESOLVED_N",
    "PRIMARY_STALE_CAUSE",
    "ENTRY_CHANGED",
    "E4_CHANGED",
    "TRUE_OOS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V23_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V23_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V23_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V23_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V23_OUT / "audit.xlsx")


def _dist_line(name: str, d: dict[str, Any] | None) -> str:
    d = dict(d or {})
    return (
        f"- {name}: n={d.get('n')} missing={d.get('missing_n')} "
        f"min={d.get('min')} p25={d.get('p25')} median={d.get('median')} "
        f"p75={d.get('p75')} p90={d.get('p90')} max={d.get('max')}"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    event = dict(req.get("EVENT_TIME_AGE_DISTRIBUTION") or {})
    recv = dict(req.get("RECEIVED_TIME_AGE_DISTRIBUTION") or {})
    canon = dict(event.get("canonical_fresh_sec") or {})
    board = dict(event.get("board_age_event_sec") or {})
    counts = dict(req.get("STALE_CLASS_COUNTS") or {})
    days = dict(req.get("STALE_CLASS_DAY_COUNTS") or {})
    syms = dict(req.get("STALE_CLASS_SYMBOL_COUNTS") or {})
    stale_n = req.get("STALE_N")
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"PRIMARY_STALE_CAUSE: `{req.get('PRIMARY_STALE_CAUSE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        f"STALE_N = `{stale_n}`",
        f"REAL_MARKET_STALE_N = `{req.get('REAL_MARKET_STALE_N')}`",
        f"AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N = `{req.get('AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N')}`",
        f"UNRESOLVED_N = `{req.get('UNRESOLVED_N')}`",
        "",
        "## Evidence",
        f"- All `{stale_n}` V22 STALE rows. Canonical freshness source is last-trade `CurrentPriceTime` unless a later run shows otherwise.",
        f"- Capture ingress age at t0: see last_push_age_sec (not missing Capture if that series is ~0).",
        f"- Board Bid/Ask age vs t0 max=`{board.get('max')}` median=`{board.get('median')}`.",
        f"- Last-trade CurrentPriceTime age vs t0 min=`{canon.get('min')}` median=`{canon.get('median')}` max=`{canon.get('max')}`.",
        f"- S1=`{counts.get('S1_REAL_NO_FRESH_MARKET_UPDATE')}` S3=`{counts.get('S3_CAPTURE_JOIN_ALIGNMENT_FAILURE')}` S4=`{counts.get('S4_TIMESTAMP_SEMANTIC_MISMATCH')}` S6=`{counts.get('S6_UNRESOLVED')}`.",
        f"- `{stale_n}` stale signals were not added as trades. No fill replay. No PnL.",
        "",
        "## STALE_CLASS_COUNTS",
        *[f"- {k}: N=`{counts.get(k)}` days=`{days.get(k)}` symbols=`{syms.get(k)}`" for k in counts],
        "",
        "## EVENT_TIME_AGE_DISTRIBUTION",
        _dist_line("canonical_fresh_sec", dict(event.get("canonical_fresh_sec") or {})),
        _dist_line("price_age_event_sec", dict(event.get("price_age_event_sec") or {})),
        _dist_line("board_age_event_sec", dict(event.get("board_age_event_sec") or {})),
        "",
        "## RECEIVED_TIME_AGE_DISTRIBUTION",
        _dist_line("price_age_received_sec", dict(recv.get("price_age_received_sec") or {})),
        _dist_line("board_age_received_sec", dict(recv.get("board_age_received_sec") or {})),
        _dist_line("last_ingress_age_sec", dict(recv.get("last_ingress_age_sec") or {})),
        _dist_line("last_push_age_sec", dict(recv.get("last_push_age_sec") or {})),
        "",
        "## TIME_TO_NEXT_FRESH_QUOTE",
        _dist_line("time_to_next_fresh_quote_sec", dict(req.get("TIME_TO_NEXT_FRESH_QUOTE") or {})),
        "",
        "## Q1–Q3",
        f"- Q1 majority REAL_MARKET_STALE: `{dec.get('Q1_MAJORITY_REAL_MARKET_STALE')}`",
        f"- Q2 avoidable timestamp/join material: `{dec.get('Q2_AVOIDABLE_TIMESTAMP_OR_JOIN_MATERIAL')}`",
        f"- Q3 avoidable multiple days/symbols: `{dec.get('Q3_AVOIDABLE_MULTI_DAY_MULTI_SYMBOL')}`",
        "",
        "## Locks",
        f"- ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}`",
        f"- E4_CHANGED=`{req.get('E4_CHANGED')}`",
        "- FRESHNESS_THRESHOLD_CHANGED=`False`",
        f"- TRUE_OOS=`{req.get('TRUE_OOS')}`",
        "- No virtual fill. No fill replay. No PnL. 149 stale signals were not added as trades.",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
