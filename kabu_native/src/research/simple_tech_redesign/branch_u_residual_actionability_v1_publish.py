"""Publish exactly 3 artifacts for Branch U residual actionability + optional one Full Causal."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT
from research.simple_tech_redesign.branch_u_residual_actionability_v1_spec import (
    ANALYSIS_ID,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
)

SHEET_ORDER = (
    "summary",
    "inventory",
    "predicate",
    "semantic_dup",
    "phase_a",
    "selection",
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
    BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    phase_a = dict(report.get("phase_a") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "SELECTED_PRIMITIVE": dec.get("SELECTED_PRIMITIVE") or report.get("selected_primitive"),
            "CANDIDATE_ID": dec.get("CANDIDATE_ID") or report.get("candidate_id"),
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "PHASE": dec.get("PHASE"),
            "CANDIDATE_FROZEN": dec.get("CANDIDATE_FROZEN"),
            "FAMILY_CLOSED": dec.get("FAMILY_CLOSED"),
            "PNL_USED_FOR_SELECTION": False,
            "CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B": report.get("CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
            "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        }
    )
    sheets["inventory"] = kv_rows(dict(report.get("inventory") or {}))
    pred_rows = []
    primitives = dict((report.get("predicate") or {}).get("primitives") or {})
    if primitives:
        for pid, body in primitives.items():
            pred_rows.append({"primitive": pid, **dict(body)})
    else:
        pred_rows = kv_rows(dict(report.get("predicate") or {}))
    sheets["predicate"] = pred_rows
    sheets["semantic_dup"] = list((report.get("semantic_duplicate") or {}).get("rows") or []) or [{"empty": True}]
    sheets["phase_a"] = list((phase_a.get("table") or [])) or [{"empty": True}]
    sheets["selection"] = kv_rows(dict(report.get("selection") or {}))
    sheets["identity"] = kv_rows(
        {
            "PHASE_A": phase_a.get("identity") or report.get("phase_a_identity"),
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
            "PRIM_FIRST_N": dev.get("PRIM_FIRST_N"),
            "BE_FIRST_N": dev.get("BE_FIRST_N"),
            "SAME_TIMESTAMP_N": dev.get("SAME_TIMESTAMP_N"),
            "U_EARLY_NEVER_BE_DIRECT_DELTA": dev.get("U_EARLY_NEVER_BE_DIRECT_DELTA"),
            "P_EARLY_DIRECT_DELTA": dev.get("P_EARLY_DIRECT_DELTA"),
            "GOOD_DIRECT_DELTA": dev.get("GOOD_DIRECT_DELTA"),
            "DIP_DIRECT_DELTA": dev.get("DIP_DIRECT_DELTA"),
            "PROTECTED_KEEP_DIRECT_DELTA": dev.get("PROTECTED_KEEP_DIRECT_DELTA"),
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


def _phase_a_lines(phase_a: dict[str, Any]) -> list[str]:
    lines = ["## Phase A table", ""]
    for r in list(phase_a.get("table") or []):
        day = dict(r.get("top_U_hit_day") or {})
        sym = dict(r.get("top_U_hit_symbol") or {})
        lines.append(
            f"- `{r.get('primitive')}` qualified={r.get('qualified')} dup={r.get('SEMANTIC_DUPLICATE')} "
            f"U_EARLY hit {r.get('U_EARLY_HIT_N')}/{r.get('U_EARLY_N')} rate={_n(r.get('U_EARLY_HIT_RATE'))} "
            f"P_EARLY false {r.get('P_EARLY_PRE_BE_HIT_N')}/{r.get('P_EARLY_N')} "
            f"GOOD={r.get('GOOD_PRE_BE_HIT_N')} DIP={r.get('DIP_PRE_BE_HIT_N')} "
            f"CORE/ADDED U hit={r.get('CORE_U_HIT_N')}/{r.get('ADDED_U_HIT_N')} "
            f"hit_day_n={r.get('hit_day_n')} hit_symbol_n={r.get('hit_symbol_n')} "
            f"top_day={day.get('top')} share={_n(day.get('abs_share'))} "
            f"top_symbol={sym.get('top')} share={_n(sym.get('abs_share'))}"
        )
    if not list(phase_a.get("table") or []):
        lines.append("- (empty)")
    return lines


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    phase_a = dict(report.get("phase_a") or {})
    sel = dict(report.get("selection") or {})
    inv = dict(report.get("inventory") or {})
    answers = dict(report.get("answers") or {})
    cid = dec.get("CANDIDATE_ID") or report.get("candidate_id")
    pid = dec.get("SELECTED_PRIMITIVE") or report.get("selected_primitive")
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (PHASE {dec.get('PHASE')} CASE {dec.get('CASE')})",
        f"**SELECTED_PRIMITIVE:** `{pid}` **CANDIDATE_ID:** `{cid}`",
        f"**CANDIDATE_FROZEN:** `{dec.get('CANDIDATE_FROZEN')}` FAMILY_CLOSED=`{dec.get('FAMILY_CLOSED')}`",
        f"**PNL_USED_FOR_SELECTION:** `false` **spec frozen before Phase B:** `{report.get('CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B')}`",
        "",
        "## Remaining primitive inventory",
        f"- remaining: `{inv.get('remaining')}`",
        f"- excluded: `{inv.get('excluded')}`",
        "",
        *_phase_a_lines(phase_a),
        "",
        f"## Selection (qualified_n={sel.get('n')})",
        f"- selected=`{sel.get('selected')}` rule=`{sel.get('rule')}` PNL_USED=`{sel.get('PNL_USED_FOR_SELECTION')}`",
        "",
        "## Control identity",
        f"- PHASE_A unconstrained: {phase_a.get('identity') or report.get('phase_a_identity')}",
        f"- DEVELOPMENT occupancy: fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        f"- BURNED_STRESS occupancy: fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "",
        "## Direct / causal",
        f"- PRIM_FIRST/BE_FIRST/SAME={dev.get('PRIM_FIRST_N')}/{dev.get('BE_FIRST_N')}/{dev.get('SAME_TIMESTAMP_N')}",
        f"- U_EARLY_DIRECT={_n(dev.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))} P_EARLY={_n(dev.get('P_EARLY_DIRECT_DELTA'))} GOOD={_n(dev.get('GOOD_DIRECT_DELTA'))} DIP={_n(dev.get('DIP_DIRECT_DELTA'))}",
        f"- A={_n(dev.get('DIRECT_EXIT_DELTA'))} B={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))} C={_n(dev.get('DISPLACED_TRADE_DELTA'))} TOTAL={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        f"- DEV PnL Control/Treatment={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))}",
        f"- Burned TOTAL={_n(fwd.get('TOTAL_CAUSAL_DELTA'))} U_EARLY={_n(fwd.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))} P_EARLY={_n(fwd.get('P_EARLY_DIRECT_DELTA'))}",
        "",
        "## Required answers",
    ]
    for k in sorted(answers, key=lambda x: int(x) if str(x).isdigit() else 999):
        lines.append(f"{k}. {answers[k]}")
    lines += ["", "STOP.", ""]
    return "\n".join(lines)
