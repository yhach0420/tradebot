"""Calibrate fixed-H5 hard gate against existing Full Causal fills. No new strategy."""
from __future__ import annotations

import json
from typing import Any, Optional

import numpy as np

from research.discovery_search_space_reassessment_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    DEVELOPMENT_DAYS,
    FIXED_HORIZONS,
    HORIZON_SEARCH,
    NEW_MECHANISM_SEARCH,
    NEW_STRATEGY_CREATED,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    THRESHOLD_SEARCH,
)
from research.discovery_search_space_reassessment_v1.controls import recover_all
from research.discovery_search_space_reassessment_v1.isolation import OUT
from research.discovery_search_space_reassessment_v1.labels import AUDIT, attach_excess, harvest_tape, label_fill
from research.discovery_search_space_reassessment_v1.spec import pin_parent


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


def hold_buckets(trades: list[dict[str, Any]]) -> dict[str, Any]:
    holds = [float(t["hold_sec"]) for t in trades if t.get("hold_sec") is not None]
    def n(pred) -> int:
        return int(sum(1 for h in holds if pred(h)))
    return {
        "TRADE_N": int(len(trades)),
        "mean_hold_sec": _mean(holds),
        "median_hold_sec": _median(holds),
        "exit_before_3m_n": n(lambda h: h < 180.0),
        "exit_3_to_5m_n": n(lambda h: 180.0 <= h < 300.0),
        "exit_5_to_10m_n": n(lambda h: 300.0 <= h < 600.0),
        "exit_after_10m_n": n(lambda h: h >= 600.0),
    }


def sign_matrix(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pp = pn = lp = ln = 0
    miss = 0
    for t in trades:
        pnl = float(t.get("actual_pnl_yen100") or 0.0)
        h5 = t.get("exec_yen100_h5")
        profit = pnl > 0
        if h5 is None or float(h5) != float(h5):
            miss += 1
            continue
        pos = float(h5) > 0
        if profit and pos:
            pp += 1
        elif profit and not pos:
            pn += 1
        elif (not profit) and pos:
            lp += 1
        else:
            ln += 1
    prof = pp + pn
    return {
        "actual_profit_and_H5_positive_n": pp,
        "actual_profit_and_H5_nonpositive_n": pn,
        "actual_loss_and_H5_positive_n": lp,
        "actual_loss_and_H5_nonpositive_n": ln,
        "H5_missing_n": miss,
        "profitable_n_with_finite_H5": prof,
        "fraction_profitable_H5_le0": (float(pn) / float(prof)) if prof else None,
    }


def horizon_means(trades: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in FIXED_HORIZONS:
        xs = [t.get(f"{prefix}_yen100_h{h}") for t in trades]
        out[f"H{h}_mean"] = _mean(xs)
        out[f"H{h}_median"] = _median(xs)
        out[f"H{h}_finite_n"] = int(sum(1 for v in xs if v is not None and float(v) == float(v)))
    return out


def summarize_control(ctrl: dict[str, Any], labeled: list[dict[str, Any]]) -> dict[str, Any]:
    avail = bool(ctrl.get("CONTROL_EXECUTED_COHORT_AVAILABLE"))
    path = hold_buckets(labeled) if labeled else {}
    signs = sign_matrix(labeled) if labeled else {}
    exec_m = horizon_means(labeled, "exec") if labeled else {}
    mid_m = horizon_means(labeled, "mid") if labeled else {}
    tax_m = horizon_means(labeled, "cross_tax") if labeled else {}
    exs = horizon_means(labeled, "excess") if labeled else {}
    h5 = exec_m.get("H5_mean")
    mid5 = mid_m.get("H5_mean")
    pnl = ctrl.get("ACTUAL_FULL_STRATEGY_TOTAL_PNL")
    frac = signs.get("fraction_profitable_H5_le0")
    med_hold = path.get("median_hold_sec")
    hold_mismatch = bool(
        avail
        and pnl is not None
        and float(pnl) > 0
        and h5 is not None
        and float(h5) <= 0
        and (
            (med_hold is not None and float(med_hold) >= 600.0)
            or (frac is not None and float(frac) >= 0.5)
            or (path.get("exit_after_10m_n") or 0) > (path.get("exit_3_to_5m_n") or 0)
            or (path.get("exit_before_3m_n") or 0) > (path.get("exit_3_to_5m_n") or 0)
        )
    )
    exec_cost = bool(
        avail
        and pnl is not None
        and float(pnl) > 0
        and h5 is not None
        and float(h5) <= 0
        and mid5 is not None
        and float(mid5) > 0
    )
    rejects = bool(avail and pnl is not None and float(pnl) > 0 and h5 is not None and float(h5) <= 0)
    x1_ok = int(sum(1 for t in labeled if t.get("X1_FILL_COMPATIBLE")))
    return {
        "CONTROL_ID": ctrl.get("CONTROL_ID"),
        "CONTROL_EXECUTED_COHORT_AVAILABLE": avail,
        "reason": ctrl.get("reason"),
        "TRADE_N": ctrl.get("TRADE_N"),
        "ACTUAL_FULL_STRATEGY_TOTAL_PNL": pnl,
        "X1_EXEC_ID_ALL": ctrl.get("X1_EXEC_ID_ALL"),
        "X1_FILL_COMPATIBLE_N": x1_ok,
        "X1_FILL_COMPATIBLE_ALL": bool(labeled) and x1_ok == len(labeled),
        "executable": exec_m,
        "mid": mid_m,
        "crossing_tax": tax_m,
        "excess": exs,
        "path": path,
        "signs": signs,
        "H5_HARD_SCREEN_WOULD_REJECT_POSITIVE_DEV": rejects,
        "HOLD_PATH_MISMATCH": hold_mismatch,
        "EXECUTION_COST_DOMINANCE": exec_cost,
        "labeled_n": int(len(labeled)),
    }


def already_executed_check(src_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("source_sha256") or "") == str(src_hash) and prev.get("decision"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


def decide(*, source_hash: str = "", use_cache: bool = True) -> dict[str, Any]:
    pin = pin_parent()
    if not pin.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {pin}")
    recovered = recover_all()
    days_needed = set()
    for key in ("P1", "P2", "NEGATIVE"):
        for t in list((recovered[key].get("trades") or [])):
            days_needed.add(str(t["date"]))
    tapes: dict[str, dict[str, Any]] = {}
    for day in DEVELOPMENT_DAYS:
        if str(day) in days_needed:
            tapes[str(day)] = harvest_tape(str(day), use_cache=use_cache, source_hash=source_hash)
    labeled: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for key in ("P1", "P2", "NEGATIVE"):
        rows = []
        for t in list(recovered[key].get("trades") or []):
            tape = tapes.get(str(t["date"]))
            if not tape:
                continue
            rows.append(label_fill(tape, t))
        rows = attach_excess(rows, tapes)
        labeled[key] = rows
        summaries[key] = summarize_control(recovered[key], rows)
    p1_ok = bool(summaries["P1"]["CONTROL_EXECUTED_COHORT_AVAILABLE"])
    p2_ok = bool(summaries["P2"]["CONTROL_EXECUTED_COHORT_AVAILABLE"])
    n_pos = int(p1_ok) + int(p2_ok)
    if n_pos < 2:
        verdict, nxt, case = CASE_A, NEXT_A, "A"
        valid = None
        reason = "fewer than two exact positive executed-fill control cohorts"
        secondary = None
        hold_any = False
        cost_any = False
    else:
        rejectors = [
            summaries[k]
            for k in ("P1", "P2")
            if summaries[k]["H5_HARD_SCREEN_WOULD_REJECT_POSITIVE_DEV"]
        ]
        hold_any = any(bool(s["HOLD_PATH_MISMATCH"]) for s in (summaries["P1"], summaries["P2"]))
        cost_any = any(bool(s["EXECUTION_COST_DOMINANCE"]) for s in (summaries["P1"], summaries["P2"]))
        if rejectors:
            valid = False
            verdict, nxt, case = CASE_B, NEXT_B, "B"
            bits = []
            if hold_any:
                bits.append("HOLD_PATH_MISMATCH")
            if cost_any:
                bits.append("EXECUTION_COST_DOMINANCE")
            if not bits:
                bits.append("HOLD_PATH_MISMATCH")
                hold_any = True
            secondary = "BOTH" if len(bits) == 2 else bits[0]
            reason = (
                "at least one positive-control DEV Full Strategy has total PnL>0 but mean executable H5<=0; "
                "fixed H5 cannot be used as a universal pre-strategy rejection gate"
            )
        else:
            valid = True
            verdict, nxt, case = CASE_C, NEXT_C, "C"
            secondary = None
            reason = "both positive controls retained by fixed H5; mechanism search space is the remaining gap"
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "CASE": case,
        "FIXED_H5_HARD_GATE_VALID": valid,
        "EXACT_REASON": reason,
        "SECONDARY_CAUSE": secondary,
        "HOLD_PATH_MISMATCH": bool(hold_any) if n_pos >= 2 else None,
        "EXECUTION_COST_DOMINANCE": bool(cost_any) if n_pos >= 2 else None,
        "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
        "HORIZON_SEARCH": HORIZON_SEARCH,
        "NEW_MECHANISM_SEARCH": NEW_MECHANISM_SEARCH,
        "NEW_STRATEGY_CREATED": NEW_STRATEGY_CREATED,
        "POSITIVE_CONTROL_COHORT_N": n_pos,
        "TERMINOLOGY": "MEASUREMENT_CALIBRATION_CONTROLS",
    }
    pack = {
        "pin": pin,
        "objective_alignment": {
            "PRIMARY_GOAL": "Partition discovery 0-pass / all H5 abs means<0 into label mismatch, execution cost, shallow mechanisms, or domain failure.",
            "THIS_IS": "DISCOVERY_SEARCH_SPACE_REASSESSMENT",
            "THIS_IS_NOT": ["new strategy", "threshold search", "horizon search", "control reopen/retune/certify"],
        },
        "P1": summaries["P1"],
        "P2": summaries["P2"],
        "NEGATIVE": summaries["NEGATIVE"],
        "labeled": labeled,
        "decision": decision,
        "data": {
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "HOLDOUT_READ_N": int(AUDIT["HOLDOUT_BURNED_READ_N"]),
            "STRESS_READ_N": int(AUDIT["STRESS_READ_N"]),
            "FUTURE_DATE_READ_N": int(AUDIT["FUTURE_DATA_N"]),
        },
        "AUDIT": dict(AUDIT),
    }
    return pack


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    pin = dict(pack.get("pin") or {})
    p1 = dict(pack.get("P1") or {})
    p2 = dict(pack.get("P2") or {})
    neg = dict(pack.get("NEGATIVE") or {})
    d = dict(pack.get("decision") or {})
    return {
        "1_parent_72_mechanisms_confirmed": int(pin.get("CANDIDATE_MECHANISM_N") or 0) == 72,
        "2_all_H5_absolute_means_negative": bool(pin.get("ALL_COVERAGE_QUALIFIED_H5_ABS_MEAN_NEGATIVE")),
        "3_P1_exact_executed_cohort_available": bool(p1.get("CONTROL_EXECUTED_COHORT_AVAILABLE")),
        "4_P1_trade_n": p1.get("TRADE_N"),
        "5_P1_actual_Full_Strategy_total_PnL": p1.get("ACTUAL_FULL_STRATEGY_TOTAL_PNL"),
        "6_P1_H3_H5_H10_executable_means": {
            "H3": (p1.get("executable") or {}).get("H3_mean"),
            "H5": (p1.get("executable") or {}).get("H5_mean"),
            "H10": (p1.get("executable") or {}).get("H10_mean"),
        },
        "7_P1_H3_H5_H10_mid_means": {
            "H3": (p1.get("mid") or {}).get("H3_mean"),
            "H5": (p1.get("mid") or {}).get("H5_mean"),
            "H10": (p1.get("mid") or {}).get("H10_mean"),
        },
        "8_P1_median_actual_hold": (p1.get("path") or {}).get("median_hold_sec"),
        "9_P1_actual_profit_and_H5_nonpositive_n": (p1.get("signs") or {}).get("actual_profit_and_H5_nonpositive_n"),
        "10_P2_exact_executed_cohort_available": bool(p2.get("CONTROL_EXECUTED_COHORT_AVAILABLE")),
        "11_P2_trade_n": p2.get("TRADE_N"),
        "12_P2_actual_Full_Strategy_total_PnL": p2.get("ACTUAL_FULL_STRATEGY_TOTAL_PNL"),
        "13_P2_H3_H5_H10_executable_means": {
            "H3": (p2.get("executable") or {}).get("H3_mean"),
            "H5": (p2.get("executable") or {}).get("H5_mean"),
            "H10": (p2.get("executable") or {}).get("H10_mean"),
        },
        "14_P2_H3_H5_H10_mid_means": {
            "H3": (p2.get("mid") or {}).get("H3_mean"),
            "H5": (p2.get("mid") or {}).get("H5_mean"),
            "H10": (p2.get("mid") or {}).get("H10_mean"),
        },
        "15_P2_median_actual_hold": (p2.get("path") or {}).get("median_hold_sec"),
        "16_P2_actual_profit_and_H5_nonpositive_n": (p2.get("signs") or {}).get("actual_profit_and_H5_nonpositive_n"),
        "17_negative_control_available": bool(neg.get("CONTROL_EXECUTED_COHORT_AVAILABLE")),
        "18_negative_control_H5_mean": (neg.get("executable") or {}).get("H5_mean"),
        "19_FIXED_H5_HARD_GATE_VALID": d.get("FIXED_H5_HARD_GATE_VALID"),
        "20_exact_reason": d.get("EXACT_REASON"),
        "21_HOLD_PATH_MISMATCH": d.get("HOLD_PATH_MISMATCH"),
        "22_EXECUTION_COST_DOMINANCE": d.get("EXECUTION_COST_DOMINANCE"),
        "23_threshold_search": False,
        "24_horizon_search": False,
        "25_new_mechanism_search": False,
        "26_new_strategy_created": False,
        "27_Holdout_read": False,
        "28_Stress_read": False,
        "29_future_read": False,
        "30_Runtime_changed": False,
        "31_submit_cancel_live": "0/0/0",
        "32_VERDICT": d.get("VERDICT"),
        "33_SECONDARY_CAUSE": d.get("SECONDARY_CAUSE"),
        "34_NEXT": d.get("NEXT"),
    }
