"""Publish exit_failure_composition_shift_rca artifacts."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.exit_composition_shift_rca_spec import ANALYSIS_ID, PRIMARY_FAILURE_KEYS
from research.simple_tech_redesign.isolation import EXIT_COMPOSITION_SHIFT_RCA_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "decision",
    "incidence_severity",
    "decomposition",
    "roles",
    "role_standardized",
    "horizon",
    "concentration",
    "leave_one_out",
    "be_reach",
    "ptf",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    EXIT_COMPOSITION_SHIFT_RCA_OUT.mkdir(parents=True, exist_ok=True)
    extra = [
        p
        for p in EXIT_COMPOSITION_SHIFT_RCA_OUT.iterdir()
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}
    ]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (EXIT_COMPOSITION_SHIFT_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (EXIT_COMPOSITION_SHIFT_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(EXIT_COMPOSITION_SHIFT_RCA_OUT / "audit.xlsx")


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:,.1f}"
        return f"{v:.4f}"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}

    sheets["summary"] = kv_rows(
        {
            "analysis_id": ANALYSIS_ID,
            "verdict": dec.get("VERDICT"),
            "case": dec.get("CASE"),
            "primary_shift_driver": dec.get("PRIMARY_SHIFT_DRIVER"),
            "secondary_drivers": dec.get("SECONDARY_DRIVERS"),
            "primary_next_exit_target": dec.get("PRIMARY_NEXT_EXIT_TARGET"),
            "raw_rank_reversal_explained": dec.get("raw_rank_reversal_explained"),
        }
    )
    sheets["integrity"] = kv_rows(dict(report.get("integrity") or {}))
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})

    inc_rows: list[dict[str, Any]] = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for ck in PRIMARY_FAILURE_KEYS:
            row = dict(pack.get("by_class", {}).get(ck) or {})
            row["cohort"] = cohort
            row["class"] = ck
            inc_rows.append(row)
    sheets["incidence_severity"] = inc_rows

    decomp_rows = []
    for ck, d in dict(dec.get("decomposition") or {}).items():
        rec = dict(d)
        rec["class"] = ck
        decomp_rows.append(rec)
    sheets["decomposition"] = decomp_rows

    role_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for role in ("CORE", "ADDED"):
            rp = dict(pack.get("roles", {}).get(role) or {})
            for ck in PRIMARY_FAILURE_KEYS:
                role_rows.append(
                    {
                        "cohort": cohort,
                        "role": role,
                        "class": ck,
                        **dict(rp.get(ck) or {}),
                        "role_fill_n": rp.get("role_fill_n"),
                    }
                )
    sheets["roles"] = role_rows

    std_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        rs = dict(pack.get("role_standardized") or {})
        for ck in PRIMARY_FAILURE_KEYS:
            std_rows.append({"cohort": cohort, "class": ck, **dict(rs.get(ck) or {})})
        std_rows.append({"cohort": cohort, "class": "_RANK", "gross_rank": rs.get("gross_rank")})
    sheets["role_standardized"] = std_rows

    horizon_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for ck in PRIMARY_FAILURE_KEYS:
            horizon_rows.append({"cohort": cohort, "class": ck, **dict(pack.get("horizon", {}).get(ck) or {})})
    sheets["horizon"] = horizon_rows

    conc_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for ck in PRIMARY_FAILURE_KEYS:
            conc_rows.append({"cohort": cohort, "class": ck, **dict(pack.get("concentration", {}).get(ck) or {})})
    sheets["concentration"] = conc_rows

    loo_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for kind, key in (("day", "leave_one_out_day"), ("symbol", "leave_one_out_symbol")):
            for ck in PRIMARY_FAILURE_KEYS:
                loo_rows.append(
                    {"cohort": cohort, "kind": kind, "class": ck, **dict(pack.get(key, {}).get(ck) or {})}
                )
    sheets["leave_one_out"] = loo_rows

    be_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        be = dict(pack.get("be_reach") or {})
        be_rows.append({"cohort": cohort, "scope": "ALL", **{k: v for k, v in be.items() if k not in ("CORE", "ADDED")}})
        for role in ("CORE", "ADDED"):
            be_rows.append({"cohort": cohort, "scope": role, **dict(be.get(role) or {})})
    sheets["be_reach"] = be_rows

    ptf_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        ptf_rows.append({"cohort": cohort, **dict(pack.get("ptf") or {})})
        for role in ("CORE", "ADDED"):
            rn = int(pack.get("roles", {}).get(role, {}).get("role_fill_n") or 0)
            rbe = int(pack.get("be_reach", {}).get(role, {}).get("be_reached_n") or 0)
            ptf_rows.append({"cohort": cohort, "role": role, **dict(pack.get("roles", {}).get(role, {}).get("P_PROFIT_THEN_FAILURE") or {}), "role_fill_n": rn, "be_reached_n": rbe})
    sheets["ptf"] = ptf_rows

    return sheets


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        "## Verdict",
        f"- CASE: {dec.get('CASE')}",
        f"- VERDICT: {dec.get('VERDICT')}",
        f"- PRIMARY_SHIFT_DRIVER: {dec.get('PRIMARY_SHIFT_DRIVER')}",
        f"- SECONDARY_DRIVERS: {_fmt(dec.get('SECONDARY_DRIVERS'))}",
        f"- PRIMARY_NEXT_EXIT_TARGET: {dec.get('PRIMARY_NEXT_EXIT_TARGET')}",
        f"- NEXT: {dec.get('NEXT')}",
        "",
        "## Identity",
        f"- DEV: fill={req.get('DEV_CONTROL_FILL_N')} CORE/ADDED={req.get('DEV_CONTROL_CORE_N')}/{req.get('DEV_CONTROL_ADDED_N')} PnL={_fmt(req.get('DEV_CONTROL_PNL'))}",
        f"- FWD: fill={req.get('FWD_CONTROL_FILL_N')} CORE/ADDED={req.get('FWD_CONTROL_CORE_N')}/{req.get('FWD_CONTROL_ADDED_N')} PnL={_fmt(req.get('FWD_CONTROL_PNL'))}",
        "",
        "## Gross terminal loss rank",
        f"- DEV raw: {_fmt(dec.get('raw_gross_rank', {}).get('DEVELOPMENT'))}",
        f"- FWD raw: {_fmt(dec.get('raw_gross_rank', {}).get('FORWARD_BURNED'))}",
        f"- DEV role-std: {_fmt(dec.get('role_std_gross_rank', {}).get('DEVELOPMENT'))}",
        f"- FWD role-std: {_fmt(dec.get('role_std_gross_rank', {}).get('FORWARD_BURNED'))}",
        f"- raw reversal explained: {_fmt(dec.get('raw_rank_reversal_explained'))}",
        "",
        "## Incidence / severity (gross per fill)",
    ]
    for ck in PRIMARY_FAILURE_KEYS:
        d = dev.get("by_class", {}).get(ck, {})
        f = fwd.get("by_class", {}).get(ck, {})
        de = dec.get("decomposition", {}).get(ck, {})
        lines.append(
            f"- {ck}: DEV rate={_fmt(d.get('class_rate'))} sev={_fmt(d.get('severity_per_class_trade'))} glpf={_fmt(d.get('gross_loss_per_fill'))} | "
            f"FWD rate={_fmt(f.get('class_rate'))} sev={_fmt(f.get('severity_per_class_trade'))} glpf={_fmt(f.get('gross_loss_per_fill'))} | "
            f"INC={_fmt(de.get('INCIDENCE_EFFECT'))} SEV={_fmt(de.get('SEVERITY_EFFECT'))}"
        )
    lines.extend(
        [
            "",
            "## Concentration warnings",
            _fmt(dec.get("concentration_warnings")),
            "",
            "## Leave-one-out unstable",
            _fmt(dec.get("leave_one_out_unstable")),
            "",
            "STOP.",
        ]
    )
    return "\n".join(lines) + "\n"
