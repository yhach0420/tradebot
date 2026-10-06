"""Coverage-corrected two-component RCA. Existing artifacts only. No temporal split."""
from __future__ import annotations

import json
from typing import Any

from research.generalization_failure_component_rca_v1 import (
    ANALYSIS_ID,
    CASE_A,
    COMPONENT_STRUCTURE,
    EXPECTED_LOW_SUPPORT_N,
    EXPECTED_VALID_N,
    L1_WIDE_SELECTION_INSTABILITY_PROVEN,
    L2_SELECTION_INSTABILITY_PROVEN,
    LOW_SUPPORT_IDS,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_REPLAY,
    NEXT_IF_A,
    ORIGINAL_G1G2_N,
    PARENT_ANALYSIS_ID,
    PARENT_MODE_REQUIRED,
    PARENT_NEXT_REQUIRED,
    PARENT_VERDICT_REQUIRED,
    PRIMARY_COMPONENT,
    RAW_CAPTURE_READ_N,
    RESIDUAL_BOTTLENECK,
    SELECTION_FORMALLY_TESTED_STUDY_IDS,
    ST_BASE_PF,
    ST_BASE_PNL,
    ST_CAUSAL_PF,
    ST_CAUSAL_PNL,
    ST_EX_BEST,
    ST_FOLD_TEST_PNL,
    ST_TRADE_N,
    ST_TRAIN_TOP3_N,
    ST_WINNER_ID,
    TEMPORAL_SPLIT,
    VERDICT_IF_A,
)
from research.generalization_failure_component_rca_v1.isolation import OUT
from research.generalization_failure_component_rca_v1.sources import answers, load_sources, ranking
from research.generalization_failure_component_rca_v1.spec import canonical_spec, spec_sha256
from research.generalization_failure_rca_v1.analyze import (
    _causal_pf,
    _causal_pnl,
    _classify_mode,
    _ex_best,
    _matrix_cell,
    _mode_supported,
    _pnl,
    _pf,
    _shared,
    g1_g2,
    gate_run_state,
)
from research.simple_full_strategy_discovery_v1 import MAX_RESEARCH_DATE

NOT_COMPUTED = "NOT_COMPUTED"
NOT_APPLICABLE = "NOT_APPLICABLE"
COVERAGE_FAIL_GATE = "COVERAGE_FAIL"


def original_coverage_pass(row: dict[str, Any]) -> bool:
    """Use the source study's frozen Coverage result. Do not infer from trade_n alone."""
    gate = str(row.get("gate") or "")
    if gate == COVERAGE_FAIL_GATE:
        return False
    if gate in {"PASS", "ECONOMIC_FAIL", "INCREMENTAL_FAIL"}:
        return True
    return False


def already_executed_check(inventory_fingerprint: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "OUT_REPORT_ABSENT"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "ANALYSIS_ID_MISMATCH"}
    same_inv = str(prev.get("inventory_fingerprint") or "") == inventory_fingerprint
    same_spec = str(prev.get("spec_sha256") or "") == spec_sha256()
    if same_inv and same_spec:
        return {
            "ALREADY_EXECUTED_CHECK": True,
            "REUSED_EXISTING_RESULT": True,
            "REASON": "SAME_SOURCE_INVENTORY_AND_METHODOLOGY",
            "prior_report": prev,
        }
    return {
        "ALREADY_EXECUTED_CHECK": True,
        "REUSED_EXISTING_RESULT": False,
        "REASON": "EXISTING_OUT_DIFFERENT_INVENTORY_OR_SPEC",
    }


def _pin_parent(parent: dict[str, Any]) -> dict[str, Any]:
    report = parent["report"]
    a = answers(report)
    d = dict(report.get("decision") or {})
    pin = {
        "ANALYSIS_ID": report.get("ANALYSIS_ID"),
        "CASE": d.get("CASE"),
        "VERDICT": a.get("58_VERDICT") or d.get("VERDICT"),
        "PRIMARY_GENERALIZATION_FAILURE_MODE": a.get("40_PRIMARY_GENERALIZATION_FAILURE_MODE")
        or d.get("PRIMARY_GENERALIZATION_FAILURE_MODE"),
        "NEXT": a.get("59_NEXT") or d.get("NEXT"),
        "ORIGINAL_G1G2_N": a.get("3_PRIMARY_cohort_N"),
    }
    if pin["ANALYSIS_ID"] != PARENT_ANALYSIS_ID:
        raise RuntimeError("PARENT_ANALYSIS_ID")
    if pin["VERDICT"] != PARENT_VERDICT_REQUIRED:
        raise RuntimeError(f"PARENT_VERDICT {pin['VERDICT']}")
    if pin["PRIMARY_GENERALIZATION_FAILURE_MODE"] != PARENT_MODE_REQUIRED:
        raise RuntimeError("PARENT_MODE")
    if pin["NEXT"] != PARENT_NEXT_REQUIRED:
        raise RuntimeError("PARENT_NEXT")
    if pin["CASE"] != "A":
        raise RuntimeError("PARENT_CASE")
    return pin


def _stability_for(cid: str, st_rep: dict[str, Any]) -> dict[str, Any]:
    if cid != ST_WINNER_ID:
        return {
            "STABILITY_RAN": False,
            "STABILITY_PASS": None,
            "STABILITY_STATUS": NOT_COMPUTED,
        }
    stab = dict(st_rep.get("stability") or {})
    a = answers(st_rep)
    passed = stab.get("STABILITY_PASS")
    if passed is None:
        passed = a.get("59_STABILITY_PASS")
    if passed is not False:
        raise RuntimeError("ST_WINNER_STABILITY_PASS_PIN")
    return {
        "STABILITY_RAN": True,
        "STABILITY_PASS": False,
        "STABILITY_STATUS": "FAIL",
        "TRAIN_TOP3_N": stab.get("TRAIN_TOP3_N") if stab.get("TRAIN_TOP3_N") is not None else a.get("57_TRAIN_TOP3_N"),
        "FOLD_SELECTED_TEST_TOTAL_PNL": stab.get("FOLD_SELECTED_TEST_TOTAL_PNL")
        if stab.get("FOLD_SELECTED_TEST_TOTAL_PNL") is not None
        else a.get("58_FOLD_SELECTED_TEST_TOTAL_PNL"),
    }


def _normalize(row: dict[str, Any], *, study: str, lineage: str, library: str, st_rep: dict[str, Any]) -> dict[str, Any]:
    gates = gate_run_state(row)
    cov = original_coverage_pass(row)
    cid = str(row.get("candidate_id") or "")
    stab = _stability_for(cid, st_rep)
    causal = _causal_pnl(row)
    pnl = _pnl(row)
    pf = _pf(row)
    exb = _ex_best(row)
    symbol_evaluable = bool(cov and gates["G6_RAN"] and causal is not None)
    collapse_symbol = None
    if symbol_evaluable:
        collapse_symbol = bool(pnl is not None and pnl > 0 and pf is not None and pf > 1.10 and causal < 0)
    day_evaluable = bool(cov and exb is not None and pnl is not None and pf is not None)
    collapse_day = None
    if day_evaluable:
        collapse_day = bool(pnl is not None and pnl > 0 and pf is not None and pf > 1.10 and exb is not None and exb < 0)
    g_all = all(
        [
            gates["G1_VALUE"],
            gates["G2_VALUE"],
            gates["G3_VALUE"],
            gates["G4_VALUE"],
            gates["G5_VALUE"],
            gates["G6_RAN"] and gates["G6_VALUE"],
        ]
    )
    residual = bool(
        cov
        and g_all
        and stab["STABILITY_RAN"] is True
        and stab["STABILITY_PASS"] is False
    )
    selection_failure = bool(stab["STABILITY_RAN"] is True and stab["STABILITY_PASS"] is False)
    mis_stab = 0
    if stab["STABILITY_RAN"] is False and (stab["STABILITY_PASS"] is False or stab["STABILITY_STATUS"] == "FAIL"):
        mis_stab = 1
    if stab["STABILITY_RAN"] is False and selection_failure:
        mis_stab = 1
    return {
        "candidate_id": cid,
        "study": study,
        "lineage": lineage,
        "library": library,
        "original_gate": row.get("gate"),
        "trade_n": row.get("trade_n") if row.get("trade_n") is not None else row.get("TRADE_N"),
        "TRADING_DAY_WITH_FILL_N": row.get("TRADING_DAY_WITH_FILL_N"),
        "trades_per_day": row.get("trades_per_day"),
        "COVERAGE_PASS": cov,
        "COVERAGE_INFERRED_FROM_TRADE_N_ONLY": False,
        "LOW_SUPPORT_DIAGNOSTIC": (not cov) and g1_g2(row),
        "VALID_PRIMARY": bool(cov and g1_g2(row)),
        "BASE_PNL": pnl,
        "BASE_PF": pf,
        "top_symbol": row.get("top_symbol"),
        "CAUSAL_EX_TOP1_PNL": causal if gates["G6_RAN"] else None,
        "CAUSAL_EX_TOP1_PF": _causal_pf(row) if gates["G6_RAN"] else None,
        "CAUSAL_EX_TOP1_STATUS": gates["CAUSAL_EX_TOP1_STATUS"],
        "best_day": row.get("best_day") or None,
        "EX_BEST_DAY_PNL": exb,
        "positive_days": row.get("positive_days") if row.get("positive_days") is not None else row.get("positive_day_n"),
        "negative_days": row.get("negative_days") if row.get("negative_days") is not None else row.get("negative_day_n"),
        "CAUSAL_SYMBOL_COLLAPSE": collapse_symbol,
        "SYMBOL_EVALUABLE": symbol_evaluable,
        "BEST_DAY_COLLAPSE": collapse_day,
        "DAY_EVALUABLE": day_evaluable,
        "G1G6_ALL_PASS": g_all,
        "RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS": residual,
        "SELECTION_FAILURE": selection_failure,
        "STABILITY_NOT_RUN_MISCLASSIFIED": mis_stab,
        **gates,
        **stab,
    }


def _extract(st_rep: dict[str, Any], c1_rep: dict[str, Any], rec_rep: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in ranking(st_rep):
        if g1_g2(r):
            out.append(
                _normalize(
                    r,
                    study="SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1",
                    lineage="L1_TECHNICAL_PRICE_STATE",
                    library="ST",
                    st_rep=st_rep,
                )
            )
    for r in ranking(c1_rep):
        if g1_g2(r):
            out.append(
                _normalize(
                    r,
                    study="C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
                    lineage="L1_TECHNICAL_PRICE_STATE",
                    library="C1",
                    st_rep=st_rep,
                )
            )
    for r in ranking(rec_rep):
        if g1_g2(r):
            out.append(
                _normalize(
                    r,
                    study="RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1",
                    lineage="L2_RECOVERY_RECLAIM_PATH",
                    library="RECOVERY",
                    st_rep=st_rep,
                )
            )
    if len(out) != ORIGINAL_G1G2_N:
        raise RuntimeError(f"ORIGINAL_G1G2_N {len(out)}")
    return out


def _lineage_symbol(cands: list[dict[str, Any]]) -> dict[str, Any]:
    ev = [c for c in cands if c.get("SYMBOL_EVALUABLE")]
    collapse = [c for c in ev if c.get("CAUSAL_SYMBOL_COLLAPSE") is True]
    survive = [c for c in ev if c.get("CAUSAL_SYMBOL_COLLAPSE") is False]
    return {
        "SYMBOL_EVALUABLE_N": len(ev),
        "SYMBOL_COLLAPSE_N": len(collapse),
        "SYMBOL_SURVIVE_N": len(survive),
        "classification": _classify_mode(len(ev), len(collapse), len(survive), kind="SYMBOL"),
        "collapse_ids": [c["candidate_id"] for c in collapse],
        "survive_ids": [c["candidate_id"] for c in survive],
    }


def _lineage_day(cands: list[dict[str, Any]]) -> dict[str, Any]:
    ev = [c for c in cands if c.get("DAY_EVALUABLE")]
    collapse = [c for c in ev if c.get("BEST_DAY_COLLAPSE") is True]
    survive = [c for c in ev if c.get("BEST_DAY_COLLAPSE") is False]
    return {
        "DAY_EVALUABLE_N": len(ev),
        "BEST_DAY_COLLAPSE_N": len(collapse),
        "BEST_DAY_SURVIVE_N": len(survive),
        "classification": _classify_mode(len(ev), len(collapse), len(survive), kind="DAY"),
        "DAY_EVALUABLE_BASIS": "EX_BEST_DAY_PNL_PRESENT_AND_COVERAGE_PASS",
        "collapse_ids": [c["candidate_id"] for c in collapse],
        "survive_ids": [c["candidate_id"] for c in survive],
    }


def _winner_pack(st_rep: dict[str, Any], winner: dict[str, Any]) -> dict[str, Any]:
    if int(winner.get("trade_n") or 0) != ST_TRADE_N:
        raise RuntimeError(f"ST_TRADE_N {winner.get('trade_n')}")
    if winner.get("BASE_PNL") != ST_BASE_PNL:
        raise RuntimeError("ST_BASE_PNL")
    if winner.get("BASE_PF") is None or abs(float(winner["BASE_PF"]) - ST_BASE_PF) > 1e-12:
        raise RuntimeError("ST_BASE_PF")
    if winner.get("EX_BEST_DAY_PNL") != ST_EX_BEST:
        raise RuntimeError("ST_EX_BEST")
    if winner.get("CAUSAL_EX_TOP1_PNL") != ST_CAUSAL_PNL:
        raise RuntimeError("ST_CAUSAL_PNL")
    if winner.get("CAUSAL_EX_TOP1_PF") is None or abs(float(winner["CAUSAL_EX_TOP1_PF"]) - ST_CAUSAL_PF) > 1e-12:
        raise RuntimeError("ST_CAUSAL_PF")
    if not winner.get("G1G6_ALL_PASS"):
        raise RuntimeError("ST_G1G6")
    if winner.get("STABILITY_RAN") is not True or winner.get("STABILITY_PASS") is not False:
        raise RuntimeError("ST_STABILITY")
    if winner.get("TRAIN_TOP3_N") != ST_TRAIN_TOP3_N:
        raise RuntimeError("TRAIN_TOP3")
    if winner.get("FOLD_SELECTED_TEST_TOTAL_PNL") != ST_FOLD_TEST_PNL:
        raise RuntimeError("FOLD_PNL")
    blocks = list((st_rep.get("winner_blocks") or {}).get("blocks") or [])
    by = {str(b.get("block")): b for b in blocks}
    if set(by) != {"B1", "B2", "B3", "B4", "B5"}:
        raise RuntimeError("WINNER_BLOCKS")
    return {
        "ST_WINNER_ID": ST_WINNER_ID,
        "trade_n": ST_TRADE_N,
        "BASE_PNL": ST_BASE_PNL,
        "BASE_PF": ST_BASE_PF,
        "EX_BEST": ST_EX_BEST,
        "CAUSAL_EX_TOP1_PNL": ST_CAUSAL_PNL,
        "CAUSAL_EX_TOP1_PF": ST_CAUSAL_PF,
        "G1": True,
        "G2": True,
        "G3": True,
        "G4": True,
        "G5": True,
        "G6": True,
        "STABILITY_RAN": True,
        "STABILITY_PASS": False,
        "TRAIN_TOP3_N": ST_TRAIN_TOP3_N,
        "FOLD_SELECTED_TEST_TOTAL_PNL": ST_FOLD_TEST_PNL,
        "ST_WINNER_CONCENTRATION_RESILIENT": True,
        "ST_WINNER_ABSOLUTE_ECONOMIC_PASS": True,
        "ST_WINNER_SELECTION_FAILURE": True,
        "blocks": [
            {
                "block": k,
                "pnl": by[k].get("pnl"),
                "trade_n": by[k].get("trade_n"),
                "PF": by[k].get("PF"),
                "days": by[k].get("days"),
            }
            for k in ("B1", "B2", "B3", "B4", "B5")
        ],
        "TEMPORAL_SPLIT_COMPUTED": False,
    }


def decide(sources: dict[str, Any] | None = None) -> dict[str, Any]:
    pack = sources or load_sources()
    by = pack["by_id"]
    reused = already_executed_check(str(pack["inventory_fingerprint"]))
    parent = _pin_parent(by["GENERALIZATION_FAILURE_RCA_V1"])
    st_rep = by["SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1"]["report"]
    all_g1g2 = _extract(
        st_rep,
        by["C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"]["report"],
        by["RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1"]["report"],
    )
    low = [c for c in all_g1g2 if c.get("LOW_SUPPORT_DIAGNOSTIC")]
    valid = [c for c in all_g1g2 if c.get("VALID_PRIMARY")]
    low_ids = [c["candidate_id"] for c in low]
    if set(low_ids) != set(LOW_SUPPORT_IDS):
        raise RuntimeError(f"LOW_SUPPORT_IDS {low_ids}")
    if len(low) != EXPECTED_LOW_SUPPORT_N or len(valid) != EXPECTED_VALID_N:
        raise RuntimeError(f"COHORT {len(low)} {len(valid)}")
    if any(c["candidate_id"] in LOW_SUPPORT_IDS for c in valid):
        raise RuntimeError("LOW_SUPPORT_IN_VALID")
    mis_stab = sum(int(c.get("STABILITY_NOT_RUN_MISCLASSIFIED") or 0) for c in all_g1g2)
    if mis_stab != 0:
        raise RuntimeError(f"STABILITY_NOT_RUN_MISCLASSIFIED_N {mis_stab}")
    l1 = [c for c in valid if c["lineage"] == "L1_TECHNICAL_PRICE_STATE"]
    l2 = [c for c in valid if c["lineage"] == "L2_RECOVERY_RECLAIM_PATH"]
    l1_sym = _lineage_symbol(l1)
    l2_sym = _lineage_symbol(l2)
    l1_day = _lineage_day(l1)
    l2_day = _lineage_day(l2)
    cross_symbol = _mode_supported(str(l1_sym["classification"])) and _mode_supported(str(l2_sym["classification"]))
    cross_day = _mode_supported(str(l1_day["classification"])) and _mode_supported(str(l2_day["classification"]))
    winner = next(c for c in valid if c["candidate_id"] == ST_WINNER_ID)
    win = _winner_pack(st_rep, winner)
    residual = bool(winner.get("RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS"))
    if residual is not True:
        raise RuntimeError("RESIDUAL_REQUIRED")
    st_sel = True
    if L1_WIDE_SELECTION_INSTABILITY_PROVEN or L2_SELECTION_INSTABILITY_PROVEN:
        raise RuntimeError("WIDE_SELECTION_MUST_BE_FALSE")
    if not (cross_symbol or cross_day):
        raise RuntimeError("CROSS_LINEAGE_CONCENTRATION_REQUIRED")
    if not (winner.get("G1G6_ALL_PASS") and winner.get("STABILITY_RAN") and winner.get("STABILITY_PASS") is False):
        raise RuntimeError("TWO_STAGE_PREDICATE")
    decision = {
        "CASE": "A",
        "CASE_A_SURVIVED": True,
        "COMPONENT_STRUCTURE": COMPONENT_STRUCTURE,
        "PRIMARY_COMPONENT": PRIMARY_COMPONENT,
        "RESIDUAL_BOTTLENECK": RESIDUAL_BOTTLENECK,
        "RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS": True,
        "ST_SPECIFIC_SELECTION_INSTABILITY_SUPPORTED": st_sel,
        "L1_WIDE_SELECTION_INSTABILITY_PROVEN": False,
        "L2_SELECTION_INSTABILITY_PROVEN": False,
        "SELECTION_GENERALIZATION_FORMALLY_TESTED_STUDY_N": 1,
        "SELECTION_GENERALIZATION_FORMALLY_TESTED_STUDY_IDS": list(SELECTION_FORMALLY_TESTED_STUDY_IDS),
        "CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": cross_symbol,
        "CROSS_LINEAGE_DAY_MODE_SUPPORT": cross_day,
        "CROSS_LINEAGE": "CONCENTRATION_FRAGILITY",
        "RESIDUAL_FORMALLY_OBSERVED_IN_ST": "SELECTION_SURFACE_GENERALIZATION_FAILURE",
        "VERDICT": VERDICT_IF_A,
        "NEXT": NEXT_IF_A,
        "NEXT_SCOPE": "SYSTEMATIC_STATE_TRANSITION FORMAL BLOCKED-STABILITY MECHANISM RCA",
        "NEXT_QUESTION": "WHY DOES THE ONLY G1-G6-PASS ST STRATEGY FAIL TO TRANSFER ACROSS BLOCKED TRAIN SELECTION?",
        "NEXT_NOT_QUESTION": "WHY DOES ALL L1 FAIL?",
        "NEXT_CREATES_STRATEGY": False,
        "EXACT_EVIDENCE": (
            f"PRIMARY_VALID_COHORT N={len(valid)} after original Coverage gate "
            f"(excluded LOW_SUPPORT_DIAGNOSTIC {low_ids}). "
            f"Valid L1 symbol {l1_sym['classification']} "
            f"{l1_sym['SYMBOL_EVALUABLE_N']}/{l1_sym['SYMBOL_COLLAPSE_N']}/{l1_sym['SYMBOL_SURVIVE_N']}; "
            f"L2 symbol {l2_sym['classification']} "
            f"{l2_sym['SYMBOL_EVALUABLE_N']}/{l2_sym['SYMBOL_COLLAPSE_N']}/{l2_sym['SYMBOL_SURVIVE_N']}. "
            f"Valid L1 day {l1_day['classification']} "
            f"{l1_day['BEST_DAY_COLLAPSE_N']}/{l1_day['BEST_DAY_SURVIVE_N']}; "
            f"L2 day {l2_day['classification']} "
            f"{l2_day['BEST_DAY_COLLAPSE_N']}/{l2_day['BEST_DAY_SURVIVE_N']}. "
            f"{ST_WINNER_ID} Coverage+G1-G6 PASS, causal ex-top +{ST_CAUSAL_PNL}, "
            f"STABILITY_RAN=true STABILITY_PASS=false TRAIN_TOP3_N={ST_TRAIN_TOP3_N} "
            f"fold-selected test PnL={ST_FOLD_TEST_PNL}. "
            "Low-support G1+G2 COVERAGE_FAIL names are diagnostic only and did not change VERDICT."
        ),
        "FORBIDDEN_CLAIMS": [
            "all technical strategies suffer selection instability",
            "selection instability is proven across independent lineages",
            "RESIDUAL_BOTTLENECK=L1_WIDE_SELECTION_SURFACE_GENERALIZATION",
        ],
    }
    ran_n = sum(1 for c in all_g1g2 if c.get("STABILITY_RAN") is True)
    not_run_n = sum(1 for c in all_g1g2 if c.get("STABILITY_RAN") is False)
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ALREADY_EXECUTED": reused,
        "inventory_fingerprint": pack["inventory_fingerprint"],
        "parent": parent,
        "source_inventory": [
            {k: r[k] for k in ("ANALYSIS_ID", "dirname", "role", "lineage", "path", "report_sha256")}
            for r in pack["items"]
        ],
        "all_g1g2": all_g1g2,
        "low_support": low,
        "valid_cohort": valid,
        "ORIGINAL_G1G2_N": len(all_g1g2),
        "PRIMARY_VALID_COHORT_N": len(valid),
        "LOW_SUPPORT_DIAGNOSTIC_N": len(low),
        "MANUAL_SELECTION": False,
        "l1_symbol": l1_sym,
        "l2_symbol": l2_sym,
        "l1_day": l1_day,
        "l2_day": l2_day,
        "shared_top_symbol": _shared([c.get("top_symbol") for c in l1], [c.get("top_symbol") for c in l2]),
        "shared_best_day": _shared([c.get("best_day") for c in l1], [c.get("best_day") for c in l2]),
        "winner": win,
        "stability_counts": {
            "STABILITY_RAN_N": ran_n,
            "STABILITY_NOT_RUN_N": not_run_n,
            "STABILITY_NOT_RUN_MISCLASSIFIED_N": mis_stab,
        },
        "lineage_matrix": {
            "SYMBOL_CONCENTRATION": {"L1": _matrix_cell(str(l1_sym["classification"])), "L2": _matrix_cell(str(l2_sym["classification"]))},
            "DAY_CONCENTRATION": {"L1": _matrix_cell(str(l1_day["classification"])), "L2": _matrix_cell(str(l2_day["classification"]))},
            "SELECTION_INSTABILITY": {"L1": "ST_SPECIFIC_ONLY", "L2": NOT_APPLICABLE},
        },
        "decision": decision,
        "guards": {
            "SYMBOL_FILTER_CREATED": False,
            "DATE_FILTER_CREATED": False,
            "REGIME_GATE_CREATED": False,
            "TIME_GATE_CREATED": False,
            "WEEKDAY_RULE_CREATED": False,
            "ENTRY_RETUNE": False,
            "EXIT_RETUNE": False,
            "THRESHOLD_SEARCHED": False,
            "SIZING": False,
            "NEW_REPLAY": NEW_REPLAY,
            "NEW_CANDIDATE": NEW_CANDIDATE,
            "NEW_ENTRY": NEW_ENTRY,
            "NEW_EXIT": NEW_EXIT,
            "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
            "TEMPORAL_SPLIT": TEMPORAL_SPLIT,
            "ENSEMBLE": False,
            "CANDIDATE_AVERAGING": False,
            "L1_WIDE_SELECTION_INSTABILITY_PROVEN": False,
            "L2_SELECTION_INSTABILITY_PROVEN": False,
        },
        "spec": canonical_spec(),
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    parent = dict(pack.get("parent") or {})
    d = dict(pack.get("decision") or {})
    win = dict(pack.get("winner") or {})
    blocks = {b["block"]: b for b in list(win.get("blocks") or [])}
    l1s = dict(pack.get("l1_symbol") or {})
    l2s = dict(pack.get("l2_symbol") or {})
    l1d = dict(pack.get("l1_day") or {})
    l2d = dict(pack.get("l2_day") or {})
    stab = dict(pack.get("stability_counts") or {})
    g = dict(pack.get("guards") or {})
    reused = dict(pack.get("ALREADY_EXECUTED") or {})
    low = list(pack.get("low_support") or [])
    return {
        "ALREADY_EXECUTED_CHECK": reused.get("ALREADY_EXECUTED_CHECK"),
        "REUSED_EXISTING_RESULT": reused.get("REUSED_EXISTING_RESULT"),
        "1_parent_RCA_verdict": parent.get("VERDICT"),
        "2_parent_mode": parent.get("PRIMARY_GENERALIZATION_FAILURE_MODE"),
        "3_parent_CASE_A_survived": d.get("CASE_A_SURVIVED"),
        "4_original_G1G2_N": pack.get("ORIGINAL_G1G2_N"),
        "5_PRIMARY_VALID_COHORT_N": pack.get("PRIMARY_VALID_COHORT_N"),
        "6_coverage_from_original_gate_not_trade_n": True,
        "7_LOW_SUPPORT_DIAGNOSTIC_N": pack.get("LOW_SUPPORT_DIAGNOSTIC_N"),
        "8_LOW_SUPPORT_IDS": [c.get("candidate_id") for c in low],
        "9_manual_selection": False,
        "10_L1_valid_symbol_evaluable_collapse_survive": {
            "evaluable": l1s.get("SYMBOL_EVALUABLE_N"),
            "collapse": l1s.get("SYMBOL_COLLAPSE_N"),
            "survive": l1s.get("SYMBOL_SURVIVE_N"),
        },
        "11_L1_valid_symbol_classification": l1s.get("classification"),
        "12_L2_valid_symbol_evaluable_collapse_survive": {
            "evaluable": l2s.get("SYMBOL_EVALUABLE_N"),
            "collapse": l2s.get("SYMBOL_COLLAPSE_N"),
            "survive": l2s.get("SYMBOL_SURVIVE_N"),
        },
        "13_L2_valid_symbol_classification": l2s.get("classification"),
        "14_CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": d.get("CROSS_LINEAGE_SYMBOL_MODE_SUPPORT"),
        "15_shared_exact_top_symbols": (pack.get("shared_top_symbol") or {}).get("ids"),
        "16_L1_valid_day_collapse_survive": {
            "evaluable": l1d.get("DAY_EVALUABLE_N"),
            "collapse": l1d.get("BEST_DAY_COLLAPSE_N"),
            "survive": l1d.get("BEST_DAY_SURVIVE_N"),
        },
        "17_L1_valid_day_classification": l1d.get("classification"),
        "18_L2_valid_day_collapse_survive": {
            "evaluable": l2d.get("DAY_EVALUABLE_N"),
            "collapse": l2d.get("BEST_DAY_COLLAPSE_N"),
            "survive": l2d.get("BEST_DAY_SURVIVE_N"),
        },
        "19_L2_valid_day_classification": l2d.get("classification"),
        "20_CROSS_LINEAGE_DAY_MODE_SUPPORT": d.get("CROSS_LINEAGE_DAY_MODE_SUPPORT"),
        "21_shared_exact_best_days": (pack.get("shared_best_day") or {}).get("ids"),
        "22_ST_winner_ID": win.get("ST_WINNER_ID"),
        "23_ST_trade_n": win.get("trade_n"),
        "24_ST_BASE_PnL_PF": {"PnL": win.get("BASE_PNL"), "PF": win.get("BASE_PF")},
        "25_ST_G1_G6_all_pass": True,
        "26_ST_EX_BEST": win.get("EX_BEST"),
        "27_ST_causal_ex_top_PnL_PF": {"PnL": win.get("CAUSAL_EX_TOP1_PNL"), "PF": win.get("CAUSAL_EX_TOP1_PF")},
        "28_ST_STABILITY_RAN": win.get("STABILITY_RAN"),
        "29_ST_STABILITY_PASS": win.get("STABILITY_PASS"),
        "30_TRAIN_TOP3_N": win.get("TRAIN_TOP3_N"),
        "31_fold_selected_PnL": win.get("FOLD_SELECTED_TEST_TOTAL_PNL"),
        "32_ST_WINNER_CONCENTRATION_RESILIENT": win.get("ST_WINNER_CONCENTRATION_RESILIENT"),
        "33_ST_WINNER_ABSOLUTE_ECONOMIC_PASS": win.get("ST_WINNER_ABSOLUTE_ECONOMIC_PASS"),
        "34_ST_WINNER_SELECTION_FAILURE": win.get("ST_WINNER_SELECTION_FAILURE"),
        "35_RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS": d.get("RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS"),
        "36_B1_pnl": (blocks.get("B1") or {}).get("pnl"),
        "37_B2_pnl": (blocks.get("B2") or {}).get("pnl"),
        "38_B3_pnl": (blocks.get("B3") or {}).get("pnl"),
        "39_B4_pnl": (blocks.get("B4") or {}).get("pnl"),
        "40_B5_pnl": (blocks.get("B5") or {}).get("pnl"),
        "41_FIRST_3_LAST_2_computed": False,
        "42_COMPONENT_STRUCTURE": d.get("COMPONENT_STRUCTURE"),
        "43_PRIMARY_COMPONENT": d.get("PRIMARY_COMPONENT"),
        "44_RESIDUAL_BOTTLENECK": d.get("RESIDUAL_BOTTLENECK"),
        "45_formal_selection_tested_study_N": d.get("SELECTION_GENERALIZATION_FORMALLY_TESTED_STUDY_N"),
        "46_formal_selection_tested_study_IDs": d.get("SELECTION_GENERALIZATION_FORMALLY_TESTED_STUDY_IDS"),
        "47_ST_specific_selection_instability_supported": d.get("ST_SPECIFIC_SELECTION_INSTABILITY_SUPPORTED"),
        "48_L1_wide_selection_instability_proven": False,
        "49_L2_selection_instability_proven": False,
        "50_D3_decision_eligible": False,
        "51_strategy_created": False,
        "52_symbol_date_regime_filter": False,
        "53_threshold_search": False,
        "54_new_replay": False,
        "55_raw_Capture_read": False,
        "56_Holdout_read": False,
        "57_Stress_read": False,
        "58_future_used": False,
        "59_gate_relaxation": False,
        "60_Sizing": False,
        "61_Runtime_changed": False,
        "62_submit_cancel_live": "0/0/0",
        "63_TRUE_OOS_CERTIFIED": {"TRUE_OOS": False, "CERTIFIED": False},
        "64_STABILITY_RAN_candidate_N": stab.get("STABILITY_RAN_N"),
        "65_STABILITY_NOT_RUN_candidate_N": stab.get("STABILITY_NOT_RUN_N"),
        "66_STABILITY_NOT_RUN_MISCLASSIFIED_N": stab.get("STABILITY_NOT_RUN_MISCLASSIFIED_N"),
        "67_ST_winner_G1_G6_all_pass": True,
        "68_ST_winner_STABILITY_RAN": True,
        "69_ST_winner_STABILITY_PASS": False,
        "70_formal_selection_tested_study_N": 1,
        "71_formal_selection_tested_study_IDs": list(SELECTION_FORMALLY_TESTED_STUDY_IDS),
        "72_ST_specific_selection_instability_supported": True,
        "73_L1_wide_selection_instability_proven": False,
        "74_L2_selection_instability_proven": False,
        "75_residual_bottleneck_scope": d.get("RESIDUAL_BOTTLENECK"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "NEW_REPLAY": g.get("NEW_REPLAY"),
        "RAW_CAPTURE_READ_N": g.get("RAW_CAPTURE_READ_N"),
    }
