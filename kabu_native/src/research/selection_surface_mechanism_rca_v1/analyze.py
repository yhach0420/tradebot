"""Four-gate ST stability RCA from persisted artifacts. No new fold economics."""
from __future__ import annotations

import json
import math
from typing import Any

from research.selection_surface_mechanism_rca_v1 import (
    ANALYSIS_ID,
    B1_PNL,
    B2_PNL,
    B3_PNL,
    B4_PNL,
    B5_PNL,
    CROSS_LINEAGE_SELECTION,
    FAILING_S1,
    FAILING_S4,
    L1_WIDE,
    N3_UNKNOWN,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_FOLD_ECONOMICS,
    NEW_FOLD_RUN,
    NEW_REPLAY,
    NEXT_BOTH,
    NEXT_ELIGIBILITY,
    NEXT_EVIDENCE_GAP,
    NEXT_RANK,
    PARENT_ANALYSIS_ID,
    PARENT_NEXT_REQUIRED,
    PARENT_PRIMARY_COMPONENT,
    PARENT_RESIDUAL_BOTTLENECK,
    PARENT_VERDICT_REQUIRED,
    RAW_CAPTURE_READ_N,
    RECURRENCE_INCONCLUSIVE,
    ST_BASE_PF,
    ST_BASE_PNL,
    ST_FOLD_SELECTED_TEST_TOTAL_PNL,
    ST_SPECIFIC,
    ST_STUDY_ID,
    ST_TRADE_N,
    ST_TRAIN_TOP3_N,
    ST_WINNER_ID,
    VERDICT_INCONCLUSIVE,
)
from research.selection_surface_mechanism_rca_v1.isolation import OUT
from research.selection_surface_mechanism_rca_v1.sources import answers, load_sources
from research.selection_surface_mechanism_rca_v1.spec import canonical_spec, spec_sha256
from research.simple_full_strategy_discovery_v1 import MAX_RESEARCH_DATE

NOT_COMPUTED = "NOT_COMPUTED"
NOT_APPLICABLE = "NOT_APPLICABLE"
BLOCK_IDS = ("B1", "B2", "B3", "B4", "B5")
BLOCK_PNL_PIN = {"B1": B1_PNL, "B2": B2_PNL, "B3": B3_PNL, "B4": B4_PNL, "B5": B5_PNL}


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


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pin_parent(parent: dict[str, Any]) -> dict[str, Any]:
    report = parent["report"]
    a = answers(report)
    d = dict(report.get("decision") or {})
    pin = {
        "ANALYSIS_ID": report.get("ANALYSIS_ID"),
        "VERDICT": a.get("VERDICT") or d.get("VERDICT"),
        "NEXT": a.get("NEXT") or d.get("NEXT"),
        "PRIMARY_COMPONENT": a.get("43_PRIMARY_COMPONENT") or d.get("PRIMARY_COMPONENT"),
        "RESIDUAL_BOTTLENECK": a.get("44_RESIDUAL_BOTTLENECK") or d.get("RESIDUAL_BOTTLENECK"),
        "ST_SPECIFIC_SELECTION_INSTABILITY_SUPPORTED": d.get("ST_SPECIFIC_SELECTION_INSTABILITY_SUPPORTED"),
        "L1_WIDE_SELECTION_INSTABILITY_PROVEN": d.get("L1_WIDE_SELECTION_INSTABILITY_PROVEN"),
        "L2_SELECTION_INSTABILITY_PROVEN": d.get("L2_SELECTION_INSTABILITY_PROVEN"),
    }
    if pin["ANALYSIS_ID"] != PARENT_ANALYSIS_ID:
        raise RuntimeError("PARENT_ANALYSIS_ID")
    if pin["VERDICT"] != PARENT_VERDICT_REQUIRED:
        raise RuntimeError(f"PARENT_VERDICT {pin['VERDICT']}")
    if pin["NEXT"] != PARENT_NEXT_REQUIRED:
        raise RuntimeError("PARENT_NEXT")
    if pin["PRIMARY_COMPONENT"] != PARENT_PRIMARY_COMPONENT:
        raise RuntimeError("PARENT_PRIMARY")
    if pin["RESIDUAL_BOTTLENECK"] != PARENT_RESIDUAL_BOTTLENECK:
        raise RuntimeError("PARENT_RESIDUAL")
    return pin


def _winner_blocks(st_rep: dict[str, Any]) -> dict[str, dict[str, Any]]:
    blocks = list((st_rep.get("winner_blocks") or {}).get("blocks") or [])
    by = {str(b.get("block")): b for b in blocks}
    if set(by) != set(BLOCK_IDS):
        raise RuntimeError(f"WINNER_BLOCKS {sorted(by)}")
    for bid, expected in BLOCK_PNL_PIN.items():
        got = _f(by[bid].get("pnl"))
        if got is None or abs(got - expected) > 1e-9:
            raise RuntimeError(f"BLOCK_PNL_PIN {bid} {got}")
    return by


def _fold_field_available(fold: dict[str, Any], *keys: str) -> bool:
    for k in keys:
        if fold.get(k) not in (None, "", [], {}):
            return True
    return False


def _normalize_folds(st_rep: dict[str, Any], winner_blocks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw = list((st_rep.get("stability") or {}).get("folds") or [])
    if [str(f.get("block")) for f in raw] != list(BLOCK_IDS):
        raise RuntimeError("FOLD_ORDER")
    out = []
    g6_mis = 0
    for f in raw:
        bid = str(f.get("block"))
        pass_n = f.get("pass_n")
        eligible_n = int(pass_n) if pass_n is not None else None
        win = f.get("winner")
        exists = bool(win)
        top3 = list(f.get("top3") or [])
        raw_pnl = f.get("test_pnl")
        test_pnl = None if raw_pnl is None else _f(raw_pnl)
        test_ran = bool(exists)
        if (not exists) and test_pnl is not None:
            raise RuntimeError(f"NO_WINNER_TEST_PNL_PRESENT {bid}")
        if exists and test_pnl is None:
            raise RuntimeError(f"WINNER_WITHOUT_TEST_PNL {bid}")
        g6_avail = _fold_field_available(
            f, "G6", "G6_CAUSAL_EX_TOP1", "CAUSAL_EX_TOP1", "CAUSAL_EX_TOP1_PNL", "g_table"
        )
        rank_avail = _fold_field_available(f, "FULL_WINNER_TRAIN_RANK", "TRAIN_RANK", "rank", "ranks")
        score_avail = _fold_field_available(f, "ROBUST_SCORE", "score", "FULL_WINNER_TRAIN_SCORE")
        eligible_saved = f.get("FULL_WINNER_TRAIN_ELIGIBLE")
        if eligible_saved is None:
            eligible_saved = f.get("full_winner_train_eligible")
        if (not g6_avail) and eligible_saved is False:
            g6_mis += 1
        rec = {
            "block": bid,
            "test_days": list(f.get("test_days") or []),
            "train_days": list(f.get("train_days") or []),
            "TRAIN_FINAL_ELIGIBLE_N": eligible_n,
            "SELECTED_WINNER_EXISTS": exists,
            "SELECTED_WINNER_ID": str(win) if exists else None,
            "top3": top3,
            "FULL_DEV_WINNER_IN_TOP3": bool(ST_WINNER_ID in top3),
            "TEST_EVALUATION_RAN": test_ran,
            "TEST_PNL": test_pnl,
            "TEST_PF": None,
            "TEST_PF_STATUS": NOT_COMPUTED,
            "TEST_TRADE_N": f.get("test_trade_n") if exists else None,
            "FULL_DEV_WINNER_BLOCK_PNL": _f(winner_blocks[bid].get("pnl")),
            "FULL_WINNER_TRAIN_ELIGIBLE": eligible_saved,
            "FULL_WINNER_TRAIN_RANK": f.get("FULL_WINNER_TRAIN_RANK")
            if f.get("FULL_WINNER_TRAIN_RANK") is not None
            else f.get("TRAIN_RANK"),
            "G6_AVAILABLE": g6_avail,
            "G6_VALUE": None,
            "G6_RAN": False,
            "G6_STATUS": NOT_COMPUTED,
            "RANK_AVAILABLE": rank_avail,
            "RANK_VALUE": None,
            "RANK_STATUS": NOT_COMPUTED,
            "ROBUST_SCORE_AVAILABLE": score_avail,
            "ROBUST_SCORE_VALUE": None,
            "ROBUST_SCORE_STATUS": NOT_COMPUTED,
            "GATE_MARGIN": {
                "PF_MINUS_1_10": NOT_COMPUTED,
                "positive_days_minus_negative_days": NOT_COMPUTED,
                "EX_BEST": NOT_COMPUTED,
                "TOTAL_PNL_PLUS_MAXDD": NOT_COMPUTED,
                "CAUSAL_EX_TOP1_PNL": NOT_COMPUTED,
            },
            "SOURCE_KEYS": sorted(f.keys()),
        }
        if not test_ran:
            rec["COMPARATOR_STATUS"] = NOT_APPLICABLE
            rec["FOLD_SELECTED_TEST_PNL"] = None
        else:
            rec["COMPARATOR_STATUS"] = "COMPARED"
            rec["FOLD_SELECTED_TEST_PNL"] = test_pnl
        out.append(rec)
    if g6_mis != 0:
        raise RuntimeError(f"FOLD_G6_NOT_RUN_MISCLASSIFIED_N {g6_mis}")
    return out


def _classify_non_top3(fold: dict[str, Any]) -> str:
    eligible = fold.get("FULL_WINNER_TRAIN_ELIGIBLE")
    rank = fold.get("FULL_WINNER_TRAIN_RANK")
    if eligible is False and fold.get("G6_AVAILABLE"):
        return "N1_INELIGIBLE"
    if eligible is True and rank is not None:
        try:
            if int(rank) > 3:
                return "N2_ELIGIBLE_BUT_RANK_DISPLACED"
        except (TypeError, ValueError):
            return N3_UNKNOWN
    return N3_UNKNOWN


def _s2_s3(winner_blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pnls = {bid: float(BLOCK_PNL_PIN[bid]) for bid in BLOCK_IDS}
    pos = sum(1 for v in pnls.values() if v > 0)
    best_id = max(pnls, key=lambda k: pnls[k])
    ex_best = sum(v for k, v in pnls.items() if k != best_id)
    return {
        "FULLDEV_WINNER_POSITIVE_BLOCK_N": pos,
        "FULLDEV_WINNER_POSITIVE_BLOCK_IDS": [bid for bid, v in pnls.items() if v > 0],
        "FULLDEV_WINNER_BEST_BLOCK_ID": best_id,
        "FULLDEV_WINNER_BEST_BLOCK_PNL": pnls[best_id],
        "FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL": ex_best,
        "S2_PASS": pos >= 3,
        "S3_PASS": ex_best >= 0.0,
        "FIXED_WINNER_BLOCK_ROBUSTNESS_PASS": bool(pos >= 3 and ex_best >= 0.0),
        "winner_block_pnls": pnls,
    }


def _heldout_aggregate(folds: list[dict[str, Any]], stored_total: Any) -> dict[str, Any]:
    contrib = [f for f in folds if f.get("TEST_PNL") is not None]
    no_win = [f for f in folds if not f.get("SELECTED_WINNER_EXISTS")]
    recon = float(sum(float(f["TEST_PNL"]) for f in contrib)) if contrib else None
    stored = _f(stored_total)
    proven = recon is not None and stored is not None and math.isclose(recon, stored, rel_tol=0.0, abs_tol=1e-6)
    zeroed = any(f.get("TEST_PNL") == 0 for f in no_win)
    semantics = (
        "SUM_OF_TEST_PNL_WHERE_SELECTED_WINNER_EXISTS; "
        "NO_WINNER_FOLDS_EXCLUDED_TEST_PNL_NULL_NOT_ZERO"
    )
    status = "PROVEN" if proven and not zeroed else "NOT_FULLY_RESOLVED"
    return {
        "FOLD_SELECTED_TEST_TOTAL_PNL": stored,
        "RECONSTRUCTED_SUM": recon,
        "FOLD_SELECTED_TOTAL_SOURCE_COMPONENTS": [
            {"block": f["block"], "TEST_PNL": f["TEST_PNL"], "SELECTED_WINNER_ID": f["SELECTED_WINNER_ID"]}
            for f in contrib
        ],
        "FOLD_SELECTED_TOTAL_SOURCE_SEMANTICS": semantics,
        "SOURCE_CONTRIBUTING_FOLD_IDS": [f["block"] for f in contrib],
        "NO_WINNER_TREATED_AS_ZERO_BY_SOURCE": False,
        "RECONSTRUCTED_FROM_EXISTING_SOURCE_ONLY": True,
        "NEW_FOLD_ECONOMICS": False,
        "HELDOUT_TRANSFER_STATUS": status,
        "AGGREGATE_MATCH": proven,
    }


def decide(sources: dict[str, Any] | None = None) -> dict[str, Any]:
    pack = sources or load_sources()
    reused = already_executed_check(str(pack["inventory_fingerprint"]))
    parent = _pin_parent(pack["by_id"][PARENT_ANALYSIS_ID])
    st_rep = pack["by_id"][ST_STUDY_ID]["report"]
    stab = dict(st_rep.get("stability") or {})
    a = answers(st_rep)
    winner_blocks = _winner_blocks(st_rep)
    folds = _normalize_folds(st_rep, winner_blocks)
    s23 = _s2_s3(winner_blocks)
    top3_n = stab.get("TRAIN_TOP3_N")
    if top3_n is None:
        top3_n = a.get("57_TRAIN_TOP3_N")
    if int(top3_n) != ST_TRAIN_TOP3_N:
        raise RuntimeError(f"TRAIN_TOP3_N {top3_n}")
    stored_total = stab.get("FOLD_SELECTED_TEST_TOTAL_PNL")
    if stored_total is None:
        stored_total = a.get("58_FOLD_SELECTED_TEST_TOTAL_PNL")
    if _f(stored_total) != ST_FOLD_SELECTED_TEST_TOTAL_PNL:
        raise RuntimeError("FOLD_SELECTED_TEST_TOTAL_PNL")
    agg = _heldout_aggregate(folds, stored_total)
    s1_pass = int(top3_n) >= 3
    s4_pass = float(ST_FOLD_SELECTED_TEST_TOTAL_PNL) > 0.0
    failing = []
    if not s1_pass:
        failing.append(FAILING_S1)
    if not s23["S2_PASS"]:
        failing.append("S2_FULLDEV_WINNER_POSITIVE_BLOCK")
    if not s23["S3_PASS"]:
        failing.append("S3_FULLDEV_WINNER_EX_BEST_BLOCK")
    if not s4_pass:
        failing.append(FAILING_S4)
    if failing != [FAILING_S1, FAILING_S4]:
        raise RuntimeError(f"FAILING_IDS {failing}")
    no_win = [f for f in folds if not f["SELECTED_WINNER_EXISTS"]]
    tested = [f for f in folds if f["TEST_EVALUATION_RAN"]]
    not_top3 = [f for f in folds if not f["FULL_DEV_WINNER_IN_TOP3"]]
    if len(not_top3) != 3:
        raise RuntimeError(f"NOT_TOP3_N {len(not_top3)}")
    n_labels = {f["block"]: _classify_non_top3(f) for f in not_top3}
    n1 = sum(1 for v in n_labels.values() if v == "N1_INELIGIBLE")
    n2 = sum(1 for v in n_labels.values() if v == "N2_ELIGIBLE_BUT_RANK_DISPLACED")
    n3 = sum(1 for v in n_labels.values() if v == N3_UNKNOWN)
    if n1 + n2 + n3 != 3:
        raise RuntimeError("N_SUM")
    if n3 > 0:
        rec_mode = RECURRENCE_INCONCLUSIVE
        rec_outcome = RECURRENCE_INCONCLUSIVE
        verdict = VERDICT_INCONCLUSIVE
        nxt = NEXT_EVIDENCE_GAP
    elif n1 > 0 and n2 > 0:
        rec_mode = "MIXED_ELIGIBILITY_AND_RANK_INSTABILITY"
        rec_outcome = rec_mode
        verdict = "SELECTION_SURFACE_MECHANISM_MIXED"
        nxt = NEXT_BOTH
    elif n1 > 0:
        rec_mode = "ELIGIBILITY_GATE_INSTABILITY"
        rec_outcome = rec_mode
        verdict = "SELECTION_SURFACE_MECHANISM_ELIGIBILITY"
        nxt = NEXT_ELIGIBILITY
    elif n2 > 0:
        rec_mode = "RANK_SURFACE_INSTABILITY"
        rec_outcome = rec_mode
        verdict = "SELECTION_SURFACE_MECHANISM_RANK"
        nxt = NEXT_RANK
    else:
        rec_mode = RECURRENCE_INCONCLUSIVE
        rec_outcome = RECURRENCE_INCONCLUSIVE
        verdict = VERDICT_INCONCLUSIVE
        nxt = NEXT_EVIDENCE_GAP
    heldout_supported = bool(
        agg["HELDOUT_TRANSFER_STATUS"] == "PROVEN"
        and len(tested) >= 1
        and float(ST_FOLD_SELECTED_TEST_TOTAL_PNL) <= 0.0
    )
    g6_avail = {f["block"]: f["G6_AVAILABLE"] for f in folds}
    rank_avail = {f["block"]: f["RANK_AVAILABLE"] for f in folds}
    score_avail = {f["block"]: f["ROBUST_SCORE_AVAILABLE"] for f in folds}
    if any(g6_avail.values()) or any(rank_avail.values()) or any(score_avail.values()):
        raise RuntimeError("UNEXPECTED_FOLD_METRIC")
    winner_row = dict(st_rep.get("winner") or {})
    if int(winner_row.get("TRADE_N") or 0) != ST_TRADE_N:
        raise RuntimeError("ST_TRADE_N")
    if _f(winner_row.get("TOTAL_PNL")) != ST_BASE_PNL:
        raise RuntimeError("ST_BASE_PNL")
    if abs(float(winner_row.get("PF")) - ST_BASE_PF) > 1e-12:
        raise RuntimeError("ST_BASE_PF")
    decision = {
        "CASE": "INCONCLUSIVE" if verdict == VERDICT_INCONCLUSIVE else "RESOLVED",
        "STABILITY_COMPONENT_N": 4,
        "S1_PASS": s1_pass,
        "S2_PASS": s23["S2_PASS"],
        "S3_PASS": s23["S3_PASS"],
        "S4_PASS": s4_pass,
        "FIXED_WINNER_BLOCK_ROBUSTNESS_PASS": s23["FIXED_WINNER_BLOCK_ROBUSTNESS_PASS"],
        "FAILING_STABILITY_COMPONENT_IDS": failing,
        "BOTH_IDENTIFIED_FAILING_COMPONENTS_CONFIRMED": True,
        "ALL_STABILITY_COMPONENTS_FAIL": False,
        "BOTH_FORMAL_STABILITY_COMPONENTS_FAIL": False,
        "RECURRENCE_FAILURE_MODE": rec_mode,
        "PRIMARY_RCA_OUTCOME": rec_outcome,
        "HELDOUT_TRANSFER_FAILURE_SUPPORTED": heldout_supported,
        "HELDOUT_TRANSFER_STATUS": agg["HELDOUT_TRANSFER_STATUS"],
        "FORCED_INTO_ONE_CAUSE": False,
        "ST_SPECIFIC": ST_SPECIFIC,
        "L1_WIDE": L1_WIDE,
        "CROSS_LINEAGE": CROSS_LINEAGE_SELECTION,
        "PARENT_PRIMARY_COMPONENT_UNCHANGED": PARENT_PRIMARY_COMPONENT,
        "RESIDUAL_BOTTLENECK_SUBJECT": PARENT_RESIDUAL_BOTTLENECK,
        "VERDICT": verdict,
        "NEXT": nxt,
        "NEXT_SCOPE": "SELECTION_SURFACE_EVIDENCE_GAP: persist fold-level FULL_WINNER eligibility, gate states, rank, G6, robust score",
        "NEXT_QUESTION": "WHY DID THE FULL-DEV WINNER APPEAR IN TRAIN TOP3 ONLY 2/5?",
        "NEXT_NOT_QUESTION": "WHY DOES ALL L1 FAIL?",
        "NEXT_CREATES_STRATEGY": False,
        "EXACT_EVIDENCE": (
            f"S1 TRAIN_TOP3_N={top3_n} FAIL; "
            f"S2 FULLDEV_WINNER_POSITIVE_BLOCK_N={s23['FULLDEV_WINNER_POSITIVE_BLOCK_N']} PASS; "
            f"S3 EX_BEST_BLOCK={s23['FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL']} PASS; "
            f"S4 FOLD_SELECTED_TEST_TOTAL_PNL={ST_FOLD_SELECTED_TEST_TOTAL_PNL} FAIL. "
            f"No-winner folds {[f['block'] for f in no_win]} keep TEST_PNL=null. "
            f"Aggregate -67720 reconstructed from {agg['SOURCE_CONTRIBUTING_FOLD_IDS']} only. "
            f"Non-top3 folds {[f['block'] for f in not_top3]} classified {n_labels}; "
            f"N1={n1} N2={n2} N3={n3}. Fold G6/rank/robust-score not persisted. "
            "Recurrence inconclusive; held-out transfer failure supported separately. ST-specific only."
        ),
        "FORBIDDEN_CLAIMS": [
            "ALL_STABILITY_COMPONENTS_FAIL",
            "BOTH_FORMAL_STABILITY_COMPONENTS_FAIL",
            "all technical strategies suffer selection instability",
            "L1-wide selection instability",
            "cross-lineage selection instability",
        ],
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ALREADY_EXECUTED": reused,
        "inventory_fingerprint": pack["inventory_fingerprint"],
        "parent": parent,
        "source_inventory": [
            {k: r[k] for k in ("ANALYSIS_ID", "dirname", "role", "path", "report_sha256")} for r in pack["items"]
        ],
        "winner": {
            "ST_WINNER_ID": ST_WINNER_ID,
            "trade_n": ST_TRADE_N,
            "BASE_PNL": ST_BASE_PNL,
            "BASE_PF": ST_BASE_PF,
            "G1_G6_ALL_PASS": True,
        },
        "s2_s3": s23,
        "folds": folds,
        "n_labels": n_labels,
        "n_counts": {"N1_N": n1, "N2_N": n2, "N3_N": n3},
        "stability_gates": {
            "S1_TRAIN_TOP3_N": int(top3_n),
            "S1_PASS": s1_pass,
            "S2_FULLDEV_WINNER_POSITIVE_BLOCK_N": s23["FULLDEV_WINNER_POSITIVE_BLOCK_N"],
            "S2_PASS": s23["S2_PASS"],
            "S3_FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL": s23["FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL"],
            "S3_PASS": s23["S3_PASS"],
            "S4_FOLD_SELECTED_TEST_TOTAL_PNL": ST_FOLD_SELECTED_TEST_TOTAL_PNL,
            "S4_PASS": s4_pass,
            "ORIGINAL_ST_STORED_POSITIVE_BLOCKS_FOLD_SELECTED": stab.get("POSITIVE_BLOCKS"),
            "ORIGINAL_ST_STORED_EX_BEST_BLOCK_TOTAL_PNL_FOLD_SELECTED": stab.get("EX_BEST_BLOCK_TOTAL_PNL"),
            "ORIGINAL_ST_STORED_FIELDS_ARE_NOT_S2_S3": True,
        },
        "aggregate": agg,
        "NO_WINNER_FOLD_N": len(no_win),
        "NO_WINNER_FOLD_IDS": [f["block"] for f in no_win],
        "TEST_EVALUATED_FOLD_N": len(tested),
        "NOT_TOP3_FOLD_IDS": [f["block"] for f in not_top3],
        "FOLD_G6_AVAILABILITY": g6_avail,
        "FOLD_RANK_AVAILABILITY": rank_avail,
        "FOLD_ROBUST_SCORE_AVAILABILITY": score_avail,
        "FOLD_G6_NOT_RUN_MISCLASSIFIED_N": 0,
        "decision": decision,
        "guards": {
            "NEW_REPLAY": NEW_REPLAY,
            "NEW_FOLD_RUN": NEW_FOLD_RUN,
            "NEW_FOLD_ECONOMICS": NEW_FOLD_ECONOMICS,
            "NEW_CANDIDATE": NEW_CANDIDATE,
            "NEW_ENTRY": NEW_ENTRY,
            "NEW_EXIT": NEW_EXIT,
            "ENTRY_RETUNE": False,
            "EXIT_RETUNE": False,
            "ST_RESCUE": False,
            "ENSEMBLE": False,
            "TOPK_CHANGE": False,
            "ROBUST_SCORE_CHANGE": False,
            "GATE_RELAXATION": False,
            "SYMBOL_FILTER_CREATED": False,
            "DATE_FILTER_CREATED": False,
            "REGIME_GATE_CREATED": False,
            "SIZING": False,
            "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
            "ST_SPECIFIC": ST_SPECIFIC,
            "L1_WIDE": L1_WIDE,
            "CROSS_LINEAGE_SELECTION": CROSS_LINEAGE_SELECTION,
            "RETROSPECTIVE_RCA_ONLY": True,
            "PROSPECTIVE_BENCHMARK": False,
            "MISSING_FIELD_RECONSTRUCTED_BY_NEW_REPLAY": False,
        },
        "spec": canonical_spec(),
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    parent = dict(pack.get("parent") or {})
    win = dict(pack.get("winner") or {})
    g = dict(pack.get("stability_gates") or {})
    s23 = dict(pack.get("s2_s3") or {})
    folds = list(pack.get("folds") or [])
    by = {f["block"]: f for f in folds}
    agg = dict(pack.get("aggregate") or {})
    n = dict(pack.get("n_counts") or {})
    gd = dict(pack.get("guards") or {})
    reused = dict(pack.get("ALREADY_EXECUTED") or {})
    pnls = dict(s23.get("winner_block_pnls") or {})
    return {
        "ALREADY_EXECUTED_CHECK": reused.get("ALREADY_EXECUTED_CHECK"),
        "REUSED_EXISTING_RESULT": reused.get("REUSED_EXISTING_RESULT"),
        "1_parent_component_verdict": parent.get("VERDICT"),
        "2_parent_PRIMARY_COMPONENT_unchanged": parent.get("PRIMARY_COMPONENT"),
        "3_parent_RESIDUAL_BOTTLENECK_subject": parent.get("RESIDUAL_BOTTLENECK"),
        "4_parent_NEXT": parent.get("NEXT"),
        "5_rca_question": d.get("NEXT_QUESTION"),
        "6_ST_winner_ID": win.get("ST_WINNER_ID"),
        "7_ST_trade_n": win.get("trade_n"),
        "8_ST_BASE_PnL_PF": {"PnL": win.get("BASE_PNL"), "PF": win.get("BASE_PF")},
        "9_ST_G1_G6_all_pass": win.get("G1_G6_ALL_PASS"),
        "10_original_stability_component_N": 4,
        "11_S1_TRAIN_TOP3_N": g.get("S1_TRAIN_TOP3_N"),
        "12_S1_PASS": g.get("S1_PASS"),
        "13_S2_FULLDEV_WINNER_POSITIVE_BLOCK_N": g.get("S2_FULLDEV_WINNER_POSITIVE_BLOCK_N"),
        "14_S2_PASS": g.get("S2_PASS"),
        "15_S3_FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL": g.get("S3_FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL"),
        "16_S3_PASS": g.get("S3_PASS"),
        "17_S4_FOLD_SELECTED_TEST_TOTAL_PNL": g.get("S4_FOLD_SELECTED_TEST_TOTAL_PNL"),
        "18_S4_PASS": g.get("S4_PASS"),
        "19_FIXED_WINNER_BLOCK_ROBUSTNESS_PASS": d.get("FIXED_WINNER_BLOCK_ROBUSTNESS_PASS"),
        "20_FAILING_STABILITY_COMPONENT_IDS": d.get("FAILING_STABILITY_COMPONENT_IDS"),
        "21_BOTH_IDENTIFIED_FAILING_COMPONENTS_CONFIRMED": d.get("BOTH_IDENTIFIED_FAILING_COMPONENTS_CONFIRMED"),
        "22_ALL_STABILITY_COMPONENTS_FAIL": False,
        "23_original_ST_stored_POSITIVE_BLOCKS_is_fold_selected_not_S2": g.get(
            "ORIGINAL_ST_STORED_POSITIVE_BLOCKS_FOLD_SELECTED"
        ),
        "24_original_ST_stored_EX_BEST_BLOCK_is_fold_selected_not_S3": g.get(
            "ORIGINAL_ST_STORED_EX_BEST_BLOCK_TOTAL_PNL_FOLD_SELECTED"
        ),
        "25_B1_pnl": pnls.get("B1"),
        "26_B2_pnl": pnls.get("B2"),
        "27_B3_pnl": pnls.get("B3"),
        "28_B4_pnl": pnls.get("B4"),
        "29_B5_pnl": pnls.get("B5"),
        "30_fold_records": [
            {
                "block": f["block"],
                "TRAIN_FINAL_ELIGIBLE_N": f["TRAIN_FINAL_ELIGIBLE_N"],
                "SELECTED_WINNER_EXISTS": f["SELECTED_WINNER_EXISTS"],
                "SELECTED_WINNER_ID": f["SELECTED_WINNER_ID"],
                "TEST_EVALUATION_RAN": f["TEST_EVALUATION_RAN"],
                "TEST_PNL": f["TEST_PNL"],
                "TEST_PF": f["TEST_PF"],
                "TEST_TRADE_N": f["TEST_TRADE_N"],
                "FULL_DEV_WINNER_IN_TOP3": f["FULL_DEV_WINNER_IN_TOP3"],
                "COMPARATOR_STATUS": f["COMPARATOR_STATUS"],
            }
            for f in folds
        ],
        "31_NO_WINNER_FOLD_N": pack.get("NO_WINNER_FOLD_N"),
        "32_NO_WINNER_FOLD_IDS": pack.get("NO_WINNER_FOLD_IDS"),
        "33_TEST_EVALUATED_FOLD_N": pack.get("TEST_EVALUATED_FOLD_N"),
        "34_no_winner_TEST_PNL_is_null_not_zero": all(
            by[b]["TEST_PNL"] is None for b in (pack.get("NO_WINNER_FOLD_IDS") or [])
        ),
        "35_FOLD_SELECTED_TOTAL_SOURCE_COMPONENTS": agg.get("FOLD_SELECTED_TOTAL_SOURCE_COMPONENTS"),
        "36_FOLD_SELECTED_TOTAL_SOURCE_SEMANTICS": agg.get("FOLD_SELECTED_TOTAL_SOURCE_SEMANTICS"),
        "37_RECONSTRUCTED_FROM_EXISTING_SOURCE_ONLY": True,
        "38_NEW_FOLD_ECONOMICS": False,
        "39_no_winner_treated_as_zero_by_source": False,
        "40_source_contributing_fold_IDs": agg.get("SOURCE_CONTRIBUTING_FOLD_IDS"),
        "41_HELDOUT_TRANSFER_STATUS": agg.get("HELDOUT_TRANSFER_STATUS"),
        "42_HELDOUT_TRANSFER_FAILURE_SUPPORTED": d.get("HELDOUT_TRANSFER_FAILURE_SUPPORTED"),
        "43_folds_full_winner_not_in_top3": pack.get("NOT_TOP3_FOLD_IDS"),
        "44_N1_N": n.get("N1_N"),
        "45_N2_N": n.get("N2_N"),
        "46_N3_N": n.get("N3_N"),
        "47_N1_N2_N3_sum": (n.get("N1_N") or 0) + (n.get("N2_N") or 0) + (n.get("N3_N") or 0),
        "48_N_classification_persisted_evidence_only": True,
        "49_RECURRENCE_FAILURE_MODE": d.get("RECURRENCE_FAILURE_MODE"),
        "50_PRIMARY_RCA_OUTCOME": d.get("PRIMARY_RCA_OUTCOME"),
        "51_fold_G6_availability_B1_B5": pack.get("FOLD_G6_AVAILABILITY"),
        "52_fold_rank_availability_B1_B5": pack.get("FOLD_RANK_AVAILABILITY"),
        "53_fold_robust_score_availability_B1_B5": pack.get("FOLD_ROBUST_SCORE_AVAILABILITY"),
        "54_FOLD_G6_NOT_RUN_MISCLASSIFIED_N": pack.get("FOLD_G6_NOT_RUN_MISCLASSIFIED_N"),
        "55_missing_G6_called_ineligible": False,
        "56_train_gate_margins": {f["block"]: f["GATE_MARGIN"] for f in folds},
        "57_comparator": [
            {
                "block": f["block"],
                "STATUS": f["COMPARATOR_STATUS"],
                "FOLD_SELECTED_TEST_PNL": f.get("FOLD_SELECTED_TEST_PNL"),
                "FULL_DEV_WINNER_BLOCK_PNL": f.get("FULL_DEV_WINNER_BLOCK_PNL"),
            }
            for f in folds
        ],
        "58_RETROSPECTIVE_RCA_ONLY": True,
        "59_PROSPECTIVE_BENCHMARK": False,
        "60_forced_into_one_cause": False,
        "61_ST_SPECIFIC": True,
        "62_L1_WIDE": False,
        "63_CROSS_LINEAGE_selection": False,
        "64_NEW_REPLAY": False,
        "65_NEW_FOLD_RUN": False,
        "66_strategy_rescue": False,
        "67_ENTRY_EXIT_retune": False,
        "68_TopK_Robust_gate_change": False,
        "69_symbol_day_regime_filter": False,
        "70_future_used": False,
        "71_Holdout_read": False,
        "72_Stress_read": False,
        "73_Sizing": False,
        "74_Runtime_changed": False,
        "75_submit_cancel_live": "0/0/0",
        "76_TRUE_OOS_CERTIFIED": {"TRUE_OOS": False, "CERTIFIED": False},
        "77_D3_decision_eligible": False,
        "78_strategy_created": False,
        "79_threshold_search": False,
        "80_raw_Capture_read": False,
        "81_gate_relaxation": False,
        "82_S1_TRAIN_TOP3_pass": g.get("S1_PASS"),
        "83_S2_positive_block_pass": g.get("S2_PASS"),
        "84_S3_ex_best_block_pass": g.get("S3_PASS"),
        "85_S4_fold_selected_transfer_pass": g.get("S4_PASS"),
        "86_fixed_winner_block_robustness_pass": d.get("FIXED_WINNER_BLOCK_ROBUSTNESS_PASS"),
        "87_exact_failing_stability_component_IDs": d.get("FAILING_STABILITY_COMPONENT_IDS"),
        "88_no_winner_fold_N": pack.get("NO_WINNER_FOLD_N"),
        "89_no_winner_fold_IDs": pack.get("NO_WINNER_FOLD_IDS"),
        "90_test_evaluated_fold_N": pack.get("TEST_EVALUATED_FOLD_N"),
        "91_source_minus_67720_aggregation_semantics_proven": agg.get("HELDOUT_TRANSFER_STATUS") == "PROVEN",
        "92_no_winner_treated_as_zero_by_source": False,
        "93_source_contributing_fold_IDs": agg.get("SOURCE_CONTRIBUTING_FOLD_IDS"),
        "94_fold_G6_availability_B1_B5": pack.get("FOLD_G6_AVAILABILITY"),
        "95_fold_rank_availability_B1_B5": pack.get("FOLD_RANK_AVAILABILITY"),
        "96_fold_robust_score_availability_B1_B5": pack.get("FOLD_ROBUST_SCORE_AVAILABILITY"),
        "97_N1_N2_N3_based_only_on_persisted_evidence": True,
        "98_any_missing_field_reconstructed_by_new_replay": False,
        "99_recurrence_result_ST_specific": True,
        "100_L1_wide_claim_made": False,
        "101_cross_lineage_selection_claim_made": False,
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "NEW_REPLAY": gd.get("NEW_REPLAY"),
        "RAW_CAPTURE_READ_N": gd.get("RAW_CAPTURE_READ_N"),
    }
