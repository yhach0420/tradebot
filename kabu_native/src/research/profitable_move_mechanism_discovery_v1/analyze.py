"""Score finite mechanisms on executable markout labels. Not strategy PnL. Not a Full Strategy freeze."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.profitable_move_mechanism_discovery_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    DEVELOPMENT_DAYS,
    EVENT_DAY_N_MIN,
    EVENT_N_MIN,
    FIXED_HORIZONS,
    FULL_CAUSAL_PORTFOLIO_RUN,
    FULL_STRATEGY_FROZEN,
    INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    POSITIVE_BLOCK_MIN,
    PRIMARY_DISCOVERY_HORIZON,
    Q_VALUE_H5_MAX,
    TOP_MECHANISM_N,
)
from research.profitable_move_mechanism_discovery_v1.harvest import AUDIT, harvest_all
from research.profitable_move_mechanism_discovery_v1.isolation import OUT
from research.profitable_move_mechanism_discovery_v1.library import candidate_library, evaluate_row
from research.profitable_move_mechanism_discovery_v1.predicates import CUTPOINT_KIND, SOURCE_IDENTITY
from research.profitable_move_mechanism_discovery_v1.prior_use import classify_library
from research.profitable_move_mechanism_discovery_v1.spec import dumps_sha256, pin_parent_v3
from research.profitable_move_mechanism_discovery_v1.stats import bh_correct, sign_flip_p_one_sided
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS

PNL_CLAIM_KEYS = frozenset(
    {
        "TOTAL_PNL",
        "PF",
        "STRATEGY_PNL",
        "PAPER_PNL",
        "CERTIFIED_EDGE",
        "PROFITABLE_STRATEGY",
    }
)


def _mean(xs: list[float]) -> Optional[float]:
    arr = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not arr:
        return None
    return float(np.mean(arr))


def _median(xs: list[float]) -> Optional[float]:
    arr = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not arr:
        return None
    return float(np.median(arr))


def _block_of(day: str) -> str | None:
    for bid, days in FOLD_BLOCKS.items():
        if str(day) in days:
            return str(bid)
    return None


def attach_excess(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("complete_triple"):
            continue
        groups[(str(r["date"]), float(r["T_i"]))].append(r)
    out: list[dict[str, Any]] = []
    for _key, chunk in groups.items():
        med: dict[int, float] = {}
        for h in FIXED_HORIZONS:
            vals = [float(r[f"markout_yen100_h{h}"]) for r in chunk]
            med[int(h)] = float(np.median(vals))
        for r in chunk:
            rec = dict(r)
            for h in FIXED_HORIZONS:
                rec[f"excess_yen100_h{h}"] = float(r[f"markout_yen100_h{h}"]) - float(med[int(h)])
                rec[f"universe_median_yen100_h{h}"] = float(med[int(h)])
            out.append(rec)
    return out


def _mask(rows: list[dict[str, Any]], cand: dict[str, Any]) -> np.ndarray:
    hit = np.zeros(len(rows), dtype=bool)
    for i, r in enumerate(rows):
        hit[i] = bool(evaluate_row(cand, dict(r.get("preds") or {}), dict(r.get("preds_prev") or {})))
    return hit


def _horizon_stats(rows: list[dict[str, Any]], hit: np.ndarray, h: int) -> dict[str, Any]:
    yen = [float(rows[i][f"markout_yen100_h{h}"]) for i, v in enumerate(hit) if v]
    exc = [float(rows[i][f"excess_yen100_h{h}"]) for i, v in enumerate(hit) if v]
    bps = [float(rows[i][f"markout_bps_h{h}"]) for i, v in enumerate(hit) if v]
    return {
        "n": int(len(yen)),
        "mean_markout_yen100": _mean(yen),
        "median_markout_yen100": _median(yen),
        "mean_excess_yen100": _mean(exc),
        "median_excess_yen100": _median(exc),
        "mean_markout_bps": _mean(bps),
        "median_markout_bps": _median(bps),
    }


def _daily_excess_h5(rows: list[dict[str, Any]], hit: np.ndarray) -> dict[str, float]:
    bucket: dict[str, list[float]] = defaultdict(list)
    for i, v in enumerate(hit):
        if not v:
            continue
        bucket[str(rows[i]["date"])].append(float(rows[i]["excess_yen100_h5"]))
    return {d: float(np.mean(vs)) for d, vs in bucket.items()}


def _daily_total_excess_h5(rows: list[dict[str, Any]], hit: np.ndarray) -> dict[str, float]:
    bucket: dict[str, list[float]] = defaultdict(list)
    for i, v in enumerate(hit):
        if not v:
            continue
        bucket[str(rows[i]["date"])].append(float(rows[i]["excess_yen100_h5"]))
    return {d: float(np.sum(vs)) for d, vs in bucket.items()}


def _block_mean_excess_h5(rows: list[dict[str, Any]], hit: np.ndarray) -> dict[str, Optional[float]]:
    bucket: dict[str, list[float]] = {b: [] for b in FOLD_BLOCKS}
    for i, v in enumerate(hit):
        if not v:
            continue
        b = _block_of(str(rows[i]["date"]))
        if b:
            bucket[b].append(float(rows[i]["excess_yen100_h5"]))
    return {b: (_mean(vs) if vs else None) for b, vs in bucket.items()}


def score_one(cand: dict[str, Any], rows: list[dict[str, Any]], hit: np.ndarray) -> dict[str, Any]:
    days = sorted({str(rows[i]["date"]) for i, v in enumerate(hit) if v})
    daily_mean = _daily_excess_h5(rows, hit)
    daily_total = _daily_total_excess_h5(rows, hit)
    perm = sign_flip_p_one_sided(np.asarray([daily_mean[d] for d in days], dtype=float)) if days else {
        "p": 1.0,
        "obs_mean": float("nan"),
        "n_days": 0,
        "iters": 0,
    }
    best_day = None
    rest_total = None
    if daily_total:
        best_day = max(daily_total.items(), key=lambda kv: (kv[1], kv[0]))[0]
        rest_total = float(sum(v for d, v in daily_total.items() if d != best_day))
    blocks = _block_mean_excess_h5(rows, hit)
    pos_block_n = int(sum(1 for v in blocks.values() if v is not None and float(v) > 0))
    hstats = {int(h): _horizon_stats(rows, hit, int(h)) for h in FIXED_HORIZONS}
    event_n = int(hit.sum())
    return {
        "MECHANISM_ID": cand["MECHANISM_ID"],
        "TEMPLATE": cand["TEMPLATE"],
        "PRIMITIVE_N": cand["PRIMITIVE_N"],
        "ONSET_OF": cand.get("ONSET_OF"),
        "DEFINITION": cand["DEFINITION"],
        "EVENT_N": event_n,
        "EVENT_DAY_N": int(len(days)),
        "event_days": days,
        "h3": hstats[3],
        "h5": hstats[5],
        "h10": hstats[10],
        "daily_mean_excess_h5": daily_mean,
        "daily_total_excess_h5": daily_total,
        "blocks_mean_excess_h5": blocks,
        "H5_POSITIVE_BLOCK_N": pos_block_n,
        "H5_BEST_DAY": best_day,
        "H5_EX_BEST_DAY_TOTAL_EXCESS": rest_total,
        "H5_PERM_P": perm.get("p"),
        "H5_PERM_OBS_MEAN": perm.get("obs_mean"),
        "H5_PERM_N_DAYS": perm.get("n_days"),
    }


def apply_gates(row: dict[str, Any], q_h5: float) -> dict[str, Any]:
    h3 = dict(row.get("h3") or {})
    h5 = dict(row.get("h5") or {})
    h10 = dict(row.get("h10") or {})
    m5 = h5.get("mean_markout_yen100")
    e5 = h5.get("mean_excess_yen100")
    m3 = h3.get("mean_markout_yen100")
    m10 = h10.get("mean_markout_yen100")
    e3 = h3.get("mean_excess_yen100")
    e10 = h10.get("mean_excess_yen100")
    rest = row.get("H5_EX_BEST_DAY_TOTAL_EXCESS")
    d1 = int(row.get("EVENT_N") or 0) >= int(EVENT_N_MIN)
    d2 = int(row.get("EVENT_DAY_N") or 0) >= int(EVENT_DAY_N_MIN)
    d3 = m5 is not None and float(m5) > 0
    d4 = e5 is not None and float(e5) > 0
    d5 = m3 is not None and float(m3) >= 0
    d6 = m10 is not None and float(m10) >= 0
    d7 = e3 is not None and float(e3) >= 0
    d8 = e10 is not None and float(e10) >= 0
    d9 = int(row.get("H5_POSITIVE_BLOCK_N") or 0) >= int(POSITIVE_BLOCK_MIN)
    d10 = rest is not None and float(rest) > 0
    d11 = q_h5 == q_h5 and float(q_h5) <= float(Q_VALUE_H5_MAX)
    gates = {
        "D1_EVENT_N": d1,
        "D2_EVENT_DAY_N": d2,
        "D3_MEAN_ABS_H5_GT0": d3,
        "D4_MEAN_EXCESS_H5_GT0": d4,
        "D5_MEAN_ABS_H3_GE0": d5,
        "D6_MEAN_ABS_H10_GE0": d6,
        "D7_MEAN_EXCESS_H3_GE0": d7,
        "D8_MEAN_EXCESS_H10_GE0": d8,
        "D9_POS_BLOCK_N": d9,
        "D10_EX_BEST_DAY": d10,
        "D11_BH_Q_H5": d11,
    }
    return {
        **gates,
        "PASS_D1_D11": all(gates.values()),
        "Q_VALUE_H5": float(q_h5) if q_h5 == q_h5 else None,
    }


def _pos_all_horizons(row: dict[str, Any]) -> bool:
    e3 = (row.get("h3") or {}).get("mean_excess_yen100")
    e5 = (row.get("h5") or {}).get("mean_excess_yen100")
    e10 = (row.get("h10") or {}).get("mean_excess_yen100")
    return e3 is not None and e5 is not None and e10 is not None and float(e3) > 0 and float(e5) > 0 and float(e10) > 0


def _min_excess(row: dict[str, Any]) -> float:
    vals = []
    for h in ("h3", "h5", "h10"):
        v = (row.get(h) or {}).get("mean_excess_yen100")
        if v is None:
            return float("-inf")
        vals.append(float(v))
    return float(min(vals))


def rank_key(row: dict[str, Any]) -> tuple:
    return (
        0 if _pos_all_horizons(row) else 1,
        -_min_excess(row),
        -int(row.get("H5_POSITIVE_BLOCK_N") or 0),
        -(float(row.get("H5_EX_BEST_DAY_TOTAL_EXCESS") or 0.0)),
        int(row.get("PRIMITIVE_N") or 99),
        int(row.get("TEMPLATE_RANK") or 99),
        str(row.get("MECHANISM_ID") or ""),
    )


def already_executed_check(src_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("source_sha256") or "") == str(src_hash) and prev.get("decision"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


def _walk_forbidden(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in PNL_CLAIM_KEYS:
                raise RuntimeError(f"PNL_CLAIM_KEY {path}.{k}")
            _walk_forbidden(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_forbidden(v, f"{path}[{i}]")


def decide(*, source_hash: str = "", use_cache: bool = True) -> dict[str, Any]:
    pin = pin_parent_v3()
    if not pin.get("ok"):
        raise RuntimeError(f"PARENT_V3_PIN_FAIL {pin}")
    lib = candidate_library()
    prior = classify_library(lib)
    prior_map = {str(r["MECHANISM_ID"]): r for r in prior}
    days = harvest_all(use_cache=use_cache, source_hash=source_hash)
    raw_rows: list[dict[str, Any]] = []
    for d in days:
        raw_rows.extend(list(d.get("snapshots") or []))
    rows = attach_excess(raw_rows)
    scored: list[dict[str, Any]] = []
    for cand in lib:
        hit = _mask(rows, cand)
        rec = score_one(cand, rows, hit)
        rec["TEMPLATE_RANK"] = cand["TEMPLATE_RANK"]
        rec["PRIMITIVES"] = cand["PRIMITIVES"]
        rec["FAMILIES"] = cand["FAMILIES"]
        rec["SOURCE_IDENTITIES"] = cand["SOURCE_IDENTITIES"]
        rec.update(prior_map[str(cand["MECHANISM_ID"])])
        scored.append(rec)
    pvals = [float(r.get("H5_PERM_P") if r.get("H5_PERM_P") is not None else 1.0) for r in scored]
    qvals = bh_correct(pvals)
    gated: list[dict[str, Any]] = []
    for rec, q in zip(scored, qvals):
        g = apply_gates(rec, float(q))
        rec = {**rec, **g}
        gated.append(rec)
    coverage_n = int(sum(1 for r in gated if r["D1_EVENT_N"] and r["D2_EVENT_DAY_N"]))
    passing = [r for r in gated if r["PASS_D1_D11"]]
    eligible = [r for r in passing if r.get("ELIGIBLE_AS_NEW_ARCHITECTURE")]
    closed_only = [r for r in passing if r.get("LINEAGE_CLASS") == "A_EXACT_CLOSED_STRATEGY_MATCH"]
    ranked = sorted(eligible, key=rank_key)
    top = ranked[: int(TOP_MECHANISM_N)]
    selected = dict(top[0]) if top else None
    if selected:
        selected["DISCOVERY_RESULT_HASH"] = dumps_sha256(
            {
                "MECHANISM_ID": selected["MECHANISM_ID"],
                "DEFINITION": selected["DEFINITION"],
                "TEMPLATE": selected["TEMPLATE"],
                "PRIMITIVES": selected["PRIMITIVES"],
                "h3": selected["h3"],
                "h5": selected["h5"],
                "h10": selected["h10"],
                "EVENT_N": selected["EVENT_N"],
                "EVENT_DAY_N": selected["EVENT_DAY_N"],
                "Q_VALUE_H5": selected["Q_VALUE_H5"],
                "H5_PERM_P": selected["H5_PERM_P"],
            }
        )
    if eligible:
        verdict, nxt, case = CASE_A, NEXT_A, "A"
    elif closed_only and not eligible:
        verdict, nxt, case = CASE_B, NEXT_B, "B"
    else:
        verdict, nxt, case = CASE_C, NEXT_C, "C"
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "CASE": case,
        "PASS_D1_D11_N": int(len(passing)),
        "PASSING_IDS": [str(r["MECHANISM_ID"]) for r in passing],
        "ELIGIBLE_NON_CLOSED_N": int(len(eligible)),
        "EXACT_CLOSED_PASS_N": int(len(closed_only)),
        "SELECTED_MECHANISM_ID": None if selected is None else str(selected["MECHANISM_ID"]),
        "SELECTED_EXACT_CLOSED_MATCH": False if selected is None else bool(selected.get("EXACT_PRIOR_TEST_MATCH")),
        "FULL_STRATEGY_FROZEN": FULL_STRATEGY_FROZEN,
        "FULL_CAUSAL_PORTFOLIO_RUN": FULL_CAUSAL_PORTFOLIO_RUN,
        "STRATEGY_PNL_CLAIMED": False,
        "INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT": INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT,
        "TERMINOLOGY": "PROFITABLE_MOVE_MECHANISM",
    }
    pack = {
        "pin": pin,
        "objective_alignment": {
            "PRIMARY_GOAL": "Discover causal profitable-move mechanisms from sealed DEV before Full Strategy construction.",
            "THIS_IS": "PROFITABLE_MOVE_MECHANISM_DISCOVERY",
            "THIS_IS_NOT": [
                "strategy PnL optimization",
                "ENTRY x fixed EXIT optimization",
                "architecture inventory",
                "indicator threshold search",
                "V4 rescue",
                "Pullback rescue",
                "CSB rescue",
            ],
            "DISCOVERY_REPLACES_ARCHITECTURE_FIRST": True,
            "INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT": False,
            "STOP_IF_GOAL_MISMATCH": True,
        },
        "data": {
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "MAX_RESEARCH_DATE": "20260807",
            "HOLDOUT_READ_N": int(AUDIT["HOLDOUT_BURNED_READ_N"]),
            "STRESS_READ_N": int(AUDIT["STRESS_READ_N"]),
            "FUTURE_DATE_READ_N": int(AUDIT["FUTURE_DATA_N"]),
            "FEATURE_LOOKAHEAD_N": int(AUDIT["FEATURE_LOOKAHEAD_N"]),
            "LABEL_USED_AS_INPUT_N": int(AUDIT["LABEL_USED_AS_INPUT_N"]),
            "day_universe_n": {str(d["date"]): int(d.get("universe_n") or 0) for d in days},
            "day_complete_n": {str(d["date"]): int(d.get("complete_n") or 0) for d in days},
        },
        "outcome_semantics": {
            "ENTRY_REFERENCE": "first causal fresh valid Ask1 at/after T_i (standing timer snap then later ask_ok)",
            "EXIT_REFERENCE_H": "first causal fresh valid Bid1 at/after T_i+H within that 1m interval",
            "BOARD_FRESHNESS_SEC": 5.0,
            "FIXED_HORIZONS": list(FIXED_HORIZONS),
            "PRIMARY_DISCOVERY_HORIZON": int(PRIMARY_DISCOVERY_HORIZON),
            "HORIZON_SEARCH": False,
            "STRATEGY_EXIT_USED_FOR_DISCOVERY": False,
            "H10_TARGET_AFTER_1129_FORBIDDEN": True,
            "MARKOUT_YEN100_H": "(Bid_H - Ask_entry) * 100",
            "EXCESS_MARKOUT_H": "symbol MARKOUT_H - same T_i frozen-universe median MARKOUT_H",
            "LABEL_LOOKAHEAD_USED_ONLY_FOR_OUTCOME": True,
        },
        "predicate_library": [
            {"PREDICATE_ID": k, "SOURCE": SOURCE_IDENTITY[k], "CUTPOINT_KIND": CUTPOINT_KIND[k]}
            for k in SOURCE_IDENTITY
        ],
        "candidate_library": lib,
        "prior_use": prior,
        "coverage_rows": [
            {
                "MECHANISM_ID": r["MECHANISM_ID"],
                "EVENT_N": r["EVENT_N"],
                "EVENT_DAY_N": r["EVENT_DAY_N"],
                "COVERAGE_OK": bool(r["D1_EVENT_N"] and r["D2_EVENT_DAY_N"]),
            }
            for r in gated
        ],
        "scored": gated,
        "passing": passing,
        "selection_ranked": top,
        "selected": selected,
        "decision": decision,
        "raw_snapshot_n": int(len(raw_rows)),
        "complete_snapshot_n": int(len(rows)),
        "RAW_PREDICATE_N": int(len(SOURCE_IDENTITY)),
        "CANDIDATE_MECHANISM_N": int(len(lib)),
        "EXACT_PRIOR_MATCH_CANDIDATE_N": int(sum(1 for r in prior if r.get("EXACT_PRIOR_TEST_MATCH"))),
        "COVERAGE_QUALIFIED_N": coverage_n,
        "harvest_flags": [d.get("flags") for d in days],
        "AUDIT": dict(AUDIT),
    }
    _walk_forbidden(pack)
    return pack


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    sel = pack.get("selected") if isinstance(pack.get("selected"), dict) else None
    data = dict(pack.get("data") or {})
    h3 = dict((sel or {}).get("h3") or {})
    h5 = dict((sel or {}).get("h5") or {})
    h10 = dict((sel or {}).get("h10") or {})
    return {
        "1_discovery_replaces_architecture_first": True,
        "2_information_family_inventory_as_primary_next": False,
        "3_DEV_days_exact": list(DEVELOPMENT_DAYS),
        "4_Holdout_read": False,
        "5_Stress_read": False,
        "6_future_dates_read": False,
        "7_feature_lookahead_n": 0,
        "8_label_lookahead_only_outcome": True,
        "9_label_ever_used_as_predicate": False,
        "10_entry_reference": "first causal fresh valid Ask1 at/after T_i using X1 AskTime-then-ingress freshness 5.0s",
        "11_H3_H5_H10_semantics": "first causal fresh valid Bid1 at/after T_i+H within that target 1m interval; missing if none; H10 target after 11:29 forbidden",
        "12_horizon_search": False,
        "13_raw_predicate_n": int(pack.get("RAW_PREDICATE_N") or 0),
        "14_candidate_mechanism_n": int(pack.get("CANDIDATE_MECHANISM_N") or 0),
        "15_new_threshold_search": False,
        "16_exact_prior_match_candidate_n": int(pack.get("EXACT_PRIOR_MATCH_CANDIDATE_N") or 0),
        "17_coverage_qualified_mechanism_n": int(pack.get("COVERAGE_QUALIFIED_N") or 0),
        "18_D1_D11_pass_mechanism_n": int(d.get("PASS_D1_D11_N") or 0),
        "19_passing_mechanism_ids": list(d.get("PASSING_IDS") or []),
        "20_selected_abs_markouts": None
        if sel is None
        else {
            "H3": h3.get("mean_markout_yen100"),
            "H5": h5.get("mean_markout_yen100"),
            "H10": h10.get("mean_markout_yen100"),
        },
        "21_selected_excess_markouts": None
        if sel is None
        else {
            "H3": h3.get("mean_excess_yen100"),
            "H5": h5.get("mean_excess_yen100"),
            "H10": h10.get("mean_excess_yen100"),
        },
        "22_H5_positive_block_n": None if sel is None else sel.get("H5_POSITIVE_BLOCK_N"),
        "23_H5_ex_best_day_excess": None if sel is None else sel.get("H5_EX_BEST_DAY_TOTAL_EXCESS"),
        "24_H5_permutation_p": None if sel is None else sel.get("H5_PERM_P"),
        "25_H5_BH_q": None if sel is None else sel.get("Q_VALUE_H5"),
        "26_selected_exact_closed_match": False if sel is None else bool(sel.get("EXACT_PRIOR_TEST_MATCH")),
        "27_nearest_closed_lineage": None if sel is None else sel.get("NEAREST_CLOSED_LINEAGE"),
        "28_same_information_new_decision_mechanism": None
        if sel is None
        else sel.get("LINEAGE_CLASS") == "B_SAME_INFORMATION_NEW_DECISION_MECHANISM",
        "29_selected_mechanism_id": d.get("SELECTED_MECHANISM_ID"),
        "30_Full_Strategy_frozen": False,
        "31_Full_Causal_portfolio_run": False,
        "32_strategy_PnL_claimed": False,
        "33_Runtime_changed": False,
        "34_submit_cancel_live": "0/0/0",
        "35_VERDICT": d.get("VERDICT"),
        "36_NEXT": d.get("NEXT"),
        "HOLDOUT_READ_N": int(data.get("HOLDOUT_READ_N") or 0),
        "STRESS_READ_N": int(data.get("STRESS_READ_N") or 0),
        "FUTURE_DATE_READ_N": int(data.get("FUTURE_DATE_READ_N") or 0),
    }
