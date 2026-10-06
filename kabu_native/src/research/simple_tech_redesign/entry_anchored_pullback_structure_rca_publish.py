"""Publish entry-anchored pullback structure RCA artifacts."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_spec import ANALYSIS_ID, P2_SOURCE
from research.simple_tech_redesign.isolation import ENTRY_ANCHORED_PULLBACK_RCA_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "reference",
    "decision",
    "event_rates",
    "timing",
    "distance",
    "executability",
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
    ENTRY_ANCHORED_PULLBACK_RCA_OUT.mkdir(parents=True, exist_ok=True)
    for p in ENTRY_ANCHORED_PULLBACK_RCA_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (ENTRY_ANCHORED_PULLBACK_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (ENTRY_ANCHORED_PULLBACK_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(ENTRY_ANCHORED_PULLBACK_RCA_OUT / "audit.xlsx")


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
    sheets["reference"] = kv_rows(dict(P2_SOURCE))
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    rate_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            rate_rows.append(
                {
                    "cohort": cohort,
                    "pool": pool,
                    "n": ep.get("n"),
                    "floor_break_n": ep.get("floor_break_n"),
                    "floor_break_rate": ep.get("floor_break_rate"),
                    "upside_break_n": ep.get("upside_break_n"),
                    "upside_break_rate": ep.get("upside_break_rate"),
                    "floor_break_first_n": ep.get("floor_break_first_n"),
                    "floor_break_first_rate": ep.get("floor_break_first_rate"),
                    "upside_break_first_n": ep.get("upside_break_first_n"),
                    "upside_break_first_rate": ep.get("upside_break_first_rate"),
                    "same_bar_n": ep.get("same_bar_n"),
                    "same_bar_rate": ep.get("same_bar_rate"),
                    "neither_n": ep.get("neither_n"),
                    "neither_rate": ep.get("neither_rate"),
                }
            )
    sheets["event_rates"] = rate_rows
    timing_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED", "PROTECTED_DIP", "PROTECTED_GOOD"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            t = dict(ep.get("timing") or {})
            timing_rows.append({"cohort": cohort, "pool": pool, **{k: v for k, v in t.items()}})
    sheets["timing"] = timing_rows
    dist_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            dist_rows.append({"cohort": cohort, "pool": pool, **dict(ep.get("distance") or {})})
    sheets["distance"] = dist_rows
    exe_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool in ("FAILURE", "PROTECTED"):
            ep = dict(pack.get("by_pool", {}).get(pool) or {})
            exe_rows.append({"cohort": cohort, "pool": pool, **dict(ep.get("executability") or {})})
    sheets["executability"] = exe_rows
    seq_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for pool, ep in dict(pack.get("by_pool") or {}).items():
            seq_rows.append({"cohort": cohort, "pool": pool, **dict(ep.get("sequence") or {})})
    sheets["sequences"] = seq_rows
    overlay_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for lab, ov in dict(pack.get("sequence_overlay_all") or {}).items():
            overlay_rows.append({"cohort": cohort, "sequence": lab, **dict(ov)})
    sheets["overlay"] = overlay_rows
    loo_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        loo_rows.append({"cohort": cohort, **dict(pack.get("loo_floor_break_first") or {})})
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
    dip = dict(dev.get("by_pool", {}).get("PROTECTED_DIP") or {})

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
        f"- Signal-reference parity OK n={integ.get('signal_reference_ok_n')}",
        "",
        "## SETUP reference",
        f"- P2 `{P2_SOURCE['function']}` last {P2_SOURCE['lookback_bars']} completed 1m bars at T3 signal t0",
        f"- SETUP_LOW = min(Low of those bars); SETUP_HIGH = max(High of those bars)",
        f"- Events: completed-bar Close vs frozen reference (no buffer / persistence)",
        "",
        "## DEV FLOOR_BREAK_FIRST",
        f"- FAILURE = {_fmt_rate(dev_fail.get('floor_break_first_rate'))} (n={dev_fail.get('n')})",
        f"- PROTECTED = {_fmt_rate(dev_prot.get('floor_break_first_rate'))} (n={dev_prot.get('n')})",
        f"- DIP = {_fmt_rate(dip.get('floor_break_first_rate'))} (n={dip.get('n')})",
        f"- delta FAILURE-PROTECTED = {_fmt_rate(dev.get('failure_vs_protected_delta', {}).get('floor_break_first_rate'))}",
        "",
        "## FWD directional check",
        f"- FWD delta FLOOR_BREAK_FIRST = {_fmt_rate(fwd.get('failure_vs_protected_delta', {}).get('floor_break_first_rate'))}",
        "",
        f"**PRIMARY_NEXT_MECHANISM:** `{dec.get('PRIMARY_NEXT_MECHANISM')}`",
        f"**NEW_EXIT_RULE:** {dec.get('NEW_EXIT_RULE')}",
        f"**CANDIDATE_FROZEN:** {dec.get('CANDIDATE_FROZEN')}",
        "",
        f"**NEXT:** {dec.get('NEXT')}",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
