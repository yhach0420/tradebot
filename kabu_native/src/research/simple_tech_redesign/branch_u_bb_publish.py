"""Write report.json / report.md / audit.xlsx only under branch_u_bb_lower_exit_one_shot/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.branch_u_bb_spec import ANALYSIS_ID, PATH_TYPES
from research.simple_tech_redesign.isolation import BRANCH_U_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "trades",
    "path",
    "core_added",
    "day_robustness",
    "decision",
)

REQUIRED_KEYS = (
    "V27_FILL_IDENTITY_PARITY",
    "SIGNAL_N",
    "EXECUTION_EVALUABLE_N",
    "CORE_FILL_N",
    "ADDED_FILL_N",
    "TOTAL_RESEARCH_FILL_N",
    "TESTED_BRANCH_U_EXIT_MECHANISMS",
    "BRANCH_U_EXIT_N",
    "SESSION_CLOSE_EXIT_N",
    "DIP_BRANCH_U_EXIT_N",
    "PATH_ECONOMICS",
    "OVERALL_CONTROL_ECONOMICS",
    "OVERALL_TREATMENT_ECONOMICS",
    "DELTA_TOTAL_PNL",
    "DELTA_PF",
    "DELTA_MAX_DD",
    "ENTRY_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def _trade_sheet_row(t: dict[str, Any]) -> dict[str, Any]:
    seq = t.get("exit_event_sequence")
    fresh = t.get("freshness_evidence")
    return {
        "trade_id": t.get("trade_id"),
        "fill_role": t.get("fill_role"),
        "path_type": t.get("path_type"),
        "fill_t": t.get("fill_t"),
        "lifecycle_state": t.get("lifecycle_state"),
        "break_even_reached": t.get("break_even_reached"),
        "first_break_even_time": t.get("first_break_even_time"),
        "u_bb_lower_break_time": t.get("u_bb_lower_break_time"),
        "bb_break_before_be": t.get("bb_break_before_be"),
        "trigger_time": t.get("trigger_time"),
        "exit_bid_time": t.get("exit_bid_time"),
        "exit_price": t.get("exit_price"),
        "control_reason": t.get("control_reason"),
        "treatment_reason": t.get("treatment_reason"),
        "control_pnl": t.get("control_pnl"),
        "treatment_pnl": t.get("treatment_pnl"),
        "paired_pnl_delta": t.get("paired_pnl_delta"),
        "freshness_evidence": json.dumps(fresh, ensure_ascii=True, default=str) if fresh is not None else None,
        "exit_event_sequence": json.dumps(seq, ensure_ascii=True, default=str) if seq is not None else None,
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    BRANCH_U_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in BRANCH_U_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    summary = dict(body.get("summary") or {})
    summary.pop("trades", None)
    body["summary"] = summary
    (BRANCH_U_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (BRANCH_U_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(BRANCH_U_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    summary = dict(report.get("summary") or {})
    path = dict(summary.get("PATH_ECONOMICS") or {})
    path_rows = []
    for p in PATH_TYPES:
        row = dict(path.get(p) or {})
        row["path_type"] = p
        path_rows.append(row)
    core_added = [
        {"scope": "OVERALL", "arm": "control", **dict(summary.get("OVERALL_CONTROL_ECONOMICS") or {})},
        {"scope": "OVERALL", "arm": "treatment", **dict(summary.get("OVERALL_TREATMENT_ECONOMICS") or {})},
        {"scope": "CORE", "arm": "control", **dict(summary.get("CORE_CONTROL_ECONOMICS") or {})},
        {"scope": "CORE", "arm": "treatment", **dict(summary.get("CORE_TREATMENT_ECONOMICS") or {})},
        {"scope": "ADDED", "arm": "control", **dict(summary.get("ADDED_CONTROL_ECONOMICS") or {})},
        {"scope": "ADDED", "arm": "treatment", **dict(summary.get("ADDED_TREATMENT_ECONOMICS") or {})},
    ]
    day_rows = list((summary.get("DAY_ROBUSTNESS") or {}).get("daily") or [])
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "trades": [_trade_sheet_row(t) for t in list(summary.get("trades") or [])] or [{"empty": True}],
        "path": path_rows or [{"empty": True}],
        "core_added": core_added,
        "day_robustness": day_rows or [{"empty": True}],
        "decision": kv_rows(dict(report.get("decision") or {})),
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1000 else f"{v:,.2f}"
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    summary = dict(report.get("summary") or {})
    path = dict(summary.get("PATH_ECONOMICS") or {})
    oc = dict(summary.get("OVERALL_CONTROL_ECONOMICS") or {})
    ot = dict(summary.get("OVERALL_TREATMENT_ECONOMICS") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "V26 / V27 / V28 / V29 / EXIT Lifecycle Branch RCA frozen. "
        "One-shot Branch U EXIT only: UNPROVEN + frozen `U_BB_LOWER_BREAK` then first causal Bid1. "
        "Control is canonical session-close Bid1. Branch P unchanged. No time stop. "
        "TESTED_BRANCH_U_EXIT_MECHANISMS must be `[U_BB_LOWER_BREAK]`.",
        "",
        f"V27_FILL_IDENTITY_PARITY = `{req.get('V27_FILL_IDENTITY_PARITY')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}` EXECUTION_EVALUABLE_N = `{req.get('EXECUTION_EVALUABLE_N')}`",
        f"CORE_FILL_N = `{req.get('CORE_FILL_N')}` ADDED_FILL_N = `{req.get('ADDED_FILL_N')}` "
        f"TOTAL_RESEARCH_FILL_N = `{req.get('TOTAL_RESEARCH_FILL_N')}`",
        f"TESTED_BRANCH_U_EXIT_MECHANISMS = `{req.get('TESTED_BRANCH_U_EXIT_MECHANISMS')}`",
        f"BRANCH_U_EXIT_N = `{req.get('BRANCH_U_EXIT_N')}` SESSION_CLOSE_EXIT_N = `{req.get('SESSION_CLOSE_EXIT_N')}`",
        f"DIP_BRANCH_U_EXIT_N = `{req.get('DIP_BRANCH_U_EXIT_N')}` "
        f"(Lifecycle RCA diagnostic was 0/66; this is actual causal EXIT, not a retune target)",
        "",
        "## Overall economics (100 shares, fees excluded)",
        "",
        f"Control total PnL = `{_fmt(oc.get('TOTAL_PNL_YEN'))}` PF = `{_fmt(oc.get('PF'))}` "
        f"maxDD = `{_fmt(oc.get('REALIZED_MAX_DD'))}`",
        f"Treatment total PnL = `{_fmt(ot.get('TOTAL_PNL_YEN'))}` PF = `{_fmt(ot.get('PF'))}` "
        f"maxDD = `{_fmt(ot.get('REALIZED_MAX_DD'))}`",
        f"DELTA_TOTAL_PNL = `{_fmt(req.get('DELTA_TOTAL_PNL'))}` DELTA_PF = `{_fmt(req.get('DELTA_PF'))}` "
        f"DELTA_MAX_DD = `{_fmt(req.get('DELTA_MAX_DD'))}`",
        "",
        "## Path economics",
        "",
    ]
    for p in PATH_TYPES:
        body = dict(path.get(p) or {})
        lines.append(
            f"- {p}: N=`{body.get('n')}` U_EXIT=`{body.get('BRANCH_U_EXIT_N')}` "
            f"control=`{_fmt(body.get('control_pnl'))}` treatment=`{_fmt(body.get('treatment_pnl'))}` "
            f"delta=`{_fmt(body.get('delta_pnl'))}`"
        )
    lines.extend(
        [
            "",
            f"ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}` SIZING_CHANGED=`{req.get('SIZING_CHANGED')}` "
            f"TRUE_OOS=`{req.get('TRUE_OOS')}` CERTIFIED=`{dec.get('CERTIFIED')}` "
            f"NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            "STOP.",
            "",
        ]
    )
    return "\n".join(lines)
