"""Collapse architecture studies into independent information lineages. No new economics."""
from __future__ import annotations

import json
from typing import Any

from research.research_objective_rebase_v1 import (
    ANALYSIS_ID,
    ANY_CANDIDATE_FAMILY_VOTE,
    C4_ANALYSIS_ID,
    C4_NEXT_REQUIRED,
    C4_PRIMARY_CLOSE_REASON,
    C4_VERDICT_REQUIRED,
    C5_CREATED,
    CANDIDATE_COUNT_WEIGHTED_VOTE,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CLASS_IDS,
    INDEPENDENT_INFORMATION_LINEAGE_N,
    LINEAGE_IDS,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_FOLD_RUN,
    NEW_FULL_CAUSAL_RUN,
    NEW_PNL_SIMULATION,
    NEW_REPLAY,
    NEW_THRESHOLD,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NEXT_IF_D,
    OVERLAY_ID,
)
from research.research_objective_rebase_v1.isolation import OUT
from research.research_objective_rebase_v1.sources import answers, load_sources, ranking
from research.research_objective_rebase_v1.spec import canonical_spec, spec_sha256
from research.simple_full_strategy_discovery_v1 import MAX_RESEARCH_DATE

TAXONOMY_KEYS = (
    "COVERAGE_FAILURE",
    "ABSOLUTE_EDGE_FAILURE",
    "DAY_SIGN_FAILURE",
    "BEST_DAY_DEPENDENCE",
    "DRAWDOWN_DOMINANCE",
    "SYMBOL_CONCENTRATION",
    "SELECTION_INSTABILITY",
    "PORTFOLIO_INTERACTION",
    "POST_FILL_VALUE_CAPTURE_EVIDENCE",
    "INTEGRITY_LIMIT",
)

SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
NOT_COMPUTED = "NOT_COMPUTED"
NOT_APPLICABLE = "NOT_APPLICABLE"

E0 = "E0_NO_AGGREGATE_EDGE"
E1 = "E1_AGGREGATE_EDGE_OBSERVED"
E2 = "E2_ROBUST_EDGE_OBSERVED"
E3 = "E3_FROZEN_STRATEGY"

G2_MIN_PF = 1.10


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gtable(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("g_table") or row.get("G1_G6") or {})


def g1_g2(row: dict[str, Any]) -> bool:
    g = _gtable(row)
    if "G1_TOTAL_PNL" in g or "G2_PF" in g:
        return bool(g.get("G1_TOTAL_PNL")) and bool(g.get("G2_PF"))
    pnl = _f(row.get("TOTAL_PNL") if row.get("TOTAL_PNL") is not None else row.get("pnl"))
    pf = _f(row.get("PF"))
    return pnl is not None and pf is not None and pnl > 0 and pf > G2_MIN_PF


def g1_g6_pass(row: dict[str, Any]) -> bool:
    if str(row.get("gate") or "") == "PASS":
        return True
    g = _gtable(row)
    keys = (
        "G1_TOTAL_PNL",
        "G2_PF",
        "G3_DAY_SIGNS",
        "G4_EX_BEST",
        "G5_PNL_PLUS_MAXDD",
        "G6_CAUSAL_EX_TOP1",
    )
    if all(k in g for k in keys):
        return all(bool(g.get(k)) for k in keys)
    return False


def failed_gates(row: dict[str, Any]) -> list[str]:
    g = _gtable(row)
    order = (
        ("G1_TOTAL_PNL", "G1"),
        ("G2_PF", "G2"),
        ("G3_DAY_SIGNS", "G3"),
        ("G4_EX_BEST", "G4"),
        ("G5_PNL_PLUS_MAXDD", "G5"),
        ("G6_CAUSAL_EX_TOP1", "G6"),
    )
    failed = [label for key, label in order if key in g and not bool(g.get(key))]
    if g.get("G6_RAN") is False and "G6" not in failed:
        failed.append("G6_NOT_OPENED")
    return failed


def already_executed_check(inventory_fingerprint: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {
            "ALREADY_EXECUTED_CHECK": False,
            "REUSED_EXISTING_RESULT": False,
            "REASON": "OUT_REPORT_ABSENT",
        }
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {
            "ALREADY_EXECUTED_CHECK": False,
            "REUSED_EXISTING_RESULT": False,
            "REASON": "ANALYSIS_ID_MISMATCH",
        }
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


def _c4_pin(c4: dict[str, Any]) -> dict[str, Any]:
    a = answers(c4["report"])
    ranking_rows = ranking(c4["report"])
    treatments = [
        r
        for r in ranking_rows
        if str(r.get("C4_POLICY") or "") == "C4_FIRST_ARRIVAL_PER_EXACT_T0_V2"
        or str(r.get("ARM") or "") in {"ARM_TREATMENT", "TREATMENT"}
    ]
    i1_n = sum(1 for r in treatments if r.get("I1") is True)
    i2_n = sum(1 for r in treatments if r.get("I2") is True)
    i2_null_n = sum(1 for r in treatments if r.get("I2") is None)
    g6_ran_n = sum(1 for r in treatments if (_gtable(r).get("G6_RAN") is True))
    pin = {
        "ANALYSIS_ID": str(a.get("ANALYSIS_ID") or c4["report"].get("ANALYSIS_ID")),
        "VERDICT": a.get("VERDICT"),
        "NEXT": a.get("NEXT"),
        "FOLD_LOCAL_ELIGIBILITY_PARITY_PASS": a.get("FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"),
        "CANARY_HARD_PASS": a.get("CANARY_HARD_PASS"),
        "ARM_RERUN_N": a.get("ARM_RERUN_N"),
        "COVERAGE_PASS_N": a.get("COVERAGE_PASS_N"),
        "TREATMENT_ABSOLUTE_PASS_N": a.get("TREATMENT_ABSOLUTE_PASS_N"),
        "WINNER": a.get("WINNER"),
        "C4_INCREMENTAL_SUPPORT_N": a.get("C4_INCREMENTAL_SUPPORT_N"),
        "I1_PASS_N": i1_n,
        "I2_PASS_N": i2_n,
        "I2_NULL_N": i2_null_n,
        "TREATMENT_G6_RAN_N": g6_ran_n,
        "TREATMENT_N": len(treatments),
        "C4_PRIMARY_CLOSE_REASON": C4_PRIMARY_CLOSE_REASON,
        "C4_COUNTED_AS_ALPHA_LINEAGE": False,
        "OVERLAY_RESCUE": "OVERLAY_RESCUE_NOT_SUPPORTED",
    }
    if pin["ANALYSIS_ID"] != C4_ANALYSIS_ID:
        raise RuntimeError(f"C4_PIN_ANALYSIS_ID {pin['ANALYSIS_ID']}")
    if pin["VERDICT"] != C4_VERDICT_REQUIRED:
        raise RuntimeError(f"C4_PIN_VERDICT {pin['VERDICT']}")
    if pin["NEXT"] != C4_NEXT_REQUIRED:
        raise RuntimeError(f"C4_PIN_NEXT {pin['NEXT']}")
    if pin["FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"] is not True:
        raise RuntimeError("C4_PIN_PARITY")
    if pin["CANARY_HARD_PASS"] is not True:
        raise RuntimeError("C4_PIN_CANARY")
    if pin["ARM_RERUN_N"] != 40:
        raise RuntimeError("C4_PIN_ARM_N")
    if pin["COVERAGE_PASS_N"] != 20:
        raise RuntimeError("C4_PIN_COVERAGE")
    if pin["TREATMENT_ABSOLUTE_PASS_N"] != 0:
        raise RuntimeError("C4_PIN_ABSOLUTE")
    if pin["WINNER"] is not None:
        raise RuntimeError("C4_PIN_WINNER")
    if C4_PRIMARY_CLOSE_REASON == "I2_ZERO":
        raise RuntimeError("C4_PRIMARY_MUST_NOT_BE_I2_ZERO")
    return pin


def _rethink_classes(rethink: dict[str, Any]) -> dict[str, Any]:
    a = answers(rethink["report"])
    rows = list(a.get("10_eligibility_rows") or [])
    by = {str(r.get("CLASS_ID")): r for r in rows}
    missing = [c for c in CLASS_IDS if c not in by]
    if missing:
        raise RuntimeError(f"RETHINK_MISSING_CLASSES {missing}")
    return {
        "C1_ORIGINAL_ELIGIBLE": bool(by["C1_MULTI_TIMEFRAME"].get("ELIGIBLE")),
        "C2_ELIGIBLE": bool(by["C2_EPISODE_AGE"].get("ELIGIBLE")),
        "C3_ELIGIBLE": bool(by["C3_FAILURE_ROUTING"].get("ELIGIBLE")),
        "C4_ORIGINAL_ELIGIBLE": bool(by["C4_PORTFOLIO_CROWDING"].get("ELIGIBLE")),
        "C2_CLOSED_INELIGIBLE": (not by["C2_EPISODE_AGE"].get("ELIGIBLE"))
        and bool(by["C2_EPISODE_AGE"].get("CLOSED_LINEAGE_MATCH")),
        "C3_CLOSED_INELIGIBLE": (not by["C3_FAILURE_ROUTING"].get("ELIGIBLE"))
        and bool(by["C3_FAILURE_ROUTING"].get("CLOSED_LINEAGE_MATCH")),
        "rows": rows,
    }


def _l1(simple: dict[str, Any], e4: dict[str, Any], st: dict[str, Any], c1: dict[str, Any]) -> dict[str, Any]:
    sa = answers(simple["report"])
    ea = answers(e4["report"])
    sta = answers(st["report"])
    c1a = answers(c1["report"])
    simple_rank = ranking(simple["report"])
    st_rank = ranking(st["report"])
    c1_rank = ranking(c1["report"])
    simple_g1g2 = [r for r in simple_rank if g1_g2(r)]
    st_g1g2 = [r for r in st_rank if g1_g2(r)]
    c1_g1g2 = [r for r in c1_rank if g1_g2(r)]
    st_pass = [r for r in st_rank if g1_g6_pass(r)]
    c1_g1g2_fail = [
        {
            "candidate_id": r.get("candidate_id"),
            "pnl": r.get("pnl") if r.get("pnl") is not None else r.get("TOTAL_PNL"),
            "PF": r.get("PF"),
            "failed_gates": failed_gates(r),
            "positive_days": r.get("positive_days"),
            "negative_days": r.get("negative_days"),
            "EX_BEST": r.get("EX_BEST"),
            "top_symbol": r.get("top_symbol"),
        }
        for r in c1_g1g2
    ]
    e4_base = next((r for r in simple_rank if r.get("candidate_id") == "E4_X2_Z3"), {})
    l1 = {
        "LINEAGE_ID": "L1_TECHNICAL_PRICE_STATE",
        "STUDIES": [
            "SIMPLE_FULL_STRATEGY_DISCOVERY_V1",
            "E4_X2_Z3_CAUSAL_CONCENTRATION_RECHECK_V1",
            "SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1",
            "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
        ],
        "INDEPENDENT_INFORMATION_VOTE": True,
        "simple": {
            "candidate_n": sa.get("6_total_strategy_n") or len(simple_rank),
            "coverage_n": sa.get("11_candidate_coverage_pass_n") or simple["report"].get("coverage_pass_n"),
            "economic_pass_n": sa.get("12_economic_gate_pass_n") or simple["report"].get("economic_pass_n"),
            "G1_G2_N": len(simple_g1g2),
            "PRECOMMITTED_LIBRARY": False,
            "SELECTED_AFTER_ECONOMICS": True,
            "BROAD_GRID_CANDIDATE_N": 75,
            "E4_BASE_PNL": e4_base.get("pnl"),
            "E4_BASE_PF": e4_base.get("PF"),
            "E4_BASE_EX_BEST": e4_base.get("EX_BEST"),
            "E4_BASE_POS_NEG": {
                "positive": e4_base.get("positive_days"),
                "negative": e4_base.get("negative_days"),
            },
            "E4_TRADE_N": e4_base.get("TRADE_N") or e4_base.get("fill_n"),
        },
        "e4_causal": {
            "VERDICT": ea.get("36_verdict"),
            "285A_EXCLUDED_BEFORE_SIGNAL_GENERATION": True,
            "EXCLUSION_METHOD": "CAUSAL_HARVEST_ROW_EXCLUSION_THEN_FULL_CAUSAL_RERUN",
            "PRIOR_DROP_TOP_METHOD": ea.get("4_prior_DROP_TOP_method"),
            "BASE_PNL": e4_base.get("pnl"),
            "CAUSAL_EX_285A_PNL": ea.get("13_causal_EX_TOP1_PnL"),
            "CAUSAL_EX_285A_PF": ea.get("14_causal_EX_TOP1_PF"),
            "CAUSAL_EX_285A_MAXDD": ea.get("15_causal_EX_TOP1_MaxDD"),
            "CAUSAL_EX_285A_DAY_SIGNS": ea.get("16_causal_EX_TOP1_day_signs"),
            "POSTHOC_EX_TOP1_PNL": ea.get("12_posthoc_EX_TOP1_PnL"),
            "REVIVE_E4": False,
        },
        "st": {
            "VERDICT": sta.get("VERDICT") or st["report"].get("decision", {}).get("VERDICT"),
            "provisional_winner_existed": sta.get("54_provisional_winner") is not None,
            "provisional_winner": sta.get("54_provisional_winner"),
            "coverage_n": sta.get("52_coverage_PASS_n"),
            "candidate_n": 25,
            "economic_pass_n": sta.get("53_economic_PASS_n"),
            "G1_G2_N": len(st_g1g2),
            "G1_G6_PASS_N": len(st_pass),
            "BASE_PNL": sta.get("55_winner_PnL"),
            "BASE_PF": sta.get("56_winner_PF"),
            "CAUSAL_EX_TOP1": 77580.0,
            "TRAIN_TOP3_N": sta.get("57_TRAIN_TOP3_N"),
            "FOLD_SELECTED_TEST_TOTAL_PNL": sta.get("58_FOLD_SELECTED_TEST_TOTAL_PNL"),
            "STABILITY_PASS": sta.get("59_STABILITY_PASS"),
            "PRECOMMITTED_LIBRARY": True,
            "SELECTED_AFTER_ECONOMICS": False,
            "BROAD_GRID_CANDIDATE_N": 25,
            "final_stability_verdict": "SYSTEMATIC_STATE_TRANSITION_SELECTION_UNSTABLE",
        },
        "c1": {
            "VERDICT": c1a.get("VERDICT"),
            "coverage_n": c1a.get("coverage_PASS_n"),
            "candidate_n": c1a.get("FINAL_PAIR_N") or c1a.get("PAIR_RERUN_N"),
            "economic_pass_n": c1a.get("economic_PASS_n"),
            "G1_G2_N": len(c1_g1g2),
            "G1_G2_CANDIDATES": c1_g1g2_fail,
            "PRECOMMITTED_LIBRARY": True,
            "SELECTED_AFTER_ECONOMICS": False,
            "BROAD_GRID_CANDIDATE_N": 40,
        },
        "EVIDENCE_LEVEL": E1,
        "PRIMARY_FAILURE_PATTERN": "SELECTION_INSTABILITY",
        "GENERALIZATION_FAILURE": True,
        "GENERALIZATION_FAILURE_TYPES": [
            "DAY_SIGN_FAILURE",
            "BEST_DAY_DEPENDENCE",
            "SYMBOL_CONCENTRATION",
            "SELECTION_INSTABILITY",
        ],
        "taxonomy": {
            "COVERAGE_FAILURE": NOT_SUPPORTED,
            "ABSOLUTE_EDGE_FAILURE": NOT_SUPPORTED,
            "DAY_SIGN_FAILURE": SUPPORTED,
            "BEST_DAY_DEPENDENCE": SUPPORTED,
            "DRAWDOWN_DOMINANCE": SUPPORTED,
            "SYMBOL_CONCENTRATION": SUPPORTED,
            "SELECTION_INSTABILITY": SUPPORTED,
            "PORTFOLIO_INTERACTION": NOT_COMPUTED,
            "POST_FILL_VALUE_CAPTURE_EVIDENCE": NOT_SUPPORTED,
            "INTEGRITY_LIMIT": NOT_SUPPORTED,
        },
        "discovery_flags": [
            {
                "study": "SIMPLE_FULL_STRATEGY_DISCOVERY_V1",
                "PRECOMMITTED_LIBRARY": False,
                "SELECTED_AFTER_ECONOMICS": True,
                "BROAD_GRID_CANDIDATE_N": 75,
            },
            {
                "study": "SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1",
                "PRECOMMITTED_LIBRARY": True,
                "SELECTED_AFTER_ECONOMICS": False,
                "BROAD_GRID_CANDIDATE_N": 25,
            },
            {
                "study": "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
                "PRECOMMITTED_LIBRARY": True,
                "SELECTED_AFTER_ECONOMICS": False,
                "BROAD_GRID_CANDIDATE_N": 40,
            },
        ],
    }
    # Primary: ST is the only precommitted G1-G6 pass and failed blocked stability.
    # Symbol concentration (E4 causal ex-285A) and C1 G3/G4 remain supporting.
    if not (l1["st"]["provisional_winner_existed"] and l1["st"]["STABILITY_PASS"] is False):
        raise RuntimeError("L1_ST_STABILITY_PIN")
    if int(l1["c1"]["G1_G2_N"] or 0) < 1:
        raise RuntimeError("L1_C1_G1G2_PIN")
    if float(l1["e4_causal"]["CAUSAL_EX_285A_PNL"] or 0) >= 0:
        raise RuntimeError("L1_E4_CAUSAL_PIN")
    return l1


def _l2(rec: dict[str, Any]) -> dict[str, Any]:
    a = answers(rec["report"])
    rows = list(a.get("22_complete_ranking_table") or ranking(rec["report"]))
    g1g2 = [r for r in rows if g1_g2(r)]
    r2 = next((r for r in rows if r.get("candidate_id") == "R2_X1_Z3"), {})
    causal_status = [
        {
            "candidate_id": r.get("candidate_id"),
            "pnl": r.get("pnl"),
            "PF": r.get("PF"),
            "G1_G2": g1_g2(r),
            "CAUSAL_EX_TOP1": r.get("CAUSAL_EX_TOP1"),
            "CAUSAL_EX_TOP1_NEGATIVE": (_f(r.get("CAUSAL_EX_TOP1")) is not None and float(r.get("CAUSAL_EX_TOP1")) < 0),
            "positive_days": r.get("positive_days"),
            "negative_days": r.get("negative_days"),
            "EX_BEST": r.get("EX_BEST"),
            "top_symbol": r.get("top_symbol"),
        }
        for r in rows
    ]
    all_causal_neg = all(x["CAUSAL_EX_TOP1_NEGATIVE"] for x in causal_status) and len(causal_status) == 6
    if _f(r2.get("pnl")) != 341340.0:
        raise RuntimeError(f"L2_R2_PNL_PIN {r2.get('pnl')}")
    if abs(float(r2.get("PF") or 0) - 1.4202607699979068) > 1e-12:
        raise RuntimeError(f"L2_R2_PF_PIN {r2.get('PF')}")
    if int(r2.get("positive_days") or 0) != 2 or int(r2.get("negative_days") or 0) != 8:
        raise RuntimeError("L2_R2_DAY_PIN")
    if _f(r2.get("EX_BEST")) != -197910.0:
        raise RuntimeError("L2_R2_EX_BEST_PIN")
    if not all_causal_neg:
        raise RuntimeError("L2_CAUSAL_PIN")
    return {
        "LINEAGE_ID": "L2_RECOVERY_RECLAIM_PATH",
        "STUDIES": ["RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1"],
        "INDEPENDENT_INFORMATION_VOTE": True,
        "candidate_n": 6,
        "coverage_n": a.get("23_coverage_PASS_n"),
        "economic_pass_n": a.get("24_economic_PASS_n"),
        "G1_G2_N": len(g1g2),
        "PRECOMMITTED_LIBRARY": True,
        "SELECTED_AFTER_ECONOMICS": False,
        "BROAD_GRID_CANDIDATE_N": 6,
        "candidates": causal_status,
        "R2_X1_Z3": {
            "pnl": r2.get("pnl"),
            "PF": r2.get("PF"),
            "positive_days": r2.get("positive_days"),
            "negative_days": r2.get("negative_days"),
            "EX_BEST": r2.get("EX_BEST"),
            "CAUSAL_EX_TOP1": r2.get("CAUSAL_EX_TOP1"),
            "CAUSAL_EX_TOP1_PF": r2.get("CAUSAL_EX_TOP1_PF"),
        },
        "ALL_CAUSAL_EX_TOP1_NEGATIVE": True,
        "MFE_MAE": a.get("43_MFE"),
        "VERDICT": a.get("56_verdict"),
        "REVIVE_R2": False,
        "REVIVE_R4": False,
        "EVIDENCE_LEVEL": E1,
        "PRIMARY_FAILURE_PATTERN": "SYMBOL_CONCENTRATION",
        "GENERALIZATION_FAILURE": True,
        "GENERALIZATION_FAILURE_TYPES": [
            "DAY_SIGN_FAILURE",
            "BEST_DAY_DEPENDENCE",
            "SYMBOL_CONCENTRATION",
        ],
        "taxonomy": {
            "COVERAGE_FAILURE": NOT_SUPPORTED,
            "ABSOLUTE_EDGE_FAILURE": NOT_SUPPORTED,
            "DAY_SIGN_FAILURE": SUPPORTED,
            "BEST_DAY_DEPENDENCE": SUPPORTED,
            "DRAWDOWN_DOMINANCE": SUPPORTED,
            "SYMBOL_CONCENTRATION": SUPPORTED,
            "SELECTION_INSTABILITY": NOT_APPLICABLE,
            "PORTFOLIO_INTERACTION": NOT_COMPUTED,
            "POST_FILL_VALUE_CAPTURE_EVIDENCE": NOT_SUPPORTED,
            "INTEGRITY_LIMIT": NOT_SUPPORTED,
        },
    }


def _l3(part: dict[str, Any]) -> dict[str, Any]:
    a = answers(part["report"])
    gtab = dict(a.get("43_G1_G6_table") or {})
    pnl = {r["candidate_id"]: r.get("PnL") for r in list(a.get("24_each_PnL") or [])}
    pf = {r["candidate_id"]: r.get("PF") for r in list(a.get("25_each_PF") or [])}
    causal = {r["candidate_id"]: r.get("CAUSAL_EX_TOP1") for r in list(a.get("31_each_CAUSAL_EX_TOP1_PnL") or [])}
    ids = ["P1_X1_Z3", "P1_X1_ZP", "P1_X1_ZH"]
    g1g2_n = 0
    for cid in ids:
        g = dict(gtab.get(cid) or {})
        if g.get("G1_TOTAL_PNL") and g.get("G2_PF"):
            g1g2_n += 1
        elif _f(pnl.get(cid)) is not None and _f(pf.get(cid)) is not None:
            if float(pnl[cid]) > 0 and float(pf[cid]) > G2_MIN_PF:
                g1g2_n += 1
    if int(a.get("16_candidate_N") or 0) != 3:
        raise RuntimeError("L3_CANDIDATE_N_PIN")
    if int(a.get("41_coverage_PASS_N") or 0) != 3:
        raise RuntimeError("L3_COVERAGE_PIN")
    if g1g2_n != 0:
        raise RuntimeError("L3_G1G2_PIN")
    return {
        "LINEAGE_ID": "L3_ACTIVITY_ONSET",
        "STUDIES": ["PARTICIPATION_ONSET_FULL_STRATEGY_V1"],
        "INDEPENDENT_INFORMATION_VOTE": True,
        "candidate_n": 3,
        "coverage_n": a.get("41_coverage_PASS_N"),
        "economic_pass_n": a.get("42_economic_PASS_N"),
        "G1_G2_N": g1g2_n,
        "PRECOMMITTED_LIBRARY": True,
        "SELECTED_AFTER_ECONOMICS": False,
        "BROAD_GRID_CANDIDATE_N": 3,
        "candidates": [
            {
                "candidate_id": cid,
                "pnl": pnl.get(cid),
                "PF": pf.get(cid),
                "g_table": gtab.get(cid),
                "CAUSAL_EX_TOP1": causal.get(cid),
            }
            for cid in ids
        ],
        "ALL_CAUSAL_EX_TOP1_NEGATIVE": all(_f(causal.get(cid)) is not None and float(causal[cid]) < 0 for cid in ids),
        "VERDICT": a.get("52_verdict"),
        "RETUNE_VOLUME_PERCENTILE": False,
        "ADD_VWAP": False,
        "ADD_4TH_EXIT": False,
        "EVIDENCE_LEVEL": E0,
        "PRIMARY_FAILURE_PATTERN": "ABSOLUTE_EDGE_FAILURE",
        "GENERALIZATION_FAILURE": False,
        "GENERALIZATION_FAILURE_TYPES": [],
        "taxonomy": {
            "COVERAGE_FAILURE": NOT_SUPPORTED,
            "ABSOLUTE_EDGE_FAILURE": SUPPORTED,
            "DAY_SIGN_FAILURE": SUPPORTED,
            "BEST_DAY_DEPENDENCE": SUPPORTED,
            "DRAWDOWN_DOMINANCE": SUPPORTED,
            "SYMBOL_CONCENTRATION": NOT_SUPPORTED,
            "SELECTION_INSTABILITY": NOT_APPLICABLE,
            "PORTFOLIO_INTERACTION": NOT_COMPUTED,
            "POST_FILL_VALUE_CAPTURE_EVIDENCE": NOT_SUPPORTED,
            "INTEGRITY_LIMIT": NOT_SUPPORTED,
        },
    }


def _value_capture(l1: dict[str, Any], l2: dict[str, Any], l3: dict[str, Any]) -> dict[str, Any]:
    # Same ENTRY + EXIT sign reversal + MFE/MAE realizable value + not one day/symbol,
    # across >=2 independent lineages. Existing artifacts do not establish this.
    supporting = []
    reasons = {
        "L1": "EXIT variation exists inside Simple Full / C1, but that is one lineage. One EXIT beating another is not sufficient. E4 EXIT contrast is also 285A-dominated.",
        "L2": "All six candidates share Z3. No causal EXIT contrast. MFE/MAE not computed (answers 43_MFE=null).",
        "L3": "P1 x Z3/ZP/ZH are the same ENTRY with three EXITs, but all three are G1+G2 negative. No economic sign reversal.",
    }
    return {
        "POST_FILL_VALUE_CAPTURE_EVIDENCE": "NOT_ESTABLISHED",
        "SUPPORTED": False,
        "INDEPENDENT_SUPPORTING_LINEAGE_N": 0,
        "supporting_lineage_ids": supporting,
        "A_SAME_ENTRY_ACROSS_LINEAGES": NOT_SUPPORTED,
        "B_EXIT_SIGN_REVERSAL": NOT_SUPPORTED,
        "C_MFE_MAE_REALIZABLE_VALUE": NOT_COMPUTED,
        "D_NOT_ONE_DAY_SYMBOL_WINNER": NOT_SUPPORTED,
        "reasons": reasons,
        "l2_mfe_mae": l2.get("MFE_MAE"),
        "do_not_infer_from_one_exit_beating_another": True,
    }


def _decision(l1: dict[str, Any], l2: dict[str, Any], l3: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    levels = {
        "L1_TECHNICAL_PRICE_STATE": l1["EVIDENCE_LEVEL"],
        "L2_RECOVERY_RECLAIM_PATH": l2["EVIDENCE_LEVEL"],
        "L3_ACTIVITY_ONSET": l3["EVIDENCE_LEVEL"],
    }
    robust_n = sum(1 for lv in levels.values() if lv in {E2, E3})
    agg_n = sum(1 for lv in levels.values() if lv in {E1, E2, E3})
    e0_n = sum(1 for lv in levels.values() if lv == E0)
    gen_ids = [
        lid
        for lid, pack in (
            ("L1_TECHNICAL_PRICE_STATE", l1),
            ("L2_RECOVERY_RECLAIM_PATH", l2),
            ("L3_ACTIVITY_ONSET", l3),
        )
        if pack.get("GENERALIZATION_FAILURE")
    ]
    gen_n = len(gen_ids)
    overlay_not_lineage = True
    if OVERLAY_ID in levels:
        raise RuntimeError("OVERLAY_IN_LINEAGE_COUNTS")
    post_fill = value["POST_FILL_VALUE_CAPTURE_EVIDENCE"] == "SUPPORTED" and int(value["INDEPENDENT_SUPPORTING_LINEAGE_N"]) >= 2
    if robust_n == 0 and agg_n <= 1:
        case = "A"
        deficiency = "CURRENT_INFORMATION_EDGE_DEFICIT"
        verdict = CASE_A
        nxt = NEXT_IF_A
    elif robust_n == 0 and agg_n >= 2 and gen_n >= 2:
        case = "B"
        deficiency = "AGGREGATE_EDGE_GENERALIZATION_FAILURE"
        verdict = CASE_B
        nxt = NEXT_IF_B
    elif post_fill:
        case = "C"
        deficiency = "POST_FILL_VALUE_CAPTURE_DEFICIT"
        verdict = CASE_C
        nxt = NEXT_IF_C
    else:
        case = "D"
        deficiency = "INCONCLUSIVE_WITH_EXISTING_ARTIFACTS"
        verdict = CASE_D
        nxt = NEXT_IF_D
    rejected = {
        "A_CURRENT_INFORMATION_EDGE_DEFICIT": "Rejected because AGGREGATE_EDGE_LINEAGE_N=2 (L1 and L2 both E1). CASE A requires <=1.",
        "C_POST_FILL_VALUE_CAPTURE_DEFICIT": "Rejected because POST_FILL_VALUE_CAPTURE_EVIDENCE=NOT_ESTABLISHED across independent lineages. EXIT contrast inside L1 is not two-lineage evidence.",
        "D_INCONCLUSIVE": "Rejected because CASE B conditions are fully met: ROBUST_LINEAGE_N=0, AGGREGATE_EDGE_LINEAGE_N>=2, GENERALIZATION_FAILURE_LINEAGE_N>=2.",
    }
    if case == "B":
        rejected.pop("D_INCONCLUSIVE")
        rejected["D_INCONCLUSIVE"] = "Not selected. CASE B predicates hold on existing artifacts."
    evidence = (
        "L1 E1: precommitted ST library produced G1-G6 PASS then TRAIN_TOP3_N=2 and "
        "FOLD_SELECTED_TEST_TOTAL_PNL=-67720; C1 precommitted 40/40 coverage with 4 G1+G2 "
        "and 0 G1-G6 (G3/G4/G5); E4 G1+G2 flipped to causal ex-285A PnL=-380140 PF=0.5576. "
        "L2 E1: R1/R2 G1+G2 with R2 PnL=+341340 PF=1.4202607699979068, 2/8 days, "
        "EX_BEST=-197910, all six CAUSAL_EX_TOP1 negative. L3 E0: 3/3 coverage, 0 G1+G2. "
        "ROBUST_LINEAGE_N=0. Overlay C4 excluded from counts."
    )
    return {
        "CASE": case,
        "PRIMARY_DEFICIENCY": deficiency,
        "VERDICT": verdict,
        "NEXT": nxt,
        "ROBUST_LINEAGE_N": robust_n,
        "AGGREGATE_EDGE_LINEAGE_N": agg_n,
        "NO_AGGREGATE_EDGE_LINEAGE_N": e0_n,
        "GENERALIZATION_FAILURE_LINEAGE_N": gen_n,
        "GENERALIZATION_FAILURE_LINEAGE_IDS": gen_ids,
        "LINEAGE_EVIDENCE_LEVELS": levels,
        "C4_INCLUDED_IN_LINEAGE_COUNTS": False,
        "EXACT_EVIDENCE": evidence,
        "ALTERNATIVE_DEFICIENCY_REJECTED": rejected,
        "NEXT_CREATES_STRATEGY_IMMEDIATELY": False,
        "NEXT_IS_RCA_ONLY": case == "B",
        "NEXT_MAY_NOT_CREATE": [
            "market regime gate",
            "time-of-day gate",
            "symbol gate",
            "weekday gate",
            "participation threshold",
            "volatility threshold",
            "TopK",
            "ENTRY score",
        ]
        if case == "B"
        else [],
        "FIRST_RCA_OUTPUT": "WHAT DIMENSION EXPLAINS INSTABILITY?" if case == "B" else None,
        "GATES_UNCHANGED": True,
        "overlay_not_lineage": overlay_not_lineage,
    }


def decide(sources: dict[str, Any] | None = None) -> dict[str, Any]:
    pack = sources or load_sources()
    by = pack["by_id"]
    reused = already_executed_check(str(pack["inventory_fingerprint"]))
    c4 = _c4_pin(by["C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2"])
    rethink = _rethink_classes(by["NEW_ARCHITECTURE_CLASS_RETHINK_V1"])
    l1 = _l1(
        by["SIMPLE_FULL_STRATEGY_DISCOVERY_V1"],
        by["E4_X2_Z3_CAUSAL_CONCENTRATION_RECHECK_V1"],
        by["SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1"],
        by["C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"],
    )
    l2 = _l2(by["RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1"])
    l3 = _l3(by["PARTICIPATION_ONSET_FULL_STRATEGY_V1"])
    value = _value_capture(l1, l2, l3)
    decision = _decision(l1, l2, l3, value)
    c1_closed = str(by["C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"]["verdict"]) == (
        "C1_MULTI_TIMEFRAME_ENTRY_EXIT_NO_ROBUST_CANDIDATE"
    )
    c4_closed = c4["VERDICT"] == C4_VERDICT_REQUIRED
    architecture_open = {
        "C1_OPEN": False,
        "C2_OPEN": False,
        "C3_OPEN": False,
        "C4_OPEN": False,
        "C5_CREATED": C5_CREATED,
        "C1_FINAL": "C1_MULTI_TIMEFRAME_ENTRY_EXIT_NO_ROBUST_CANDIDATE",
        "C4_FINAL": C4_VERDICT_REQUIRED,
        "C1_C4_ALL_CLOSED": bool(c1_closed and c4_closed and rethink["C2_CLOSED_INELIGIBLE"] and rethink["C3_CLOSED_INELIGIBLE"]),
    }
    if not architecture_open["C1_C4_ALL_CLOSED"]:
        raise RuntimeError("ARCHITECTURE_SPACE_NOT_CLOSED")
    if INDEPENDENT_INFORMATION_LINEAGE_N != 3:
        raise RuntimeError("LINEAGE_N")
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ALREADY_EXECUTED": reused,
        "inventory_fingerprint": pack["inventory_fingerprint"],
        "source_inventory": [
            {
                "ANALYSIS_ID": r["ANALYSIS_ID"],
                "dirname": r["dirname"],
                "role": r["role"],
                "lineage": r["lineage"],
                "overlay": r["overlay"],
                "path": r["path"],
                "report_sha256": r["report_sha256"],
                "verdict": r["verdict"],
            }
            for r in pack["items"]
        ],
        "architecture_to_lineage": pack["architecture_to_lineage"],
        "lineage_dependencies": [
            {
                "CHILD": "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
                "PARENT_INFORMATION": "SYSTEMATIC_STATE_TRANSITION / SIMPLE_TECH states",
                "RELATION": "architecture variant inside L1",
                "INDEPENDENT_VOTE": False,
            },
            {
                "CHILD": "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2",
                "PARENT_INFORMATION": "ST_PERSIST streams",
                "RELATION": "admission overlay O1, not alpha lineage",
                "INDEPENDENT_VOTE": False,
            },
            {
                "CHILD": "SIMPLE_FULL_STRATEGY_DISCOVERY_V1",
                "PARENT_INFORMATION": "MA/BB/RCI/volume/VWAP 1m states",
                "RELATION": "L1 technical price state",
                "INDEPENDENT_VOTE": False,
            },
        ],
        "architecture_space": architecture_open,
        "rethink": rethink,
        "c4": c4,
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "value_capture": value,
        "decision": decision,
        "pseudo_replication_guard": {
            "PASS": True,
            "CANDIDATE_COUNT_WEIGHTED_VOTE": CANDIDATE_COUNT_WEIGHTED_VOTE,
            "ANY_CANDIDATE_FAMILY_VOTE": ANY_CANDIDATE_FAMILY_VOTE,
            "FORBIDDEN_CONCLUSION": "ST positive + C1 positive + C4 positive => three independent confirmations",
            "WHY_FORBIDDEN": "C1 and C4 inherit ST/Simple technical information. C4 is overlay. Simple 75-grid is not an equal-weight family vote.",
            "INDEPENDENT_INFORMATION_LINEAGE_N": INDEPENDENT_INFORMATION_LINEAGE_N,
            "EXACT_LINEAGE_IDS": list(LINEAGE_IDS),
            "C4_COUNTED_AS_ALPHA_LINEAGE": False,
        },
        "closed_lineage_guard": {
            "REVIVE_SIMPLE_FULL": False,
            "REVIVE_E4": False,
            "REVIVE_RECOVERY": False,
            "REVIVE_PARTICIPATION_ONSET": False,
            "REVIVE_STATE_TRANSITION": False,
            "REVIVE_C1": False,
            "REVIVE_C4": False,
            "REVIVE_PFQ": False,
            "REVIVE_OR": False,
            "REVIVE_DYNAMIC_ANCHOR": False,
            "REVIVE_X9": False,
            "E1_X9_CLOSED": "NO_STABLE_UNIVERSE_REGIME_SEPARATION",
            "EXIT6_CREATED": False,
            "HIGH_LOW_ACTIVITY_UNIVERSE_SPLIT_IS_NOT_NEW_ARCHITECTURE": True,
        },
        "flags": {
            "NEW_REPLAY": NEW_REPLAY,
            "NEW_CANDIDATE": NEW_CANDIDATE,
            "NEW_ENTRY": NEW_ENTRY,
            "NEW_EXIT": NEW_EXIT,
            "NEW_THRESHOLD": NEW_THRESHOLD,
            "NEW_PNL_SIMULATION": NEW_PNL_SIMULATION,
            "NEW_FULL_CAUSAL_RUN": NEW_FULL_CAUSAL_RUN,
            "NEW_FOLD_RUN": NEW_FOLD_RUN,
            "ENTRY_RETUNE_PROPOSED": False,
            "EXIT_RETUNE_PROPOSED": False,
            "REGIME_FILTER_PROPOSED": False,
            "GATE_RELAXATION_PROPOSED": False,
            "SIZING_PROPOSED": False,
        },
        "spec": canonical_spec(),
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    c4 = dict(pack.get("c4") or {})
    l1 = dict(pack.get("l1") or {})
    l2 = dict(pack.get("l2") or {})
    l3 = dict(pack.get("l3") or {})
    d = dict(pack.get("decision") or {})
    v = dict(pack.get("value_capture") or {})
    arch = dict(pack.get("architecture_space") or {})
    guard = dict(pack.get("pseudo_replication_guard") or {})
    flags = dict(pack.get("flags") or {})
    reused = dict(pack.get("ALREADY_EXECUTED") or {})
    r2 = dict(l2.get("R2_X1_Z3") or {})
    return {
        "ALREADY_EXECUTED_CHECK": reused.get("ALREADY_EXECUTED_CHECK"),
        "REUSED_EXISTING_RESULT": reused.get("REUSED_EXISTING_RESULT"),
        "1_current_C4_verdict": c4.get("VERDICT"),
        "2_C4_primary_closure_reason": c4.get("C4_PRIMARY_CLOSE_REASON"),
        "3_C1_C4_all_closed": arch.get("C1_C4_ALL_CLOSED"),
        "4_C5_created": False,
        "5_architecture_studies_reviewed_N": len(pack.get("source_inventory") or []),
        "6_independent_information_lineage_N": INDEPENDENT_INFORMATION_LINEAGE_N,
        "7_exact_lineage_IDs": list(LINEAGE_IDS),
        "8_C4_counted_as_alpha_lineage": False,
        "9_L1_evidence_level": l1.get("EVIDENCE_LEVEL"),
        "10_L1_aggregate_edge_evidence": {
            "simple_G1_G2_N": (l1.get("simple") or {}).get("G1_G2_N"),
            "st_economic_PASS_N": (l1.get("st") or {}).get("economic_pass_n"),
            "st_G1_G6_PASS_N": (l1.get("st") or {}).get("G1_G6_PASS_N"),
            "c1_G1_G2_N": (l1.get("c1") or {}).get("G1_G2_N"),
            "e4_base_pnl": (l1.get("e4_causal") or {}).get("BASE_PNL"),
            "e4_causal_ex_285A_pnl": (l1.get("e4_causal") or {}).get("CAUSAL_EX_285A_PNL"),
        },
        "11_L1_main_robustness_failures": l1.get("GENERALIZATION_FAILURE_TYPES"),
        "12_L2_evidence_level": l2.get("EVIDENCE_LEVEL"),
        "13_R2_PnL_PF": {"PnL": r2.get("pnl"), "PF": r2.get("PF")},
        "14_R2_positive_negative_days": {
            "positive": r2.get("positive_days"),
            "negative": r2.get("negative_days"),
        },
        "15_R2_EX_BEST": r2.get("EX_BEST"),
        "16_L2_causal_ex_top_status": "ALL_SIX_CAUSAL_EX_TOP1_NEGATIVE",
        "17_L2_main_failure": l2.get("PRIMARY_FAILURE_PATTERN"),
        "18_L3_evidence_level": l3.get("EVIDENCE_LEVEL"),
        "19_L3_coverage_N": l3.get("coverage_n"),
        "20_L3_aggregate_edge_status": "NO_G1_G2",
        "21_L3_main_failure": l3.get("PRIMARY_FAILURE_PATTERN"),
        "22_C4_overlay_rescue_supported": False,
        "23_robust_lineage_N": d.get("ROBUST_LINEAGE_N"),
        "24_aggregate_edge_lineage_N": d.get("AGGREGATE_EDGE_LINEAGE_N"),
        "25_no_aggregate_edge_lineage_N": d.get("NO_AGGREGATE_EDGE_LINEAGE_N"),
        "26_generalization_failure_lineage_N": d.get("GENERALIZATION_FAILURE_LINEAGE_N"),
        "27_post_fill_value_capture_supported": False,
        "28_if_yes_independent_supporting_lineage_N": v.get("INDEPENDENT_SUPPORTING_LINEAGE_N"),
        "29_pseudo_replication_guard_pass": guard.get("PASS"),
        "30_candidate_count_weighted_vote_used": False,
        "31_PRIMARY_DEFICIENCY": d.get("PRIMARY_DEFICIENCY"),
        "32_exact_evidence_supporting_deficiency": d.get("EXACT_EVIDENCE"),
        "33_alternative_deficiency_rejected_and_why": d.get("ALTERNATIVE_DEFICIENCY_REJECTED"),
        "34_next_objective": d.get("NEXT"),
        "35_next_objective_creates_strategy_immediately": False,
        "36_ENTRY_retune_proposed": flags.get("ENTRY_RETUNE_PROPOSED"),
        "37_EXIT_retune_proposed": flags.get("EXIT_RETUNE_PROPOSED"),
        "38_regime_filter_proposed": flags.get("REGIME_FILTER_PROPOSED"),
        "39_gate_relaxation_proposed": flags.get("GATE_RELAXATION_PROPOSED"),
        "40_Sizing_proposed": flags.get("SIZING_PROPOSED"),
        "41_new_economics_run": False,
        "42_new_replay_run": False,
        "43_Holdout_read": False,
        "44_Stress_read": False,
        "45_future_used": False,
        "46_Runtime_changed": False,
        "47_submit_cancel_live": "0/0/0",
        "48_TRUE_OOS": False,
        "49_CERTIFIED": False,
        "50_VERDICT": d.get("VERDICT"),
        "51_NEXT": d.get("NEXT"),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "C4_I1_PASS_N": c4.get("I1_PASS_N"),
        "C4_I2_PASS_N": c4.get("I2_PASS_N"),
        "C4_SUPPORTED_PASS_N": c4.get("C4_INCREMENTAL_SUPPORT_N"),
        "NEW_REPLAY": NEW_REPLAY,
        "NEW_CANDIDATE": NEW_CANDIDATE,
        "NEW_ENTRY": NEW_ENTRY,
        "NEW_EXIT": NEW_EXIT,
        "NEW_THRESHOLD": NEW_THRESHOLD,
        "NEW_PNL_SIMULATION": NEW_PNL_SIMULATION,
        "NEW_FULL_CAUSAL_RUN": NEW_FULL_CAUSAL_RUN,
        "NEW_FOLD_RUN": NEW_FOLD_RUN,
    }
