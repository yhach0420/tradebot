"""Write report.json / report.md / audit.xlsx only under causal_board_information_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.causal_board_rca_spec import ANALYSIS_ID
from research.simple_tech_redesign.isolation import CAUSAL_BOARD_RCA_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "availability",
    "answers",
    "pre_cap_align",
    "exit_align",
    "audit",
    "decision",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    CAUSAL_BOARD_RCA_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in CAUSAL_BOARD_RCA_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (CAUSAL_BOARD_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (CAUSAL_BOARD_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(CAUSAL_BOARD_RCA_OUT / "audit.xlsx")


def _flat_align(aligned: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for name, rows in aligned.items():
        for r in rows:
            rec = {"compare": name, **dict(r)}
            out.append(rec)
    return out or [{"empty": True}]


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    av = dict(report.get("availability") or {})
    levels = dict(av.get("levels") or {})
    scalars = dict(av.get("scalars") or {})
    avail_rows = [{"field": k, **dict(v)} for k, v in levels.items()] + [{"field": k, **dict(v)} for k, v in scalars.items()]
    decision = dict(report.get("decision") or {})
    audit = list(report.get("audit") or [])
    audit_rows = []
    for a in audit:
        audit_rows.append(
            {
                "date": a.get("date"),
                "symbol": a.get("symbol"),
                "t0": a.get("t0"),
                "fill_t": a.get("fill_t"),
                "fill_price": a.get("fill_price"),
                "fill_role": a.get("fill_role"),
                "path_type": a.get("path_type"),
                "u_triggered": a.get("u_triggered"),
                "trigger_t": a.get("trigger_t"),
                "control_pnl": (a.get("control_exit") or {}).get("pnl_yen_100"),
                "treatment_pnl": (a.get("treatment_exit") or {}).get("pnl_yen_100"),
                "control_exit_bid": (a.get("control_exit") or {}).get("exit_bid"),
                "treatment_exit_bid": (a.get("treatment_exit") or {}).get("exit_bid"),
                "pre_trigger_event_n": len(list(a.get("pre_trigger_events") or [])),
                "post_trigger_event_n": len(list(a.get("post_trigger_events") or [])),
            }
        )
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "availability": avail_rows or [{"empty": True}],
        "answers": kv_rows(dict(report.get("answers") or {})),
        "pre_cap_align": _flat_align(dict(decision.get("ALIGNED_PRE") or {})),
        "exit_align": _flat_align(dict(decision.get("ALIGNED_EXIT") or {})),
        "audit": audit_rows or [{"empty": True}],
        "decision": kv_rows({k: v for k, v in decision.items() if k not in {"ALIGNED_PRE", "ALIGNED_EXIT"}}),
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1000 else f"{v:,.2f}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


def _q(ans: dict[str, Any], key: str) -> str:
    p = dict(ans.get(key) or {})
    feats = p.get("supported_features") or []
    agree = p.get("agree_features") or []
    return (
        f"supported_var_n=`{p.get('supported_var_n')}` "
        f"direction_agree_n=`{p.get('direction_agree_n')}` "
        f"supported=`{_fmt(feats)}` agree=`{_fmt(agree)}`"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    ans = dict(report.get("answers") or {})
    av = dict(report.get("availability") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}** CASE=`{dec.get('CASE')}`",
        f"PRIMARY_NEXT_MECHANISM: `{req.get('PRIMARY_NEXT_MECHANISM')}`",
        f"TRUE_OOS=`{req.get('TRUE_OOS')}` CERTIFIED=`{req.get('CERTIFIED')}` POLICY_CREATED=`{req.get('POLICY_CREATED')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "Holdout formal VERDICT remains `SIMPLE_TECH_BRANCH_U_HOLDOUT_ACCUMULATING`. "
        "Added status `SIMPLE_TECH_BRANCH_U_HOLDOUT_TERMINATED_INCONCLUSIVE`. "
        "Not CONTRADICTED. Not SUPPORTED. 20260903 unused.",
        "",
        f"DEVELOPMENT_DAYS = `{req.get('DEVELOPMENT_DAYS')}`",
        f"FORWARD_BURNED_DAYS = `{req.get('FORWARD_BURNED_DAYS')}`",
        f"EVOLUTION_WINDOW_SEC = `{req.get('EVOLUTION_WINDOW_SEC')}` source=`{req.get('EVOLUTION_WINDOW_SOURCE')}`",
        f"CAP_ROLE = `{req.get('CAP_ROLE')}` BOARD_OK_REUSED=`{req.get('BOARD_OK_REUSED')}`",
        "",
        "## BOARD_FIELD_AVAILABILITY",
        "",
        f"sample_events_n=`{av.get('sample_events_n')}` true_L1_Buy1_Sell1=`{av.get('true_l1_buy1_sell1_usable')}` "
        f"depth_1to10=`{av.get('depth_buy1to10_sell1to10_usable')}`",
        f"Note: {av.get('note')}",
        "",
        "## PRE-CAP questions",
        "",
        f"1. pos vs neg: {_q(ans, 'q1_pos_neg_board_diff')}",
        f"2. EARLY vs GOOD/DIP: {_q(ans, 'q2_early_vs_gooddip')}",
        f"3. CORE failure: {_q(ans, 'q3_core_failure_board')}",
        f"4. ADDED vs CORE: {_q(ans, 'q4_added_vs_core')}",
        f"5. Dev vs Forward sign: {_q(ans, 'q5_dev_fwd_sign_agreement_pos_neg')}",
        "6. Day agreement is inside the pass rule (min 0.50 of days with both groups).",
        "7. Top-symbol share > 50% of the higher group blocks a variable.",
        f"8. CAP rejected vs admitted: {_q(ans, 'q8_cap_rejected_vs_admitted')}",
        f"9. {ans.get('q9_cap_is_not_quality_filter')}",
        "",
        "## EXIT board",
        "",
        f"supported features: `{_fmt(ans.get('exit_recovered_vs_terminal_supported_features'))}`",
        f"PRE_CAP_SUPPORTED=`{dec.get('PRE_CAP_SUPPORTED')}` EXIT_BOARD_SUPPORTED=`{dec.get('EXIT_BOARD_SUPPORTED')}`",
        "",
        "## Counts",
        "",
        f"DEV `{_fmt(ans.get('dev_counts'))}`",
        f"FWD `{_fmt(ans.get('fwd_counts'))}`",
        "",
        "## Audit examples",
        "",
    ]
    for a in list(report.get("audit") or []):
        cx = dict(a.get("control_exit") or {})
        tx = dict(a.get("treatment_exit") or {})
        lines.append(
            f"- {a.get('date')} {a.get('symbol')}: fill_t=`{a.get('fill_t')}` px=`{a.get('fill_price')}` "
            f"role=`{a.get('fill_role')}` path=`{a.get('path_type')}` u=`{a.get('u_triggered')}` "
            f"trigger_t=`{a.get('trigger_t')}` control_pnl=`{cx.get('pnl_yen_100')}` "
            f"treatment_pnl=`{tx.get('pnl_yen_100')}` control_exit_bid=`{cx.get('exit_bid')}` "
            f"treatment_exit_bid=`{tx.get('exit_bid')}` pre_n=`{len(list(a.get('pre_trigger_events') or []))}` "
            f"post_n=`{len(list(a.get('post_trigger_events') or []))}`"
        )
    lines.extend(["", "STOP. No new rule implemented.", ""])
    return "\n".join(lines)
