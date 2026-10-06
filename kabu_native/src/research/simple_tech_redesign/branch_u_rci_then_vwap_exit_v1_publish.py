"""Publish exactly 3 artifacts for UNPROVEN_RCI_REOVERSOLD_THEN_VWAP_LOSS_V1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT
from research.simple_tech_redesign.branch_u_rci_then_vwap_exit_v1_spec import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    STATE_MACHINE,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
)

SHEET_ORDER = (
    "summary",
    "predicate",
    "state_machine",
    "unconstrained",
    "identity",
    "class_audit",
    "direct",
    "winner_harm",
    "portfolio",
    "incremental",
    "days",
    "concentration",
    "core_added",
    "fwd",
    "common_fills",
    "incremental_fills",
    "answers",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    unc = dict(report.get("unconstrained_audit") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "CANDIDATE_FROZEN": dec.get("CANDIDATE_FROZEN"),
            "FAMILY_CLOSED": dec.get("FAMILY_CLOSED"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
            "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        }
    )
    sheets["predicate"] = kv_rows(dict(report.get("predicate") or {}))
    sheets["state_machine"] = kv_rows(dict(STATE_MACHINE))
    unc_rows = [{"scope": "overall", **dict(unc.get("overall") or {})}]
    for name, d in dict(unc.get("by_class") or {}).items():
        unc_rows.append({"scope": "class", "residual_class": name, **dict(d)})
    sheets["unconstrained"] = unc_rows
    sheets["identity"] = kv_rows(
        {
            "UNCONSTRAINED_232": unc.get("identity") or report.get("identity_232"),
            "DEV": dev.get("identity"),
            "BURNED_STRESS": fwd.get("identity"),
        }
    )
    class_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("BURNED_STRESS", fwd)):
        for name, d in dict(pack.get("class_audit") or {}).items():
            class_rows.append({"cohort": cohort, "residual_class": name, **dict(d)})
    sheets["class_audit"] = class_rows or [{"empty": True}]
    sheets["direct"] = kv_rows(
        {
            "DEV_technical_exit_n": dev.get("technical_exit_n"),
            "RCI_ARM_N": dev.get("RCI_ARM_N"),
            "VWAP_CONFIRM_N": dev.get("VWAP_CONFIRM_N"),
            "U_EARLY_NEVER_BE_DIRECT_DELTA": dev.get("U_EARLY_NEVER_BE_DIRECT_DELTA"),
            "P_EARLY_DIRECT_DELTA": dev.get("P_EARLY_DIRECT_DELTA"),
            "GOOD_DIRECT_DELTA": dev.get("GOOD_DIRECT_DELTA"),
            "DIP_DIRECT_DELTA": dev.get("DIP_DIRECT_DELTA"),
            "PTF_DIRECT_DELTA": dev.get("PTF_DIRECT_DELTA"),
            "A_DIRECT_EXIT_DELTA": dev.get("DIRECT_EXIT_DELTA"),
            "B_SLOT_RELEASE_DOWNSTREAM_DELTA": dev.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "C_DISPLACED_TRADE_DELTA": dev.get("DISPLACED_TRADE_DELTA"),
            "TOTAL_CAUSAL_DELTA": dev.get("TOTAL_CAUSAL_DELTA"),
        }
    )
    harm_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("BURNED_STRESS", fwd)):
        for lab, body in dict(pack.get("winner_harm") or {}).items():
            if not isinstance(body, dict):
                harm_rows.append({"cohort": cohort, "class": lab, "value": body})
                continue
            harm_rows.append({"cohort": cohort, "class": lab, **{k: v for k, v in dict(body).items() if k != "trades"}})
            for t in list(body.get("trades") or []):
                harm_rows.append({"cohort": cohort, "class": lab, **dict(t)})
    sheets["winner_harm"] = harm_rows or [{"empty": True}]
    port_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("BURNED_STRESS", fwd)):
        for arm in ("control", "treatment"):
            port_rows.append({"cohort": cohort, "arm": arm, **dict(pack.get(arm) or {})})
    sheets["portfolio"] = port_rows or [{"empty": True}]
    sheets["incremental"] = kv_rows(
        {
            "DEV": {k: v for k, v in dict(dev.get("incremental") or {}).items() if k != "trades"},
            "BURNED": {k: v for k, v in dict(fwd.get("incremental") or {}).items() if k != "trades"},
        }
    )
    sheets["days"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("days") or [])] + [
        {"cohort": "BURNED_STRESS", **r} for r in list(fwd.get("days") or [])
    ] or [{"empty": True}]
    sheets["concentration"] = kv_rows({"DEV": dev.get("concentration"), "BURNED_STRESS": fwd.get("concentration")})
    sheets["core_added"] = kv_rows({"DEV": dev.get("core_added"), "BURNED_STRESS": fwd.get("core_added")})
    sheets["fwd"] = kv_rows(
        {
            "U_EARLY_NEVER_BE_DIRECT_DELTA": fwd.get("U_EARLY_NEVER_BE_DIRECT_DELTA"),
            "P_EARLY_DIRECT_DELTA": fwd.get("P_EARLY_DIRECT_DELTA"),
            "PROTECTED_KEEP_DIRECT_DELTA": fwd.get("PROTECTED_KEEP_DIRECT_DELTA"),
            "TOTAL_CAUSAL_DELTA": fwd.get("TOTAL_CAUSAL_DELTA"),
            "control": fwd.get("control"),
            "treatment": fwd.get("treatment"),
        }
    )
    sheets["common_fills"] = list(dev.get("common_fills") or []) + [
        {"cohort": "BURNED_STRESS", **r} for r in list(fwd.get("common_fills") or [])
    ] or [{"empty": True}]
    sheets["incremental_fills"] = list((dev.get("incremental") or {}).get("trades") or []) + [
        {"cohort": "BURNED_STRESS", **t} for t in list((fwd.get("incremental") or {}).get("trades") or [])
    ] or [{"empty": True}]
    ans = dict(report.get("answers") or {})
    sheets["answers"] = [{"n": k, "answer": ans[k]} for k in sorted(ans, key=lambda x: int(x) if str(x).isdigit() else 999)]
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    for name in SHEET_ORDER:
        if not sheets.get(name):
            sheets[name] = [{"empty": True}]
    return sheets


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    pred = dict(report.get("predicate") or {})
    unc = dict(report.get("unconstrained_audit") or {})
    answers = dict(report.get("answers") or {})
    rci = dict(pred.get("RCI") or {})
    vwap = dict(pred.get("VWAP") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**Candidate:** `{CANDIDATE_ID}`",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**CANDIDATE_FROZEN:** `{dec.get('CANDIDATE_FROZEN')}` FAMILY_CLOSED=`{dec.get('FAMILY_CLOSED')}`",
        "",
        "## Exact predicates",
        f"- RCI FILE={rci.get('SOURCE_FILE')} FN={rci.get('SOURCE_FUNCTION')} LINE={rci.get('SOURCE_LINE')} SHA={rci.get('SOURCE_SHA')}",
        f"- RCI_EXACT_PREDICATE={rci.get('RCI_EXACT_PREDICATE')}",
        f"- VWAP FILE={vwap.get('SOURCE_FILE')} FN={vwap.get('SOURCE_FUNCTION')} LINE={vwap.get('SOURCE_LINE')} SHA={vwap.get('SOURCE_SHA')}",
        f"- VWAP_EXACT_PREDICATE={vwap.get('VWAP_EXACT_PREDICATE')}",
        f"- TIE={pred.get('TIE_RESOLUTION_RULE')}",
        "",
        "## Unconstrained 232",
        f"- identity={unc.get('identity') or report.get('identity_232')}",
        f"- overall={unc.get('overall')}",
        "",
        "## Control identity",
        f"- DEVELOPMENT: fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        f"- BURNED_STRESS: fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "",
        "## Direct / causal",
        f"- RCI_ARM/VWAP_CONFIRM/TECH={dev.get('RCI_ARM_N')}/{dev.get('VWAP_CONFIRM_N')}/{dev.get('technical_exit_n')}",
        f"- U_EARLY={_n(dev.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))} P_EARLY={_n(dev.get('P_EARLY_DIRECT_DELTA'))} GOOD={_n(dev.get('GOOD_DIRECT_DELTA'))} DIP={_n(dev.get('DIP_DIRECT_DELTA'))}",
        f"- TOTAL_CAUSAL_DELTA={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        "",
        "## Required answers",
    ]
    for k in sorted(answers, key=lambda x: int(x) if str(x).isdigit() else 999):
        lines.append(f"{k}. {answers[k]}")
    lines += ["", "STOP.", ""]
    return "\n".join(lines)
