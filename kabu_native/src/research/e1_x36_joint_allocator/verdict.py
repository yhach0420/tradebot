"""Verdict + PASSIVE_FIXED600_FULL_STRATEGY_V1 freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    ANCHOR_SHA,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    HORIZON_SEC,
    LODO_MIN_POS_DAYS,
    LOT_QTY,
    NEXT_PASS,
    NEXT_RESEARCH,
    POSITION_CAP,
    VERDICT_ALLOC_FAIL,
    VERDICT_CAP_FAIL,
    VERDICT_NEUTRAL,
    VERDICT_PASS,
    WAIT_SEC,
)


def _robust_ok(sm: dict[str, Any], *, lodo_maj: bool) -> dict[str, Any]:
    gates = {
        "ret_gt0": (sm.get("opp_bps_per_signal") or 0) > 0,
        "total_pnl_gt0": (sm.get("total_pnl_yen") or 0) > 0,
        "pf_gt1": sm.get("pf") is not None and sm["pf"] > 1.0,
        "positive_days_ge9": (sm.get("positive_days") or 0) >= LODO_MIN_POS_DAYS,
        "ss_balanced_gt0": (sm.get("ss_balanced") or 0) > 0,
        "lodo_majority": bool(lodo_maj),
        "no_severe_symbol_conc": not bool(sm.get("severe_symbol_concentration")),
        "no_severe_day_conc": not bool(sm.get("severe_day_concentration")),
        "hard_cap_zero": (sm.get("hard_cap_violations") or 0) == 0,
    }
    return {"pass": all(gates.values()), "gates": gates, "failed": [k for k, v in gates.items() if not v]}


def decide_verdict(
    *,
    cross: dict[str, Any],
    baselines: dict[str, Any],
    selected_per_fold: dict[str, Any],
    lodo: dict[str, Any],
    loso: dict[str, Any],
) -> dict[str, Any]:
    asc = baselines.get("B1_ASC") or {}
    hash_med_pnl = (baselines.get("HASH_DIAG") or {}).get("median_pnl_yen")
    hash_med_opp = (baselines.get("HASH_DIAG") or {}).get("median_opp_bps")

    learned_pnl = cross.get("total_pnl_yen") or 0.0
    learned_opp = cross.get("opp_bps_per_signal") or 0.0
    asc_pnl = asc.get("total_pnl_yen") or 0.0
    asc_opp = asc.get("opp_bps_per_signal") or 0.0

    beat_asc = learned_pnl > asc_pnl + 1e-6 or (
        abs(learned_pnl - asc_pnl) <= 1e-6 and learned_opp > asc_opp + 1e-9
    )
    beat_hash = (
        hash_med_pnl is not None and learned_pnl > float(hash_med_pnl) + 1e-6
    ) or (
        hash_med_pnl is not None
        and abs(learned_pnl - float(hash_med_pnl)) <= 1e-6
        and hash_med_opp is not None
        and learned_opp > float(hash_med_opp) + 1e-9
    )

    rob_l = _robust_ok(cross, lodo_maj=bool(lodo.get("majority_positive")))
    rob_asc = _robust_ok(asc, lodo_maj=(asc.get("positive_days") or 0) > (asc.get("n_days") or 0) / 2.0)

    all_a0 = all(
        (v or {}).get("family") == "A0_ASC" for v in selected_per_fold.values()
    )
    any_learned = any(
        (v or {}).get("family") not in (None, "A0_ASC") for v in selected_per_fold.values()
    )

    # Cap destroys everything
    if (cross.get("hard_cap_violations") or 0) > 0:
        return {
            "verdict": VERDICT_CAP_FAIL,
            "freeze": False,
            "next": NEXT_RESEARCH,
            "reason": "hard cap violations > 0",
            "learned_gates": rob_l,
            "asc_gates": rob_asc,
            "beat_asc": beat_asc,
            "beat_hash_median": beat_hash,
        }

    if not rob_l["pass"] and not rob_asc["pass"]:
        return {
            "verdict": VERDICT_CAP_FAIL,
            "freeze": False,
            "next": NEXT_RESEARCH,
            "reason": f"neither learned nor ASC robust: learned_fail={rob_l['failed']} asc_fail={rob_asc['failed']}",
            "learned_gates": rob_l,
            "asc_gates": rob_asc,
            "beat_asc": beat_asc,
            "beat_hash_median": beat_hash,
        }

    # Neutral sufficient
    if rob_asc["pass"] and (all_a0 or not beat_asc or (rob_l["pass"] and not beat_hash and not any_learned)):
        if rob_asc["pass"] and (not any_learned or not beat_asc or learned_pnl <= asc_pnl + 1e-6):
            return {
                "verdict": VERDICT_NEUTRAL,
                "freeze": True,
                "freeze_as": "NEUTRAL_ASC",
                "next": NEXT_PASS,
                "reason": "neutral ASC admission is robust; ML allocator not required",
                "learned_gates": rob_l,
                "asc_gates": rob_asc,
                "beat_asc": beat_asc,
                "beat_hash_median": beat_hash,
            }

    # Learned PASS
    if rob_l["pass"] and beat_asc and beat_hash and any_learned:
        return {
            "verdict": VERDICT_PASS,
            "freeze": True,
            "freeze_as": "LEARNED_ALLOCATOR",
            "next": NEXT_PASS,
            "reason": "learned allocator beats ASC and hash-median with robustness gates",
            "learned_gates": rob_l,
            "asc_gates": rob_asc,
            "beat_asc": beat_asc,
            "beat_hash_median": beat_hash,
        }

    # ASC robust but learned not clearly better
    if rob_asc["pass"]:
        return {
            "verdict": VERDICT_NEUTRAL,
            "freeze": True,
            "freeze_as": "NEUTRAL_ASC",
            "next": NEXT_PASS,
            "reason": "ASC robust; learned not clearly superior vs ASC/hash",
            "learned_gates": rob_l,
            "asc_gates": rob_asc,
            "beat_asc": beat_asc,
            "beat_hash_median": beat_hash,
        }

    return {
        "verdict": VERDICT_ALLOC_FAIL,
        "freeze": False,
        "next": NEXT_RESEARCH,
        "reason": "entry/exit edge may exist but admission allocator not robust",
        "learned_gates": rob_l,
        "asc_gates": rob_asc,
        "beat_asc": beat_asc,
        "beat_hash_median": beat_hash,
    }


def freeze_manifest(
    *,
    decision: dict,
    selected_per_fold: dict,
    final_allocator: dict | None,
    cross: dict,
) -> dict[str, Any]:
    freeze_as = decision.get("freeze_as")
    if freeze_as == "NEUTRAL_ASC":
        alloc = {
            "type": "NEUTRAL_SYMBOL_ASC",
            "family": "A0_ASC",
            "features": [],
            "model_params": {},
            "training_procedure": "none",
        }
    else:
        alloc = final_allocator or {
            "type": "LEARNED_CROSS_FITTED",
            "selected_per_fold": selected_per_fold,
        }

    body = {
        "manifest_id": "PASSIVE_FIXED600_FULL_STRATEGY_V1",
        "anchor_sha": ANCHOR_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "allocator": alloc,
        "pending_semantics": {
            "reserves_slot": True,
            "expiry_sec": WAIT_SEC,
            "fill_evidence": "ASK_CROSS_CONSERVATIVE",
        },
        "position_cap": POSITION_CAP,
        "lot_qty": LOT_QTY,
        "duplicate_rule": "no_overlap_replace",
        "exit_rule": {
            "horizon_sec": HORIZON_SEC,
            "lookup": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
            "session_close": True,
        },
        "execution_rules": {
            "passive_bid_conservative": True,
            "wait_sec": WAIT_SEC,
            "buy1_qty_min": 100,
            "freshness_max_sec": 5.0,
            "no_special_quote": True,
            "same_session": True,
            "no_mid_exit": True,
            "no_synthetic": True,
        },
        "cross_fitted_total_pnl_yen": cross.get("total_pnl_yen"),
        "cross_fitted_opp_bps": cross.get("opp_bps_per_signal"),
        "research_paper_only": True,
        "runtime_reflect": False,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
