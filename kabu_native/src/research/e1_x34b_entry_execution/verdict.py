"""Verdict + optional ENTRY_V3 / ROUTER freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    EXEC_POLICY_SHA,
    VERDICT_BASELINE,
    VERDICT_DUAL,
    VERDICT_NONE,
    VERDICT_PASSIVE_ONLY,
    WAIT_PASSIVE_SEC,
)


def decide_verdict(
    *,
    cross: dict[str, Any],
    baselines: dict[str, Any],
    lodo: dict[str, Any],
    selected_per_fold: dict[str, Any],
) -> dict[str, Any]:
    b2 = baselines.get("B2_PASSIVE_ALL") or {}
    routed = cross.get("opp_w_ret600")
    passive_all = b2.get("opp_w_ret600")
    ss = cross.get("ss_balanced_ret600")
    pf = cross.get("pf_equiv_600")
    pos_days = cross.get("positive_days") or 0
    n_days = cross.get("n_days") or 0

    gates = {
        "opp_w_ret600_gt0": routed is not None and routed > 0,
        "ss_balanced_gt0": ss is not None and ss > 0,
        "pf_gt1": pf is not None and pf > 1.0,
        "positive_days_ge9": pos_days >= 9 and n_days >= 14,
        "lodo_majority": bool(lodo.get("majority_positive")),
        "no_severe_symbol_conc": not bool(cross.get("severe_symbol_concentration")),
        "routed_ge_passive_all": (
            routed is not None
            and passive_all is not None
            and routed + 1e-12 >= passive_all
        ),
    }
    any_rule = any(v is not None for v in selected_per_fold.values())
    failed = [k for k, v in gates.items() if not v]

    if not any_rule:
        return {
            "verdict": VERDICT_NONE,
            "gates": gates,
            "all_gates": False,
            "freeze": False,
            "reason": "no outer-fold rule selected",
        }

    if failed:
        # Had rules but failed CV gates → no robust cost-aware ENTRY
        # If ROUTED never beats/matches PASSIVE_ALL and passive baseline works → BASELINE_ONLY
        if (
            passive_all is not None
            and passive_all > 0
            and (routed is None or routed < passive_all - 0.01)
        ):
            return {
                "verdict": VERDICT_BASELINE,
                "gates": gates,
                "all_gates": False,
                "freeze": False,
                "reason": f"failed gates {failed}; PASSIVE_ALL remains stronger baseline",
            }
        return {
            "verdict": VERDICT_NONE,
            "gates": gates,
            "all_gates": False,
            "freeze": False,
            "reason": f"failed gates {failed}",
        }

    agg_c = float(cross.get("agg_route_contrib_600") or 0.0)
    pas_c = float(cross.get("pas_route_contrib_600") or 0.0)
    n_agg = int(cross.get("aggressive_count") or 0)
    routed_gt = routed is not None and passive_all is not None and routed > passive_all + 1e-9

    if routed_gt and n_agg > 0 and agg_c > 0 and pas_c > 0:
        return {
            "verdict": VERDICT_DUAL,
            "gates": gates,
            "all_gates": True,
            "strong_success": True,
            "freeze": True,
            "reason": "ROUTED > PASSIVE_ALL; both AGG and PASSIVE routes contribute",
        }
    if n_agg == 0 or agg_c <= 0:
        return {
            "verdict": VERDICT_PASSIVE_ONLY,
            "gates": gates,
            "all_gates": True,
            "freeze": True,
            "reason": "gates passed; AGGRESSIVE route adds no positive value",
        }
    return {
        "verdict": VERDICT_PASSIVE_ONLY if not routed_gt else VERDICT_DUAL,
        "gates": gates,
        "all_gates": True,
        "freeze": True,
        "reason": "success gates passed",
    }


def freeze_manifests(
    *,
    selected_per_fold: dict[str, Any],
    cross: dict[str, Any],
    verdict: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = {
        "manifest_id": "ENTRY_V3_MANIFEST",
        "role": "cost_aware_absolute_rise_entry_v3_research",
        "verdict": verdict,
        "observation_anchor": "NEUTRAL_FIXED_CLOCK_ANCHOR_V1",
        "outer_selected_rules": selected_per_fold,
        "primary_objective": "OPPORTUNITY_NET_600 after SKIP/AGG/PASSIVE",
        "no_runtime_reflect": True,
        "features": "pre-entry board path + microstructure only; fill outcome forbidden",
        "cross_fitted_opp600": cross.get("opp_w_ret600"),
        "cross_fitted_ss600": cross.get("ss_balanced_ret600"),
    }
    raw = json.dumps(entry, sort_keys=True, default=str).encode()
    entry["sha256"] = hashlib.sha256(raw).hexdigest()

    router = {
        "manifest_id": "ENTRY_EXECUTION_ROUTER_V1",
        "allowed_modes": ["SKIP", "AGGRESSIVE_ASK_NOW", "PASSIVE_BID_CONSERVATIVE"],
        "passive_contract_sha": EXEC_POLICY_SHA,
        "passive_wait_sec": WAIT_PASSIVE_SEC,
        "passive_fill_evidence": "ASK_CROSS_CONSERVATIVE",
        "not_all_entry_force_passive": True,
        "outer_selected_rules": selected_per_fold,
        "no_runtime_reflect": True,
        "no_execution_param_tuning": True,
    }
    raw2 = json.dumps(router, sort_keys=True, default=str).encode()
    router["sha256"] = hashlib.sha256(raw2).hexdigest()
    return entry, router
