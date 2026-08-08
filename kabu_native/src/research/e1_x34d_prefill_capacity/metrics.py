"""Economics for U0 / C1 / C2; drag; LODO/LOSO; ordering sensitivity."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.e1_x34c_passive_deployability.metrics import summarize_mode as x34c_summarize

MAX_SYMBOL_CONTRIB = 0.50
HORIZONS = (300, 600, 900)


def summarize_prefill(events: list[dict], *, label: str) -> dict[str, Any]:
    """
    Opportunity denominator = all signals.
    Only prefill accepted fills contribute fill-based ret; else 0.
    """
    # Map accepted flag already set by admission sim
    sm = x34c_summarize(events, mode="deployable", ret_key_prefix="fill_based_ret")
    sm["label"] = label
    # concentration
    by_sym: dict[str, float] = defaultdict(float)
    tot_pos = 0.0
    for e in events:
        if e.get("accepted") and e.get("fill_based_ret_600") is not None:
            v = float(e["fill_based_ret_600"])
            if v > 0:
                by_sym[e["symbol"]] += v
                tot_pos += v
    top_share = float(max(by_sym.values()) / tot_pos) if tot_pos > 1e-12 and by_sym else None
    sm["max_symbol_contrib_share"] = top_share
    sm["severe_symbol_concentration"] = bool(top_share is not None and top_share > MAX_SYMBOL_CONTRIB)
    sm["orders_admitted"] = sum(1 for e in events if e.get("admitted"))
    sm["admission_blocked"] = sum(1 for e in events if e.get("admission_blocked") and e.get("CAPACITY_BLOCKED"))
    sm["duplicate_blocked"] = sum(1 for e in events if e.get("DUPLICATE_BLOCKED"))
    sm["expired"] = sum(1 for e in events if e.get("expired"))
    return sm


def capacity_drag(u0: float | None, c1: float | None, c2: float | None) -> dict[str, Any]:
    def sub(a, b):
        if a is None or b is None:
            return None
        return float(a - b)

    return {
        "U0_to_C1_bps": sub(u0, c1),
        "C1_to_C2_bps": sub(c1, c2),
        "U0_to_C2_bps": sub(u0, c2),
        "U0": u0,
        "C1": c1,
        "C2": c2,
    }


def lodo_prefill(events: list[dict]) -> dict[str, Any]:
    days = sorted({e["date"] for e in events})
    folds = []
    for hold in days:
        sub = [e for e in events if e["date"] != hold]
        hold_rows = [e for e in events if e["date"] == hold]
        sm = summarize_prefill(sub, label="rest")
        hs = summarize_prefill(hold_rows, label="hold")
        folds.append({
            "holdout_day": hold,
            "rest_opp600": sm.get("opp_w_ret600"),
            "holdout_opp600": hs.get("opp_w_ret600"),
        })
    pos = sum(1 for f in folds if (f.get("holdout_opp600") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_holdout_days": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "mean_holdout": float(np.mean([f["holdout_opp600"] for f in folds if f["holdout_opp600"] is not None])) if folds else None,
        "folds": folds,
    }


def loso_prefill(events: list[dict], *, max_symbols: int = 40) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e["symbol"]] += 1
    top = [s for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_symbols]]
    folds = []
    for hold in top:
        sm = summarize_prefill([e for e in events if e["symbol"] != hold], label="rest")
        folds.append({"holdout_symbol": hold, "rest_opp600": sm.get("opp_w_ret600")})
    pos = sum(1 for f in folds if (f.get("rest_opp600") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_folds": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "mean_rest": float(np.mean([f["rest_opp600"] for f in folds if f["rest_opp600"] is not None])) if folds else None,
        "sample": folds[:12],
    }


def ordering_sensitivity(results: dict[str, dict]) -> dict[str, Any]:
    """results: mode -> summarize dict. Detect sign instability."""
    modes = list(results.keys())
    opp = {m: results[m].get("opp_w_ret600") for m in modes}
    signs = []
    for m, v in opp.items():
        if v is None:
            signs.append(0)
        elif v > 0:
            signs.append(1)
        elif v < 0:
            signs.append(-1)
        else:
            signs.append(0)
    # sensitive if any positive and any negative among primary metrics
    has_pos = any(s > 0 for s in signs)
    has_neg = any(s < 0 for s in signs)
    sensitive = has_pos and has_neg
    # also if SS-balanced flips
    ss = {m: results[m].get("ss_balanced_ret600") for m in modes}
    ss_signs = [1 if (v or 0) > 0 else (-1 if (v or 0) < 0 else 0) for v in ss.values()]
    ss_flip = (any(s > 0 for s in ss_signs) and any(s < 0 for s in ss_signs))
    return {
        "opp600_by_mode": opp,
        "ss600_by_mode": ss,
        "pf_by_mode": {m: results[m].get("pf_equiv_600") for m in modes},
        "positive_days_by_mode": {m: results[m].get("positive_days") for m in modes},
        "sign_sensitive_opp600": sensitive,
        "sign_sensitive_ss600": ss_flip,
        "CAPACITY_ADMISSION_ORDER_SENSITIVE": bool(sensitive or ss_flip),
        "note": "diagnostic only - primary remains symbol_ascending; do not pick best",
    }


def day_table(events: list[dict], u0_day: dict | None = None) -> list[dict]:
    by: dict[str, list] = defaultdict(list)
    for e in events:
        by[e["date"]].append(e)
    out = []
    for day in sorted(by):
        sm = summarize_prefill(by[day], label=day)
        row = {
            "date": day,
            "admitted": sm["orders_admitted"],
            "fills": sm["accepted_fills"],
            "opp600": sm["opp_w_ret600"],
            "pf": sm["pf_equiv_600"],
        }
        if u0_day and day in u0_day:
            row["capacity_drag_vs_u0"] = (
                None if sm["opp_w_ret600"] is None else float(u0_day[day] - sm["opp_w_ret600"])
            )
        out.append(row)
    return out
