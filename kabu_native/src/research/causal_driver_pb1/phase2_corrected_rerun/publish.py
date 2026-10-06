"""Write the three corrected-rerun artifacts. Does not overwrite the invalid Phase 2 OUT."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.phase2_corrected_rerun import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.phase2_corrected_rerun.isolation import OUT

SHEETS = (
    "Manifest",
    "Technical_Invalidation",
    "Contract_Diff",
    "Resolver_Tests",
    "Coverage_Before",
    "Coverage_After",
    "Price_Age",
    "DEV_All_288",
    "DEV_Gates",
    "Corrected_Candidate_Freeze",
    "C1_Access_Barrier",
    "Safety",
)

C1_SHEETS = (
    "C1_Confirmation",
    "Offset_Map",
    "Day_Shuffle",
    "Concentration",
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


def _markdown(report: dict[str, Any]) -> str:
    a = report["answers"]
    inv = a.get("technical_invalidation") or {}
    cov = a.get("coverage") or {}
    sel = a.get("selection") or {}
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- old_phase2_classification `{a.get('old_phase2_classification')}`",
        f"- technical_invalidation `{inv.get('technical_verdict')}`",
        f"- exact contract violation `{inv.get('reason')}`",
        f"- expected `{inv.get('contract_expected')}`",
        f"- actual invalid implementation `{inv.get('actual_invalid_implementation')}`",
        f"- affected_clock_n `{inv.get('affected_clock_n')}`",
        f"- affected_fraction `{inv.get('affected_fraction')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- resolver tests `{json.dumps(a.get('resolver_tests_summary'), ensure_ascii=False)}`",
        f"- exact usable rate `{cov.get('exact_usable_rate')}`",
        f"- rca asof usable rate `{cov.get('rca_asof_usable_rate')}`",
        f"- session asof usable rate `{cov.get('session_asof_usable_rate')}`",
        f"- recovered_clocks_rca_asof `{cov.get('recovered_clocks_rca_asof')}`",
        f"- corrected DEV candidate n `{a.get('DEV_candidate_n')}`",
        f"- corrected candidate SHA `{a.get('candidate_list_sha256')}`",
        f"- C1 opened? `{a.get('C1_opened')}`",
        f"- C1 rows read before freeze `{a.get('C1_rows_read_before_corrected_candidate_freeze')}`",
        f"- USDJPY_DRIVER_FAMILY_STATUS `{a.get('USDJPY_DRIVER_FAMILY_STATUS')}`",
        f"- leader-laggard selection status `{a.get('leader_laggard_selection_status')}`",
        f"- INDEX_FUTURES_READY `{sel.get('INDEX_FUTURES_READY')}`",
        f"- LEADER_LAGGARD_READY `{sel.get('LEADER_LAGGARD_READY')}`",
        f"- BACKUP `{sel.get('BACKUP_DRIVER')}`",
        f"- leader_laggard_precommit_started `{sel.get('leader_laggard_precommit_started')}`",
        f"- FV / prospective `{a.get('FROZEN_VALIDATION_OPENED')}` / `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- invalid run not overwritten `{inv.get('invalid_run_not_overwritten')}`",
        f"- invalid run betas not used `{inv.get('invalid_run_betas_not_used')}`",
        "",
        "This task does not start leader-laggard precommit or Phase 3.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    inv = evaluation.get("technical_invalidation") or {}
    cov = evaluation.get("coverage") or {}
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "old_phase2_classification": evaluation.get("old_phase2_classification"),
        "technical_invalidation": inv,
        "exact_contract_violation": inv.get("reason"),
        "affected_clock_n": inv.get("affected_clock_n"),
        "affected_fraction": inv.get("affected_fraction"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "resolver_tests": evaluation.get("resolver_tests"),
        "resolver_tests_summary": {
            "n": len(evaluation.get("resolver_tests") or []),
            "n_pass": sum(1 for r in (evaluation.get("resolver_tests") or []) if r.get("pass")),
            "all_pass": all(r.get("pass") for r in (evaluation.get("resolver_tests") or [])) if evaluation.get("resolver_tests") else False,
        },
        "coverage": {k: v for k, v in cov.items() if k not in {"asof", "src_idx", "sector_rows"}},
        "exact_usable_rate": cov.get("exact_usable_rate"),
        "asof_usable_rate": cov.get("session_asof_usable_rate"),
        "rca_asof_usable_rate": cov.get("rca_asof_usable_rate"),
        "price_age": evaluation.get("price_age"),
        "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
        "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
        "C1_opened": bool(evaluation.get("C1_opened")),
        "C1_rows_read_before_corrected_candidate_freeze": evaluation.get("C1_rows_read_before_corrected_candidate_freeze"),
        "USDJPY_DRIVER_FAMILY_STATUS": evaluation.get("USDJPY_DRIVER_FAMILY_STATUS"),
        "leader_laggard_selection_status": evaluation.get("leader_laggard_selection_status"),
        "selection": evaluation.get("selection"),
        "C1_confirmed_n": evaluation.get("C1_confirmed_n"),
        "lead_gate_pass_n": evaluation.get("lead_gate_pass_n"),
        "day_shuffle_pass_n": evaluation.get("day_shuffle_pass_n"),
        "concentration_pass_n": evaluation.get("concentration_pass_n"),
        "final_pass_n": evaluation.get("final_pass_n"),
        "final_candidates": evaluation.get("final_candidates"),
        "top_failed_candidates": evaluation.get("top_failed_candidates"),
        "failure_stage_counts": evaluation.get("failure_stage_counts"),
        "DEV_all_288_n": evaluation.get("DEV_all_288_n"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "missingness_status": evaluation.get("missingness_status"),
        "FROZEN_VALIDATION_OPENED": False,
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
        "blockers": evaluation.get("blockers"),
        "response_contract": evaluation.get("response_contract"),
        "same_session_only": evaluation.get("same_session_only"),
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    if isinstance(slim.get("coverage"), dict):
        slim["coverage"] = {k: v for k, v in slim["coverage"].items() if k not in {"asof", "src_idx"}}
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
    family = list(evaluation.get("family") or [])
    freeze_obj = evaluation.get("corrected_freeze") or {}
    payload = list(evaluation.get("candidate_payload") or freeze_obj.get("candidates") or [])
    c1 = list(evaluation.get("c1") or [])
    access = evaluation.get("access") or {}
    tests = list(evaluation.get("resolver_tests") or [])
    age = evaluation.get("price_age") or {}
    opened = bool(evaluation.get("C1_opened"))
    sheets = list(SHEETS) + (list(C1_SHEETS) if opened else [])
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "old_phase2_classification": evaluation.get("old_phase2_classification"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
                    "C1_opened": opened,
                    "USDJPY_DRIVER_FAMILY_STATUS": evaluation.get("USDJPY_DRIVER_FAMILY_STATUS"),
                    "leader_laggard_selection_status": evaluation.get("leader_laggard_selection_status"),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([inv or {"empty": True}]).to_excel(xw, sheet_name="Technical_Invalidation", index=False)
        _df(
            [
                {
                    "expected": inv.get("contract_expected"),
                    "actual_invalid_run": inv.get("actual_invalid_implementation"),
                    "corrected": "last_completed_close available_at<=T / T+h; same_session_only; no max-age filter",
                    "retune": False,
                    "lookbacks_unchanged": "1/3/5/10/20/30m",
                    "horizons_unchanged": "5/10/20/30m",
                    "targets_unchanged": 12,
                    "clock_unchanged": "09:10-11:25",
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Contract_Diff", index=False)
        _df(tests or [{"empty": True}]).to_excel(xw, sheet_name="Resolver_Tests", index=False)
        _df(
            [
                {
                    "exact_usable_rate": cov.get("exact_usable_rate"),
                    "rca_asof_usable_rate": cov.get("rca_asof_usable_rate"),
                    "rca_expected_asof_usable": cov.get("rca_expected_asof_usable"),
                    "recovered_clocks_rca_asof": cov.get("recovered_clocks_rca_asof"),
                    "rca_expected_recovered_clocks": cov.get("rca_expected_recovered_clocks"),
                    "rca_match": cov.get("rca_match"),
                    "future_returns_computed": False,
                    "note": "RCA replica: presence accumulate from grid 08:00",
                }
            ]
        ).to_excel(xw, sheet_name="Coverage_Before", index=False)
        _df(
            [
                {
                    "session_asof_usable_rate": cov.get("session_asof_usable_rate"),
                    "session_vs_rca_delta": cov.get("session_vs_rca_delta"),
                    "session_semantics_note": cov.get("session_semantics_note"),
                    "proceed_to_dev_rerun": cov.get("proceed_to_dev_rerun"),
                    "same_session_only": True,
                    "note": "corrected resolver: TSE cash session open 09:00; no max-age filter",
                }
            ]
            + list(cov.get("sector_rows") or [])
        ).to_excel(xw, sheet_name="Coverage_After", index=False)
        _df([age or {"empty": True}]).to_excel(xw, sheet_name="Price_Age", index=False)
        _df(family).to_excel(xw, sheet_name="DEV_All_288", index=False)
        _df(
            [
                {
                    "target_scope": r.get("target_scope"),
                    "fx_lookback": r.get("fx_lookback"),
                    "response_horizon": r.get("response_horizon"),
                    "D1": r.get("D1"),
                    "D2": r.get("D2"),
                    "D3": r.get("D3"),
                    "D4": r.get("D4"),
                    "D5": r.get("D5"),
                    "D6": r.get("D6"),
                    "candidate": r.get("candidate"),
                }
                for r in family
            ]
            or [{"empty": True}]
        ).to_excel(xw, sheet_name="DEV_Gates", index=False)
        _df(
            [
                {
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "n": len(payload),
                    "invalid_run_sha_not_reused": evaluation.get("candidate_list_sha256")
                    != (inv.get("invalid_candidate_list_sha256") if inv else None),
                    "response_contract": (freeze_obj or {}).get("response_contract"),
                    "STAGE_A_COMPLETE": access.get("STAGE_A_COMPLETE"),
                    "C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE": access.get("C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE"),
                }
            ]
            + (payload or [])
        ).to_excel(xw, sheet_name="Corrected_Candidate_Freeze", index=False)
        _df(
            [
                {
                    "C1_opened": opened,
                    "C1_USDJPY_OUTCOMES_OPENED": opened,
                    "C1_rows_read_before_corrected_candidate_freeze": evaluation.get(
                        "C1_rows_read_before_corrected_candidate_freeze"
                    ),
                    "required_before_freeze": 0,
                    "C1_after_freeze": access.get("c1_after_freeze"),
                    "seal_until_candidate_n_ge_1": True,
                }
            ]
        ).to_excel(xw, sheet_name="C1_Access_Barrier", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
        if opened:
            _df(c1 or [{"empty": True}]).to_excel(xw, sheet_name="C1_Confirmation", index=False)
            _df(
                [{"lead": r.get("lead"), **{k: r.get(k) for k in ("target_scope", "fx_lookback", "response_horizon")}} for r in c1]
                or [{"empty": True}]
            ).to_excel(xw, sheet_name="Offset_Map", index=False)
            _df(
                [{"shuffle": r.get("shuffle"), **{k: r.get(k) for k in ("target_scope", "fx_lookback", "response_horizon")}} for r in c1]
                or [{"empty": True}]
            ).to_excel(xw, sheet_name="Day_Shuffle", index=False)
            _df(
                [
                    {"concentration": r.get("concentration"), **{k: r.get(k) for k in ("target_scope", "fx_lookback", "response_horizon")}}
                    for r in c1
                ]
                or [{"empty": True}]
            ).to_excel(xw, sheet_name="Concentration", index=False)
    return {"ok": True, "out": str(OUT), "sheets": sheets}
