"""Publish 3 artifacts for existing-data ENTRY quality mechanism V1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRECAP_EXISTING_MECHANISM_V1_OUT
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "identity",
    "features",
    "qualify",
    "missing",
    "threshold_policy",
    "blocks",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRECAP_EXISTING_MECHANISM_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in PRECAP_EXISTING_MECHANISM_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRECAP_EXISTING_MECHANISM_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRECAP_EXISTING_MECHANISM_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PRECAP_EXISTING_MECHANISM_V1_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "selected_feature": dec.get("selected_feature"),
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": "20260902",
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "BURNED_EXISTING_DATA_ONLY": True,
            "TRUE_OOS": False,
        }
    )
    sheets["identity"] = kv_rows(
        {
            "AB": ev.get("identity_AB_as_development"),
            "C": ev.get("identity_C_as_forward_burned"),
        }
    )
    feat_rows = []
    for f in list(ev.get("features") or []):
        feat_rows.append(
            {
                "feature": f.get("feature"),
                "qualify": f.get("qualify"),
                "has_a_direction": f.get("has_a_direction"),
                "higher_is_better": f.get("higher_is_better"),
                "availability_A": (f.get("availability") or {}).get("BLOCK_A_DISCOVERY"),
                "dir_A": f.get("direction_A"),
                "ab_same": f.get("block_ab_same_direction"),
                "c_reversal": f.get("block_c_clear_reversal"),
                "added_same": f.get("added_same_direction"),
                "not_one_sided": f.get("not_one_sided"),
                "arrival_multi": f.get("arrival_multi_same_direction"),
                "concentration": f.get("concentration"),
                "confound": f.get("confound"),
                "q30": f.get("block_a_q30"),
                "q70": f.get("block_a_q70"),
            }
        )
    sheets["features"] = feat_rows or [{"empty": True}]
    sheets["qualify"] = kv_rows({"qualified": dec.get("qualified_features"), "confounded": dec.get("confounded_features")})
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
    sheets["decision"] = kv_rows(dec)
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    req = dict(report.get("required") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**selected_feature:** `{dec.get('selected_feature')}`",
        "**INDEPENDENT_ALPHA_FAMILY:** false",
        "**TRUE_OOS:** false · **PROSPECTIVE_HARVEST_SUSPENDED:** true",
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
        "",
        "## Identity",
        f"AB-as-DEV ok={(ev.get('identity_AB_as_development') or {}).get('ok')} C-as-FWD ok={(ev.get('identity_C_as_forward_burned') or {}).get('ok')}",
        "",
        "## Features (incremental T3 stream quality only)",
    ]
    for f in list(ev.get("features") or []):
        av = ((f.get("availability") or {}).get("BLOCK_A_DISCOVERY") or {})
        lines.append(
            f"- {f.get('feature_id')} `{f.get('feature')}` qualify={f.get('qualify')} "
            f"avail_A={av.get('availability')} missing_A={av.get('missing_n')} "
            f"dir_A={f.get('direction_A')} hib={f.get('higher_is_better')} "
            f"AB_same={f.get('block_ab_same_direction')} C_rev={f.get('block_c_clear_reversal')} "
            f"ADDED_same={f.get('added_same_direction')} not_one_sided={f.get('not_one_sided')} "
            f"arrival_multi={f.get('arrival_multi_same_direction')} conc={ (f.get('concentration') or {}).get('warning_gt_50pct') }"
        )
    thr = dict(dec.get("NEXT_THRESHOLD") or {})
    lines.extend(
        [
            "",
            "## Next threshold (frozen, not applied this run)",
            f"policy={thr.get('policy')} threshold={thr.get('threshold')} side={thr.get('side')}",
            f"missing next-run policy={dec.get('MISSING_POLICY_FROZEN')}",
            f"candidate_evaluated_this_run={dec.get('candidate_evaluated_this_run')}",
            "",
            f"NEXT: {dec.get('NEXT')}",
            "",
            "STOP.",
            "",
        ]
    )
    return "\n".join(lines)
