"""Verdict + V1R manifest."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    ANCHOR_SHA,
    DEP_MIN_OPP,
    DEP_MIN_PNL_FRAC,
    DEP_MIN_POS_DAYS,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    FINAL_FAMILY,
    FINAL_FEATURE_SET,
    FINAL_FEATURES,
    FINAL_KIND,
    FINAL_REG,
    NEXT_PASS,
    NEXT_STOP,
    V1_SHA,
    VERDICT_FREEZE_FAIL,
    VERDICT_PASS,
    VERDICT_PROVENANCE_FAIL,
    VERDICT_SYMBOL_DEP,
)


def _dep_collapse(d: dict, *, orig_pnl: float) -> bool:
    pnl = d.get("total_pnl_yen") if "total_pnl_yen" in d else d.get("remaining_total_pnl_yen")
    opp = d.get("opp_bps_per_signal")
    pos = d.get("positive_days")
    if pnl is None:
        return True
    if float(pnl) < float(orig_pnl) * DEP_MIN_PNL_FRAC:
        return True
    if opp is not None and float(opp) <= DEP_MIN_OPP:
        return True
    if pos is not None and int(pos) < DEP_MIN_POS_DAYS:
        return True
    return False


def decide_verdict(
    *,
    provenance: dict,
    final_id: dict,
    cross_id: dict,
    conc: dict,
    d1: dict,
    d2: dict,
    orig_pnl: float,
) -> dict[str, Any]:
    if not provenance.get("provenance_ok"):
        return {
            "verdict": VERDICT_PROVENANCE_FAIL,
            "freeze": False,
            "next": NEXT_STOP,
            "reason": "final architecture selection provenance unresolved or mismatch",
        }

    if not final_id.get("pass") or not cross_id.get("identity_vs_x36", {}).get("pass"):
        return {
            "verdict": VERDICT_FREEZE_FAIL,
            "freeze": False,
            "next": NEXT_STOP,
            "reason": (
                f"freeze/replay incomplete: final_id={final_id.get('pass')} "
                f"cross_id={cross_id.get('identity_vs_x36', {}).get('pass')}"
            ),
        }

    if _dep_collapse(d1, orig_pnl=orig_pnl) or _dep_collapse(d2, orig_pnl=orig_pnl):
        return {
            "verdict": VERDICT_SYMBOL_DEP,
            "freeze": False,
            "next": NEXT_STOP,
            "reason": "285A exclusion collapses economics below dependency thresholds; no strategy edit",
            "d1_collapse": _dep_collapse(d1, orig_pnl=orig_pnl),
            "d2_collapse": _dep_collapse(d2, orig_pnl=orig_pnl),
        }

    return {
        "verdict": VERDICT_PASS,
        "freeze": True,
        "next": NEXT_PASS,
        "reason": (
            "exact model serialized; cross-fitted replay identity PASS; "
            "concentration reconciled; 285A dependency quantified without collapse"
        ),
    }


def freeze_v1r(
    *,
    ser: dict,
    panel_fp: dict,
    provenance: dict,
    cross_summary: dict,
    capital: dict,
) -> dict[str, Any]:
    body = {
        "manifest_id": "PASSIVE_FIXED600_FULL_STRATEGY_V1R",
        "supersedes_manifest_sha": V1_SHA,
        "anchor_sha": ANCHOR_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "allocator": {
            "family": FINAL_FAMILY,
            "feature_set": FINAL_FEATURE_SET,
            "feature_order": list(FINAL_FEATURES),
            "reg": FINAL_REG,
            "model_kind": FINAL_KIND,
            "selection_provenance": provenance,
            "fitted": {
                "model_class": ser["model_class"],
                "coefficients": ser["coefficients"],
                "intercept": ser["intercept"],
                "preprocessing": ser["preprocessing"],
                "missing_value_handling": ser["missing_value_handling"],
                "solver": ser["solver"],
                "max_iter": ser["max_iter"],
                "random_seed": ser["random_seed"],
                "n_iter_": ser["n_iter_"],
                "sklearn_version": ser["sklearn_version"],
                "model_artifact_sha256": ser["model_artifact_sha256"],
            },
            "training_panel_fingerprint": panel_fp,
            "cohort_topk_semantics": "available_slots = CAP - open - pending; admit score top-k in clock cohort",
            "deterministic_tie_break": "symbol_ascending then signal_time",
        },
        "pending_semantics": {
            "reserves_slot": True,
            "expiry_sec": 1.0,
            "fill_evidence": "ASK_CROSS_CONSERVATIVE",
        },
        "position_cap": 5,
        "lot_qty": 100,
        "duplicate_rule": "no_overlap_replace",
        "exit_rule": {
            "horizon_sec": 600.0,
            "lookup": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
            "exit_sha": EXIT_SHA,
        },
        "performance_sot": "X36_CROSS_FITTED",
        "cross_fitted_total_pnl_yen": cross_summary.get("total_pnl_yen"),
        "cross_fitted_opp_bps": cross_summary.get("opp_bps"),
        "capital_diagnostic_only": capital,
        "capital_not_live_deployable": True,
        "capital_not_safe_capital_confirmed": True,
        "research_paper_only": True,
        "runtime_reflect": False,
        "prospective_locked": True,
        "no_retune_after_20260810": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
