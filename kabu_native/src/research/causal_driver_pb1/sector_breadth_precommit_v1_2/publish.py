"""Write V1.2 precommit artifacts. Does not overwrite V1, V1.1, or discovery."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.isolation import DISC_OUT, OUT, V1_OUT, V11_OUT

SHEETS = (
    "Manifest",
    "Technical_Invalidation",
    "Old_Control_Rule",
    "Corrected_Control_Rule",
    "Sector_Structural_Feasibility",
    "Universe105",
    "Sector_Mapping",
    "Family384",
    "Eligible_Days",
    "Folds",
    "Bootstrap",
    "BH",
    "C1_Firewall",
    "Contamination",
    "Diff",
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
    inv = a.get("invalidation") or {}
    gate = a.get("control_gate") or {}
    feas = a.get("feasibility") or {}
    s3650 = feas.get("sector_3650") or {}
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- old discovery classification `{inv.get('old_discovery_classification')}`",
        f"- invalidation `{inv.get('INVALIDATION_VERDICT')}`",
        f"- reason `{inv.get('reason')}`",
        f"- affected sector `{inv.get('affected_sector_id')}` `{inv.get('affected_sector_name')}` n={inv.get('affected_hypothesis_n')}",
        f"- old max ex-sector n `{inv.get('old_max_ex_sector_n')}` old required `{inv.get('old_required_n')}`",
        f"- corrected rule `{gate.get('if_N_EX_SECTOR_PIT_ge_80')}` else `{gate.get('else')}`",
        f"- all other sectors old gate possible `{feas.get('all_other_sectors_old_gate_structurally_possible')}`",
        f"- 3650 new gate possible `{s3650.get('new_gate_structurally_possible')}`",
        f"- family_n `{a.get('family_n')}` family_384_sha256 `{a.get('family_384_sha256')}`",
        f"- eligible_day_sha256 `{a.get('eligible_day_sha256')}`",
        f"- fold_boundary_sha256 `{a.get('fold_boundary_sha256')}`",
        f"- bootstrap_index_sha256 `{a.get('bootstrap_index_sha256')}`",
        f"- permutation_sha256 `{a.get('permutation_sha256')}`",
        f"- C1 opened `{a.get('C1_opened')}` rows_before_freeze `{a.get('C1_rows_read_before_candidate_freeze')}`",
        f"- FV / Prospective `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}` / `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- old precommit SHA `{a.get('supersedes_precommit_sha256')}`",
        f"- new precommit SHA `{a.get('precommit_sha256')}`",
        f"- DEV_OUTCOMES_PREVIOUSLY_OPENED `{a.get('DEV_OUTCOMES_PREVIOUSLY_OPENED')}`",
        "",
        "Corrected discovery is not run. V1 / V1.1 / discovery artifacts are not overwritten.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    inv = evaluation.get("invalidation") or {}
    gate = evaluation.get("control_gate") or {}
    feas = evaluation.get("feasibility") or {}
    days = evaluation.get("days") or {}
    folds = days.get("folds") or {}
    contract = evaluation.get("contract") or {}
    sectors = evaluation.get("sectors") or {}
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
        "invalidation": {
            "INVALIDATION_VERDICT": inv.get("INVALIDATION_VERDICT"),
            "reason": inv.get("reason"),
            "old_discovery_classification": inv.get("old_discovery_classification"),
            "old_published_verdict": inv.get("old_published_verdict"),
            "old_published_next_suspended": inv.get("old_published_next_suspended"),
            "affected_sector_id": inv.get("affected_sector_id"),
            "affected_sector_name": inv.get("affected_sector_name"),
            "affected_hypothesis_n": inv.get("affected_hypothesis_n"),
            "old_max_ex_sector_n": inv.get("old_max_ex_sector_n"),
            "old_required_n": inv.get("old_required_n"),
            "all_32_model_n_zero": inv.get("all_32_model_n_zero"),
            "control_gate_impossibility_not_missing_driver": inv.get("control_gate_impossibility_not_missing_driver"),
            "DEV_first_fail_counts_status": inv.get("DEV_first_fail_counts_status"),
        },
        "control_gate": gate,
        "feasibility": {
            "all_other_sectors_old_gate_structurally_possible": feas.get("all_other_sectors_old_gate_structurally_possible"),
            "all_sectors_new_gate_structurally_possible": feas.get("all_sectors_new_gate_structurally_possible"),
            "sector_3650": feas.get("sector_3650"),
        },
        "unit_tests": evaluation.get("unit_tests"),
        "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
        "RESEARCH_FAMILY_ID": evaluation.get("RESEARCH_FAMILY_ID"),
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "C1_outcomes_opened": False,
        "C1_opened": False,
        "C1_rows_read_before_candidate_freeze": 0,
        "candidate_list_sha256": None,
        "universe105_sha256": evaluation.get("universe105_sha256"),
        "sector_mapping_sha256": evaluation.get("sector_mapping_sha256"),
        "family_n": evaluation.get("family_n"),
        "family_384_sha256": evaluation.get("family_384_sha256"),
        "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
        "permutation_sha256": evaluation.get("permutation_sha256"),
        "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
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
        "corrected_discovery_not_run": True,
        "v1_path": str(V1_OUT),
        "v1_1_path": str(V11_OUT),
        "discovery_path": str(DISC_OUT),
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
        "evaluation": _clean(slim),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    mapping = list(sectors.get("rows") or [])
    family_rows = list(evaluation.get("family_384") or [])
    fold_dates = []
    for name in ("DEV_EARLY", "DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
        for day in folds.get(name) or []:
            fold_dates.append({"fold": name, "date": day})
    changed = diff.get("changed") or {}
    unchanged = diff.get("unchanged") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "supersedes_precommit_sha256": evaluation.get("supersedes_precommit_sha256"),
                    "family_n": evaluation.get("family_n"),
                    "C1_opened": False,
                    "discovery_not_run": True,
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([inv or {"empty": True}]).to_excel(xw, sheet_name="Technical_Invalidation", index=False)
        _df(
            [
                {
                    "rule": "valid_n >= 80 AND valid_fraction >= 0.80",
                    "impossible_for_sector": "3650",
                    "max_ex_sector_n": inv.get("old_max_ex_sector_n"),
                    "required_n": inv.get("old_required_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Old_Control_Rule", index=False)
        _df([gate or {"empty": True}]).to_excel(xw, sheet_name="Corrected_Control_Rule", index=False)
        _df(list(feas.get("matrix") or [{"empty": True}])).to_excel(xw, sheet_name="Sector_Structural_Feasibility", index=False)
        _df([{"n": 105, "sha256": sectors.get("universe105_sha256")}]).to_excel(xw, sheet_name="Universe105", index=False)
        _df(mapping or [{"empty": True}]).to_excel(xw, sheet_name="Sector_Mapping", index=False)
        _df(family_rows or [{"empty": True}]).to_excel(xw, sheet_name="Family384", index=False)
        _df(
            [{"date": d, "eligible": True, "inherited": True} for d in (folds.get("development_dates") or []) + (folds.get("c1_dates") or [])]
            or [{"empty": True}]
        ).to_excel(xw, sheet_name="Eligible_Days", index=False)
        _df(fold_dates or [{"empty": True}]).to_excel(xw, sheet_name="Folds", index=False)
        _df(
            [
                {
                    "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
                    "unchanged": True,
                    "draws_not_regenerated": True,
                }
            ]
        ).to_excel(xw, sheet_name="Bootstrap", index=False)
        _df(
            [
                {
                    "bh_m": 384,
                    "bh_q": 0.05,
                    "do_not_set_m_to_352": True,
                    "old_q_invalid_for_final_decision": True,
                    "method": inf.get("bh_method") or "BENJAMINI_HOCHBERG_MONOTONE_Q",
                }
            ]
        ).to_excel(xw, sheet_name="BH", index=False)
        _df(
            [
                {
                    "C1_opened": False,
                    "C1_outcomes_opened": False,
                    "C1_rows_read_before_candidate_freeze": 0,
                    "C1_not_accessed_during_v1_2": True,
                }
            ]
        ).to_excel(xw, sheet_name="C1_Firewall", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df(
            [{"side": "changed", "item": k, "value": v} for k, v in changed.items()]
            + [{"side": "unchanged", "item": k, "value": v} for k, v in unchanged.items()]
        ).to_excel(xw, sheet_name="Diff", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
