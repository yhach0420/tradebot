"""Decompose L1/L2 generalization failure. Existing fields only. No replay."""
from __future__ import annotations

import json
import math
from typing import Any

from research.generalization_failure_rca_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    CASE_F,
    E4_CAN_CHANGE_CASE,
    E4_PRIMARY_VOTE,
    EXPECTED_C1_G1G2_N,
    EXPECTED_RECOVERY_G1G2_N,
    EXPECTED_ST_G1G2_N,
    G2_MIN_PF,
    L3_PRIMARY_VOTE,
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
    NEXT_IF_E,
    NEXT_IF_F,
    PRIMARY_E1_STRATEGY_N,
    RAW_CAPTURE_READ_N,
    REBASE_ANALYSIS_ID,
    REBASE_DEFICIENCY_REQUIRED,
    REBASE_NEXT_REQUIRED,
    REBASE_VERDICT_REQUIRED,
    ST_BASE_PF,
    ST_BASE_PNL,
    ST_CAUSAL_PF,
    ST_CAUSAL_PNL,
    ST_FOLD_TEST_PNL,
    ST_TRAIN_TOP3_N,
    ST_WINNER_ID,
)
from research.generalization_failure_rca_v1.isolation import OUT
from research.generalization_failure_rca_v1.sources import answers, load_sources, ranking
from research.generalization_failure_rca_v1.spec import canonical_spec, spec_sha256
from research.simple_full_strategy_discovery_v1 import MAX_RESEARCH_DATE

NOT_COMPUTED = "NOT_COMPUTED"
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_SUPPORTED = "NOT_SUPPORTED"


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gtable(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("g_table") or {})


def g1_g2(row: dict[str, Any]) -> bool:
    g = _gtable(row)
    if "G1_TOTAL_PNL" in g or "G2_PF" in g:
        return bool(g.get("G1_TOTAL_PNL")) and bool(g.get("G2_PF"))
    pnl = _f(row.get("TOTAL_PNL") if row.get("TOTAL_PNL") is not None else row.get("pnl"))
    pf = _f(row.get("PF"))
    return pnl is not None and pf is not None and pnl > 0 and pf > G2_MIN_PF


def _pnl(row: dict[str, Any]) -> float | None:
    if row.get("TOTAL_PNL") is not None:
        return _f(row.get("TOTAL_PNL"))
    return _f(row.get("pnl"))


def _pf(row: dict[str, Any]) -> float | None:
    return _f(row.get("PF"))


def _ex_best(row: dict[str, Any]) -> float | None:
    if row.get("EX_BEST_DAY_PNL") is not None:
        return _f(row.get("EX_BEST_DAY_PNL"))
    return _f(row.get("EX_BEST"))


def _causal_pnl(row: dict[str, Any]) -> float | None:
    if row.get("CAUSAL_EX_TOP1_PNL") is not None:
        return _f(row.get("CAUSAL_EX_TOP1_PNL"))
    return _f(row.get("CAUSAL_EX_TOP1"))


def _causal_pf(row: dict[str, Any]) -> float | None:
    return _f(row.get("CAUSAL_EX_TOP1_PF"))


def gate_run_state(row: dict[str, Any]) -> dict[str, Any]:
    g = _gtable(row)
    pnl = _pnl(row)
    pf = _pf(row)
    pos = row.get("positive_days")
    if pos is None:
        pos = row.get("positive_day_n")
    neg = row.get("negative_days")
    if neg is None:
        neg = row.get("negative_day_n")
    exb = _ex_best(row)
    mx = _f(row.get("MaxDD") if row.get("MaxDD") is not None else row.get("MAXDD"))
    causal = _causal_pnl(row)
    g6_ran_flag = g.get("G6_RAN")
    if g6_ran_flag is None:
        g6_ran = causal is not None
    else:
        g6_ran = bool(g6_ran_flag)
    g1_ran = pnl is not None or "G1_TOTAL_PNL" in g
    g2_ran = pf is not None or "G2_PF" in g
    g3_ran = pos is not None or "G3_DAY_SIGNS" in g
    g4_ran = exb is not None or "G4_EX_BEST" in g
    g5_ran = (pnl is not None and mx is not None) or "G5_PNL_PLUS_MAXDD" in g
    g1_val = bool(g["G1_TOTAL_PNL"]) if "G1_TOTAL_PNL" in g else (pnl is not None and pnl > 0)
    g2_val = bool(g["G2_PF"]) if "G2_PF" in g else (pf is not None and pf > G2_MIN_PF)
    g3_val = bool(g["G3_DAY_SIGNS"]) if "G3_DAY_SIGNS" in g else (
        pos is not None and neg is not None and int(pos) > int(neg)
    )
    g4_val = bool(g["G4_EX_BEST"]) if "G4_EX_BEST" in g else (exb is not None and exb > 0)
    g5_val = bool(g["G5_PNL_PLUS_MAXDD"]) if "G5_PNL_PLUS_MAXDD" in g else (
        pnl is not None and mx is not None and (pnl + mx) > 0
    )
    if g6_ran:
        g6_val = bool(g["G6_CAUSAL_EX_TOP1"]) if "G6_CAUSAL_EX_TOP1" in g else (causal is not None and causal >= 0)
        causal_status = "PASS" if g6_val else "G6_FAIL"
    else:
        g6_val = False
        causal_status = NOT_COMPUTED
    return {
        "G1_VALUE": g1_val,
        "G1_RAN": g1_ran,
        "G2_VALUE": g2_val,
        "G2_RAN": g2_ran,
        "G3_VALUE": g3_val,
        "G3_RAN": g3_ran,
        "G4_VALUE": g4_val,
        "G4_RAN": g4_ran,
        "G5_VALUE": g5_val,
        "G5_RAN": g5_ran,
        "G6_VALUE": g6_val,
        "G6_RAN": g6_ran,
        "CAUSAL_EX_TOP1_STATUS": causal_status,
    }


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


def _pin_rebase(rebased: dict[str, Any]) -> dict[str, Any]:
    report = rebased["report"]
    a = answers(report)
    d = dict(report.get("decision") or {})
    pin = {
        "ANALYSIS_ID": report.get("ANALYSIS_ID"),
        "VERDICT": a.get("50_VERDICT") or d.get("VERDICT"),
        "PRIMARY_DEFICIENCY": a.get("31_PRIMARY_DEFICIENCY") or d.get("PRIMARY_DEFICIENCY"),
        "NEXT": a.get("51_NEXT") or d.get("NEXT"),
        "ROBUST_LINEAGE_N": d.get("ROBUST_LINEAGE_N") if d.get("ROBUST_LINEAGE_N") is not None else a.get("23_robust_lineage_N"),
        "AGGREGATE_EDGE_LINEAGE_N": d.get("AGGREGATE_EDGE_LINEAGE_N") if d.get("AGGREGATE_EDGE_LINEAGE_N") is not None else a.get("24_aggregate_edge_lineage_N"),
        "GENERALIZATION_FAILURE_LINEAGE_N": d.get("GENERALIZATION_FAILURE_LINEAGE_N") if d.get("GENERALIZATION_FAILURE_LINEAGE_N") is not None else a.get("26_generalization_failure_lineage_N"),
        "E1_LINEAGES": ["L1_TECHNICAL_PRICE_STATE", "L2_RECOVERY_RECLAIM_PATH"],
        "L3_ROLE": "NEGATIVE_CONTROL_ONLY",
        "C4_ROLE": "OVERLAY_ONLY",
    }
    if pin["ANALYSIS_ID"] != REBASE_ANALYSIS_ID:
        raise RuntimeError("REBASE_ANALYSIS_ID")
    if pin["VERDICT"] != REBASE_VERDICT_REQUIRED:
        raise RuntimeError(f"REBASE_VERDICT {pin['VERDICT']}")
    if pin["PRIMARY_DEFICIENCY"] != REBASE_DEFICIENCY_REQUIRED:
        raise RuntimeError("REBASE_DEFICIENCY")
    if pin["NEXT"] != REBASE_NEXT_REQUIRED:
        raise RuntimeError("REBASE_NEXT")
    if pin["ROBUST_LINEAGE_N"] != 0:
        raise RuntimeError("REBASE_ROBUST_N")
    if pin["AGGREGATE_EDGE_LINEAGE_N"] != 2:
        raise RuntimeError("REBASE_AGG_N")
    if pin["GENERALIZATION_FAILURE_LINEAGE_N"] != 2:
        raise RuntimeError("REBASE_GEN_N")
    return pin


def _normalize_candidate(row: dict[str, Any], *, study: str, lineage: str, library: str) -> dict[str, Any]:
    gates = gate_run_state(row)
    daily = row.get("daily_pnl")
    daily_ok = isinstance(daily, dict) and len(daily) > 0
    top = row.get("top_symbol")
    best_day = row.get("best_day")
    causal = _causal_pnl(row)
    pnl = _pnl(row)
    pf = _pf(row)
    collapse_symbol = False
    symbol_evaluable = False
    if gates["G6_RAN"] and causal is not None:
        symbol_evaluable = True
        collapse_symbol = bool(pnl is not None and pnl > 0 and pf is not None and pf > G2_MIN_PF and causal < 0)
    exb = _ex_best(row)
    day_evaluable = exb is not None and pnl is not None and pf is not None
    collapse_day = bool(day_evaluable and pnl is not None and pnl > 0 and pf is not None and pf > G2_MIN_PF and exb is not None and exb < 0)
    misclassified = 0
    if (not gates["G6_RAN"]) and gates["G6_VALUE"] is False:
        # false value while not run must never be treated as G6_FAIL / SYMBOL_COLLAPSE
        if gates["CAUSAL_EX_TOP1_STATUS"] != NOT_COMPUTED:
            misclassified = 1
        if collapse_symbol:
            misclassified = 1
    return {
        "candidate_id": row.get("candidate_id"),
        "study": study,
        "lineage": lineage,
        "library": library,
        "PRECOMMITTED_LIBRARY": True,
        "SELECTED_AFTER_ECONOMICS": False,
        "E4_PRIMARY_VOTE": False,
        "BASE_PNL": pnl,
        "BASE_PF": pf,
        "top_symbol": top,
        "CAUSAL_EX_TOP1_PNL": causal if gates["G6_RAN"] else None,
        "CAUSAL_EX_TOP1_PF": _causal_pf(row) if gates["G6_RAN"] else None,
        "CAUSAL_EX_TOP1_STATUS": gates["CAUSAL_EX_TOP1_STATUS"],
        "best_day": best_day if best_day not in (None, "") else None,
        "best_day_pnl": _f(row.get("best_day_pnl")),
        "positive_days": row.get("positive_days") if row.get("positive_days") is not None else row.get("positive_day_n"),
        "negative_days": row.get("negative_days") if row.get("negative_days") is not None else row.get("negative_day_n"),
        "EX_BEST_DAY_PNL": exb,
        "MaxDD": _f(row.get("MaxDD") if row.get("MaxDD") is not None else row.get("MAXDD")),
        "daily_pnl": daily if daily_ok else None,
        "DAILY_VECTOR_AVAILABLE": daily_ok,
        "CAUSAL_SYMBOL_COLLAPSE": collapse_symbol if symbol_evaluable else None,
        "SYMBOL_EVALUABLE": symbol_evaluable,
        "BEST_DAY_COLLAPSE": collapse_day if day_evaluable else None,
        "DAY_EVALUABLE": day_evaluable,
        "FALSE_VALUE_WITH_NOT_RUN_MISCLASSIFIED": misclassified,
        **gates,
    }


def _extract_primary(st_rep: dict[str, Any], c1_rep: dict[str, Any], rec_rep: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    st_rows = [r for r in ranking(st_rep) if g1_g2(r)]
    c1_rows = [r for r in ranking(c1_rep) if g1_g2(r)]
    rec_rows = [r for r in ranking(rec_rep) if g1_g2(r)]
    if len(st_rows) != EXPECTED_ST_G1G2_N:
        raise RuntimeError(f"ST_G1G2_N {len(st_rows)}")
    if len(c1_rows) != EXPECTED_C1_G1G2_N:
        raise RuntimeError(f"C1_G1G2_N {len(c1_rows)}")
    if len(rec_rows) != EXPECTED_RECOVERY_G1G2_N:
        raise RuntimeError(f"REC_G1G2_N {len(rec_rows)}")
    for r in st_rows:
        out.append(_normalize_candidate(r, study="SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1", lineage="L1_TECHNICAL_PRICE_STATE", library="ST"))
    for r in c1_rows:
        out.append(_normalize_candidate(r, study="C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2", lineage="L1_TECHNICAL_PRICE_STATE", library="C1"))
    for r in rec_rows:
        out.append(_normalize_candidate(r, study="RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1", lineage="L2_RECOVERY_RECLAIM_PATH", library="RECOVERY"))
    if len(out) != PRIMARY_E1_STRATEGY_N:
        raise RuntimeError(f"PRIMARY_N {len(out)}")
    if any(not c["PRECOMMITTED_LIBRARY"] or c["SELECTED_AFTER_ECONOMICS"] for c in out):
        raise RuntimeError("PRIMARY_LIBRARY_FLAG")
    return out


def _classify_mode(evaluable_n: int, collapse_n: int, survive_n: int, *, kind: str) -> str:
    if evaluable_n <= 0:
        return NOT_COMPUTED
    if collapse_n == evaluable_n:
        return f"CONSISTENT_{kind}_COLLAPSE"
    if collapse_n >= 1 and survive_n >= 1:
        return f"MIXED_{kind}_DEPENDENCE"
    if collapse_n == 0:
        return f"NO_{kind}_COLLAPSE"
    return f"MIXED_{kind}_DEPENDENCE"


def _mode_supported(label: str) -> bool:
    return label.startswith("CONSISTENT_") or label.startswith("MIXED_")


def _matrix_cell(label: str) -> str:
    if label == NOT_COMPUTED:
        return NOT_COMPUTED
    if label == NOT_APPLICABLE:
        return NOT_APPLICABLE
    if label.startswith("CONSISTENT_"):
        return "CONSISTENT"
    if label.startswith("MIXED_"):
        return "MIXED"
    if label.startswith("NO_"):
        return NOT_SUPPORTED
    return label


def _lineage_symbol(cands: list[dict[str, Any]]) -> dict[str, Any]:
    ev = [c for c in cands if c.get("SYMBOL_EVALUABLE")]
    collapse = [c for c in ev if c.get("CAUSAL_SYMBOL_COLLAPSE") is True]
    survive = [c for c in ev if c.get("CAUSAL_SYMBOL_COLLAPSE") is False]
    label = _classify_mode(len(ev), len(collapse), len(survive), kind="SYMBOL")
    return {
        "SYMBOL_EVALUABLE_N": len(ev),
        "SYMBOL_COLLAPSE_N": len(collapse),
        "SYMBOL_SURVIVE_N": len(survive),
        "classification": label,
        "evaluable_ids": [c["candidate_id"] for c in ev],
        "collapse_ids": [c["candidate_id"] for c in collapse],
        "survive_ids": [c["candidate_id"] for c in survive],
    }


def _lineage_day(cands: list[dict[str, Any]]) -> dict[str, Any]:
    ev = [c for c in cands if c.get("DAY_EVALUABLE")]
    collapse = [c for c in ev if c.get("BEST_DAY_COLLAPSE") is True]
    survive = [c for c in ev if c.get("BEST_DAY_COLLAPSE") is False]
    label = _classify_mode(len(ev), len(collapse), len(survive), kind="DAY")
    return {
        "DAY_EVALUABLE_N": len(ev),
        "BEST_DAY_COLLAPSE_N": len(collapse),
        "BEST_DAY_SURVIVE_N": len(survive),
        "classification": label,
        "DAY_EVALUABLE_BASIS": "EX_BEST_DAY_PNL_PRESENT",
        "evaluable_ids": [c["candidate_id"] for c in ev],
        "collapse_ids": [c["candidate_id"] for c in collapse],
        "survive_ids": [c["candidate_id"] for c in survive],
    }


def _shared(ids_a: list[Any], ids_b: list[Any]) -> dict[str, Any]:
    a = {str(x) for x in ids_a if x not in (None, "")}
    b = {str(x) for x in ids_b if x not in (None, "")}
    shared = sorted(a & b)
    return {"ids": shared, "support": bool(shared), "NOTE": "COMMON_DRIVER_EVIDENCE; not required for mode support"}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return _pearson(ranks(xs), ranks(ys))


def _daily_comovement(l1: list[dict[str, Any]], l2: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    pears: list[float] = []
    spears: list[float] = []
    for a in l1:
        da = a.get("daily_pnl") or {}
        if not da:
            continue
        for b in l2:
            db = b.get("daily_pnl") or {}
            if not db:
                continue
            days = sorted(set(da) & set(db))
            if not days:
                continue
            xs = [float(da[d]) for d in days]
            ys = [float(db[d]) for d in days]
            same = sum(1 for x, y in zip(xs, ys) if (x > 0 and y > 0) or (x < 0 and y < 0) or (x == 0 and y == 0))
            opp = sum(1 for x, y in zip(xs, ys) if (x > 0 > y) or (y > 0 > x))
            pr = _pearson(xs, ys)
            sp = _spearman(xs, ys)
            rec = {
                "L1_ID": a["candidate_id"],
                "L2_ID": b["candidate_id"],
                "COMMON_DAY_N": len(days),
                "Pearson": pr,
                "Spearman": sp,
                "same_sign_n": same,
                "opposite_sign_n": opp,
            }
            pairs.append(rec)
            if pr is not None:
                pears.append(pr)
            if sp is not None:
                spears.append(sp)
    return {
        "PAIR_N": len(pairs),
        "pairs": pairs,
        "Pearson_range": {"min": min(pears), "max": max(pears)} if pears else NOT_COMPUTED,
        "Spearman_range": {"min": min(spears), "max": max(spears)} if spears else NOT_COMPUTED,
        "INFERENTIAL_SIGNIFICANCE_CLAIMED": False,
        "P_VALUE": False,
        "CORRELATION_THRESHOLD": False,
        "L2_DAILY_VECTOR_AVAILABLE_N": sum(1 for c in l2 if c.get("DAILY_VECTOR_AVAILABLE")),
        "L1_DAILY_VECTOR_AVAILABLE_N": sum(1 for c in l1 if c.get("DAILY_VECTOR_AVAILABLE")),
        "NOTE": "Descriptive only. Missing L2 daily vectors => pair N may be 0. NOT_COMPUTED is not a failure.",
    }


def _selection(st_rep: dict[str, Any], primary: list[dict[str, Any]]) -> dict[str, Any]:
    a = answers(st_rep)
    d = dict(st_rep.get("decision") or {})
    winner = next((c for c in primary if c["candidate_id"] == ST_WINNER_ID), None)
    if winner is None:
        raise RuntimeError("ST_WINNER_NOT_IN_PRIMARY")
    pnl = winner["BASE_PNL"]
    pf = winner["BASE_PF"]
    causal = winner["CAUSAL_EX_TOP1_PNL"]
    cpf = winner["CAUSAL_EX_TOP1_PF"]
    if pnl != ST_BASE_PNL:
        raise RuntimeError(f"ST_BASE_PNL {pnl}")
    if pf is None or abs(float(pf) - ST_BASE_PF) > 1e-12:
        raise RuntimeError(f"ST_BASE_PF {pf}")
    if causal != ST_CAUSAL_PNL:
        raise RuntimeError(f"ST_CAUSAL_PNL {causal}")
    if cpf is None or abs(float(cpf) - ST_CAUSAL_PF) > 1e-12:
        raise RuntimeError(f"ST_CAUSAL_PF {cpf}")
    top3 = a.get("57_TRAIN_TOP3_N")
    fold = a.get("58_FOLD_SELECTED_TEST_TOTAL_PNL")
    stab = a.get("59_STABILITY_PASS")
    if top3 != ST_TRAIN_TOP3_N:
        raise RuntimeError(f"TRAIN_TOP3 {top3}")
    if fold != ST_FOLD_TEST_PNL:
        raise RuntimeError(f"FOLD_PNL {fold}")
    if stab is not False:
        raise RuntimeError("STABILITY")
    all_by_symbol = bool(winner.get("CAUSAL_SYMBOL_COLLAPSE") is True)
    return {
        "ST_WINNER_ID": ST_WINNER_ID,
        "BASE_PNL": pnl,
        "BASE_PF": pf,
        "CAUSAL_EX_TOP1_PNL": causal,
        "CAUSAL_EX_TOP1_PF": cpf,
        "TRAIN_TOP3_N": top3,
        "FOLD_SELECTED_TEST_TOTAL_PNL": fold,
        "STABILITY_PASS": False,
        "VERDICT": d.get("VERDICT") or a.get("VERDICT"),
        "L1_SELECTION_SURFACE_INSTABILITY_SUPPORTED": True,
        "L1_ALL_EDGE_EXPLAINED_BY_SYMBOL": all_by_symbol,
        "L2_SELECTION_SURFACE_STATUS": NOT_APPLICABLE,
        "NOTE": "ST winner survives causal top-symbol removal. L1 edge is not entirely one-symbol.",
    }


def _e4(simple_rep: dict[str, Any], e4_rep: dict[str, Any]) -> dict[str, Any]:
    row = next((r for r in ranking(simple_rep) if r.get("candidate_id") == "E4_X2_Z3"), {})
    ea = answers(e4_rep)
    base_pnl = _pnl(row)
    base_pf = _pf(row)
    exb = _ex_best(row)
    causal = ea.get("13_causal_EX_TOP1_PnL")
    cpf = ea.get("14_causal_EX_TOP1_PF")
    symbol_mode = bool(base_pnl is not None and base_pnl > 0 and base_pf is not None and base_pf > G2_MIN_PF and _f(causal) is not None and float(causal) < 0)
    day_mode = bool(base_pnl is not None and base_pnl > 0 and base_pf is not None and base_pf > G2_MIN_PF and exb is not None and exb < 0)
    return {
        "E4_PRIMARY_VOTE": E4_PRIMARY_VOTE,
        "E4_CAN_CHANGE_CASE": E4_CAN_CHANGE_CASE,
        "ROLE": "ADAPTIVE_SECONDARY_REFERENCE",
        "BASE_PNL": base_pnl,
        "BASE_PF": base_pf,
        "EX_BEST_DAY_PNL": exb,
        "CAUSAL_EX_285A_PNL": causal,
        "CAUSAL_EX_285A_PF": cpf,
        "top_symbol": "285A",
        "E4_CORROBORATES_SYMBOL_MODE": symbol_mode,
        "E4_CORROBORATES_DAY_MODE": day_mode,
        "E4_CORROBORATES_EPISODE_MODE": NOT_COMPUTED,
        "PRECOMMITTED_LIBRARY": False,
        "SELECTED_AFTER_ECONOMICS": True,
        "BROAD_GRID_CANDIDATE_N": 75,
    }


def _l3(part_rep: dict[str, Any], shared_symbols: list[str], shared_days: list[str]) -> dict[str, Any]:
    a = answers(part_rep)
    tops = [r.get("top_symbol") for r in list(a.get("29_each_top_symbol") or [])]
    shared_top = sorted({str(t) for t in tops if t} & set(shared_symbols))
    return {
        "L3_PRIMARY_VOTE": L3_PRIMARY_VOTE,
        "ROLE": "MARKET_OPPORTUNITY_CONTEXT_ONLY",
        "candidate_n": a.get("16_candidate_N"),
        "G1_G2_N": 0,
        "top_symbols": tops,
        "shares_primary_top_symbol": bool(shared_top),
        "shared_top_symbols_with_primary": shared_top,
        "shares_primary_best_day": False,
        "shared_best_days_with_primary": [],
        "shares_primary_top_cell": NOT_COMPUTED,
        "INTERPRETATION": "MARKET_OPPORTUNITY_CONTEXT_ONLY",
        "CASE_CHANGE": False,
        "shared_days_checked_against": shared_days,
    }


def _joint_episode() -> dict[str, Any]:
    return {
        "JOINT_LEDGER_AVAILABLE": False,
        "L1_CLASSIFICATION": NOT_COMPUTED,
        "L2_CLASSIFICATION": NOT_COMPUTED,
        "SHARED_TOP_DAY_SYMBOL_CELLS": [],
        "CAUSAL_JOINT_EPISODE_PROVEN": False,
        "D3_DECISION_ELIGIBLE": False,
        "POSTHOC_DESCRIPTIVE_ONLY": True,
        "NOTE": "No existing executed trade ledger was present under prior OUT/CACHE freeze paths. Work grids are harvest signals, not trade ledgers. Replay to rebuild trades is forbidden.",
    }


def _decision(
    sel: dict[str, Any],
    cross_symbol: bool,
    cross_day: bool,
    joint: dict[str, Any],
    l1_sym: dict[str, Any],
    l2_sym: dict[str, Any],
    l1_day: dict[str, Any],
    l2_day: dict[str, Any],
) -> dict[str, Any]:
    sel_ok = bool(sel.get("L1_SELECTION_SURFACE_INSTABILITY_SUPPORTED"))
    if sel_ok and (cross_symbol or cross_day):
        case = "A"
        mode = "MIXED_CONCENTRATION_AND_SELECTION_INSTABILITY"
        verdict = CASE_A
        nxt = NEXT_IF_A
    elif cross_symbol and not cross_day:
        case = "B"
        mode = "CROSS_LINEAGE_SYMBOL_CONCENTRATION"
        verdict = CASE_B
        nxt = NEXT_IF_B
    elif cross_day and not cross_symbol:
        case = "C"
        mode = "CROSS_LINEAGE_DAY_CONCENTRATION"
        verdict = CASE_C
        nxt = NEXT_IF_C
    elif cross_symbol and cross_day:
        case = "D"
        mode = "MIXED_SYMBOL_AND_DAY_CONCENTRATION"
        verdict = CASE_D
        nxt = NEXT_IF_D
    elif sel_ok:
        case = "E"
        mode = "SELECTION_SURFACE_INSTABILITY"
        verdict = CASE_E
        nxt = NEXT_IF_E
    else:
        case = "F"
        mode = "INCONCLUSIVE_WITH_EXISTING_ARTIFACTS"
        verdict = CASE_F
        nxt = NEXT_IF_F
    evidence = (
        f"L1 selection: {ST_WINNER_ID} BASE PnL={ST_BASE_PNL} PF={ST_BASE_PF} "
        f"CAUSAL_EX_TOP1={ST_CAUSAL_PNL}/{ST_CAUSAL_PF} TRAIN_TOP3_N={ST_TRAIN_TOP3_N} "
        f"fold-selected test PnL={ST_FOLD_TEST_PNL} STABILITY_PASS=false; "
        f"winner survives causal top-symbol so L1_ALL_EDGE_EXPLAINED_BY_SYMBOL=false. "
        f"L1 symbol {l1_sym['classification']} evaluable/collapse/survive="
        f"{l1_sym['SYMBOL_EVALUABLE_N']}/{l1_sym['SYMBOL_COLLAPSE_N']}/{l1_sym['SYMBOL_SURVIVE_N']}. "
        f"L2 symbol {l2_sym['classification']} "
        f"{l2_sym['SYMBOL_EVALUABLE_N']}/{l2_sym['SYMBOL_COLLAPSE_N']}/{l2_sym['SYMBOL_SURVIVE_N']}. "
        f"L1 day {l1_day['classification']} collapse/survive="
        f"{l1_day['BEST_DAY_COLLAPSE_N']}/{l1_day['BEST_DAY_SURVIVE_N']}. "
        f"L2 day {l2_day['classification']} "
        f"{l2_day['BEST_DAY_COLLAPSE_N']}/{l2_day['BEST_DAY_SURVIVE_N']}. "
        f"CROSS_SYMBOL={cross_symbol} CROSS_DAY={cross_day}. "
        "Same exact symbol/day IDs are not required for mode support. "
        f"D3 ledger available={joint['JOINT_LEDGER_AVAILABLE']}; "
        f"CAUSAL_JOINT_EPISODE_PROVEN={joint['CAUSAL_JOINT_EPISODE_PROVEN']}; D3 cannot override CASE."
    )
    competing = {
        "ONE_BAD_SYMBOL_EXPLAINS_EVERYTHING": "Rejected. ST winner causal ex-top PnL remains +77580 and PF 1.6108. L1 symbol mode is MIXED, not consistent collapse.",
        "SAME_SYMBOL_ID_REQUIRED_FOR_D1": "Rejected by spec. Mode is lineage-level collapse/survive, not 285A identity.",
        "SAME_DAY_ID_REQUIRED_FOR_D2": "Rejected by spec. Shared best-day IDs are common-driver evidence only.",
        "POST_FILL_VALUE_CAPTURE": "Not this RCA. Rebase already set POST_FILL_VALUE_CAPTURE_EVIDENCE=NOT_ESTABLISHED.",
        "D3_JOINT_EPISODE_AS_PRIMARY": "Forbidden. No causal day×symbol exclusion+rerun exists. D3_DECISION_ELIGIBLE=false.",
    }
    return {
        "CASE": case,
        "PRIMARY_GENERALIZATION_FAILURE_MODE": mode,
        "VERDICT": verdict,
        "NEXT": nxt,
        "EXACT_EVIDENCE": evidence,
        "COMPETING_EXPLANATIONS": competing,
        "D3_OVERRIDE": False,
        "NEXT_CREATES_STRATEGY": False,
        "NEXT_CREATES_FILTER": False,
        "CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": cross_symbol,
        "CROSS_LINEAGE_DAY_MODE_SUPPORT": cross_day,
    }


def decide(sources: dict[str, Any] | None = None) -> dict[str, Any]:
    pack = sources or load_sources()
    by = pack["by_id"]
    reused = already_executed_check(str(pack["inventory_fingerprint"]))
    rebase = _pin_rebase(by["RESEARCH_OBJECTIVE_REBASE_V1"])
    primary = _extract_primary(
        by["SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1"]["report"],
        by["C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"]["report"],
        by["RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1"]["report"],
    )
    mis_n = sum(int(c.get("FALSE_VALUE_WITH_NOT_RUN_MISCLASSIFIED") or 0) for c in primary)
    if mis_n != 0:
        raise RuntimeError(f"FALSE_VALUE_WITH_NOT_RUN_MISCLASSIFIED_N {mis_n}")
    l1 = [c for c in primary if c["lineage"] == "L1_TECHNICAL_PRICE_STATE"]
    l2 = [c for c in primary if c["lineage"] == "L2_RECOVERY_RECLAIM_PATH"]
    l1_sym = _lineage_symbol(l1)
    l2_sym = _lineage_symbol(l2)
    l1_day = _lineage_day(l1)
    l2_day = _lineage_day(l2)
    cross_symbol = _mode_supported(str(l1_sym["classification"])) and _mode_supported(str(l2_sym["classification"]))
    cross_day = _mode_supported(str(l1_day["classification"])) and _mode_supported(str(l2_day["classification"]))
    shared_sym = _shared([c.get("top_symbol") for c in l1], [c.get("top_symbol") for c in l2])
    shared_day = _shared([c.get("best_day") for c in l1], [c.get("best_day") for c in l2])
    joint = _joint_episode()
    sel = _selection(by["SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1"]["report"], primary)
    if sel["L1_ALL_EDGE_EXPLAINED_BY_SYMBOL"] is not False:
        raise RuntimeError("L1_ALL_EDGE_EXPLAINED_BY_SYMBOL_MUST_BE_FALSE")
    e4 = _e4(by["SIMPLE_FULL_STRATEGY_DISCOVERY_V1"]["report"], by["E4_X2_Z3_CAUSAL_CONCENTRATION_RECHECK_V1"]["report"])
    l3 = _l3(by["PARTICIPATION_ONSET_FULL_STRATEGY_V1"]["report"], shared_sym["ids"], shared_day["ids"])
    como = _daily_comovement(l1, l2)
    decision = _decision(sel, cross_symbol, cross_day, joint, l1_sym, l2_sym, l1_day, l2_day)
    g6_true = sum(1 for c in primary if c.get("G6_RAN") is True)
    g6_false = sum(1 for c in primary if c.get("G6_RAN") is False)
    matrix = {
        "SYMBOL_CONCENTRATION": {"L1": _matrix_cell(str(l1_sym["classification"])), "L2": _matrix_cell(str(l2_sym["classification"]))},
        "DAY_CONCENTRATION": {"L1": _matrix_cell(str(l1_day["classification"])), "L2": _matrix_cell(str(l2_day["classification"]))},
        "EPISODE_CONCENTRATION": {"L1": NOT_COMPUTED, "L2": NOT_COMPUTED},
        "SELECTION_INSTABILITY": {"L1": "CONSISTENT", "L2": NOT_APPLICABLE},
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ALREADY_EXECUTED": reused,
        "inventory_fingerprint": pack["inventory_fingerprint"],
        "rebase": rebase,
        "source_inventory": [
            {k: r[k] for k in ("ANALYSIS_ID", "dirname", "role", "lineage", "path", "report_sha256")}
            for r in pack["items"]
        ],
        "primary_cohort": primary,
        "PRIMARY_E1_STRATEGY_N": len(primary),
        "MANUAL_SELECTION": False,
        "gate_counts": {
            "G6_RAN_TRUE_N": g6_true,
            "G6_RAN_FALSE_N": g6_false,
            "FALSE_VALUE_WITH_NOT_RUN_MISCLASSIFIED_N": mis_n,
        },
        "l1_symbol": l1_sym,
        "l2_symbol": l2_sym,
        "l1_day": l1_day,
        "l2_day": l2_day,
        "CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": cross_symbol,
        "CROSS_LINEAGE_DAY_MODE_SUPPORT": cross_day,
        "shared_top_symbol": shared_sym,
        "shared_best_day": shared_day,
        "joint_episode": joint,
        "selection_surface": sel,
        "daily_comovement": como,
        "e4_secondary": e4,
        "l3_control": l3,
        "lineage_matrix": matrix,
        "specific_driver": {
            "SHARED_TOP_SYMBOL": shared_sym,
            "SHARED_BEST_DAY": shared_day,
            "SHARED_TOP_DAY_SYMBOL_CELL": {"ids": [], "support": False, "status": NOT_COMPUTED},
            "NOTE": "These answer same concrete market episode? They do not answer same failure mode?",
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
            "C4_RULE_CREATED": False,
            "THRESHOLD_SEARCHED": False,
            "SIZING": False,
            "REVIVE_E4": False,
            "REVIVE_RECOVERY": False,
            "REVIVE_STATE_TRANSITION": False,
            "REVIVE_C1": False,
            "REVIVE_C4": False,
            "REVIVE_X9": False,
            "GATE_RELAXATION": False,
            "NEW_REPLAY": NEW_REPLAY,
            "NEW_FULL_CAUSAL_RUN": NEW_FULL_CAUSAL_RUN,
            "NEW_PNL_SIMULATION": NEW_PNL_SIMULATION,
            "NEW_FOLD_RUN": NEW_FOLD_RUN,
            "NEW_ENTRY": NEW_ENTRY,
            "NEW_EXIT": NEW_EXIT,
            "NEW_THRESHOLD": NEW_THRESHOLD,
            "NEW_CANDIDATE": NEW_CANDIDATE,
            "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
            "E4_PRIMARY_VOTE": E4_PRIMARY_VOTE,
            "L3_PRIMARY_VOTE": L3_PRIMARY_VOTE,
            "E4_CAN_CHANGE_CASE": E4_CAN_CHANGE_CASE,
        },
        "spec": canonical_spec(),
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    rebase = dict(pack.get("rebase") or {})
    gates = dict(pack.get("gate_counts") or {})
    l1s = dict(pack.get("l1_symbol") or {})
    l2s = dict(pack.get("l2_symbol") or {})
    l1d = dict(pack.get("l1_day") or {})
    l2d = dict(pack.get("l2_day") or {})
    shs = dict(pack.get("shared_top_symbol") or {})
    shd = dict(pack.get("shared_best_day") or {})
    joint = dict(pack.get("joint_episode") or {})
    sel = dict(pack.get("selection_surface") or {})
    como = dict(pack.get("daily_comovement") or {})
    e4 = dict(pack.get("e4_secondary") or {})
    l3 = dict(pack.get("l3_control") or {})
    d = dict(pack.get("decision") or {})
    g = dict(pack.get("guards") or {})
    reused = dict(pack.get("ALREADY_EXECUTED") or {})
    corr_range = como.get("Pearson_range")
    if isinstance(corr_range, dict):
        corr_txt = {"Pearson": corr_range, "Spearman": como.get("Spearman_range")}
    else:
        corr_txt = NOT_COMPUTED
    return {
        "ALREADY_EXECUTED_CHECK": reused.get("ALREADY_EXECUTED_CHECK"),
        "REUSED_EXISTING_RESULT": reused.get("REUSED_EXISTING_RESULT"),
        "1_rebase_verdict": rebase.get("VERDICT"),
        "2_PRIMARY_DEFICIENCY": rebase.get("PRIMARY_DEFICIENCY"),
        "3_PRIMARY_cohort_N": pack.get("PRIMARY_E1_STRATEGY_N"),
        "4_manual_selection": False,
        "5_G6_RAN_true_N": gates.get("G6_RAN_TRUE_N"),
        "6_G6_RAN_false_N": gates.get("G6_RAN_FALSE_N"),
        "7_false_but_not_run_misclassified_N": gates.get("FALSE_VALUE_WITH_NOT_RUN_MISCLASSIFIED_N"),
        "8_L1_symbol_evaluable_collapse_survive_N": {
            "evaluable": l1s.get("SYMBOL_EVALUABLE_N"),
            "collapse": l1s.get("SYMBOL_COLLAPSE_N"),
            "survive": l1s.get("SYMBOL_SURVIVE_N"),
        },
        "9_L1_symbol_classification": l1s.get("classification"),
        "10_L2_symbol_evaluable_collapse_survive_N": {
            "evaluable": l2s.get("SYMBOL_EVALUABLE_N"),
            "collapse": l2s.get("SYMBOL_COLLAPSE_N"),
            "survive": l2s.get("SYMBOL_SURVIVE_N"),
        },
        "11_L2_symbol_classification": l2s.get("classification"),
        "12_CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": pack.get("CROSS_LINEAGE_SYMBOL_MODE_SUPPORT"),
        "13_shared_exact_top_symbols": shs.get("ids"),
        "14_shared_symbol_support": shs.get("support"),
        "15_L1_day_collapse_survive_N": {
            "evaluable": l1d.get("DAY_EVALUABLE_N"),
            "collapse": l1d.get("BEST_DAY_COLLAPSE_N"),
            "survive": l1d.get("BEST_DAY_SURVIVE_N"),
        },
        "16_L1_day_classification": l1d.get("classification"),
        "17_L2_day_collapse_survive_N": {
            "evaluable": l2d.get("DAY_EVALUABLE_N"),
            "collapse": l2d.get("BEST_DAY_COLLAPSE_N"),
            "survive": l2d.get("BEST_DAY_SURVIVE_N"),
        },
        "18_L2_day_classification": l2d.get("classification"),
        "19_CROSS_LINEAGE_DAY_MODE_SUPPORT": pack.get("CROSS_LINEAGE_DAY_MODE_SUPPORT"),
        "20_shared_exact_best_days": shd.get("ids"),
        "21_shared_day_support": shd.get("support"),
        "22_joint_ledger_available": joint.get("JOINT_LEDGER_AVAILABLE"),
        "23_joint_episode_classification_L1": joint.get("L1_CLASSIFICATION"),
        "24_joint_episode_classification_L2": joint.get("L2_CLASSIFICATION"),
        "25_shared_exact_top_cells": joint.get("SHARED_TOP_DAY_SYMBOL_CELLS"),
        "26_causal_joint_proof": joint.get("CAUSAL_JOINT_EPISODE_PROVEN"),
        "27_D3_decision_eligible": joint.get("D3_DECISION_ELIGIBLE"),
        "28_ST_winner_ID": sel.get("ST_WINNER_ID"),
        "29_ST_BASE_PnL_PF": {"PnL": sel.get("BASE_PNL"), "PF": sel.get("BASE_PF")},
        "30_ST_causal_ex_top_PnL_PF": {"PnL": sel.get("CAUSAL_EX_TOP1_PNL"), "PF": sel.get("CAUSAL_EX_TOP1_PF")},
        "31_TRAIN_TOP3_N": sel.get("TRAIN_TOP3_N"),
        "32_fold_selected_PnL": sel.get("FOLD_SELECTED_TEST_TOTAL_PNL"),
        "33_L1_selection_instability_supported": sel.get("L1_SELECTION_SURFACE_INSTABILITY_SUPPORTED"),
        "34_L1_all_edge_explained_by_symbol": sel.get("L1_ALL_EDGE_EXPLAINED_BY_SYMBOL"),
        "35_daily_pair_N": como.get("PAIR_N"),
        "36_correlation_range": corr_txt,
        "37_inferential_significance_claimed": False,
        "38_E4_corroboration": {
            "symbol": e4.get("E4_CORROBORATES_SYMBOL_MODE"),
            "day": e4.get("E4_CORROBORATES_DAY_MODE"),
            "episode": e4.get("E4_CORROBORATES_EPISODE_MODE"),
            "can_change_case": False,
        },
        "39_L3_context": l3.get("INTERPRETATION"),
        "40_PRIMARY_GENERALIZATION_FAILURE_MODE": d.get("PRIMARY_GENERALIZATION_FAILURE_MODE"),
        "41_exact_evidence": d.get("EXACT_EVIDENCE"),
        "42_specific_shared_driver_exists": bool(shs.get("support")),
        "43_competing_explanations": d.get("COMPETING_EXPLANATIONS"),
        "44_strategy_created": False,
        "45_symbol_date_regime_filter": False,
        "46_threshold_search": False,
        "47_new_replay": False,
        "48_raw_Capture_read": False,
        "49_Holdout_read": False,
        "50_Stress_read": False,
        "51_future_used": False,
        "52_gate_relaxation": False,
        "53_Sizing": False,
        "54_Runtime_changed": False,
        "55_submit_cancel_live": "0/0/0",
        "56_TRUE_OOS": False,
        "57_CERTIFIED": False,
        "58_VERDICT": d.get("VERDICT"),
        "59_NEXT": d.get("NEXT"),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "C4_ROLE": "OVERLAY_ONLY",
        "L3_ROLE": "NEGATIVE_CONTROL_ONLY",
        "NEW_REPLAY": g.get("NEW_REPLAY"),
        "RAW_CAPTURE_READ_N": g.get("RAW_CAPTURE_READ_N"),
    }
