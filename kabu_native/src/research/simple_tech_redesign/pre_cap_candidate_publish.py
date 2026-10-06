"""Write report.json / report.md / audit.xlsx only under pre_cap_entry_quality_candidate_v1/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRE_CAP_CANDIDATE_OUT
from research.simple_tech_redesign.pre_cap_candidate_spec import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    COMPONENT_DEFINITIONS,
    COMPONENT_NAMES,
    REJECT_REASON,
)

SHEET_ORDER = (
    "summary",
    "integrity",
    "answers",
    "quality_overall",
    "quality_by_role",
    "portfolio",
    "attribution",
    "day_robustness",
    "concentration",
    "candidate_manifest",
    "decision",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRE_CAP_CANDIDATE_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in PRE_CAP_CANDIDATE_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRE_CAP_CANDIDATE_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRE_CAP_CANDIDATE_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PRE_CAP_CANDIDATE_OUT / "audit.xlsx")


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        return f"{v:.4f}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


def _qpack(q: dict[str, Any], prefix: str) -> dict[str, Any]:
    out = {f"{prefix}candidate_n": q.get("candidate_n"), f"{prefix}filled_n": q.get("filled_n")}
    for k in (
        "NEVER_BE_rate",
        "EARLY_FAILURE_rate",
        "GOOD_rate",
        "DIP_rate",
        "PTF_rate",
        "win_rate",
        "mean_pnl_yen_100",
        "median_pnl_yen_100",
        "MAE_mean_bps",
        "MFE_mean_bps",
    ):
        out[f"{prefix}{k}"] = q.get(k)
    return out


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    decision = dict(report.get("decision") or {})
    ans = dict(report.get("answers") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    qo = dict(dev.get("QUALITY_OVERALL") or {})
    roles = dict(dev.get("QUALITY_BY_ROLE") or {})
    role_rows = []
    for role, pack in roles.items():
        rec = {"role": role, "flagged_rate_among_role_signals": pack.get("flagged_rate_among_role_signals")}
        rec.update(_qpack(dict(pack.get("flagged") or {}), "flagged_"))
        rec.update(_qpack(dict(pack.get("retained") or {}), "retained_"))
        w = dict(pack.get("within_role_worse") or {})
        rec["within_worse"] = w.get("ok")
        rec["within_underpowered"] = w.get("underpowered")
        rec["within_reasons"] = ",".join(str(x) for x in (w.get("reasons") or []))
        role_rows.append(rec)
    port = []
    for cohort, body in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for arm in ("CONTROL", "TREATMENT"):
            rec = {"cohort": cohort, "arm": arm, **dict(body.get(arm) or {})}
            port.append(rec)
    attr_rows = []
    for cohort, body in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        a = dict(body.get("ATTRIBUTION") or {})
        attr_rows.append({"cohort": cohort, **a})
    days = list(dev.get("DAY_ROBUSTNESS") or []) + [
        {**r, "cohort": "FORWARD_BURNED"} for r in list(fwd.get("DAY_ROBUSTNESS") or [])
    ]
    for r in list(dev.get("DAY_ROBUSTNESS") or []):
        r.setdefault("cohort", "DEVELOPMENT")
    days = list(dev.get("DAY_ROBUSTNESS") or []) + list(fwd.get("DAY_ROBUSTNESS") or [])
    conc_rows = []
    for cohort, body in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        conc_rows.append({"cohort": cohort, **dict(body.get("CONCENTRATION") or {})})
    qov = {"cohort": "DEVELOPMENT", **{k: v for k, v in qo.items() if k not in {"flagged", "retained", "overall_worse"}}}
    qov.update(_qpack(dict(qo.get("flagged") or {}), "flagged_"))
    qov.update(_qpack(dict(qo.get("retained") or {}), "retained_"))
    man = dict(report.get("candidate_manifest") or {})
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "answers": kv_rows(ans),
        "quality_overall": [qov] if qov else [{"empty": True}],
        "quality_by_role": role_rows or [{"empty": True}],
        "portfolio": port or [{"empty": True}],
        "attribution": attr_rows or [{"empty": True}],
        "day_robustness": days or [{"empty": True}],
        "concentration": conc_rows or [{"empty": True}],
        "candidate_manifest": kv_rows(man) if man else [{"CANDIDATE_FROZEN": False}],
        "decision": kv_rows(decision),
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    ans = dict(report.get("answers") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    qo = dict(dev.get("QUALITY_OVERALL") or {})
    roles = dict(dev.get("QUALITY_BY_ROLE") or {})
    ce, te = dict(dev.get("CONTROL") or {}), dict(dev.get("TREATMENT") or {})
    fe, ft = dict(fwd.get("CONTROL") or {}), dict(fwd.get("TREATMENT") or {})
    da = dict(dev.get("ATTRIBUTION") or {})
    fa = dict(fwd.get("ATTRIBUTION") or {})
    conc = dict(dev.get("CONCENTRATION") or {})
    man = dict(report.get("candidate_manifest") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}** CASE=`{dec.get('CASE')}`",
        f"CANDIDATE_ID=`{CANDIDATE_ID}` FROZEN=`{req.get('CANDIDATE_FROZEN')}`",
        f"TRUE_OOS=`{req.get('TRUE_OOS')}` CERTIFIED=`{req.get('CERTIFIED')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "PRIMARY EXIT is session-close only. Branch U is not used. CAP remains occupancy-only. "
        "board_ok is not used. Window is frozen 5s. 2-of-3 is not searched. 20260903 unused.",
        "",
        "## Candidate rule",
        "",
        f"- `{ans.get('Q1_CANDIDATE_RULE')}`",
        f"- reject reason (research replay only): `{REJECT_REASON}`",
        f"- threshold search: `{ans.get('Q2_THRESHOLD_SEARCH')}`",
        f"- window 5s fixed: `{ans.get('Q3_BOARD_WINDOW_5S_FIXED')}`",
        f"- board_ok used: `{ans.get('Q4_BOARD_OK_USED')}`",
        "",
        "Components:",
    ]
    for name in COMPONENT_NAMES:
        lines.append(f"- `{name}`: {COMPONENT_DEFINITIONS.get(name)}")
    lines.extend(
        [
            "",
            "## Role proxy vs within-role quality",
            "",
            f"Mere CORE/ADDED role proxy: `{ans.get('Q5_MERE_ROLE_PROXY')}`",
            f"Within-role quality separation: `{ans.get('Q6_WITHIN_ROLE_QUALITY_SEPARATION')}`",
            f"CORE flagged rate: `{_fmt(ans.get('CORE_FLAGGED_RATE'))}` ADDED flagged rate: `{_fmt(ans.get('ADDED_FLAGGED_RATE'))}`",
            "",
        ]
    )
    for role, pack in roles.items():
        fq, rq = dict(pack.get("flagged") or {}), dict(pack.get("retained") or {})
        w = dict(pack.get("within_role_worse") or {})
        lines.append(
            f"- {role}: flagged_n=`{fq.get('filled_n')}` retained_n=`{rq.get('filled_n')}` "
            f"flagged median PnL=`{_fmt(fq.get('median_pnl_yen_100'))}` retained median=`{_fmt(rq.get('median_pnl_yen_100'))}` "
            f"flagged NEVER_BE=`{_fmt(fq.get('NEVER_BE_rate'))}` retained NEVER_BE=`{_fmt(rq.get('NEVER_BE_rate'))}` "
            f"flagged EARLY=`{_fmt(fq.get('EARLY_FAILURE_rate'))}` retained EARLY=`{_fmt(rq.get('EARLY_FAILURE_rate'))}` "
            f"within_worse=`{w.get('ok')}` underpowered=`{w.get('underpowered')}` reasons=`{_fmt(w.get('reasons'))}`"
        )
    qf, qr = dict(qo.get("flagged") or {}), dict(qo.get("retained") or {})
    lines.extend(
        [
            "",
            "## Overall quality (unconstrained fills; mixed roles)",
            "",
            f"candidate_n=`{qo.get('candidate_n')}` flagged_n=`{qo.get('flagged_n')}` retained_n=`{qo.get('retained_n')}` "
            f"flagged_rate=`{_fmt(qo.get('flagged_rate'))}`",
            f"flagged NEVER_BE=`{_fmt(qf.get('NEVER_BE_rate'))}` EARLY=`{_fmt(qf.get('EARLY_FAILURE_rate'))}` "
            f"GOOD=`{_fmt(qf.get('GOOD_rate'))}` DIP=`{_fmt(qf.get('DIP_rate'))}` PTF=`{_fmt(qf.get('PTF_rate'))}` "
            f"win=`{_fmt(qf.get('win_rate'))}` mean=`{_fmt(qf.get('mean_pnl_yen_100'))}` median=`{_fmt(qf.get('median_pnl_yen_100'))}`",
            f"retained NEVER_BE=`{_fmt(qr.get('NEVER_BE_rate'))}` EARLY=`{_fmt(qr.get('EARLY_FAILURE_rate'))}` "
            f"GOOD=`{_fmt(qr.get('GOOD_rate'))}` DIP=`{_fmt(qr.get('DIP_rate'))}` PTF=`{_fmt(qr.get('PTF_rate'))}` "
            f"win=`{_fmt(qr.get('win_rate'))}` mean=`{_fmt(qr.get('mean_pnl_yen_100'))}` median=`{_fmt(qr.get('median_pnl_yen_100'))}`",
            f"MAE/MFE source: `{qf.get('MAE_SOURCE')}`",
            "",
            "## DEVELOPMENT portfolio",
            "",
            f"Control PnL=`{_fmt(ce.get('TOTAL_PNL_YEN'))}` PF=`{_fmt(ce.get('PF'))}` MaxDD=`{_fmt(ce.get('REALIZED_MAX_DD'))}` "
            f"fill_n=`{ce.get('fill_n')}` CORE=`{ce.get('CORE_fill_n')}` ADDED=`{ce.get('ADDED_fill_n')}` "
            f"CAP reject=`{ce.get('CAP_reject_n')}` same-symbol=`{ce.get('same_symbol_reject_n')}`",
            f"Treatment PnL=`{_fmt(te.get('TOTAL_PNL_YEN'))}` PF=`{_fmt(te.get('PF'))}` MaxDD=`{_fmt(te.get('REALIZED_MAX_DD'))}` "
            f"fill_n=`{te.get('fill_n')}` CORE=`{te.get('CORE_fill_n')}` ADDED=`{te.get('ADDED_fill_n')}` "
            f"pre-cap reject=`{te.get('pre_cap_reject_n')}` CAP reject=`{te.get('CAP_reject_n')}` same-symbol=`{te.get('same_symbol_reject_n')}` "
            f"pos days=`{te.get('POSITIVE_DAY_N')}` neg days=`{te.get('NEGATIVE_DAY_N')}`",
            f"DIRECT_REMOVAL_EFFECT=`{_fmt(da.get('DIRECT_REMOVAL_EFFECT'))}` "
            f"DOWNSTREAM_OCCUPANCY_EFFECT=`{_fmt(da.get('DOWNSTREAM_OCCUPANCY_EFFECT'))}` "
            f"TOTAL_CAUSAL_DELTA=`{_fmt(da.get('TOTAL_CAUSAL_DELTA'))}`",
            "",
            "## FORWARD_BURNED (robustness diagnostic, not validation)",
            "",
            f"Control PnL=`{_fmt(fe.get('TOTAL_PNL_YEN'))}` PF=`{_fmt(fe.get('PF'))}` MaxDD=`{_fmt(fe.get('REALIZED_MAX_DD'))}` fill_n=`{fe.get('fill_n')}`",
            f"Treatment PnL=`{_fmt(ft.get('TOTAL_PNL_YEN'))}` PF=`{_fmt(ft.get('PF'))}` MaxDD=`{_fmt(ft.get('REALIZED_MAX_DD'))}` "
            f"pre-cap reject=`{ft.get('pre_cap_reject_n')}` fill_n=`{ft.get('fill_n')}`",
            f"DIRECT_REMOVAL_EFFECT=`{_fmt(fa.get('DIRECT_REMOVAL_EFFECT'))}` "
            f"DOWNSTREAM_OCCUPANCY_EFFECT=`{_fmt(fa.get('DOWNSTREAM_OCCUPANCY_EFFECT'))}` "
            f"TOTAL_CAUSAL_DELTA=`{_fmt(fa.get('TOTAL_CAUSAL_DELTA'))}`",
            "",
            "## Day robustness",
            "",
        ]
    )
    for r in list(dev.get("DAY_ROBUSTNESS") or []):
        lines.append(
            f"- DEV {r.get('date')}: C=`{_fmt(r.get('control_pnl'))}` T=`{_fmt(r.get('treatment_pnl'))}` "
            f"d=`{_fmt(r.get('delta_pnl'))}` pre-cap reject=`{r.get('pre_cap_reject_n')}` "
            f"incr fill=`{r.get('incremental_fill_n')}` direct=`{_fmt(r.get('direct_removal_effect'))}` "
            f"downstream=`{_fmt(r.get('downstream_occupancy_effect'))}`"
        )
    for r in list(fwd.get("DAY_ROBUSTNESS") or []):
        lines.append(
            f"- FWD {r.get('date')}: C=`{_fmt(r.get('control_pnl'))}` T=`{_fmt(r.get('treatment_pnl'))}` "
            f"d=`{_fmt(r.get('delta_pnl'))}` pre-cap reject=`{r.get('pre_cap_reject_n')}` "
            f"incr fill=`{r.get('incremental_fill_n')}` direct=`{_fmt(r.get('direct_removal_effect'))}` "
            f"downstream=`{_fmt(r.get('downstream_occupancy_effect'))}`"
        )
    lines.extend(
        [
            "",
            "## Concentration",
            "",
            f"flagged top symbol=`{conc.get('flagged_top_symbol')}` share=`{_fmt(conc.get('flagged_top_symbol_share'))}`",
            f"removed |PnL| top symbol=`{conc.get('removed_pnl_abs_top_symbol')}` share=`{_fmt(conc.get('removed_pnl_abs_top_share'))}`",
            f"downstream +PnL top symbol=`{conc.get('downstream_pos_top_symbol')}` share=`{_fmt(conc.get('downstream_pos_top_share'))}`",
            f"top day=`{conc.get('top_day')}` |delta| share=`{_fmt(conc.get('top_day_abs_delta_share'))}` warning=`{conc.get('warning')}`",
            "",
            "## Required answers",
            "",
            f"1. rule: {ans.get('Q1_CANDIDATE_RULE')}",
            f"2. threshold search: {ans.get('Q2_THRESHOLD_SEARCH')}",
            f"3. 5s window fixed: {ans.get('Q3_BOARD_WINDOW_5S_FIXED')}",
            f"4. board_ok used: {ans.get('Q4_BOARD_OK_USED')}",
            f"5. mere role proxy: {ans.get('Q5_MERE_ROLE_PROXY')}",
            f"6. within-role quality split: {ans.get('Q6_WITHIN_ROLE_QUALITY_SEPARATION')}",
            f"7. DEV Control/Treatment PnL: {_fmt(ans.get('Q7_DEV_CONTROL_PNL'))} / {_fmt(ans.get('Q7_DEV_TREATMENT_PNL'))}",
            f"8. DEV Control/Treatment PF: {_fmt(ans.get('Q8_DEV_CONTROL_PF'))} / {_fmt(ans.get('Q8_DEV_TREATMENT_PF'))}",
            f"9. DEV Control/Treatment MaxDD: {_fmt(ans.get('Q9_DEV_CONTROL_MAXDD'))} / {_fmt(ans.get('Q9_DEV_TREATMENT_MAXDD'))}",
            f"10. FWD Control/Treatment PnL: {_fmt(ans.get('Q10_FWD_CONTROL_PNL'))} / {_fmt(ans.get('Q10_FWD_TREATMENT_PNL'))}",
            f"11. direct removal DEV/FWD: {_fmt(ans.get('Q11_DIRECT_REMOVAL_EFFECT_DEV'))} / {_fmt(ans.get('Q11_DIRECT_REMOVAL_EFFECT_FWD'))}",
            f"12. downstream occupancy DEV/FWD: {_fmt(ans.get('Q12_DOWNSTREAM_OCCUPANCY_EFFECT_DEV'))} / {_fmt(ans.get('Q12_DOWNSTREAM_OCCUPANCY_EFFECT_FWD'))}",
            f"13. TOTAL_CAUSAL_DELTA DEV/FWD: {_fmt(ans.get('Q13_TOTAL_CAUSAL_DELTA_DEV'))} / {_fmt(ans.get('Q13_TOTAL_CAUSAL_DELTA_FWD'))}",
            f"14. day concentration: {_fmt(ans.get('Q14_DAY_CONCENTRATION'))}",
            f"15. symbol concentration: {_fmt(ans.get('Q15_SYMBOL_CONCENTRATION'))}",
            f"16. verdict: {ans.get('Q16_VERDICT')} CASE={ans.get('Q16_CASE')}",
            f"17. candidate frozen: {ans.get('Q17_CANDIDATE_FROZEN')}",
            f"18. TRUE_OOS: {ans.get('Q18_TRUE_OOS')}",
            f"19. CERTIFIED: {ans.get('Q19_CERTIFIED')}",
            f"20. next: {ans.get('Q20_NEXT')}",
            "",
            "## Freeze manifest",
            "",
            f"FROZEN=`{man.get('CANDIDATE_FROZEN')}` timestamp=`{man.get('CREATION_TIMESTAMP')}`",
            f"SPEC_SHA256=`{man.get('SPEC_SHA256')}` SOURCE_SHA256=`{man.get('SOURCE_SHA256')}`",
            f"TRUE_OOS=`{man.get('TRUE_OOS')}` CERTIFIED=`{man.get('CERTIFIED')}` RUNTIME=`{man.get('RUNTIME_IMPLEMENTED')}`",
            "",
            "STOP.",
            "",
        ]
    )
    return "\n".join(lines)
