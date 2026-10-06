"""Publish the transmission precommit. No symbol-beta columns."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_state_transmission_precommit import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import OUT

SHEETS = (
    "Manifest",
    "Parent_Mechanisms",
    "Parent_Mechanism_SHA",
    "Universe105",
    "Sector3650_Targets",
    "Target_Sets",
    "Leave_Target_Out",
    "Input_Feasibility",
    "Discovery_Days",
    "Discovery_Folds",
    "FV_Input_Days",
    "FV_Folds",
    "Timestamp_Semantics",
    "Freshness",
    "Family270",
    "Models",
    "Bootstrap_Discovery",
    "Bootstrap_FV",
    "Inference",
    "Discovery_Gates",
    "FV_Gates",
    "Coverage_Gates",
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
        return pd.DataFrame([{"empty": True}])
    flat = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if isinstance(v, (dict, list, tuple)):
                row[k] = json.dumps(_clean(v), ensure_ascii=False)
            else:
                row[k] = v
        flat.append(row)
    return pd.DataFrame(flat)


def _markdown(report: dict[str, Any]) -> str:
    a = report["answers"]
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- parent verdict `{a.get('parent_verdict')}`",
        f"- parent precommit SHA `{a.get('parent_precommit_sha256')}`",
        f"- parent_mechanism_n `{a.get('parent_mechanism_n')}`",
        f"- parent_mechanism_set_sha256 `{a.get('parent_mechanism_set_sha256')}`",
        f"- global target n `{a.get('global_target_n')}`",
        f"- sector3650 target n `{a.get('sector3650_target_n')}`",
        f"- global_target_set_sha256 `{a.get('global_target_set_sha256')}`",
        f"- sector3650_target_set_sha256 `{a.get('sector3650_target_set_sha256')}`",
        f"- family_n `{a.get('family_n')}`",
        f"- family_sha256 `{a.get('family_sha256')}`",
        f"- all 270 input-feasible `{a.get('input_feasible_all_270')}`",
        f"- structural peer-infeasible hypotheses `{a.get('structural_peer_infeasible_n')}`",
        f"- reason `{a.get('reason')}`",
        f"- LTO all 270 `{a.get('lto_all_270')}`",
        f"- discovery date n `{a.get('discovery_date_n')}`",
        f"- discovery date SHA `{a.get('discovery_date_sha256')}`",
        f"- FV input-eligible n `{a.get('fv_input_eligible_n')}`",
        f"- FV eligible-day SHA `{a.get('fv_eligible_day_sha256')}`",
        f"- FV fold SHA `{a.get('fv_fold_sha256')}`",
        f"- discovery bootstrap SHA `{a.get('discovery_bootstrap_sha256')}`",
        f"- FV bootstrap SHA `{a.get('fv_bootstrap_sha256')}`",
        f"- symbol outcomes opened `{a.get('symbol_outcomes_opened')}`",
        f"- FV economic opened `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- Prospective opened `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    contract = evaluation.get("contract") or {}
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "parent_verdict": evaluation.get("parent_verdict"),
        "parent_precommit_sha256": evaluation.get("parent_precommit_sha256"),
        "parent_mechanism_n": evaluation.get("parent_mechanism_n"),
        "parent_mechanism_set_sha256": evaluation.get("parent_mechanism_set_sha256"),
        "global_target_n": evaluation.get("global_target_n"),
        "sector3650_target_n": evaluation.get("sector3650_target_n"),
        "global_target_set_sha256": evaluation.get("global_target_set_sha256"),
        "sector3650_target_set_sha256": evaluation.get("sector3650_target_set_sha256"),
        "family_n": evaluation.get("family_n"),
        "family_sha256": evaluation.get("family_sha256"),
        "input_feasible_all_270": evaluation.get("input_feasible_all_270"),
        "feasibility_fail_n": evaluation.get("feasibility_fail_n"),
        "structural_peer_infeasible_n": evaluation.get("structural_peer_infeasible_n"),
        "structural_peer_symbols": evaluation.get("structural_peer_symbols"),
        "lto_all_270": evaluation.get("lto_all_270"),
        "discovery_date_n": evaluation.get("discovery_date_n"),
        "discovery_dev_n": evaluation.get("discovery_dev_n"),
        "discovery_c1_n": evaluation.get("discovery_c1_n"),
        "discovery_date_sha256": evaluation.get("discovery_date_sha256"),
        "discovery_fold_sha256": evaluation.get("discovery_fold_sha256"),
        "fv_input_eligible_n": evaluation.get("fv_input_eligible_n"),
        "fv_eligible_day_sha256": evaluation.get("fv_eligible_day_sha256"),
        "fv_fold_sha256": evaluation.get("fv_fold_sha256"),
        "discovery_bootstrap_sha256": evaluation.get("discovery_bootstrap_sha256"),
        "fv_bootstrap_sha256": evaluation.get("fv_bootstrap_sha256"),
        "symbol_outcomes_opened": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": _clean({k: v for k, v in evaluation.items() if k != "identity"}),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    feas_rows = list(evaluation.get("feasibility_rows") or [])
    family_rows = list(evaluation.get("family_rows") or [])
    inf = contract.get("inference") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df([{"verdict": evaluation.get("VERDICT"), "next": evaluation.get("NEXT"), "precommit_sha256": evaluation.get("precommit_sha256")}]).to_excel(
            xw, sheet_name="Manifest", index=False
        )
        _df(list(evaluation.get("mechanisms") or [])).to_excel(xw, sheet_name="Parent_Mechanisms", index=False)
        _df([{"parent_mechanism_set_sha256": evaluation.get("parent_mechanism_set_sha256")}]).to_excel(xw, sheet_name="Parent_Mechanism_SHA", index=False)
        _df([{"universe105_sha256": evaluation.get("universe105_sha256"), "n": evaluation.get("global_target_n"), "global_target_set_sha256": evaluation.get("global_target_set_sha256")}]).to_excel(
            xw, sheet_name="Universe105", index=False
        )
        _df([{"symbol": s, "sector_id": "3650"} for s in (evaluation.get("sector3650_symbols") or [])] or [{"empty": True}]).to_excel(
            xw, sheet_name="Sector3650_Targets", index=False
        )
        _df(
            [
                {
                    "global_target_n": evaluation.get("global_target_n"),
                    "global_target_set_sha256": evaluation.get("global_target_set_sha256"),
                    "sector3650_target_n": evaluation.get("sector3650_target_n"),
                    "sector3650_target_set_sha256": evaluation.get("sector3650_target_set_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Target_Sets", index=False)
        _df(family_rows or [{"empty": True}]).to_excel(xw, sheet_name="Leave_Target_Out", index=False)
        _df(feas_rows or [{"empty": True}]).to_excel(xw, sheet_name="Input_Feasibility", index=False)
        _df(
            [
                {
                    "discovery_date_n": evaluation.get("discovery_date_n"),
                    "discovery_dev_n": evaluation.get("discovery_dev_n"),
                    "discovery_c1_n": evaluation.get("discovery_c1_n"),
                    "discovery_date_sha256": evaluation.get("discovery_date_sha256"),
                    "label": "TRANSMISSION_DISCOVERY_EXPOSED",
                }
            ]
        ).to_excel(xw, sheet_name="Discovery_Days", index=False)
        _df([{"discovery_fold_sha256": evaluation.get("discovery_fold_sha256"), "DEV": "DISCOVERY_DEV", "C1": "DISCOVERY_C1"}]).to_excel(
            xw, sheet_name="Discovery_Folds", index=False
        )
        _df([{"fv_input_eligible_n": evaluation.get("fv_input_eligible_n"), "fv_eligible_day_sha256": evaluation.get("fv_eligible_day_sha256"), "economic_outcomes": False}]).to_excel(
            xw, sheet_name="FV_Input_Days", index=False
        )
        _df([evaluation.get("fv_folds") or {"empty": True}]).to_excel(xw, sheet_name="FV_Folds", index=False)
        _df([{"semantics": "BAR_START", "available_at": "bar_start+1m", "price": "last completed same-session close", "no_future_fill": True}]).to_excel(
            xw, sheet_name="Timestamp_Semantics", index=False
        )
        _df([{"driver_age_sec_le": 60, "target_primary_age_sec_le": 120, "target_strict_age_sec_le": 60, "same_session_only": True}]).to_excel(
            xw, sheet_name="Freshness", index=False
        )
        _df(family_rows or [{"empty": True}]).to_excel(xw, sheet_name="Family270", index=False)
        _df([contract.get("models") or {"empty": True}]).to_excel(xw, sheet_name="Models", index=False)
        _df([((evaluation.get("bootstraps") or {}).get("discovery") or {"empty": True})]).to_excel(xw, sheet_name="Bootstrap_Discovery", index=False)
        _df([((evaluation.get("bootstraps") or {}).get("fv") or {"empty": True})]).to_excel(xw, sheet_name="Bootstrap_FV", index=False)
        _df([inf or {"empty": True}]).to_excel(xw, sheet_name="Inference", index=False)
        _df([contract.get("discovery_gates") or {"empty": True}]).to_excel(xw, sheet_name="Discovery_Gates", index=False)
        _df([contract.get("fv_gates") or {"empty": True}]).to_excel(xw, sheet_name="FV_Gates", index=False)
        _df([contract.get("coverage_gates") or {"empty": True}]).to_excel(xw, sheet_name="Coverage_Gates", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
