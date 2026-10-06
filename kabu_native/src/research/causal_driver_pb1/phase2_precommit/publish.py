"""Write report.json / report.md / audit.xlsx for Phase 2 precommit only."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.phase2_precommit import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.phase2_precommit.isolation import OUT

SHEETS = (
    "Manifest",
    "Universe105",
    "Sector_Mapping",
    "Stock_Semantics",
    "USDJPY_Identity",
    "Research_Clock",
    "Folds",
    "Primary_Model",
    "Gates",
    "Placebos",
    "Eligibility",
    "Missingness",
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
    if isinstance(x, float) and x != x:
        return None
    return x


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
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
    return "\n".join(
        [
            f"# {PROGRAM_ID} / {ANALYSIS_ID}",
            "",
            f"**VERDICT:** `{a.get('VERDICT')}`",
            "",
            f"**NEXT:** `{a.get('NEXT')}`",
            "",
            "This artifact is a Phase 2 **precommit** only. No USDJPY lead outcomes were estimated.",
            "",
            f"- precommit_id `{a.get('precommit_id')}`",
            f"- precommit_sha256 `{a.get('precommit_sha256')}`",
            f"- stock_timestamp_semantics_proven `{a.get('stock_timestamp_semantics_proven')}`",
            f"- universe105_sha256 `{a.get('universe105_sha256')}`",
            f"- sector_mapping_sha256 `{a.get('sector_mapping_sha256')}`",
            f"- parent_phase1_inventory_sha256 `{a.get('parent_phase1_inventory_sha256')}`",
            f"- USDJPY_source_fingerprint `{a.get('USDJPY_source_fingerprint')}`",
            "",
            "Phase 2 execution is not started. C1 must not select the rule.",
            "",
        ]
    )


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "parent_phase1_inventory_sha256": evaluation.get("parent_phase1_inventory_sha256"),
        "USDJPY_source_fingerprint": evaluation.get("USDJPY_source_fingerprint"),
        "universe105_sha256": evaluation.get("universe105_sha256"),
        "sector_mapping_sha256": evaluation.get("sector_mapping_sha256"),
        "stock_response_source_identity": evaluation.get("stock_response_source_identity"),
        "stock_timestamp_semantics_proven": evaluation.get("stock_timestamp_semantics_proven"),
        "eligible_periods": evaluation.get("eligible_periods"),
        "research_clock": evaluation.get("research_clock"),
        "fx_lookbacks": evaluation.get("fx_lookbacks"),
        "response_horizons": evaluation.get("response_horizons"),
        "development_fold_boundaries": evaluation.get("development_fold_boundaries"),
        "c1_fold_boundaries": evaluation.get("c1_fold_boundaries"),
        "primary_model": evaluation.get("primary_model"),
        "control_variables": evaluation.get("control_variables"),
        "bootstrap_method": evaluation.get("bootstrap_method"),
        "bootstrap_n": evaluation.get("bootstrap_n"),
        "bootstrap_seed_sha": evaluation.get("bootstrap_seed_sha"),
        "multiple_testing_method": evaluation.get("multiple_testing_method"),
        "fdr_q": evaluation.get("fdr_q"),
        "dev_candidate_gate": evaluation.get("dev_candidate_gate"),
        "c1_confirmation_gate": evaluation.get("c1_confirmation_gate"),
        "offset_placebo_gate": evaluation.get("offset_placebo_gate"),
        "day_shuffle_gate": evaluation.get("day_shuffle_gate"),
        "concentration_gate": evaluation.get("concentration_gate"),
        "FROZEN_VALIDATION_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "PHASE2_OUTCOMES_OPENED": False,
        "blockers": evaluation.get("blockers"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": slim,
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    sectors = evaluation.get("sectors") or {}
    contract = evaluation.get("contract") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "precommit_id": evaluation.get("precommit_id"),
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "PHASE2_OUTCOMES_OPENED": False,
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df(
            [
                {
                    "n": 105,
                    "sha256": evaluation.get("universe105_sha256"),
                    "dynamic40": False,
                    "pb1": False,
                }
            ]
        ).to_excel(xw, sheet_name="Universe105", index=False)
        _df(list(sectors.get("mapping_rows") or [])).to_excel(xw, sheet_name="Sector_Mapping", index=False)
        _df([evaluation.get("stock") or {}]).to_excel(xw, sheet_name="Stock_Semantics", index=False)
        _df(
            [
                {
                    "inventory_sha256": evaluation.get("parent_phase1_inventory_sha256"),
                    "fingerprint": evaluation.get("USDJPY_source_fingerprint"),
                }
            ]
        ).to_excel(xw, sheet_name="USDJPY_Identity", index=False)
        _df([evaluation.get("research_clock") or {}]).to_excel(xw, sheet_name="Research_Clock", index=False)
        _df(
            [
                {
                    "dev": evaluation.get("development_fold_boundaries"),
                    "c1": evaluation.get("c1_fold_boundaries"),
                    "sha": evaluation.get("fold_boundary_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Folds", index=False)
        _df([evaluation.get("primary_model") or {}]).to_excel(xw, sheet_name="Primary_Model", index=False)
        _df(
            [
                {"gate": "dev", "spec": evaluation.get("dev_candidate_gate")},
                {"gate": "c1", "spec": evaluation.get("c1_confirmation_gate")},
                {"gate": "concentration", "spec": evaluation.get("concentration_gate")},
            ]
        ).to_excel(xw, sheet_name="Gates", index=False)
        _df(
            [
                {"gate": "offset", "spec": evaluation.get("offset_placebo_gate")},
                {"gate": "day_shuffle", "spec": evaluation.get("day_shuffle_gate")},
            ]
        ).to_excel(xw, sheet_name="Placebos", index=False)
        _df(list((evaluation.get("fx_eligibility") or {}).get("excluded_rows") or [{"none_excluded": True}])).to_excel(
            xw, sheet_name="Eligibility", index=False
        )
        _df([contract.get("missingness_policy") or {}]).to_excel(xw, sheet_name="Missingness", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
