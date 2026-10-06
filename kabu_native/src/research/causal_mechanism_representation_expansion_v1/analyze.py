"""Discovery-unit H5 gate, then optional frozen O1/O2/O3 scoring. No Full Strategy. No threshold/horizon search."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.causal_mechanism_representation_expansion_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_PARITY,
    CASE_UNIT,
    DEVELOPMENT_DAYS,
    EVENT_DAY_N_MIN,
    EVENT_N_MIN,
    FIXED_HORIZONS,
    FULL_CAUSAL_PORTFOLIO_RUN,
    FULL_STRATEGY_FROZEN,
    HORIZON_SEARCH,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    NEXT_PARITY,
    NEXT_UNIT,
    POSITIVE_BLOCK_MIN,
    PRIMARY_DISCOVERY_HORIZON,
    Q_VALUE_H5_MAX,
    THRESHOLD_SEARCH,
    TOP_MECHANISM_N,
)
from research.causal_mechanism_representation_expansion_v1.isolation import OUT
from research.causal_mechanism_representation_expansion_v1.labels import AUDIT as LABEL_AUDIT
from research.causal_mechanism_representation_expansion_v1.labels import label_cohort, load_tape
from research.causal_mechanism_representation_expansion_v1.library import candidate_library, classify_library
from research.causal_mechanism_representation_expansion_v1.operators import evaluate_row
from research.causal_mechanism_representation_expansion_v1.reconstruct import AUDIT as RECON_AUDIT
from research.causal_mechanism_representation_expansion_v1.reconstruct import reconstruct_controls
from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256, pin_parents
from research.profitable_move_mechanism_discovery_v1.analyze import apply_gates, attach_excess, rank_key, score_one
from research.profitable_move_mechanism_discovery_v1.harvest import harvest_all
from research.profitable_move_mechanism_discovery_v1.spec import source_sha256 as discovery_source_sha256
from research.profitable_move_mechanism_discovery_v1.stats import bh_correct


def _mean(xs: list[Any]) -> Optional[float]:
    arr = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not arr:
        return None
    return float(np.mean(arr))


def _median(xs: list[Any]) -> Optional[float]:
    arr = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not arr:
        return None
    return float(np.median(arr))


def horizon_means(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in FIXED_HORIZONS:
        xs = [r.get(f"{field}_h{h}") for r in rows]
        out[f"H{h}_mean"] = _mean(xs)
        out[f"H{h}_median"] = _median(xs)
        out[f"H{h}_finite_n"] = int(sum(1 for v in xs if v is not None and float(v) == float(v)))
    return out


def summarize_raw(ctrl_id: str, labeled: list[dict[str, Any]], parity: dict[str, Any]) -> dict[str, Any]:
    abs_m = horizon_means(labeled, "markout_yen100")
    exc_m = horizon_means(labeled, "excess_yen100")
    h5 = abs_m.get("H5_mean")
    return {
        "CONTROL_ID": ctrl_id,
        "RAW_SIGNAL_N": int(parity.get("RAW_SIGNAL_N") or 0),
        "SIGNAL_IDENTITY_PRECISION": parity.get("SIGNAL_IDENTITY_PRECISION"),
        "SIGNAL_IDENTITY_RECALL": parity.get("SIGNAL_IDENTITY_RECALL"),
        "PARITY_PASS": bool(parity.get("PARITY_PASS")),
        "LABELED_N": int(len(labeled)),
        "COMPLETE_TRIPLE_N": int(sum(1 for r in labeled if r.get("complete_triple"))),
        "RAW_SIGNAL_H3_MEAN": abs_m.get("H3_mean"),
        "RAW_SIGNAL_H5_MEAN": h5,
        "RAW_SIGNAL_H10_MEAN": abs_m.get("H10_mean"),
        "RAW_SIGNAL_H3_EXCESS_MEAN": exc_m.get("H3_mean"),
        "RAW_SIGNAL_H5_EXCESS_MEAN": exc_m.get("H5_mean"),
        "RAW_SIGNAL_H10_EXCESS_MEAN": exc_m.get("H10_mean"),
        "RAW_SIGNAL_H5_RECOGNIZED": bool(h5 is not None and float(h5) > 0.0),
        "executable": abs_m,
        "excess": exc_m,
    }


def unit_gate(p1: dict[str, Any], p2: dict[str, Any], pin: dict[str, Any]) -> dict[str, Any]:
    fill1 = pin.get("P1_EXECUTED_H5_MEAN")
    fill2 = pin.get("P2_EXECUTED_H5_MEAN")
    raw1 = p1.get("RAW_SIGNAL_H5_MEAN")
    raw2 = p2.get("RAW_SIGNAL_H5_MEAN")
    rec1 = bool(p1.get("RAW_SIGNAL_H5_RECOGNIZED"))
    rec2 = bool(p2.get("RAW_SIGNAL_H5_RECOGNIZED"))
    mismatch = False
    if fill1 is not None and float(fill1) > 0.0 and (raw1 is None or float(raw1) <= 0.0):
        mismatch = True
    if fill2 is not None and float(fill2) > 0.0 and (raw2 is None or float(raw2) <= 0.0):
        mismatch = True
    both = rec1 and rec2
    valid = bool(both) and not mismatch
    return {
        "P1_RAW_SIGNAL_H5_RECOGNIZED": rec1,
        "P2_RAW_SIGNAL_H5_RECOGNIZED": rec2,
        "RAW_SIGNAL_H5_DISCOVERY_VALID": bool(valid),
        "DISCOVERY_UNIT_VALID": bool(valid),
        "PORTFOLIO_ADMISSION_MATTERS": bool(mismatch),
        "mismatch": bool(mismatch),
    }


def _prev2_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(str(r["date"]), str(r["symbol"]))].append(r)
    out: list[dict[str, Any]] = []
    for _k, xs in groups.items():
        xs = sorted(xs, key=lambda r: (int(r["i"]), float(r["T_i"])))
        for k, rec in enumerate(xs):
            row = dict(rec)
            prev2: dict[str, bool] = {}
            if k >= 2 and int(xs[k - 2]["i"]) == int(rec["i"]) - 2 and int(xs[k - 1]["i"]) == int(rec["i"]) - 1:
                prev2 = dict(xs[k - 2].get("preds") or {})
            elif k >= 1 and int(xs[k - 1]["i"]) == int(rec["i"]) - 1:
                prev2 = dict(xs[k - 1].get("preds_prev") or {})
            row["preds_prev2"] = prev2
            out.append(row)
    return out


def _mask(rows: list[dict[str, Any]], cand: dict[str, Any]) -> np.ndarray:
    hit = np.zeros(len(rows), dtype=bool)
    for i, r in enumerate(rows):
        hit[i] = bool(
            evaluate_row(
                cand,
                dict(r.get("preds") or {}),
                dict(r.get("preds_prev") or {}),
                dict(r.get("preds_prev2") or {}),
            )
        )
    return hit


def _events_to_score_rows(labeled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in labeled:
        if not r.get("complete_triple"):
            continue
        rec = dict(r)
        rec["T_i"] = float(r["signal_t0"])
        out.append(rec)
    return out


def already_executed_check(src_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("source_sha256") or "") == str(src_hash) and prev.get("decision"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


def score_expanded(
    lib: list[dict[str, Any]],
    prior_map: dict[str, dict[str, Any]],
    snap_rows: list[dict[str, Any]],
    o3_labeled: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    o3_rows = {k: _events_to_score_rows(v) for k, v in o3_labeled.items()}
    for cand in lib:
        template = str(cand["TEMPLATE"])
        if template == "O3_RECLAIM_ACCEPT_NEXT":
            rid = str(cand.get("RECLAIM_ID") or "")
            events = o3_rows.get(rid) or []
            hit = np.ones(len(events), dtype=bool)
            rec = score_one(cand, events, hit)
        else:
            hit = _mask(snap_rows, cand)
            rec = score_one(cand, snap_rows, hit)
        rec["TEMPLATE_RANK"] = cand["TEMPLATE_RANK"]
        rec["PRIMITIVES"] = cand["PRIMITIVES"]
        rec["FAMILIES"] = cand["FAMILIES"]
        rec["SOURCE_IDENTITIES"] = cand["SOURCE_IDENTITIES"]
        rec["RECLAIM_ID"] = cand.get("RECLAIM_ID")
        rec.update(prior_map[str(cand["MECHANISM_ID"])])
        scored.append(rec)
    pvals = [float(r.get("H5_PERM_P") if r.get("H5_PERM_P") is not None else 1.0) for r in scored]
    qvals = bh_correct(pvals)
    gated: list[dict[str, Any]] = []
    for rec, q in zip(scored, qvals):
        g = apply_gates(rec, float(q))
        gated.append({**rec, **g})
    return gated


def decide(*, source_hash: str = "", use_cache: bool = True) -> dict[str, Any]:
    pin = pin_parents()
    if not pin.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {pin}")
    lib = candidate_library()
    lib_hash = dumps_sha256(
        [{"MECHANISM_ID": r["MECHANISM_ID"], "TEMPLATE": r["TEMPLATE"], "DEFINITION": r["DEFINITION"]} for r in lib]
    )
    prior = classify_library(lib)
    prior_map = {str(r["MECHANISM_ID"]): r for r in prior}
    recon = reconstruct_controls(use_cache=use_cache)
    new_outcome_read_before = False
    new_outcomes_read = False
    scored: list[dict[str, Any]] | None = None
    p1_lab: list[dict[str, Any]] = []
    p2_lab: list[dict[str, Any]] = []
    snap_n = 0
    complete_n = 0
    if not recon.get("ok"):
        verdict, nxt, case = CASE_PARITY, NEXT_PARITY, "PARITY"
        gate = {
            "P1_RAW_SIGNAL_H5_RECOGNIZED": None,
            "P2_RAW_SIGNAL_H5_RECOGNIZED": None,
            "RAW_SIGNAL_H5_DISCOVERY_VALID": False,
            "DISCOVERY_UNIT_VALID": False,
            "PORTFOLIO_ADMISSION_MATTERS": None,
        }
        p1_sum = summarize_raw(str((recon.get("P1") or {}).get("CONTROL_ID")), [], recon.get("P1") or {})
        p2_sum = summarize_raw(str((recon.get("P2") or {}).get("CONTROL_ID")), [], recon.get("P2") or {})
        reason = "control signal identity precision/recall not both 1.0"
    else:
        days_needed = {str(d) for d in DEVELOPMENT_DAYS}
        tapes: dict[str, dict[str, Any]] = {}
        for day in days_needed:
            tapes[day] = load_tape(day)
        p1_lab = label_cohort(list((recon.get("P1") or {}).get("signals") or []), tapes)
        p2_lab = label_cohort(list((recon.get("P2") or {}).get("signals") or []), tapes)
        p1_sum = summarize_raw(str((recon["P1"]).get("CONTROL_ID")), p1_lab, recon["P1"])
        p2_sum = summarize_raw(str((recon["P2"]).get("CONTROL_ID")), p2_lab, recon["P2"])
        gate = unit_gate(p1_sum, p2_sum, pin)
        if gate.get("PORTFOLIO_ADMISSION_MATTERS") or not gate.get("DISCOVERY_UNIT_VALID"):
            verdict, nxt, case = CASE_UNIT, NEXT_UNIT, "UNIT"
            reason = (
                "at least one positive control has executed-fill H5>0 but raw-signal H5 mean<=0; "
                "fixed H5 does not recognize the mechanism before portfolio admission"
                if gate.get("PORTFOLIO_ADMISSION_MATTERS")
                else "raw-signal H5 mean not >0 on both positive controls"
            )
        else:
            days = harvest_all(use_cache=True, source_hash=discovery_source_sha256())
            raw_rows: list[dict[str, Any]] = []
            for d in days:
                raw_rows.extend(list(d.get("snapshots") or []))
            snap_rows = _prev2_rows(attach_excess(raw_rows))
            snap_n = int(len(raw_rows))
            complete_n = int(len(snap_rows))
            o3_lab = {
                "R1": label_cohort(list(recon.get("r1_signals") or []), tapes),
                "R2": p1_lab,
                "R3": label_cohort(list(recon.get("r3_signals") or []), tapes),
            }
            new_outcomes_read = True
            scored = score_expanded(lib, prior_map, snap_rows, o3_lab)
            passing_tmp = [r for r in scored if r["PASS_D1_D11"]]
            eligible_tmp = [r for r in passing_tmp if r.get("ELIGIBLE_AS_NEW_ARCHITECTURE")]
            closed_tmp = [r for r in passing_tmp if r.get("LINEAGE_CLASS") == "A_EXACT_CLOSED_STRATEGY_MATCH"]
            if eligible_tmp:
                verdict, nxt, case = CASE_A, NEXT_A, "A"
                reason = "eligible expanded operator passed D1-D11"
            elif closed_tmp and not eligible_tmp:
                verdict, nxt, case = CASE_B, NEXT_B, "B"
                reason = "only exact prior-control operators passed D1-D11"
            else:
                verdict, nxt, case = CASE_C, NEXT_C, "C"
                reason = "frozen O1/O2/O3 library produced no D1-D11 pass"
    passing = [r for r in (scored or []) if r.get("PASS_D1_D11")]
    eligible = [r for r in passing if r.get("ELIGIBLE_AS_NEW_ARCHITECTURE")]
    closed_only = [r for r in passing if r.get("LINEAGE_CLASS") == "A_EXACT_CLOSED_STRATEGY_MATCH"]
    ranked = sorted(eligible, key=rank_key)
    top = ranked[: int(TOP_MECHANISM_N)]
    selected = dict(top[0]) if top else None
    coverage_n = int(sum(1 for r in (scored or []) if r.get("D1_EVENT_N") and r.get("D2_EVENT_DAY_N")))
    if selected:
        selected["DISCOVERY_RESULT_HASH"] = dumps_sha256(
            {
                "MECHANISM_ID": selected["MECHANISM_ID"],
                "DEFINITION": selected["DEFINITION"],
                "TEMPLATE": selected["TEMPLATE"],
                "h5": selected.get("h5"),
                "EVENT_N": selected.get("EVENT_N"),
                "Q_VALUE_H5": selected.get("Q_VALUE_H5"),
            }
        )
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "CASE": case,
        "EXACT_REASON": reason,
        "RAW_SIGNAL_H5_DISCOVERY_VALID": gate.get("RAW_SIGNAL_H5_DISCOVERY_VALID"),
        "DISCOVERY_UNIT_VALID": gate.get("DISCOVERY_UNIT_VALID"),
        "PORTFOLIO_ADMISSION_MATTERS": gate.get("PORTFOLIO_ADMISSION_MATTERS"),
        "P1_RAW_SIGNAL_H5_RECOGNIZED": gate.get("P1_RAW_SIGNAL_H5_RECOGNIZED"),
        "P2_RAW_SIGNAL_H5_RECOGNIZED": gate.get("P2_RAW_SIGNAL_H5_RECOGNIZED"),
        "NEW_MECHANISM_OUTCOME_READ_BEFORE_UNIT_GATE": bool(new_outcome_read_before),
        "NEW_MECHANISM_OUTCOMES_READ": bool(new_outcomes_read),
        "LIBRARY_FROZEN_BEFORE_SELECTABLE_OUTCOMES": True,
        "LIBRARY_SHA256": lib_hash,
        "CANDIDATE_MECHANISM_N": int(len(lib)),
        "PASS_D1_D11_N": int(len(passing)),
        "PASSING_IDS": [str(r["MECHANISM_ID"]) for r in passing],
        "ELIGIBLE_NON_CLOSED_N": int(len(eligible)),
        "EXACT_CLOSED_PASS_N": int(len(closed_only)),
        "SELECTED_MECHANISM_ID": None if selected is None else str(selected["MECHANISM_ID"]),
        "COVERAGE_QUALIFIED_N": coverage_n if new_outcomes_read else None,
        "FULL_STRATEGY_FROZEN": FULL_STRATEGY_FROZEN,
        "FULL_CAUSAL_PORTFOLIO_RUN": FULL_CAUSAL_PORTFOLIO_RUN,
        "STRATEGY_PNL_CLAIMED": False,
        "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
        "HORIZON_SEARCH": HORIZON_SEARCH,
        "NEW_STRATEGY_CREATED": False,
        "TERMINOLOGY": "DISCOVERY_UNIT_CALIBRATION then FROZEN_OPERATOR_EXPANSION",
    }
    pack = {
        "pin": pin,
        "objective_alignment": {
            "PRIMARY_GOAL": "Calibrate H5 at the raw-signal discovery unit, then expand representation with frozen O1/O2/O3.",
            "THIS_IS": "CAUSAL_MECHANISM_REPRESENTATION_EXPANSION",
            "THIS_IS_NOT": [
                "CAP optimization",
                "R2 RCA",
                "ST RCA",
                "strategy rerun",
                "threshold search",
                "horizon search",
                "Full Strategy freeze",
            ],
        },
        "library_freeze": {
            "LIBRARY_FROZEN_BEFORE_SELECTABLE_OUTCOMES": True,
            "LIBRARY_SHA256": lib_hash,
            "OPERATORS": ["O1_PERSIST_NEXT", "O2_HANDOFF_NEXT", "O3_RECLAIM_ACCEPT_NEXT"],
            "CANDIDATE_MECHANISM_N": int(len(lib)),
            "O1_N": int(sum(1 for r in lib if r["TEMPLATE"] == "O1_PERSIST_NEXT")),
            "O2_N": int(sum(1 for r in lib if r["TEMPLATE"] == "O2_HANDOFF_NEXT")),
            "O3_N": int(sum(1 for r in lib if r["TEMPLATE"] == "O3_RECLAIM_ACCEPT_NEXT")),
        },
        "P1": p1_sum,
        "P2": p2_sum,
        "gate": gate,
        "candidate_library": lib,
        "prior_use": prior,
        "scored": scored or [],
        "passing": passing,
        "selection_ranked": top,
        "selected": selected,
        "labeled_p1": p1_lab,
        "labeled_p2": p2_lab,
        "decision": decision,
        "data": {
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "MAX_RESEARCH_DATE": "20260807",
            "HOLDOUT_READ_N": int(RECON_AUDIT["HOLDOUT_BURNED_READ_N"] + LABEL_AUDIT["HOLDOUT_BURNED_READ_N"]),
            "STRESS_READ_N": int(RECON_AUDIT["STRESS_READ_N"] + LABEL_AUDIT["STRESS_READ_N"]),
            "FUTURE_DATE_READ_N": int(RECON_AUDIT["FUTURE_DATA_N"] + LABEL_AUDIT["FUTURE_DATA_N"]),
            "FEATURE_LOOKAHEAD_N": 0,
            "raw_snapshot_n": snap_n,
            "complete_snapshot_n": complete_n,
            "PRIMARY_DISCOVERY_HORIZON": int(PRIMARY_DISCOVERY_HORIZON),
            "EVENT_N_MIN": int(EVENT_N_MIN),
            "EVENT_DAY_N_MIN": int(EVENT_DAY_N_MIN),
            "POSITIVE_BLOCK_MIN": int(POSITIVE_BLOCK_MIN),
            "Q_VALUE_H5_MAX": float(Q_VALUE_H5_MAX),
        },
        "AUDIT": {**dict(RECON_AUDIT), **{f"LABEL_{k}": v for k, v in LABEL_AUDIT.items()}},
        "CAP_APPLIED": False,
        "OCCUPANCY_APPLIED": False,
    }
    return pack


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    p1 = dict(pack.get("P1") or {})
    p2 = dict(pack.get("P2") or {})
    d = dict(pack.get("decision") or {})
    pin = dict(pack.get("pin") or {})
    data = dict(pack.get("data") or {})
    sel = pack.get("selected") if isinstance(pack.get("selected"), dict) else None
    scored = list(pack.get("scored") or [])
    return {
        "RAW_P1_SIGNAL_N": p1.get("RAW_SIGNAL_N"),
        "RAW_P1_H5_MEAN": p1.get("RAW_SIGNAL_H5_MEAN"),
        "RAW_P1_H5_EXCESS_MEAN": p1.get("RAW_SIGNAL_H5_EXCESS_MEAN"),
        "RAW_P2_SIGNAL_N": p2.get("RAW_SIGNAL_N"),
        "RAW_P2_H5_MEAN": p2.get("RAW_SIGNAL_H5_MEAN"),
        "RAW_P2_H5_EXCESS_MEAN": p2.get("RAW_SIGNAL_H5_EXCESS_MEAN"),
        "P1_RAW_SIGNAL_H5_RECOGNIZED": d.get("P1_RAW_SIGNAL_H5_RECOGNIZED"),
        "P2_RAW_SIGNAL_H5_RECOGNIZED": d.get("P2_RAW_SIGNAL_H5_RECOGNIZED"),
        "RAW_SIGNAL_H5_DISCOVERY_VALID": d.get("RAW_SIGNAL_H5_DISCOVERY_VALID"),
        "DISCOVERY_UNIT_VALID": d.get("DISCOVERY_UNIT_VALID"),
        "PORTFOLIO_ADMISSION_MATTERS": d.get("PORTFOLIO_ADMISSION_MATTERS"),
        "NEW_MECHANISM_OUTCOME_READ_BEFORE_UNIT_GATE": False,
        "1_parent_discovery_72_0_pass": int(pin.get("CANDIDATE_MECHANISM_N") or 0) == 72
        and pin.get("PASS_D1_D11_N") is not None
        and int(pin.get("PASS_D1_D11_N")) == 0,
        "2_fill_H5_gate_was_valid": bool(pin.get("FIXED_H5_HARD_GATE_VALID")),
        "3_P1_identity_precision": p1.get("SIGNAL_IDENTITY_PRECISION"),
        "4_P1_identity_recall": p1.get("SIGNAL_IDENTITY_RECALL"),
        "5_P2_identity_precision": p2.get("SIGNAL_IDENTITY_PRECISION"),
        "6_P2_identity_recall": p2.get("SIGNAL_IDENTITY_RECALL"),
        "7_P1_raw_H3_H5_H10_means": {
            "H3": p1.get("RAW_SIGNAL_H3_MEAN"),
            "H5": p1.get("RAW_SIGNAL_H5_MEAN"),
            "H10": p1.get("RAW_SIGNAL_H10_MEAN"),
        },
        "8_P2_raw_H3_H5_H10_means": {
            "H3": p2.get("RAW_SIGNAL_H3_MEAN"),
            "H5": p2.get("RAW_SIGNAL_H5_MEAN"),
            "H10": p2.get("RAW_SIGNAL_H10_MEAN"),
        },
        "9_library_frozen_before_outcomes": True,
        "10_candidate_mechanism_n": d.get("CANDIDATE_MECHANISM_N"),
        "11_new_mechanism_outcomes_read": d.get("NEW_MECHANISM_OUTCOMES_READ"),
        "12_D1_D11_pass_n": d.get("PASS_D1_D11_N"),
        "13_passing_ids": list(d.get("PASSING_IDS") or []),
        "14_selected_mechanism_id": d.get("SELECTED_MECHANISM_ID"),
        "15_selected_h5_abs_mean": None if sel is None else (sel.get("h5") or {}).get("mean_markout_yen100"),
        "16_coverage_qualified_n": d.get("COVERAGE_QUALIFIED_N"),
        "17_scored_n": int(len(scored)),
        "18_threshold_search": False,
        "19_horizon_search": False,
        "20_new_strategy_created": False,
        "21_Full_Strategy_frozen": False,
        "22_Holdout_read": False,
        "23_Stress_read": False,
        "24_future_read": False,
        "25_Runtime_changed": False,
        "26_submit_cancel_live": "0/0/0",
        "27_CAP_before_raw_signal_measure": False,
        "28_occupancy_before_raw_signal_measure": False,
        "29_VERDICT": d.get("VERDICT"),
        "30_NEXT": d.get("NEXT"),
        "HOLDOUT_READ_N": int(data.get("HOLDOUT_READ_N") or 0),
        "STRESS_READ_N": int(data.get("STRESS_READ_N") or 0),
        "FUTURE_DATE_READ_N": int(data.get("FUTURE_DATE_READ_N") or 0),
    }
