"""Write V1.1 precommit artifacts. Does not mutate V1 OUT."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.phase2_precommit_v1_1 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.phase2_precommit_v1_1.isolation import OUT

SHEETS = (
    "Manifest",
    "Universe105",
    "Sector_Mapping",
    "Stock_Semantics",
    "USDJPY_Identity",
    "Research_Clock",
    "Folds",
    "Fold_Dates",
    "Calendar",
    "Shuffle",
    "Diff",
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


def _fold_date_rows(folds: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name in ("DEV_EARLY", "DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
        for day in folds.get(name) or []:
            rows.append({"fold": name, "date": day})
    return rows


def _markdown(report: dict[str, Any]) -> str:
    a = report["answers"]
    ev = report.get("evaluation") or {}
    cal = ev.get("calendar") or {}
    sh = ev.get("shuffle") or {}
    diff = ev.get("diff") or {}
    b = a.get("development_fold_boundaries") or {}
    c = a.get("c1_fold_boundaries") or {}
    return "\n".join(
        [
            f"# {PROGRAM_ID} / {ANALYSIS_ID}",
            "",
            f"**VERDICT:** `{a.get('VERDICT')}`",
            "",
            f"**NEXT:** `{a.get('NEXT')}`",
            "",
            "Phase 2 **precommit correction** only. Outcomes remain unopened. Discovery was not run.",
            "",
            f"- precommit_id `{a.get('precommit_id')}`",
            f"- precommit_sha256 `{a.get('precommit_sha256')}`",
            f"- supersedes_precommit_sha256 `{a.get('supersedes_precommit_sha256')}`",
            f"- superseded_fold_boundary_sha256 `{a.get('superseded_fold_boundary_sha256')}`",
            f"- fold_boundary_sha256 `{a.get('fold_boundary_sha256')}`",
            f"- tse_calendar_method `{cal.get('method')}`",
            f"- tse_calendar_source_sha256 `{cal.get('source_sha256')}`",
            f"- eligible_dev_n `{a.get('eligible_dev_n')}`",
            f"- eligible_c1_n `{a.get('eligible_c1_n')}`",
            f"- DEV_EARLY `{json.dumps(b.get('DEV_EARLY'), ensure_ascii=False)}`",
            f"- DEV_LATE `{json.dumps(b.get('DEV_LATE'), ensure_ascii=False)}`",
            f"- C1_EARLY `{json.dumps(c.get('C1_EARLY'), ensure_ascii=False)}`",
            f"- C1_MIDDLE `{json.dumps(c.get('C1_MIDDLE'), ensure_ascii=False)}`",
            f"- C1_LATE `{json.dumps(c.get('C1_LATE'), ensure_ascii=False)}`",
            f"- shuffle_primary_n `{sh.get('shuffle_primary_n')}`",
            f"- shuffle_fallback_month_n `{sh.get('shuffle_fallback_month_n')}`",
            f"- shuffle_unshufflable_n `{sh.get('shuffle_unshufflable_n')}`",
            f"- permutation_sha256 `{sh.get('permutation_sha256')}`",
            f"- PHASE2_OUTCOMES_OPENED `{a.get('PHASE2_OUTCOMES_OPENED')}`",
            f"- candidate_list_sha256 `{a.get('candidate_list_sha256')}`",
            f"- universe105_sha256 `{a.get('universe105_sha256')}`",
            f"- sector_mapping_sha256 `{a.get('sector_mapping_sha256')}`",
            "",
            "## Diff",
            "",
            f"- unchanged_pass `{diff.get('unchanged_pass')}`",
            f"- old_fold_sha `{diff.get('old_fold_sha')}`",
            f"- new_fold_sha `{diff.get('new_fold_sha')}`",
            f"- old_precommit_sha `{diff.get('old_precommit_sha')}`",
            f"- new_precommit_sha `{diff.get('new_precommit_sha')}`",
            "",
            "Hypothesis, 105 universe, sector mapping, USDJPY source, lookbacks, horizons, clock, model, FDR, D1–D6, C1–C6, offset, concentration, missingness, FV/Prospective firewall are unchanged.",
            "",
        ]
    )


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "supersedes_precommit_sha256": evaluation.get("supersedes_precommit_sha256"),
        "superseded_fold_boundary_sha256": evaluation.get("superseded_fold_boundary_sha256"),
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
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "primary_model": evaluation.get("primary_model"),
        "control_variables": evaluation.get("control_variables"),
        "bootstrap_method": evaluation.get("bootstrap_method"),
        "bootstrap_n": evaluation.get("bootstrap_n"),
        "bootstrap_seed_sha": evaluation.get("bootstrap_seed_sha"),
        "bootstrap_eligible_day_population": evaluation.get("bootstrap_eligible_day_population"),
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
        "candidate_list_sha256": None,
        "blockers": evaluation.get("blockers"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
        "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    if "calendar_rows" in slim:
        slim["calendar_rows"] = f"omitted:{len(evaluation.get('calendar_rows') or [])}"
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
    folds = evaluation.get("folds") or {}
    diff = evaluation.get("diff") or {}
    elig = evaluation.get("eligibility") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "precommit_id": evaluation.get("precommit_id"),
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "supersedes_precommit_sha256": evaluation.get("supersedes_precommit_sha256"),
                    "PHASE2_OUTCOMES_OPENED": False,
                    "candidate_list_sha256": None,
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
                    "superseded_sha": evaluation.get("superseded_fold_boundary_sha256"),
                    "eligible_dev_n": evaluation.get("eligible_dev_n"),
                    "eligible_c1_n": evaluation.get("eligible_c1_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Folds", index=False)
        _df(_fold_date_rows(folds)).to_excel(xw, sheet_name="Fold_Dates", index=False)
        _df(list(evaluation.get("calendar_rows") or [{"empty": True}])).to_excel(xw, sheet_name="Calendar", index=False)
        _df([evaluation.get("shuffle") or {}]).to_excel(xw, sheet_name="Shuffle", index=False)
        _df(
            [
                {"section": "unchanged", "key": k, "same": v}
                for k, v in (diff.get("unchanged") or {}).items()
            ]
            + [
                {"section": "changed_required", "key": k, "same": v}
                for k, v in (diff.get("changed_as_required") or {}).items()
            ]
            + [
                {
                    "section": "sha",
                    "key": "fold_and_precommit",
                    "same": {
                        "old_fold_sha": diff.get("old_fold_sha"),
                        "new_fold_sha": diff.get("new_fold_sha"),
                        "old_precommit_sha": diff.get("old_precommit_sha"),
                        "new_precommit_sha": diff.get("new_precommit_sha"),
                        "unchanged_pass": diff.get("unchanged_pass"),
                    },
                }
            ]
        ).to_excel(xw, sheet_name="Diff", index=False)
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
        _df(
            [
                {
                    "fx_only_non_tse_n": elig.get("fx_only_non_tse_n"),
                    "tse_but_fx_below_95_n": elig.get("tse_but_fx_below_95_n"),
                    "named_date_exclusions": elig.get("named_date_exclusions"),
                    "fx_only_non_tse_dates": elig.get("fx_only_non_tse_dates"),
                    "tse_but_fx_below_95": elig.get("tse_but_fx_below_95"),
                }
            ]
        ).to_excel(xw, sheet_name="Eligibility", index=False)
        _df([contract.get("missingness_policy") or {}]).to_excel(xw, sheet_name="Missingness", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
