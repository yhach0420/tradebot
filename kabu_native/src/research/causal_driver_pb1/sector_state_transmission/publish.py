"""Publish the transmission run. No symbol ranking."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_state_transmission import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_state_transmission.isolation import OUT

SHEETS = (
    "Manifest",
    "Identity_Check",
    "Parent_Mechanisms",
    "Target_Sets",
    "Control_Regimes",
    "Discovery_Rows",
    "Discovery_Results_270",
    "Discovery_BH",
    "Discovery_Gates",
    "Discovery_Candidates",
    "Candidate_Freeze",
    "FV_Access_Firewall",
    "FV_Rows",
    "FV_Results",
    "FV_BH",
    "FV_Gates",
    "Parent_Coverage",
    "Validated_Transmission",
    "Contamination",
    "Firewall",
    "Safety",
)


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900")


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and type(x).__module__.startswith("numpy"):
        return _clean(x.item())
    if isinstance(x, float) and x != x:
        return None
    return x


def _df(rows: list[dict[str, Any]]):
    import pandas as pd

    if not rows:
        return pd.DataFrame([{"status": "NOT_RUN"}])
    flat = []
    for r in rows:
        row = {}
        for k, v in r.items():
            row[k] = json.dumps(_clean(v), ensure_ascii=False) if isinstance(v, (dict, list, tuple)) else v
        flat.append(row)
    return pd.DataFrame(flat)


def _answers(ev: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "VERDICT",
        "NEXT",
        "reason",
        "precommit_id",
        "precommit_sha256",
        "parent_precommit_sha256",
        "parent_mechanism_set_sha256",
        "family_n",
        "family_serialization_sha256",
        "family_hypothesis_sha256",
        "model_contract_sha256",
        "symbol_control_contract_sha256",
        "discovery_evaluated_n",
        "T1_pass_n",
        "T2_pass_n",
        "T3_pass_n",
        "T4_pass_n",
        "T5_pass_n",
        "T6_pass_n",
        "transmission_candidate_n",
        "transmission_candidate_list",
        "transmission_candidate_list_sha256",
        "candidate_freeze_timestamp",
        "FV_future_return_rows_read_before_candidate_freeze",
        "first_FV_outcome_access_timestamp",
        "FV_evaluated_n",
        "FV_confirmed_n",
        "F1_pass_n",
        "F2_pass_n",
        "F3_pass_n",
        "F4_pass_n",
        "F5_pass_n",
        "F6_pass_n",
        "parent_results",
        "validated_parent_mechanisms",
        "validated_target_symbol_sets",
        "validated_transmission_sha256",
        "FROZEN_VALIDATION_ECONOMIC_OPENED",
        "PROSPECTIVE_DATA_OPENED",
        "ALPHA_CREATED",
        "MECHANISM_FROZEN",
        "PB1_BOUND",
        "COMPLETE_STRATEGY_RUN",
        "submit",
        "cancel",
        "live",
    )
    return {k: ev.get(k) for k in keys}


def _markdown(answers: dict[str, Any]) -> str:
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{answers.get('VERDICT')}`",
        "",
        f"**NEXT:** `{answers.get('NEXT')}`",
        "",
        f"- candidate n `{answers.get('transmission_candidate_n')}`",
        f"- candidate SHA `{answers.get('transmission_candidate_list_sha256')}`",
        f"- FV confirmed n `{answers.get('FV_confirmed_n')}`",
        f"- validated parents `{answers.get('validated_parent_mechanisms')}`",
        f"- FV economic opened `{answers.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- Prospective opened `{answers.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
    ]
    for row in answers.get("parent_results") or []:
        lines.append(
            f"- {row.get('parent_mechanism_id')}: discovery {row.get('discovery_candidate_target_n')} "
            f"FV confirmed {row.get('FV_confirmed_target_n')} standard {row.get('GLOBAL_STANDARD_CONFIRMED_TARGET_N')} "
            f"sectors {row.get('distinct_confirmed_sector_n')} share {row.get('largest_confirmed_sector_share')} "
            f"coverage {row.get('coverage_gate_pass')}"
        )
    return "\n".join(lines) + "\n"


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    answers = _answers(evaluation)
    report = {"program_id": PROGRAM_ID, "analysis_id": ANALYSIS_ID, "created_at": _now(), "answers": _clean(answers), "evaluation": _clean(evaluation), "safety": safety}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(answers), encoding="utf-8")
    disc = list(evaluation.get("discovery_rows") or [])
    fv = list(evaluation.get("fv_rows") or [])
    fv_open = bool(evaluation.get("FROZEN_VALIDATION_ECONOMIC_OPENED"))
    not_run = [{"status": "NOT_RUN"}]
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df([{"verdict": answers.get("VERDICT"), "next": answers.get("NEXT"), "reason": answers.get("reason")}]).to_excel(xw, sheet_name="Manifest", index=False)
        _df([{"precommit_sha256": answers.get("precommit_sha256"), "family_n": answers.get("family_n"), "blockers": evaluation.get("blockers")}]).to_excel(xw, sheet_name="Identity_Check", index=False)
        _df(list(evaluation.get("mechanisms") or [])).to_excel(xw, sheet_name="Parent_Mechanisms", index=False)
        _df([{"family_serialization_sha256": answers.get("family_serialization_sha256"), "family_hypothesis_sha256": answers.get("family_hypothesis_sha256"), "symbol_control_contract_sha256": answers.get("symbol_control_contract_sha256")}]).to_excel(xw, sheet_name="Target_Sets", index=False)
        _df([{"contract": "SECTOR_CONTROL_FEASIBILITY_BRANCH_V1_1", "model_contract_sha256": answers.get("model_contract_sha256")}]).to_excel(xw, sheet_name="Control_Regimes", index=False)
        _df([{"hypothesis_id": r.get("hypothesis_id"), "primary_row_n": r.get("primary_row_n"), "strict60_row_n": r.get("strict60_row_n")} for r in disc] or not_run).to_excel(xw, sheet_name="Discovery_Rows", index=False)
        _df(disc or not_run).to_excel(xw, sheet_name="Discovery_Results_270", index=False)
        _df([{"hypothesis_id": r.get("hypothesis_id"), "p": r.get("p"), "q": r.get("q")} for r in disc] or not_run).to_excel(xw, sheet_name="Discovery_BH", index=False)
        _df([{"T1": answers.get("T1_pass_n"), "T2": answers.get("T2_pass_n"), "T3": answers.get("T3_pass_n"), "T4": answers.get("T4_pass_n"), "T5": answers.get("T5_pass_n"), "T6": answers.get("T6_pass_n")}]).to_excel(xw, sheet_name="Discovery_Gates", index=False)
        _df(list(answers.get("transmission_candidate_list") or []) or not_run).to_excel(xw, sheet_name="Discovery_Candidates", index=False)
        _df([{"candidate_freeze_timestamp": answers.get("candidate_freeze_timestamp"), "transmission_candidate_list_sha256": answers.get("transmission_candidate_list_sha256"), "candidate_n": answers.get("transmission_candidate_n")}]).to_excel(xw, sheet_name="Candidate_Freeze", index=False)
        _df([{"rows_before_freeze": answers.get("FV_future_return_rows_read_before_candidate_freeze"), "first_FV_outcome_access_timestamp": answers.get("first_FV_outcome_access_timestamp"), "opened": fv_open}]).to_excel(xw, sheet_name="FV_Access_Firewall", index=False)
        _df([{k: r.get(k) for k in ("hypothesis_id", "primary_row_n", "strict60_row_n")} for r in fv] if fv_open else not_run).to_excel(xw, sheet_name="FV_Rows", index=False)
        _df(fv if fv_open else not_run).to_excel(xw, sheet_name="FV_Results", index=False)
        _df([{"hypothesis_id": r.get("hypothesis_id"), "p": r.get("p"), "q": r.get("q")} for r in fv] if fv_open else not_run).to_excel(xw, sheet_name="FV_BH", index=False)
        _df([{"F1": answers.get("F1_pass_n"), "F2": answers.get("F2_pass_n"), "F3": answers.get("F3_pass_n"), "F4": answers.get("F4_pass_n"), "F5": answers.get("F5_pass_n"), "F6": answers.get("F6_pass_n")}] if fv_open else not_run).to_excel(xw, sheet_name="FV_Gates", index=False)
        _df(list(answers.get("parent_results") or []) if fv_open or answers.get("VERDICT", "").endswith("NOT_FOUND_V1") else not_run).to_excel(xw, sheet_name="Parent_Coverage", index=False)
        _df([{"validated_parent_mechanisms": answers.get("validated_parent_mechanisms"), "validated_target_symbol_sets": answers.get("validated_target_symbol_sets"), "validated_transmission_sha256": answers.get("validated_transmission_sha256")}] if answers.get("validated_parent_mechanisms") is not None else not_run).to_excel(xw, sheet_name="Validated_Transmission", index=False)
        _df([evaluation.get("contamination") or {"status": "NOT_RUN"}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
