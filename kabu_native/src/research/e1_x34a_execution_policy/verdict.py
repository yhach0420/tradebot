"""Verdict + optional ENTRY_EXECUTION_POLICY_V1 freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    ARM_INSIDE,
    ARM_PASSIVE,
    FILL_EVIDENCE,
    VERDICT_AGGRESSIVE,
    VERDICT_INSUFFICIENT,
    VERDICT_INSIDE,
    VERDICT_PASSIVE,
    WAIT_PRIMARY_SEC,
)


def decide_verdict(
    *,
    aggressive: dict[str, Any],
    passive: dict[str, Any],
    inside: dict[str, Any],
    day_passive: dict[str, Any],
    day_inside: dict[str, Any],
    adverse_passive: dict[str, Any],
    adverse_inside: dict[str, Any],
    conc_passive: dict[str, Any],
    conc_inside: dict[str, Any],
    lodo_passive: dict[str, Any],
    fill_evidence_ok: bool,
) -> dict[str, Any]:
    if not fill_evidence_ok:
        return {
            "verdict": VERDICT_INSUFFICIENT,
            "selected_policy": None,
            "reason": "conservative ask-cross fill evidence not usable / insufficient",
        }

    def _supported(arm_sum, day_st, adverse, conc, name) -> tuple[bool, str]:
        a_opp = aggressive.get("opp_w_ret600")
        p_opp = arm_sum.get("opp_w_ret600")
        a_ss = aggressive.get("opp_w_ret600_symbol_session")
        p_ss = arm_sum.get("opp_w_ret600_symbol_session")
        if a_opp is None or p_opp is None:
            return False, "missing opp_w"
        if not (p_opp > a_opp):
            return False, f"opp_w_ret600 not improved ({p_opp:.4f} vs {a_opp:.4f})"
        if a_ss is None or p_ss is None or not (p_ss > a_ss):
            return False, f"symbol-session balanced not improved ({p_ss} vs {a_ss})"
        if not day_st.get("ok"):
            return False, f"day majority worse ({day_st})"
        if conc.get("severe_concentration"):
            return False, "severe day/symbol concentration"
        if adverse.get("PASSIVE_ADVERSE_SELECTION") and p_opp < a_opp + 1.0:
            # adverse selection flagged and weak edge
            return False, "adverse selection with weak edge"
        return True, f"{name} clears support gates"

    pas_ok, pas_reason = _supported(
        passive, day_passive, adverse_passive, conc_passive, ARM_PASSIVE,
    )
    ins_ok, ins_reason = _supported(
        inside, day_inside, adverse_inside, conc_inside, ARM_INSIDE,
    )

    if pas_ok:
        return {
            "verdict": VERDICT_PASSIVE,
            "selected_policy": ARM_PASSIVE,
            "reason": pas_reason,
            "inside_note": ins_reason,
        }
    if ins_ok:
        return {
            "verdict": VERDICT_INSIDE,
            "selected_policy": ARM_INSIDE,
            "reason": ins_reason,
            "passive_note": pas_reason,
        }
    return {
        "verdict": VERDICT_AGGRESSIVE,
        "selected_policy": None,
        "reason": f"passive: {pas_reason}; inside: {ins_reason}",
        "lodo_passive_mean_adv": lodo_passive.get("mean_advantage600"),
    }


def freeze_policy(
    *,
    mode: str,
    wait_sec: float = WAIT_PRIMARY_SEC,
) -> dict[str, Any]:
    if mode == ARM_PASSIVE:
        limit_rule = "limit_price = Buy1.Price at signal t0"
    elif mode == ARM_INSIDE:
        limit_rule = "limit_price = Buy1.Price + 1 JPX tick (jpx_tick_size_yen), strictly inside spread"
    else:
        raise ValueError(mode)
    body = {
        "manifest_id": "ENTRY_EXECUTION_POLICY_V1",
        "execution_mode": mode,
        "limit_pricing_rule": limit_rule,
        "wait_window_sec": float(wait_sec),
        "fill_evidence_rule": FILL_EVIDENCE,
        "fill_evidence_detail": (
            "future Sell1.Price <= limit_price AND Sell1.Qty >= 100 "
            "AND freshness <= 5s AND special_quote == false AND same session; "
            "fill_price = limit_price (no price improvement); "
            "no queue/last/trade-touch fill assumption"
        ),
        "qty_requirement": 100,
        "freshness_sec": 5.0,
        "special_quote_rule": "blocked (cannot count as fill evidence)",
        "session_rule": "same session as signal; no session cross",
        "primary_metric": "opportunity_weighted_return (unfilled = 0)",
        "no_runtime_reflect": True,
        "research_paper_only": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
