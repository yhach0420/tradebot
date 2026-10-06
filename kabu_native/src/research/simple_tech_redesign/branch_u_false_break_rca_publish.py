"""Write report.json / report.md / audit.xlsx only under branch_u_false_break_sequence_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.branch_u_false_break_rca_spec import ANALYSIS_ID, SEQUENCE_LABELS
from research.simple_tech_redesign.isolation import BRANCH_U_FALSE_BREAK_RCA_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "decision",
    "agreement",
    "slices",
    "sequences",
    "questions",
    "trades",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    BRANCH_U_FALSE_BREAK_RCA_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in BRANCH_U_FALSE_BREAK_RCA_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (BRANCH_U_FALSE_BREAK_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (BRANCH_U_FALSE_BREAK_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(BRANCH_U_FALSE_BREAK_RCA_OUT / "audit.xlsx")


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:,.1f}"
        return f"{v:.4f}"
    if isinstance(v, dict):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


def _event_t(pack: Any) -> Any:
    if isinstance(pack, dict):
        return pack.get("event_t")
    return None


def _trade_row(t: dict[str, Any], *, cohort: str) -> dict[str, Any]:
    rec, adv, be = dict(t.get("reclaim") or {}), dict(t.get("adverse") or {}), dict(t.get("break_even") or {})
    return {
        "cohort": cohort,
        "date": t.get("date"),
        "symbol": t.get("symbol"),
        "t0": t.get("t0"),
        "fill_t": t.get("fill_t"),
        "fill_price": t.get("fill_price"),
        "fill_role": t.get("fill_role"),
        "path_type": t.get("path_type"),
        "lifecycle_state": t.get("lifecycle_state"),
        "never_break_even": t.get("never_break_even"),
        "trigger_t": t.get("trigger_t"),
        "trigger_bar_t": t.get("trigger_bar_t"),
        "trigger_low": t.get("trigger_low"),
        "close_at_trigger": t.get("close_at_trigger"),
        "bb_lower_at_trigger": t.get("bb_lower_at_trigger"),
        "first_label": t.get("first_label"),
        "first_event": t.get("first_event"),
        "first_t": t.get("first_t"),
        "tie_at_first": t.get("tie_at_first"),
        "reclaim_hit": rec.get("hit"),
        "reclaim_t": rec.get("event_t") or _event_t(rec),
        "adverse_hit": adv.get("hit"),
        "adverse_t": adv.get("event_t"),
        "adverse_low": adv.get("low"),
        "break_even_hit": be.get("hit"),
        "break_even_t": be.get("event_t"),
        "immediate_u_pnl": t.get("immediate_u_pnl"),
        "session_close_pnl": t.get("session_close_pnl"),
        "immediate_minus_close": t.get("immediate_minus_close"),
        "actual_immediate_u_exit": t.get("actual_immediate_u_exit"),
        "immediate_reason": t.get("immediate_reason"),
        "bb_recompute_match": t.get("bb_recompute_match"),
        "be_cache_match": t.get("be_cache_match"),
    }


def _seq_row(cohort: str, slice_name: str, label: str, pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "cohort": cohort,
        "slice": slice_name,
        "first_label": label,
        "trade_n": pack.get("trade_n"),
        "immediate_u_pnl": pack.get("immediate_u_pnl"),
        "session_close_pnl": pack.get("session_close_pnl"),
        "immediate_minus_close": pack.get("immediate_minus_close"),
        "positive_immediate_benefit_n": pack.get("positive_immediate_benefit_n"),
        "negative_immediate_benefit_n": pack.get("negative_immediate_benefit_n"),
        "median_delta": pack.get("median_delta"),
        "total_delta": pack.get("total_delta"),
        "CORE_n": pack.get("CORE_n"),
        "ADDED_n": pack.get("ADDED_n"),
        "EARLY_n": pack.get("EARLY_n"),
        "GOOD_n": pack.get("GOOD_n"),
        "DIP_n": pack.get("DIP_n"),
        "tie_at_first_n": pack.get("tie_at_first_n"),
    }


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    decision = dict(report.get("decision") or {})
    agree = dict(decision.get("agreement") or report.get("agreement") or {})
    seq_rows = []
    slice_rows = []
    q_rows = []
    trade_rows = []
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        slices = dict(body.get("slices") or {})
        for sname, spack in slices.items():
            dpack = dict(spack or {})
            ov = dict(dpack.get("overlay") or {})
            dr = dict(dpack.get("direction") or {})
            slice_rows.append(
                {
                    "cohort": label,
                    "slice": sname,
                    "trade_n": ov.get("trade_n"),
                    "direction": dr.get("direction"),
                    "sample_adequate": dr.get("sample_adequate"),
                    "RECLAIM_n": dr.get("RECLAIM_n"),
                    "ADVERSE_n": dr.get("ADVERSE_n"),
                    "RECLAIM_median_delta": dr.get("RECLAIM_median_delta"),
                    "ADVERSE_median_delta": dr.get("ADVERSE_median_delta"),
                    **{k: ov.get(k) for k in (
                        "immediate_u_pnl",
                        "session_close_pnl",
                        "immediate_minus_close",
                        "positive_immediate_benefit_n",
                        "negative_immediate_benefit_n",
                        "median_delta",
                        "total_delta",
                    )},
                }
            )
            for lab in SEQUENCE_LABELS:
                seq_rows.append(_seq_row(label, sname, lab, dict((dpack.get("by_label") or {}).get(lab) or {})))
            q_rows.append({"cohort": label, "slice": sname, **dict(dpack.get("questions") or {})})
        for t in list(body.get("rows") or []):
            trade_rows.append(_trade_row(t, cohort=label))
    agree_rows = []
    for lab, pack in dict(agree.get("by_label") or {}).items():
        agree_rows.append({"kind": "label", "name": lab, **dict(pack)})
    for sname, pack in dict(agree.get("by_slice") or {}).items():
        agree_rows.append({"kind": "slice", "name": sname, **dict(pack)})
    agree_rows.append({"kind": "primary_EARLY", "name": "EARLY_FAILURE", **dict(agree.get("primary_EARLY") or {})})
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "decision": kv_rows({k: v for k, v in decision.items() if k != "agreement"}),
        "agreement": agree_rows or [{"empty": True}],
        "slices": slice_rows or [{"empty": True}],
        "sequences": seq_rows or [{"empty": True}],
        "questions": q_rows or [{"empty": True}],
        "trades": trade_rows or [{"empty": True}],
    }


def _overlay_line(label: str, pack: dict[str, Any]) -> str:
    return (
        f"- {label}: n=`{pack.get('trade_n')}` "
        f"immediate=`{_fmt(pack.get('immediate_u_pnl'))}` "
        f"close=`{_fmt(pack.get('session_close_pnl'))}` "
        f"delta=`{_fmt(pack.get('immediate_minus_close'))}` "
        f"+N=`{pack.get('positive_immediate_benefit_n')}` -N=`{pack.get('negative_immediate_benefit_n')}` "
        f"median_delta=`{_fmt(pack.get('median_delta'))}` "
        f"total_delta=`{_fmt(pack.get('total_delta'))}`"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    agree = dict(dec.get("agreement") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}** CASE=`{dec.get('CASE')}`",
        f"TRUE_OOS=`{req.get('TRUE_OOS')}` CERTIFIED=`{req.get('CERTIFIED')}` NEW_EXIT_RULE=`false`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "Diagnostic RCA only. Frozen `UNPROVEN + U_BB_LOWER_BREAK`. Immediate U EXIT and session-close are controls. "
        "No K/wait/bps/BB retune. Pre-CAP board family remains `SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED`. 20260903 unused.",
        "",
        "## Population",
        "",
        f"- DEVELOPMENT U-trigger n=`{dec.get('DEV_U_TRIGGER_N')}` expected=`{dec.get('DEV_U_TRIGGER_N_EXPECTED')}`",
        f"- FORWARD_BURNED U-trigger n=`{dec.get('FWD_U_TRIGGER_N')}` expected=`{dec.get('FWD_U_TRIGGER_N_EXPECTED')}`",
        f"- PRIMARY path=`{dec.get('PRIMARY_PATH')}`",
        "",
        "## Questions",
        "",
        f"1. Immediate-worse concentrated in RECLAIM_FIRST? DEV=`{dec.get('Q1_DEV')}` FWD=`{dec.get('Q1_FWD')}`",
        f"2. Immediate-better concentrated in ADVERSE_EXTENSION_FIRST? DEV=`{dec.get('Q2_DEV')}` FWD=`{dec.get('Q2_FWD')}`",
        f"3. Development / Forward-burned direction agreement? `{dec.get('Q3_direction_agreement')}` "
        f"DEV=`{dec.get('DEVELOPMENT_direction')}` FWD=`{dec.get('FORWARD_BURNED_direction')}`",
        f"4. Split without GOOD/DIP? DEV=`{dec.get('Q4_split_without_GOOD_DIP_DEV')}` FWD=`{dec.get('Q4_split_without_GOOD_DIP_FWD')}`",
        f"5. EARLY_FAILURE internal same direction as ALL? `{dec.get('Q5_EARLY_internal')}` "
        f"DEV=`{dec.get('Q5_EARLY_internal_DEV')}` FWD=`{dec.get('Q5_EARLY_internal_FWD')}`",
        "",
    ]
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        slices = dict(body.get("slices") or {})
        lines.append(f"## {label}")
        lines.append("")
        for sname in ("EARLY_FAILURE", "ALL", "CORE", "ADDED", "OTHER_PATH"):
            sp = dict(slices.get(sname) or {})
            lines.append(f"### {sname}")
            lines.append(_overlay_line("slice", dict(sp.get("overlay") or {})))
            dr = dict(sp.get("direction") or {})
            lines.append(
                f"- direction=`{dr.get('direction')}` sample_adequate=`{dr.get('sample_adequate')}` "
                f"RECLAIM n/median=`{dr.get('RECLAIM_n')}`/`{_fmt(dr.get('RECLAIM_median_delta'))}` "
                f"ADVERSE n/median=`{dr.get('ADVERSE_n')}`/`{_fmt(dr.get('ADVERSE_median_delta'))}`"
            )
            for lab in SEQUENCE_LABELS:
                lines.append(_overlay_line(lab, dict((sp.get("by_label") or {}).get(lab) or {})))
            lines.append("")
        lines.append("")
    lines.extend(
        [
            "## Development / Forward consistency",
            "",
        ]
    )
    prim = dict(agree.get("primary_EARLY") or {})
    lines.append(
        f"- PRIMARY EARLY: DEV=`{prim.get('DEVELOPMENT_direction')}` FWD=`{prim.get('FORWARD_BURNED_direction')}` "
        f"agreement=`{prim.get('agreement')}`"
    )
    for lab, pack in dict(agree.get("by_label") or {}).items():
        lines.append(
            f"- {lab}: DEV median=`{_fmt(pack.get('DEVELOPMENT_median_delta'))}` "
            f"FWD median=`{_fmt(pack.get('FORWARD_BURNED_median_delta'))}` "
            f"status=`{pack.get('status')}`"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"CASE=`{dec.get('CASE')}` VERDICT=`{dec.get('VERDICT')}`",
            f"reasons=`{dec.get('reasons')}`",
            "",
            "STOP. No new EXIT rule implemented.",
            "",
        ]
    )
    return "\n".join(lines)
