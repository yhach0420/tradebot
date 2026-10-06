"""Write V1.1 precommit artifacts. Does not overwrite V1. No discovery outcomes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_breadth_precommit_v1_1 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.isolation import V1_OUT, OUT

SHEETS = (
    "Manifest",
    "Diff",
    "Parent_Closeout",
    "Inference",
    "Universe105",
    "Sector_Mapping",
    "Eligible_Sectors",
    "PointInTime_Listing",
    "Input_Coverage",
    "Eligible_Days",
    "Folds",
    "Timestamp_Semantics",
    "Freshness",
    "Driver_Definitions",
    "Scopes",
    "Family_384",
    "Models",
    "DEV_Gates",
    "C1_Gates",
    "Offset_Placebo",
    "Day_Shuffle",
    "Sector_Identity",
    "Concentration",
    "Common_Factor",
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
    parent = a.get("parent") or {}
    diff = a.get("diff") or {}
    changed = diff.get("changed") or {}
    unchanged = diff.get("unchanged") or {}
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- supersedes_precommit_sha256 `{a.get('supersedes_precommit_sha256')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- bootstrap_index_sha256 `{a.get('bootstrap_index_sha256')}`",
        f"- CI method `{a.get('ci_method')}`",
        f"- p-value method `{a.get('p_value_method')}`",
        f"- BH method `{a.get('bh_method')}`",
        f"- family_n `{a.get('family_n')}`",
        f"- eligible_day_sha256 `{a.get('eligible_day_sha256')}`",
        f"- eligible_dev_n `{a.get('eligible_dev_n')}`",
        f"- eligible_c1_n `{a.get('eligible_c1_n')}`",
        f"- fold_boundary_sha256 `{a.get('fold_boundary_sha256')}`",
        f"- permutation_sha256 `{a.get('permutation_sha256')}`",
        f"- parent verdict `{parent.get('VERDICT')}`",
        f"- parent_report_reason_raw `{parent.get('parent_report_reason_raw')}`",
        f"- parent_decision_reason `{parent.get('parent_decision_reason')}`",
        f"- SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED `{a.get('SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED')}`",
        f"- C1_outcomes_opened `{a.get('C1_outcomes_opened')}`",
        f"- candidate_list_sha256 `{a.get('candidate_list_sha256')}`",
        f"- FV / Prospective `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}` / `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
        "Discovery is not run. V1 artifacts are not overwritten.",
        "",
        "## Changed",
        "",
        f"- bootstrap block-index algorithm: `{changed.get('bootstrap_block_index_algorithm')}`",
        f"- bootstrap_index_sha256: `{changed.get('bootstrap_index_sha256')}`",
        f"- CI method: `{changed.get('ci_method')}`",
        f"- p-value method: `{changed.get('p_value_method')}`",
        f"- BH q algorithm: `{changed.get('bh_q_algorithm')}`",
        f"- precommit SHA: `{json.dumps(changed.get('precommit_sha'), ensure_ascii=False)}`",
        "",
        "## Unchanged",
        "",
        f"- universe105_sha256 `{unchanged.get('universe105_sha256')}`",
        f"- sector_mapping_sha256 `{unchanged.get('sector_mapping_sha256')}`",
        f"- eligible_day_sha256 `{unchanged.get('eligible_day_sha256')}`",
        f"- fold_boundary_sha256 `{unchanged.get('fold_boundary_sha256')}`",
        f"- permutation_sha256 `{unchanged.get('permutation_sha256')}`",
        f"- family_n `{unchanged.get('family_n')}`",
        f"- models / controls / freshness / D1-D7 / C1-C7 / placebos / FV / Prospective firewalls",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    parent = evaluation.get("parent") or {}
    days = evaluation.get("days") or {}
    folds = days.get("folds") or {}
    contract = evaluation.get("contract") or {}
    sectors = evaluation.get("sectors") or {}
    shuffle = days.get("shuffle") or {}
    diff = evaluation.get("diff") or {}
    inf = contract.get("inference") or {}
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "supersedes_precommit_sha256": evaluation.get("supersedes_precommit_sha256"),
        "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
        "RESEARCH_FAMILY_ID": evaluation.get("RESEARCH_FAMILY_ID"),
        "phase0_DriverFamily_enum_mutated": evaluation.get("phase0_DriverFamily_enum_mutated"),
        "parent": parent,
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED": False,
        "C1_outcomes_opened": False,
        "C1_rows_read_before_candidate_freeze": 0,
        "candidate_list_sha256": None,
        "universe105_sha256": evaluation.get("universe105_sha256"),
        "sector_mapping_sha256": evaluation.get("sector_mapping_sha256"),
        "eligible_sector_n": evaluation.get("eligible_sector_n"),
        "scope_n": evaluation.get("scope_n"),
        "metrics": ((contract.get("metrics") or {}).get("ids") or ["BREADTH", "DISPERSION"]),
        "lookbacks": (contract.get("family") or {}).get("lookbacks") or [1, 3, 5, 10],
        "horizons": (contract.get("family") or {}).get("horizons") or [1, 3, 5, 10],
        "family_n": evaluation.get("family_n"),
        "family_384_sha256": evaluation.get("family_384_sha256"),
        "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
        "permutation_sha256": evaluation.get("permutation_sha256"),
        "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
        "ci_method": inf.get("ci_method"),
        "p_value_method": inf.get("p_value_method"),
        "bh_method": inf.get("bh_method"),
        "bootstrap_method": inf.get("bootstrap_method"),
        "diff": diff,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "discovery_not_run": True,
        "v1_out_not_overwritten": True,
        "v1_path": str(V1_OUT),
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    if isinstance(slim.get("contract"), dict):
        cslim = dict(slim["contract"])
        cslim.pop("family_384", None)
        slim["contract"] = cslim
    slim.pop("family_384", None)
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
    mapping = list(sectors.get("rows") or [])
    elig = list(sectors.get("eligible_sectors") or [])
    family_rows = list(evaluation.get("family_384") or [])
    scopes = list(contract.get("scopes") or [])
    fold_dates = []
    for name in ("DEV_EARLY", "DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
        for day in folds.get(name) or []:
            fold_dates.append({"fold": name, "date": day})
    changed = diff.get("changed") or {}
    unchanged = diff.get("unchanged") or {}
    tests = (evaluation.get("inference_tests") or {}).get("rows") or []
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "supersedes_precommit_sha256": evaluation.get("supersedes_precommit_sha256"),
                    "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
                    "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
                    "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
                    "permutation_sha256": evaluation.get("permutation_sha256"),
                    "family_n": evaluation.get("family_n"),
                    "eligible_dev_n": evaluation.get("eligible_dev_n"),
                    "eligible_c1_n": evaluation.get("eligible_c1_n"),
                    "discovery_not_run": True,
                    "v1_path": str(V1_OUT),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df(
            [{"side": "changed", "item": k, "value": v} for k, v in changed.items()]
            + [{"side": "unchanged", "item": k, "value": v} for k, v in unchanged.items()]
        ).to_excel(xw, sheet_name="Diff", index=False)
        _df([parent or {"empty": True}]).to_excel(xw, sheet_name="Parent_Closeout", index=False)
        _df([inf or {"empty": True}] + tests).to_excel(xw, sheet_name="Inference", index=False)
        _df(
            [
                {
                    "n": 105,
                    "sha256": sectors.get("universe105_sha256"),
                    "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Universe105", index=False)
        _df(mapping).to_excel(xw, sheet_name="Sector_Mapping", index=False)
        _df(elig or [{"empty": True}]).to_excel(xw, sheet_name="Eligible_Sectors", index=False)
        _df([{"listing_start_sha256": (evaluation.get("v1") or {}).get("listing_start_sha256"), "inherited_from_v1": True}]).to_excel(
            xw, sheet_name="PointInTime_Listing", index=False
        )
        _df([((evaluation.get("v1") or {}).get("coverage_summary") or {"inherited_from_v1": True})]).to_excel(
            xw, sheet_name="Input_Coverage", index=False
        )
        _df([{"date": d, "eligible": True, "inherited_from_v1": True} for d in (folds.get("development_dates") or []) + (folds.get("c1_dates") or [])] or [{"empty": True}]).to_excel(
            xw, sheet_name="Eligible_Days", index=False
        )
        _df(fold_dates or [{"fold_boundary_sha256": folds.get("fold_boundary_sha256")}]).to_excel(xw, sheet_name="Folds", index=False)
        _df([evaluation.get("stock") or {}]).to_excel(xw, sheet_name="Timestamp_Semantics", index=False)
        _df([(contract.get("freshness") or {"empty": True})]).to_excel(xw, sheet_name="Freshness", index=False)
        _df([(contract.get("metrics") or {"empty": True})]).to_excel(xw, sheet_name="Driver_Definitions", index=False)
        _df(scopes or [{"empty": True}]).to_excel(xw, sheet_name="Scopes", index=False)
        _df(family_rows or [{"empty": True}]).to_excel(xw, sheet_name="Family_384", index=False)
        _df([(contract.get("models") or {"empty": True})]).to_excel(xw, sheet_name="Models", index=False)
        _df([(contract.get("dev_gates") or {"empty": True})]).to_excel(xw, sheet_name="DEV_Gates", index=False)
        _df([(contract.get("c1_gates") or {"empty": True})]).to_excel(xw, sheet_name="C1_Gates", index=False)
        _df([((contract.get("placebos") or {}).get("time_offset") or {"empty": True})]).to_excel(xw, sheet_name="Offset_Placebo", index=False)
        _df([(shuffle or {"empty": True})]).to_excel(xw, sheet_name="Day_Shuffle", index=False)
        _df([((contract.get("placebos") or {}).get("sector_identity_specificity_gate") or {"empty": True})]).to_excel(
            xw, sheet_name="Sector_Identity", index=False
        )
        _df([(contract.get("concentration") or {"empty": True})]).to_excel(xw, sheet_name="Concentration", index=False)
        _df([(contract.get("common_factor_check") or {"empty": True})]).to_excel(xw, sheet_name="Common_Factor", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
