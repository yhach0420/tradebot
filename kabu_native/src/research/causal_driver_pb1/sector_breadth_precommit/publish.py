"""Write the three sector-breadth precommit artifacts. Does not overwrite LL. No discovery outcomes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_breadth_precommit import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_breadth_precommit.isolation import LL_DISC_OUT, OUT

SHEETS = (
    "Manifest",
    "Parent_Closeout",
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
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        "## Parent closeout",
        "",
        f"- parent verdict `{parent.get('VERDICT')}`",
        f"- parent_report_reason_raw `{parent.get('parent_report_reason_raw')}`",
        f"- parent_decision_reason `{parent.get('parent_decision_reason')}`",
        f"- parent_decision_reason_detail `{parent.get('parent_decision_reason_detail')}`",
        f"- CROSS_SECTIONAL_LEADER_LAGGARD_STATUS `{parent.get('CROSS_SECTIONAL_LEADER_LAGGARD_STATUS')}`",
        f"- parent candidate_list_sha256 `{parent.get('candidate_list_sha256')}`",
        f"- DEV_candidate_n `{parent.get('DEV_candidate_n')}`",
        f"- C1_confirmed_n `{parent.get('C1_confirmed_n')}`",
        f"- final_pass_n `{parent.get('final_pass_n')}`",
        f"- parent precommit_sha256 `{parent.get('precommit_sha256')}`",
        f"- parent artifacts overwritten `{parent.get('parent_artifacts_overwritten')}`",
        f"- LEADER_LAGGARD_REOPENED `{a.get('LEADER_LAGGARD_REOPENED')}`",
        "",
        "## Family freeze",
        "",
        f"- RESEARCH_FAMILY_ID `{a.get('RESEARCH_FAMILY_ID')}`",
        f"- PRIMARY_NEXT_DRIVER `{a.get('PRIMARY_NEXT_DRIVER')}`",
        f"- universe105_sha256 `{a.get('universe105_sha256')}`",
        f"- sector_mapping_sha256 `{a.get('sector_mapping_sha256')}`",
        f"- eligible_sector_n `{a.get('eligible_sector_n')}`",
        f"- scope_n `{a.get('scope_n')}`",
        f"- metrics `{a.get('metrics')}`",
        f"- lookbacks `{a.get('lookbacks')}`",
        f"- horizons `{a.get('horizons')}`",
        f"- family_n `{a.get('family_n')}`",
        f"- eligible_dev_n `{a.get('eligible_dev_n')}`",
        f"- eligible_c1_n `{a.get('eligible_c1_n')}`",
        f"- eligible_day_sha256 `{a.get('eligible_day_sha256')}`",
        f"- fold_boundary_sha256 `{a.get('fold_boundary_sha256')}`",
        f"- permutation_sha256 `{a.get('permutation_sha256')}`",
        f"- bootstrap `{a.get('bootstrap')}`",
        f"- BH family `{a.get('bh_family')}`",
        f"- D1-D7 `{a.get('dev_gates')}`",
        f"- C1-C7 `{a.get('c1_gates')}`",
        f"- placebos `{a.get('placebos')}`",
        f"- FV status `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}` DENY",
        f"- Prospective status `{a.get('PROSPECTIVE_DATA_OPENED')}` DENY",
        f"- outcomes opened? `{a.get('SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED')}`",
        f"- C1_outcomes_opened `{a.get('C1_outcomes_opened')}`",
        f"- C1_rows_read_before_candidate_freeze `{a.get('C1_rows_read_before_candidate_freeze')}`",
        f"- candidate_list_sha256 `{a.get('candidate_list_sha256')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- phase0_DriverFamily_enum_mutated `{a.get('phase0_DriverFamily_enum_mutated')}`",
        "",
        "Discovery is not run. Leader-laggard artifacts are not overwritten.",
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
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
        "RESEARCH_FAMILY_ID": evaluation.get("RESEARCH_FAMILY_ID"),
        "phase0_DriverFamily_enum_mutated": evaluation.get("phase0_DriverFamily_enum_mutated"),
        "phase0_observation_family_if_later_bound": evaluation.get("phase0_observation_family_if_later_bound"),
        "parent": parent,
        "parent_verdict": parent.get("VERDICT"),
        "parent_report_reason_raw": parent.get("parent_report_reason_raw"),
        "parent_decision_reason": parent.get("parent_decision_reason"),
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
        "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
        "permutation_sha256": evaluation.get("permutation_sha256"),
        "listing_start_sha256": evaluation.get("listing_start_sha256"),
        "bootstrap": "DATE_BLOCK_BOOTSTRAP N=2000 seed=20240917 unit=eligible trading date",
        "bh_family": "BENJAMINI_HOCHBERG q=0.05 family_n=384",
        "dev_gates": "D1-D7 all required; D7 conjunctive 60s cannot rescue D1-D6",
        "c1_gates": "C1-C7 frozen DEV candidates only; C7 conjunctive 60s cannot rescue C1-C6",
        "placebos": "time-offset; day-shuffle; SECTOR_IDENTITY_SPECIFICITY_GATE; concentration; common-factor",
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
        "ll_discovery_not_overwritten": True,
        "ll_discovery_path": str(LL_DISC_OUT),
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    if isinstance(slim.get("days"), dict):
        slim["days"] = {k: v for k, v in slim["days"].items() if k not in {"rows", "listing_rows"}}
    if isinstance(slim.get("contract"), dict):
        cslim = dict(slim["contract"])
        cslim.pop("family_384", None)
        slim["contract"] = cslim
    slim.pop("day_rows", None)
    slim.pop("listing_rows", None)
    slim.pop("family_384", None)
    slim.pop("listing_start", None)
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
    day_rows = list(evaluation.get("day_rows") or [])
    listing_rows = list(evaluation.get("listing_rows") or [])
    family_rows = list(evaluation.get("family_384") or contract.get("family_384") or [])
    scopes = list(contract.get("scopes") or [])
    fold_dates = []
    for name in ("DEV_EARLY", "DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
        for day in folds.get(name) or []:
            fold_dates.append({"fold": name, "date": day})
    coverage = days.get("coverage_summary") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "RESEARCH_FAMILY_ID": evaluation.get("RESEARCH_FAMILY_ID"),
                    "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
                    "universe105_sha256": evaluation.get("universe105_sha256"),
                    "sector_mapping_sha256": evaluation.get("sector_mapping_sha256"),
                    "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
                    "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
                    "permutation_sha256": evaluation.get("permutation_sha256"),
                    "family_n": evaluation.get("family_n"),
                    "eligible_dev_n": evaluation.get("eligible_dev_n"),
                    "eligible_c1_n": evaluation.get("eligible_c1_n"),
                    "discovery_not_run": True,
                    "ll_discovery_path": str(LL_DISC_OUT),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([parent or {"empty": True}]).to_excel(xw, sheet_name="Parent_Closeout", index=False)
        _df(
            [
                {
                    "n": 105,
                    "sha256": sectors.get("universe105_sha256"),
                    "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
                    "dynamic40": False,
                    "pb1_candidates": False,
                }
            ]
        ).to_excel(xw, sheet_name="Universe105", index=False)
        _df(mapping).to_excel(xw, sheet_name="Sector_Mapping", index=False)
        _df(elig or [{"empty": True}]).to_excel(xw, sheet_name="Eligible_Sectors", index=False)
        _df(listing_rows or [{"empty": True}]).to_excel(xw, sheet_name="PointInTime_Listing", index=False)
        _df([coverage or {"empty": True}]).to_excel(xw, sheet_name="Input_Coverage", index=False)
        _df(day_rows or [{"empty": True}]).to_excel(xw, sheet_name="Eligible_Days", index=False)
        _df(
            fold_dates
            or [
                {
                    "fold_boundary_sha256": folds.get("fold_boundary_sha256"),
                    "eligible_dev_n": evaluation.get("eligible_dev_n"),
                    "eligible_c1_n": evaluation.get("eligible_c1_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Folds", index=False)
        _df([evaluation.get("stock") or {}]).to_excel(xw, sheet_name="Timestamp_Semantics", index=False)
        _df([(contract.get("freshness") or {"empty": True})]).to_excel(xw, sheet_name="Freshness", index=False)
        _df([(contract.get("metrics") or {"empty": True})]).to_excel(xw, sheet_name="Driver_Definitions", index=False)
        _df(scopes or [{"empty": True}]).to_excel(xw, sheet_name="Scopes", index=False)
        _df(family_rows or [{"empty": True}]).to_excel(xw, sheet_name="Family_384", index=False)
        _df([(contract.get("models") or {"empty": True})]).to_excel(xw, sheet_name="Models", index=False)
        _df([(contract.get("dev_gates") or {"empty": True})]).to_excel(xw, sheet_name="DEV_Gates", index=False)
        _df([(contract.get("c1_gates") or {"empty": True})]).to_excel(xw, sheet_name="C1_Gates", index=False)
        _df([((contract.get("placebos") or {}).get("time_offset") or {"empty": True})]).to_excel(
            xw, sheet_name="Offset_Placebo", index=False
        )
        _df([(shuffle or ((contract.get("placebos") or {}).get("day_shuffle") or {"empty": True}))]).to_excel(
            xw, sheet_name="Day_Shuffle", index=False
        )
        _df(
            [((contract.get("placebos") or {}).get("sector_identity_specificity_gate") or {"empty": True})]
        ).to_excel(xw, sheet_name="Sector_Identity", index=False)
        _df([(contract.get("concentration") or {"empty": True})]).to_excel(xw, sheet_name="Concentration", index=False)
        _df([(contract.get("common_factor_check") or {"empty": True})]).to_excel(xw, sheet_name="Common_Factor", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
