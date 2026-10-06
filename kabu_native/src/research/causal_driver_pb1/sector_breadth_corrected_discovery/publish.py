"""Write the three discovery artifacts. No mass CSV."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_breadth_corrected_discovery import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_breadth_corrected_discovery.isolation import OUT

SHEETS = (
    "Manifest",
    "Identity",
    "Technical_Invalidation",
    "Control_Gate",
    "Structural_Feasibility",
    "Corrected_3650",
    "Unaffected_Parity",
    "Eligible_Days",
    "Folds",
    "Inference_Contract",
    "DEV_All_384",
    "DEV_Bootstrap",
    "DEV_BH",
    "DEV_Gates",
    "DEV_Strict60",
    "DEV_Candidates",
    "Candidate_Freeze",
    "C1_Access_Ledger",
    "C1_Confirmation",
    "C1_Strict60",
    "Offset_Map",
    "Day_Shuffle",
    "Sector_Identity",
    "Driver_Concentration",
    "Target_Concentration",
    "Common_Factor",
    "Failure_Stages",
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


def _not_run(reason: str) -> list[dict[str, Any]]:
    return [{"status": "NOT_RUN", "reason": reason}]


def _metric_summary(family: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric in ("BREADTH", "DISPERSION"):
        rows = [r for r in family if r.get("metric") == metric]
        cands = [r for r in rows if r.get("candidate")]
        betas = [float(r["b_fx"]) for r in rows if r.get("b_fx") is not None]
        out[metric] = {
            "n": len(rows),
            "candidate_n": len(cands),
            "mean_abs_beta": None if not betas else float(sum(abs(b) for b in betas) / len(betas)),
        }
    return out


def _markdown(report: dict[str, Any]) -> str:
    a = report["answers"]
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- V1.2 SHA verified `{a.get('precommit_sha_verified')}`",
        f"- precommit_id `{a.get('precommit_id')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- old discovery `{a.get('old_discovery_classification')}`",
        f"- all 384 evaluable `{a.get('all_384_evaluable')}` unevaluable `{a.get('unevaluable_test_n')}`",
        f"- 3650 model_n=0 before/after `{a.get('affected_3650_model_n_zero_before')}` / `{a.get('affected_3650_model_n_zero_after')}`",
        f"- unaffected parity `{a.get('unaffected_parity_n')}` fail `{a.get('unaffected_parity_fail_n')}`",
        f"- DEV 384 / candidate_n `{a.get('DEV_all_n')}` / `{a.get('DEV_candidate_n')}`",
        f"- DEV first-fail `{a.get('DEV_first_fail_counts')}`",
        f"- BH min q `{a.get('bh_q_min')}`",
        f"- candidate_list_sha256 `{a.get('candidate_list_sha256')}`",
        f"- C1 opened `{a.get('C1_opened')}` rows_before_corrected_freeze `{a.get('C1_rows_read_before_corrected_candidate_freeze')}` confirmed `{a.get('C1_confirmed_n')}`",
        f"- offset/shuffle/sector_identity `{a.get('offset_pass_n')}` / `{a.get('shuffle_pass_n')}` / `{a.get('sector_identity_pass_n')}`",
        f"- driver/target concentration `{a.get('driver_concentration_pass_n')}` / `{a.get('target_concentration_pass_n')}`",
        f"- common-factor `{a.get('common_factor_pass_n')}`",
        f"- final_pass_n `{a.get('final_pass_n')}`",
        f"- DEV_OUTCOMES_PREVIOUSLY_OPENED `{a.get('DEV_OUTCOMES_PREVIOUSLY_OPENED')}` fresh_blind `{a.get('fresh_blind_first_look')}`",
        f"- FV/prospective `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}` / `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- ALPHA_CREATED `{a.get('ALPHA_CREATED')}`",
        "",
        "Symbol transmission was not started.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    bound = evaluation.get("bound") or {}
    access = evaluation.get("access") or {}
    family = list(evaluation.get("family") or [])
    c1_rows = list(evaluation.get("c1_rows") or [])
    c1_opened = bool(evaluation.get("C1_opened"))
    metric_sum = _metric_summary(family)
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "precommit_sha_verified": evaluation.get("precommit_sha_verified"),
        "old_discovery_classification": evaluation.get("old_discovery_classification"),
        "family_n": evaluation.get("family_n") or evaluation.get("DEV_all_n"),
        "all_384_evaluable": evaluation.get("all_384_evaluable"),
        "unevaluable_test_n": evaluation.get("unevaluable_test_n"),
        "affected_3650_n": evaluation.get("affected_3650_n"),
        "affected_3650_model_n_zero_before": evaluation.get("affected_3650_model_n_zero_before"),
        "affected_3650_model_n_zero_after": evaluation.get("affected_3650_model_n_zero_after"),
        "unaffected_parity_n": evaluation.get("unaffected_parity_n"),
        "unaffected_parity_fail_n": evaluation.get("unaffected_parity_fail_n"),
        "family_384_sha256": evaluation.get("family_384_sha256"),
        "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
        "permutation_sha256": evaluation.get("permutation_sha256"),
        "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
        "universe105_sha256": evaluation.get("universe105_sha256"),
        "sector_mapping_sha256": evaluation.get("sector_mapping_sha256"),
        "DEV_all_n": evaluation.get("DEV_all_n"),
        "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
        "DEV_first_fail_counts": evaluation.get("DEV_first_fail_counts"),
        "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
        "C1_rows_read_before_candidate_freeze": evaluation.get("C1_rows_read_before_candidate_freeze"),
        "C1_rows_read_before_corrected_candidate_freeze": evaluation.get("C1_rows_read_before_corrected_candidate_freeze"),
        "C1_opened": c1_opened,
        "C1_confirmed_n": evaluation.get("C1_confirmed_n"),
        "offset_pass_n": evaluation.get("offset_pass_n"),
        "shuffle_pass_n": evaluation.get("shuffle_pass_n"),
        "sector_identity_pass_n": evaluation.get("sector_identity_pass_n") if evaluation.get("sector_identity_pass_n") is not None else evaluation.get("identity_pass_n"),
        "driver_concentration_pass_n": evaluation.get("driver_concentration_pass_n"),
        "target_concentration_pass_n": evaluation.get("target_concentration_pass_n"),
        "common_factor_pass_n": evaluation.get("common_factor_pass_n"),
        "final_pass_n": evaluation.get("final_pass_n"),
        "final_candidates": evaluation.get("final_candidates"),
        "top_failed_candidates": evaluation.get("top_failed_candidates"),
        "failure_stage_counts": evaluation.get("failure_stage_counts"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "breadth_vs_dispersion": metric_sum,
        "control_gate_rule_id": evaluation.get("control_gate_rule_id"),
        "bh_q_min": evaluation.get("bh_q_min"),
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "FV_opened": False,
        "prospective_opened": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "USDJPY_REOPENED": False,
        "LEADER_LAGGARD_REOPENED": False,
        "research_only": True,
    }
    slim = dict(evaluation)
    slim.pop("identity", None)
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
    c1_reason = "NOT_RUN" if not c1_opened else "opened"
    nr = _not_run(c1_reason)
    folds = bound.get("folds") or {}
    p_boot = list(evaluation.get("p_boot") or [])
    q_boot = list(evaluation.get("q_boot") or [])
    bh_rows = []
    for i, r in enumerate(family):
        bh_rows.append(
            {
                "canonical_index": i,
                "metric": r.get("metric"),
                "scope_id": r.get("scope_id"),
                "lookback": r.get("lookback"),
                "horizon": r.get("horizon"),
                "p": r.get("p_boot") if r.get("p_boot") is not None else (p_boot[i] if i < len(p_boot) else None),
                "q": r.get("q") if r.get("q") is not None else (q_boot[i] if i < len(q_boot) else None),
                "D3": r.get("D3"),
            }
        )
    boot_rows = [
        {
            "metric": r.get("metric"),
            "scope_id": r.get("scope_id"),
            "lookback": r.get("lookback"),
            "horizon": r.get("horizon"),
            "n": r.get("n"),
            "b_fx": r.get("b_fx"),
            "ci_lo": r.get("ci_lo"),
            "ci_hi": r.get("ci_hi"),
            "p_boot": r.get("p_boot"),
            "ci_excludes_0": r.get("ci_excludes_0") if "ci_excludes_0" in r else None,
            "p_value_method": r.get("p_value_method") or "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE",
            "ci_method": r.get("ci_method") or "BOOTSTRAP_PERCENTILE_CI",
            "boot_n": r.get("boot_n"),
        }
        for r in family
    ]
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "reason": evaluation.get("reason"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "DEV_all_n": evaluation.get("DEV_all_n"),
                    "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
                    "C1_opened": c1_opened,
                    "final_pass_n": evaluation.get("final_pass_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df(
            [
                {
                    "precommit_id": evaluation.get("precommit_id"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "family_384_sha256": evaluation.get("family_384_sha256"),
                    "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
                    "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
                    "permutation_sha256": evaluation.get("permutation_sha256"),
                    "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
                    "universe105_sha256": evaluation.get("universe105_sha256"),
                    "sector_mapping_sha256": evaluation.get("sector_mapping_sha256"),
                    "control_gate_rule_id": evaluation.get("control_gate_rule_id"),
                    "precommit_sha_verified": evaluation.get("precommit_sha_verified"),
                }
            ]
        ).to_excel(xw, sheet_name="Identity", index=False)
        _df([evaluation.get("technical_invalidation") or {"empty": True}]).to_excel(xw, sheet_name="Technical_Invalidation", index=False)
        _df([evaluation.get("control_gate") or {"rule_id": evaluation.get("control_gate_rule_id")}]).to_excel(xw, sheet_name="Control_Gate", index=False)
        feas = evaluation.get("structural_feasibility") or {}
        _df(list(feas.get("matrix") or feas.get("rows") or [feas or {"empty": True}])).to_excel(xw, sheet_name="Structural_Feasibility", index=False)
        _df(list(evaluation.get("corrected_3650") or [{"empty": True}])).to_excel(xw, sheet_name="Corrected_3650", index=False)
        par = evaluation.get("unaffected_parity") or {}
        _df(list(par.get("fails") or [{"checked_n": par.get("checked_n"), "fail_n": par.get("fail_n"), "pass": par.get("pass")}])).to_excel(
            xw, sheet_name="Unaffected_Parity", index=False
        )
        _df([{"date": d} for d in (bound.get("eligible_dates") or [])] or [{"empty": True}]).to_excel(xw, sheet_name="Eligible_Days", index=False)
        fold_dates = []
        for name in ("DEV_EARLY", "DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
            for day in folds.get(name) or []:
                fold_dates.append({"fold": name, "date": day})
        _df(fold_dates or [{"empty": True}]).to_excel(xw, sheet_name="Folds", index=False)
        _df(
            [
                {
                    "bootstrap_method": "DATE_BLOCK_BOOTSTRAP",
                    "bootstrap_n": 2000,
                    "bootstrap_seed": 20240917,
                    "rng": "numpy.random.RandomState MT19937",
                    "ci_method": "BOOTSTRAP_PERCENTILE_CI",
                    "p_value_method": "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE",
                    "bh_method": "BENJAMINI_HOCHBERG_MONOTONE_Q",
                    "bh_m": 384,
                    "q_threshold": 0.05,
                    "bound_eq_0_fails": True,
                    "no_new_random_draws": True,
                    "bootstrap_index_sha256": evaluation.get("bootstrap_index_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Inference_Contract", index=False)
        _df(family or [{"empty": True}]).to_excel(xw, sheet_name="DEV_All_384", index=False)
        _df(boot_rows or [{"empty": True}]).to_excel(xw, sheet_name="DEV_Bootstrap", index=False)
        _df(bh_rows or [{"empty": True}]).to_excel(xw, sheet_name="DEV_BH", index=False)
        _df(family or [{"empty": True}]).to_excel(xw, sheet_name="DEV_Gates", index=False)
        _df([r for r in family if r.get("D1") and r.get("D2") and r.get("D3") and r.get("D4") and r.get("D5") and r.get("D6")] or [{"none_reached_d7": True}]).to_excel(
            xw, sheet_name="DEV_Strict60", index=False
        )
        _df(list(evaluation.get("frozen_candidates") or [{"empty": True}])).to_excel(xw, sheet_name="DEV_Candidates", index=False)
        _df(
            [
                {
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "n": evaluation.get("DEV_candidate_n"),
                    "freeze_timestamp": access.get("candidate_freeze_timestamp"),
                    "first_C1_outcome_access_timestamp": access.get("first_C1_outcome_access_timestamp"),
                    "C1_rows_read_before_candidate_freeze": access.get("C1_rows_read_before_candidate_freeze"),
                    "C1_rows_read_before_corrected_candidate_freeze": evaluation.get("C1_rows_read_before_corrected_candidate_freeze"),
                }
            ]
        ).to_excel(xw, sheet_name="Candidate_Freeze", index=False)
        _df([access or {"empty": True}]).to_excel(xw, sheet_name="C1_Access_Ledger", index=False)
        _df(c1_rows if c1_opened else nr).to_excel(xw, sheet_name="C1_Confirmation", index=False)
        _df(c1_rows if c1_opened else nr).to_excel(xw, sheet_name="C1_Strict60", index=False)
        _df([{"offset": r.get("offset"), "metric": r.get("metric"), "scope_id": r.get("scope_id")} for r in c1_rows] if c1_opened else nr).to_excel(
            xw, sheet_name="Offset_Map", index=False
        )
        _df([{"shuffle": r.get("shuffle"), "metric": r.get("metric"), "scope_id": r.get("scope_id")} for r in c1_rows] if c1_opened else nr).to_excel(
            xw, sheet_name="Day_Shuffle", index=False
        )
        _df(
            [{"identity": r.get("identity") or r.get("sector_identity"), "metric": r.get("metric"), "scope_id": r.get("scope_id")} for r in c1_rows]
            if c1_opened
            else nr
        ).to_excel(xw, sheet_name="Sector_Identity", index=False)
        _df(
            [{"driver_concentration": r.get("driver_concentration"), "metric": r.get("metric"), "scope_id": r.get("scope_id")} for r in c1_rows]
            if c1_opened
            else nr
        ).to_excel(xw, sheet_name="Driver_Concentration", index=False)
        _df(
            [{"target_concentration": r.get("target_concentration"), "metric": r.get("metric"), "scope_id": r.get("scope_id")} for r in c1_rows]
            if c1_opened
            else nr
        ).to_excel(xw, sheet_name="Target_Concentration", index=False)
        _df([{"common_factor": r.get("common_factor"), "metric": r.get("metric"), "scope_id": r.get("scope_id")} for r in c1_rows] if c1_opened else nr).to_excel(
            xw, sheet_name="Common_Factor", index=False
        )
        _df([{"stage": k, "n": v} for k, v in (evaluation.get("failure_stage_counts") or {"empty": 0}).items()]).to_excel(xw, sheet_name="Failure_Stages", index=False)
        _df([evaluation.get("contamination") or {"empty": True}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
