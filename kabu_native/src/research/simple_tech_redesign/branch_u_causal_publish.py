"""Write report.json / report.md / audit.xlsx only under branch_u_full_causal_portfolio_replay/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.branch_u_causal_spec import ANALYSIS_ID
from research.simple_tech_redesign.isolation import BRANCH_U_CAUSAL_OUT

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
    "UNCONSTRAINED_FILL_N",
    "TESTED_BRANCH_U_EXIT_MECHANISMS",
    "CONTROL_COUNTS",
    "TREATMENT_COUNTS",
    "incremental_treatment_trade_n",
    "control_only_trade_n",
    "CONTROL_ECONOMICS",
    "TREATMENT_ECONOMICS",
    "ATTRIBUTION",
    "ONE_SHOT_AUDIT",
    "DAY_ROBUSTNESS",
    "DELTA_TOTAL_PNL",
    "DELTA_PF",
    "DELTA_MAX_DD",
    "OCCUPANCY_SOT_PARITY",
    "PRE_DIVERGENCE_PARITY",
    "FORCE_TREATMENT_FILL_SET",
    "ENTRY_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def _trade_sheet_row(arm: str, t: dict[str, Any]) -> dict[str, Any]:
    return {
        "arm": arm,
        "trade_id": t.get("trade_id"),
        "date": t.get("date"),
        "symbol": t.get("symbol"),
        "t0": t.get("t0"),
        "fill_role": t.get("fill_role"),
        "path_type": t.get("path_type"),
        "fill_time": t.get("fill_time"),
        "fill_price": t.get("fill_price"),
        "exit_time": t.get("exit_time"),
        "exit_price": t.get("exit_price"),
        "exit_reason": t.get("exit_reason"),
        "pnl_yen_100": t.get("pnl_yen_100"),
        "exit_miss": t.get("exit_miss"),
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    BRANCH_U_CAUSAL_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in BRANCH_U_CAUSAL_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    summary = dict(body.get("summary") or {})
    summary.pop("trades_control", None)
    summary.pop("trades_treatment", None)
    body["summary"] = summary
    (BRANCH_U_CAUSAL_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (BRANCH_U_CAUSAL_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(BRANCH_U_CAUSAL_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    summary = dict(report.get("summary") or {})
    attr = dict(summary.get("ATTRIBUTION") or {})
    oneshot = dict(summary.get("ONE_SHOT_AUDIT") or {})
    path_rows = [
        {"scope": "DIRECT_EXIT_DELTA", "value": attr.get("DIRECT_EXIT_DELTA")},
        {"scope": "SLOT_RELEASE_DOWNSTREAM_DELTA", "value": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA")},
        {"scope": "DISPLACED_TRADE_DELTA", "value": attr.get("DISPLACED_TRADE_DELTA")},
        {"scope": "TOTAL_CAUSAL_DELTA", "value": attr.get("TOTAL_CAUSAL_DELTA")},
        {"scope": "COMMON_N", "value": attr.get("COMMON_N")},
        {"scope": "INCREMENTAL_TREATMENT_N", "value": attr.get("INCREMENTAL_TREATMENT_N")},
        {"scope": "CONTROL_ONLY_N", "value": attr.get("CONTROL_ONLY_N")},
        {"scope": "ONE_SHOT_EARLY_U_EXIT_N", "value": oneshot.get("ONE_SHOT_EARLY_U_EXIT_N")},
        {"scope": "ONE_SHOT_DIP_U_EXIT_N", "value": oneshot.get("ONE_SHOT_DIP_U_EXIT_N")},
        {"scope": "ONE_SHOT_GOOD_U_EXIT_N", "value": oneshot.get("ONE_SHOT_GOOD_U_EXIT_N")},
        {"scope": "COMMON_EARLY_U_EXIT_N", "value": oneshot.get("COMMON_EARLY_U_EXIT_N")},
        {"scope": "COMMON_DIP_U_EXIT_N", "value": oneshot.get("COMMON_DIP_U_EXIT_N")},
        {"scope": "COMMON_GOOD_U_EXIT_N", "value": oneshot.get("COMMON_GOOD_U_EXIT_N")},
        {"scope": "ONESHOT_PATH_MATCH", "value": oneshot.get("ONESHOT_PATH_MATCH")},
    ]
    core_added = [
        {"scope": "OVERALL", "arm": "control", **dict(summary.get("CONTROL_ECONOMICS") or {})},
        {"scope": "OVERALL", "arm": "treatment", **dict(summary.get("TREATMENT_ECONOMICS") or {})},
        {"scope": "CORE", "arm": "control", **dict(summary.get("CORE_CONTROL_ECONOMICS") or {})},
        {"scope": "CORE", "arm": "treatment", **dict(summary.get("CORE_TREATMENT_ECONOMICS") or {})},
        {"scope": "ADDED", "arm": "control", **dict(summary.get("ADDED_CONTROL_ECONOMICS") or {})},
        {"scope": "ADDED", "arm": "treatment", **dict(summary.get("ADDED_TREATMENT_ECONOMICS") or {})},
        {"scope": "COMMON", "arm": "control", **dict(summary.get("COMMON_CONTROL_ECONOMICS") or {})},
        {"scope": "COMMON", "arm": "treatment", **dict(summary.get("COMMON_TREATMENT_ECONOMICS") or {})},
        {"scope": "INCREMENTAL", "arm": "treatment", **dict(summary.get("INCREMENTAL_ECONOMICS") or {})},
    ]
    ctrl_tr = list(summary.get("trades_control") or [])
    treat_tr = list(summary.get("trades_treatment") or [])
    trade_rows = [_trade_sheet_row("control", t) for t in ctrl_tr] + [_trade_sheet_row("treatment", t) for t in treat_tr]
    day_rows = list((summary.get("DAY_ROBUSTNESS") or {}).get("daily") or [])
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "trades": trade_rows or [{"empty": True}],
        "path": path_rows,
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


def _count_line(label: str, pack: dict[str, Any]) -> str:
    return (
        f"{label}: signal=`{pack.get('signal_n')}` candidate=`{pack.get('candidate_n')}` "
        f"accepted=`{pack.get('accepted_entry_n')}` fill=`{pack.get('fill_n')}` "
        f"Branch_U_exit=`{pack.get('Branch_U_exit_n')}` session_close=`{pack.get('session_close_exit_n')}` "
        f"slot_release=`{pack.get('slot_release_n')}` CAP_reject=`{pack.get('CAP_reject_n')}` "
        f"same_symbol_reject=`{pack.get('same_symbol_reject_n')}`"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    summary = dict(report.get("summary") or {})
    oc = dict(summary.get("CONTROL_ECONOMICS") or {})
    ot = dict(summary.get("TREATMENT_ECONOMICS") or {})
    attr = dict(summary.get("ATTRIBUTION") or {})
    oneshot = dict(summary.get("ONE_SHOT_AUDIT") or {})
    day = dict(summary.get("DAY_ROBUSTNESS") or {})
    cc = dict(summary.get("CONTROL_COUNTS") or {})
    tc = dict(summary.get("TREATMENT_COUNTS") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "Frozen Branch U only: UNPROVEN + `U_BB_LOWER_BREAK`. "
        "Control = occupancy + session-close. Treatment = same occupancy + frozen Branch U EXIT. "
        "Treatment fill identity is not forced back to the unconstrained 232. "
        "No Branch P. No time stop. No sizing change. TRUE_OOS=false. CERTIFIED=false.",
        "",
        f"V27_FILL_IDENTITY_PARITY = `{req.get('V27_FILL_IDENTITY_PARITY')}`",
        f"OCCUPANCY_SOT_PARITY = `{req.get('OCCUPANCY_SOT_PARITY')}` "
        f"PRE_DIVERGENCE_PARITY = `{req.get('PRE_DIVERGENCE_PARITY')}` "
        f"FORCE_TREATMENT_FILL_SET = `{req.get('FORCE_TREATMENT_FILL_SET')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}` EXECUTION_EVALUABLE_N = `{req.get('EXECUTION_EVALUABLE_N')}` "
        f"UNCONSTRAINED_FILL_N = `{req.get('UNCONSTRAINED_FILL_N')}`",
        f"TESTED_BRANCH_U_EXIT_MECHANISMS = `{req.get('TESTED_BRANCH_U_EXIT_MECHANISMS')}`",
        _count_line("CONTROL", cc),
        _count_line("TREATMENT", tc),
        f"incremental_treatment_trade_n = `{req.get('incremental_treatment_trade_n')}` "
        f"control_only_trade_n = `{req.get('control_only_trade_n')}`",
        "",
        "## Attribution (yen, 100 shares, fees excluded)",
        "",
        f"A DIRECT_EXIT_DELTA = `{_fmt(attr.get('DIRECT_EXIT_DELTA'))}`",
        f"B SLOT_RELEASE_DOWNSTREAM_DELTA = `{_fmt(attr.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))}`",
        f"C DISPLACED_TRADE_DELTA = `{_fmt(attr.get('DISPLACED_TRADE_DELTA'))}`",
        f"D TOTAL_CAUSAL_DELTA = `{_fmt(attr.get('TOTAL_CAUSAL_DELTA'))}`",
        "",
        "## Occupancy economics",
        "",
        f"Control total PnL = `{_fmt(oc.get('TOTAL_PNL_YEN'))}` PF = `{_fmt(oc.get('PF'))}` "
        f"maxDD = `{_fmt(oc.get('REALIZED_MAX_DD'))}`",
        f"Treatment total PnL = `{_fmt(ot.get('TOTAL_PNL_YEN'))}` PF = `{_fmt(ot.get('PF'))}` "
        f"maxDD = `{_fmt(ot.get('REALIZED_MAX_DD'))}`",
        f"DELTA_TOTAL_PNL = `{_fmt(req.get('DELTA_TOTAL_PNL'))}` DELTA_PF = `{_fmt(req.get('DELTA_PF'))}` "
        f"DELTA_MAX_DD = `{_fmt(req.get('DELTA_MAX_DD'))}`",
        "",
        "## One-shot audit (unconstrained 232; common occupancy trades not remapped onto incremental)",
        "",
        f"ONE_SHOT EARLY/DIP/GOOD U EXIT = `{oneshot.get('ONE_SHOT_EARLY_U_EXIT_N')}` / "
        f"`{oneshot.get('ONE_SHOT_DIP_U_EXIT_N')}` / `{oneshot.get('ONE_SHOT_GOOD_U_EXIT_N')}` "
        f"(expected 29 / 0 / 0, match=`{oneshot.get('ONESHOT_PATH_MATCH')}`)",
        f"COMMON occupancy EARLY/DIP/GOOD U EXIT = `{oneshot.get('COMMON_EARLY_U_EXIT_N')}` / "
        f"`{oneshot.get('COMMON_DIP_U_EXIT_N')}` / `{oneshot.get('COMMON_GOOD_U_EXIT_N')}`",
        "",
        "## Day robustness",
        "",
        f"IMPROVE_DAY_N = `{day.get('IMPROVE_DAY_N')}` WORSEN_DAY_N = `{day.get('WORSEN_DAY_N')}` "
        f"FLAT_DAY_N = `{day.get('FLAT_DAY_N')}`",
        f"DELTA_EX_BEST_DAY = `{_fmt(req.get('DELTA_EX_BEST_DAY'))}` "
        f"DELTA_EX_TOP3_DAY = `{_fmt(req.get('DELTA_EX_TOP3_DAY'))}` "
        f"DELTA_DROP_TOP_SYMBOL = `{_fmt(req.get('DELTA_DROP_TOP_SYMBOL'))}` "
        f"DELTA_LODO_MIN = `{_fmt(summary.get('DELTA_LODO_MIN'))}` "
        f"DELTA_LODO_MEDIAN = `{_fmt(summary.get('DELTA_LODO_MEDIAN'))}`",
        "",
    ]
    for r in list(day.get("daily") or []):
        lines.append(
            f"- {r.get('date')}: control=`{_fmt(r.get('control_pnl'))}` treatment=`{_fmt(r.get('treatment_pnl'))}` "
            f"delta=`{_fmt(r.get('delta_pnl'))}` additional_fill_n=`{r.get('additional_fill_n')}` "
            f"Branch_U_EXIT_n=`{r.get('branch_u_exit_n')}` `{r.get('side')}`"
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
