"""Publish 3 artifacts for existing-data ENTRY quality mechanism V1 R1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRECAP_EXISTING_MECHANISM_V1_R1_OUT
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "identity",
    "attrition",
    "features",
    "daily_rho",
    "qualify",
    "concentration",
    "missing",
    "threshold_policy",
    "blocks",
    "capture",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT.mkdir(parents=True, exist_ok=True)
    for p in PRECAP_EXISTING_MECHANISM_V1_R1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRECAP_EXISTING_MECHANISM_V1_R1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRECAP_EXISTING_MECHANISM_V1_R1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PRECAP_EXISTING_MECHANISM_V1_R1_OUT / "audit.xlsx")


def _daily_summary(feat: dict[str, Any], bk: str) -> dict[str, Any]:
    d = ((feat.get("daily") or {}).get(bk) or {})
    return {
        "evaluable_day_n": d.get("evaluable_day_n"),
        "positive_rho_day_n": d.get("positive_rho_day_n"),
        "negative_rho_day_n": d.get("negative_rho_day_n"),
        "zero_rho_day_n": d.get("zero_rho_day_n"),
        "median_daily_rho": d.get("median_daily_rho"),
        "IQR_daily_rho": d.get("IQR_daily_rho"),
        "direction": d.get("direction"),
    }


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "PRIMARY_METHOD": "CONTINUOUS_DAILY_SPEARMAN",
            "selected_feature": dec.get("selected_feature"),
            "PRIMARY_NEXT_MECHANISM": dec.get("PRIMARY_NEXT_MECHANISM"),
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": "20260902",
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "BURNED_EXISTING_DATA_ONLY": True,
            "TRUE_OOS": False,
            "SUPERSEDED_PRIOR_FOR_DECISION": True,
        }
    )
    sheets["identity"] = kv_rows(
        {
            "AB": ev.get("identity_AB_as_development"),
            "C": ev.get("identity_C_as_forward_burned"),
            "pool": ev.get("pool_identity"),
        }
    )
    att_rows = list((ev.get("attrition") or {}).get("rows") or [])
    sheets["attrition"] = att_rows or [{"empty": True}]
    feat_rows = []
    daily_rows = []
    conc_rows = []
    for f in list(ev.get("features") or []):
        feat_rows.append(
            {
                "feature": f.get("feature"),
                "qualify": f.get("qualify"),
                "has_a_direction": f.get("has_a_direction"),
                "higher_is_better": f.get("higher_is_better"),
                "availability_A": (f.get("availability") or {}).get("BLOCK_A_DISCOVERY"),
                "daily_A": _daily_summary(f, "BLOCK_A_DISCOVERY"),
                "daily_B": _daily_summary(f, "BLOCK_B_INTERNAL_STABILITY"),
                "daily_C": _daily_summary(f, "BLOCK_C_BURNED_STRESS"),
                "ab_same": f.get("block_ab_same_direction"),
                "c_reversal": f.get("block_c_clear_reversal"),
                "added_same": f.get("added_same_direction"),
                "not_one_sided": f.get("not_one_sided"),
                "arrival_multi": f.get("arrival_multi_same_direction"),
                "concentration_warn": (f.get("concentration") or {}).get("warning_gt_50pct"),
                "confound": f.get("confound"),
                "q30": f.get("block_a_q30"),
                "q70": f.get("block_a_q70"),
                "q_used_for_qualify": False,
                "median_split_used_for_qualify": False,
            }
        )
        for bk in ("BLOCK_A_DISCOVERY", "BLOCK_B_INTERNAL_STABILITY", "BLOCK_C_BURNED_STRESS"):
            for row in list(((f.get("daily") or {}).get(bk) or {}).get("daily") or []):
                daily_rows.append(
                    {
                        "feature": f.get("feature"),
                        "block": bk,
                        "date": row.get("date"),
                        "n": row.get("n"),
                        "rho": row.get("rho"),
                        "evaluable": row.get("evaluable"),
                        "status": row.get("status"),
                    }
                )
        conc = dict(f.get("concentration") or {})
        conc_rows.append(
            {
                "feature": f.get("feature"),
                "day_top": ((conc.get("day") or {}).get("rho_abs") or {}).get("top"),
                "day_share": ((conc.get("day") or {}).get("rho_abs") or {}).get("share_of_abs"),
                "day_warn": (conc.get("day") or {}).get("warning_gt_50pct"),
                "symbol_top": (conc.get("symbol") or {}).get("top"),
                "symbol_share": (conc.get("symbol") or {}).get("share_of_abs"),
                "symbol_warn": (conc.get("symbol") or {}).get("warning_gt_50pct"),
                "population_pnl_copied": False,
            }
        )
    sheets["features"] = feat_rows or [{"empty": True}]
    sheets["daily_rho"] = daily_rows or [{"empty": True}]
    sheets["qualify"] = kv_rows({"qualified": dec.get("qualified_features"), "confounded": dec.get("confounded_features")})
    sheets["concentration"] = conc_rows or [{"empty": True}]
    sheets["missing"] = kv_rows(dict(ev.get("missing") or {}))
    sheets["threshold_policy"] = kv_rows(dict(dec.get("NEXT_THRESHOLD") or {}))
    sheets["blocks"] = kv_rows(
        {
            "BLOCK_A_USE": "mechanism discovery",
            "BLOCK_B_USE": "internal temporal stability",
            "BLOCK_C_USE": "burned stress diagnostic",
            "BLOCK_B_IS_VALIDATION": False,
            "BLOCK_C_IS_VALIDATION": False,
        }
    )
    sheets["capture"] = kv_rows(dict(report.get("capture_reporting_state") or {}))
    sheets["decision"] = kv_rows(dec)
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    return sheets


def _fmt_daily(feat: dict[str, Any], bk: str) -> str:
    d = _daily_summary(feat, bk)
    return (
        f"eval_days={d.get('evaluable_day_n')} pos={d.get('positive_rho_day_n')} "
        f"neg={d.get('negative_rho_day_n')} zero={d.get('zero_rho_day_n')} "
        f"median_rho={d.get('median_daily_rho')} IQR={d.get('IQR_daily_rho')} dir={d.get('direction')}"
    )


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    req = dict(report.get("required") or {})
    ans = dict(report.get("answers") or {})
    cap = dict(report.get("capture_reporting_state") or {})
    pool = dict(ev.get("pool_identity") or {})
    att = dict(ev.get("attrition") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**PRIMARY_METHOD:** CONTINUOUS_DAILY_SPEARMAN",
        f"**selected_feature:** `{dec.get('selected_feature')}`",
        f"**PRIMARY_NEXT_MECHANISM:** `{dec.get('PRIMARY_NEXT_MECHANISM')}`",
        "**INDEPENDENT_ALPHA_FAMILY:** false",
        "**TRUE_OOS:** false · **PROSPECTIVE_HARVEST_SUSPENDED:** true",
        "**SUPERSEDED_FOR_DECISION (prior V1):** true",
        "**REASON:** PRECOMMITTED_CONTINUOUS_DAILY_SPEARMAN_METHOD_NOT_IMPLEMENTED",
        "**SECONDARY_DIAGNOSTIC_ONLY (median split):** true",
        "",
        "## Frozen reporting fields",
        f"FUTURE_DATA_USED = {req.get('FUTURE_DATA_USED')}",
        f"MAX_RESEARCH_DATE = {req.get('MAX_RESEARCH_DATE')}",
        f"PROSPECTIVE_HARVEST_SUSPENDED = {req.get('PROSPECTIVE_HARVEST_SUSPENDED')}",
        f"BURNED_EXISTING_DATA_ONLY = {req.get('BURNED_EXISTING_DATA_ONLY')}",
        f"NEXT_THRESHOLD_POLICY_FROZEN = {req.get('NEXT_THRESHOLD_POLICY_FROZEN')}",
        f"MISSING_POLICY_FROZEN = {req.get('MISSING_POLICY_FROZEN')}",
        f"NEXT_CANDIDATE_ID_IF_CASE_A = {req.get('NEXT_CANDIDATE_ID_IF_CASE_A')}",
        f"NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED = {req.get('NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED')}",
        f"CAPTURE_STATE = {cap.get('CAPTURE_STATE')}",
        "",
        "## Pool identity",
        f"A/B/C pool_n = {pool.get('pool_n')}",
        f"outcome_evaluable_n = {pool.get('outcome_evaluable_n')}",
        f"attrition_n = {pool.get('attrition_n')}",
        f"pool_identity_ok = {pool.get('pool_identity_ok')}",
        "",
        "## Outcome attrition",
        f"unexplained_n = {att.get('unexplained_n')} fail_closed_unexplained = {att.get('fail_closed_unexplained')}",
        f"overall = {json.dumps(json_sanitize(att.get('overall') or {}), ensure_ascii=False, default=str)}",
        "",
        "## Identity",
        f"AB-as-DEV ok={(ev.get('identity_AB_as_development') or {}).get('ok')} C-as-FWD ok={(ev.get('identity_C_as_forward_burned') or {}).get('ok')}",
        "",
        "## Features (daily Spearman PRIMARY)",
    ]
    for f in list(ev.get("features") or []):
        av = ((f.get("availability") or {}).get("BLOCK_A_DISCOVERY") or {})
        lines.append(
            f"- {f.get('feature_id')} `{f.get('feature')}` qualify={f.get('qualify')} "
            f"avail_A={av.get('availability')} missing_A={av.get('missing_n')} "
            f"hib={f.get('higher_is_better')} AB_same={f.get('block_ab_same_direction')} "
            f"C_rev={f.get('block_c_clear_reversal')} ADDED_same={f.get('added_same_direction')} "
            f"not_one_sided={f.get('not_one_sided')} arrival_multi={f.get('arrival_multi_same_direction')} "
            f"conc={ (f.get('concentration') or {}).get('warning_gt_50pct') }"
        )
        lines.append(f"  A: {_fmt_daily(f, 'BLOCK_A_DISCOVERY')}")
        lines.append(f"  B: {_fmt_daily(f, 'BLOCK_B_INTERNAL_STABILITY')}")
        lines.append(f"  C: {_fmt_daily(f, 'BLOCK_C_BURNED_STRESS')}")
    thr = dict(dec.get("NEXT_THRESHOLD") or {})
    lines.extend(
        [
            "",
            "## Next threshold (frozen, not applied this run, not used in QUALIFY)",
            f"policy={thr.get('policy')} threshold={thr.get('threshold')} side={thr.get('side')}",
            f"missing next-run policy={dec.get('MISSING_POLICY_FROZEN')}",
            f"candidate_evaluated_this_run={dec.get('candidate_evaluated_this_run')}",
            "",
            "## Mandatory answers",
        ]
    )
    for i in range(1, 29):
        lines.append(f"{i}. {ans.get(str(i), ans.get(i, ''))}")
    lines.extend(["", f"NEXT: {dec.get('NEXT')}", "", "STOP.", ""])
    return "\n".join(lines)
