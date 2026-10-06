"""Publish the V1.1 control-regime precommit. No symbol-beta columns."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.isolation import OUT

SHEETS = (
    "Manifest",
    "Parent_Mechanisms",
    "Target_Sets",
    "Blocked_V1",
    "Peer_Structure",
    "Control_Regimes",
    "Single_Peer_Map",
    "No_Peer_Targets",
    "Leave_Target_Out",
    "Model_Contracts",
    "Input_Feasibility",
    "Discovery_Days",
    "Discovery_Folds",
    "FV_Input_Days",
    "FV_Folds",
    "Bootstrap_Discovery",
    "Bootstrap_FV",
    "Inference",
    "Discovery_Gates",
    "FV_Gates",
    "Coverage_Gates",
    "Small_Sector_Firewall",
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
            row[k] = json.dumps(_clean(v), ensure_ascii=False) if isinstance(v, (dict, list, tuple)) else v
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
        f"- old blocker `{a.get('old_blocker')}`",
        f"- family_n `{a.get('family_n')}`",
        f"- family hypotheses unchanged `{a.get('family_hypotheses_unchanged')}`",
        f"- MULTI_PEER target n `{a.get('multi_peer_target_n')}`",
        f"- SINGLE_PEER target n `{a.get('single_peer_target_n')}`",
        f"- NO_PEER target n `{a.get('no_peer_target_n')}`",
        f"- 34 old blocked hypotheses resolved `{a.get('old_34_resolved')}`",
        f"- all 270 input-feasible `{a.get('input_feasible_all_270')}`",
        f"- 9501 regime `{a.get('regime_9501')}`",
        f"- 1515 regime `{a.get('regime_1515')}`",
        f"- 3650 example `{a.get('regime_3650')}`",
        f"- LTO all 270 `{a.get('lto_all_270')}`",
        f"- symbol_control_contract_id `{a.get('symbol_control_contract_id')}`",
        f"- symbol_control_contract_sha256 `{a.get('symbol_control_contract_sha256')}`",
        f"- family_hypothesis_sha256 `{a.get('family_hypothesis_sha256')}`",
        f"- model_contract_sha256 `{a.get('model_contract_sha256')}`",
        f"- family_serialization_sha256 `{a.get('family_serialization_sha256')}`",
        f"- global_target_set_sha256 `{a.get('global_target_set_sha256')}`",
        f"- sector3650_target_set_sha256 `{a.get('sector3650_target_set_sha256')}`",
        f"- discovery date SHA `{a.get('discovery_date_sha256')}`",
        f"- FV date SHA `{a.get('fv_eligible_day_sha256')}`",
        f"- discovery bootstrap SHA `{a.get('discovery_bootstrap_sha256')}`",
        f"- FV bootstrap SHA `{a.get('fv_bootstrap_sha256')}`",
        f"- symbol outcomes opened `{a.get('symbol_outcomes_opened')}`",
        f"- FV economic opened `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- Prospective opened `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    examples = evaluation.get("examples") or {}
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "old_blocker": evaluation.get("old_blocker"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "parent_verdict": evaluation.get("parent_verdict"),
        "parent_precommit_sha256": evaluation.get("parent_precommit_sha256"),
        "parent_mechanism_set_sha256": evaluation.get("parent_mechanism_set_sha256"),
        "family_n": evaluation.get("family_n"),
        "family_hypotheses_unchanged": evaluation.get("family_hypotheses_unchanged"),
        "family_serialization_sha256": evaluation.get("family_serialization_sha256"),
        "family_hypothesis_sha256": evaluation.get("family_hypothesis_sha256"),
        "model_contract_sha256": evaluation.get("model_contract_sha256"),
        "multi_peer_target_n": evaluation.get("multi_peer_target_n"),
        "single_peer_target_n": evaluation.get("single_peer_target_n"),
        "no_peer_target_n": evaluation.get("no_peer_target_n"),
        "old_34_resolved": evaluation.get("old_34_resolved"),
        "input_feasible_all_270": evaluation.get("input_feasible_all_270"),
        "regime_9501": (examples.get("symbol_9501") or {}).get("control_regime"),
        "regime_1515": (examples.get("symbol_1515") or {}).get("control_regime"),
        "sole_peer_1515": (examples.get("symbol_1515") or {}).get("sole_peer"),
        "regime_3650": (examples.get("sector3650_example") or {}).get("control_regime"),
        "sector3650_example_symbol": examples.get("sector3650_example_symbol"),
        "pool_n_peer_3650": (examples.get("sector3650_example") or {}).get("pool_n_peer_pit"),
        "lto_all_270": evaluation.get("lto_all_270"),
        "symbol_control_contract_id": evaluation.get("symbol_control_contract_id"),
        "symbol_control_contract_sha256": evaluation.get("symbol_control_contract_sha256"),
        "global_target_set_sha256": evaluation.get("global_target_set_sha256"),
        "sector3650_target_set_sha256": evaluation.get("sector3650_target_set_sha256"),
        "discovery_date_sha256": evaluation.get("discovery_date_sha256"),
        "discovery_fold_sha256": evaluation.get("discovery_fold_sha256"),
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
        "evaluation": _clean(evaluation),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    contract = evaluation.get("contract") or {}
    models = evaluation.get("model_definitions") or {}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    blocked_rows = [r for r in (evaluation.get("feasibility_rows") or []) if r.get("old_v1_blocked")]
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df([{"verdict": answers["VERDICT"], "next": answers["NEXT"], "precommit_sha256": answers["precommit_sha256"], "old_blocker": answers["old_blocker"]}]).to_excel(xw, sheet_name="Manifest", index=False)
        _df(list(evaluation.get("mechanisms") or [])).to_excel(xw, sheet_name="Parent_Mechanisms", index=False)
        _df([{"global_target_set_sha256": answers["global_target_set_sha256"], "sector3650_target_set_sha256": answers["sector3650_target_set_sha256"], "family_serialization_sha256": answers["family_serialization_sha256"], "family_hypothesis_sha256": answers["family_hypothesis_sha256"]}]).to_excel(xw, sheet_name="Target_Sets", index=False)
        _df(blocked_rows or [{"old_blocker": answers["old_blocker"], "n": 34}]).to_excel(xw, sheet_name="Blocked_V1", index=False)
        _df(list(evaluation.get("peer_structure") or [])).to_excel(xw, sheet_name="Peer_Structure", index=False)
        _df([models.get("regimes") or {"empty": True}]).to_excel(xw, sheet_name="Control_Regimes", index=False)
        _df(list(evaluation.get("single_peer_map") or [])).to_excel(xw, sheet_name="Single_Peer_Map", index=False)
        _df(list(evaluation.get("no_peer_targets") or [])).to_excel(xw, sheet_name="No_Peer_Targets", index=False)
        _df(list(evaluation.get("family_rows") or [])).to_excel(xw, sheet_name="Leave_Target_Out", index=False)
        _df([{"symbol_control_contract_id": answers["symbol_control_contract_id"], "symbol_control_contract_sha256": answers["symbol_control_contract_sha256"], "model_contract_sha256": answers["model_contract_sha256"], "hashing": models.get("hashing_semantics")}]).to_excel(xw, sheet_name="Model_Contracts", index=False)
        _df(list(evaluation.get("feasibility_rows") or [])).to_excel(xw, sheet_name="Input_Feasibility", index=False)
        _df([{"discovery_date_n": evaluation.get("discovery_date_n"), "discovery_dev_n": evaluation.get("discovery_dev_n"), "discovery_c1_n": evaluation.get("discovery_c1_n"), "discovery_date_sha256": answers["discovery_date_sha256"]}]).to_excel(xw, sheet_name="Discovery_Days", index=False)
        _df([{"discovery_fold_sha256": answers["discovery_fold_sha256"], "DEV": "DISCOVERY_DEV", "C1": "DISCOVERY_C1"}]).to_excel(xw, sheet_name="Discovery_Folds", index=False)
        _df([{"fv_input_eligible_n": evaluation.get("fv_input_eligible_n"), "fv_eligible_day_sha256": answers["fv_eligible_day_sha256"], "economic_outcomes": False}]).to_excel(xw, sheet_name="FV_Input_Days", index=False)
        _df([evaluation.get("fv_folds") or {"empty": True}]).to_excel(xw, sheet_name="FV_Folds", index=False)
        _df([(evaluation.get("bootstraps") or {}).get("discovery") or {"empty": True}]).to_excel(xw, sheet_name="Bootstrap_Discovery", index=False)
        _df([(evaluation.get("bootstraps") or {}).get("fv") or {"empty": True}]).to_excel(xw, sheet_name="Bootstrap_FV", index=False)
        _df([contract.get("inference") or models and {"ci": "BOOTSTRAP_PERCENTILE_CI", "p_value": "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE", "discovery_m": 270, "q": 0.05}]).to_excel(xw, sheet_name="Inference", index=False)
        _df([contract.get("discovery_gates") or {"T1": "beta_DEV>0 AND beta_C1>0", "T2": "CI lower>0", "T3": "BH q<=0.05 m=270", "T4": ">=4/5", "T5": "LOMO>=75%", "T6": "strict60 ratio 0.50"}]).to_excel(xw, sheet_name="Discovery_Gates", index=False)
        _df([contract.get("fv_gates") or {"F1": "beta_FV>0", "F2": "CI lower>0", "F3": ">=2/3", "F4": "LOMO>=75%", "F5": "strict60", "F6": "q<=0.05"}]).to_excel(xw, sheet_name="FV_Gates", index=False)
        _df([contract.get("coverage_gates") or (models.get("small_sector_firewall") or {})]).to_excel(xw, sheet_name="Coverage_Gates", index=False)
        _df([models.get("small_sector_firewall") or {"empty": True}]).to_excel(xw, sheet_name="Small_Sector_Firewall", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
