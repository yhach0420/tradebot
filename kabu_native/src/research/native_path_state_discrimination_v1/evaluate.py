"""Discrimination and complete-strategy bars. Design evidence only."""
from __future__ import annotations

from typing import Any

from research.native_path_state_discrimination_v1 import (
    MAX_TOP_SYMBOL_POS_SHARE,
    MIN_ABLATION_AUC,
    MIN_AUC,
    MIN_LIFT,
)


def _auc(step: dict[str, Any]) -> float | None:
    return (step.get("tree_metrics") or {}).get("auc")


def _lift(step: dict[str, Any]) -> float | None:
    return (step.get("tree_metrics") or {}).get("lift_at_cutoff")


def discrimination_ok(preq: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    d2a, d3a = _auc(preq.get("D2") or {}), _auc(preq.get("D3") or {})
    d2l, d3l = _lift(preq.get("D2") or {}), _lift(preq.get("D3") or {})
    d23 = (preq.get("D2_D3") or {}).get("auc")
    fams = [(preq.get(b) or {}).get("top_family") for b in ("D1", "D2", "D3")]
    fam_stable = bool((fams[0] and fams[0] == fams[1]) or (fams[1] and fams[1] == fams[2]))
    stable_rules = list(extracted.get("stable_rules") or [])
    auc_ok = bool(d2a is not None and d3a is not None and float(d2a) >= MIN_AUC and float(d3a) >= MIN_AUC)
    lift_ok = bool(d2l is not None and d3l is not None and float(d2l) >= MIN_LIFT and float(d3l) >= MIN_LIFT)
    core_ok = bool(d23 is not None and float(d23) >= MIN_AUC)
    found = bool(stable_rules)
    return {
        "auc_ok": auc_ok,
        "lift_ok": lift_ok,
        "d2_d3_ok": core_ok,
        "family_overlap": fam_stable,
        "stable_rule_n": len(stable_rules),
        "found": found,
        "d2_auc": d2a,
        "d3_auc": d3a,
        "d4_auc": _auc(preq.get("D4") or {}),
        "d2_d3_auc": d23,
        "d2_lift": d2l,
        "d3_lift": d3l,
        "d4_cannot_certify": True,
        "classification_is_not_strategy": True,
    }


def incremental_flags(preq: dict[str, Any]) -> dict[str, Any]:
    inc = dict(preq.get("incremental_auc") or {})
    # D3/D4 are the honest tests of group (D2 rows first carry prequential groups)
    d3 = dict(inc.get("D3") or {})
    d4 = dict(inc.get("D4") or {})

    def pos(key: str) -> bool:
        a = d3.get(key)
        b = d4.get(key)
        return bool((a is not None and float(a) >= MIN_ABLATION_AUC) or (b is not None and float(b) >= MIN_ABLATION_AUC))

    return {
        "behavior_group_adds": pos("group"),
        "sector_relative_adds": pos("sector"),
        "volume_activity_adds": pos("activity"),
        "vwap_adds_after_others": pos("vwap"),
        "deltas_d3": d3,
        "deltas_d4": d4,
        "material_auc_delta": MIN_ABLATION_AUC,
    }


def strategy_survives(pack: dict[str, Any]) -> dict[str, Any]:
    if not pack.get("ok"):
        return {"survives": False, "reason": "no_trades"}
    x1 = pack.get("mean_x1_bps")
    pf = pack.get("profit_factor")
    b = dict(pack.get("block_mean_x0") or {})
    d2 = b.get("D2")
    d3 = b.get("D3")
    d23_ok = bool(
        (d2 is None or float(d2) > 0)
        and (d3 is None or float(d3) > 0)
        and ((pack.get("d2_d3") or {}).get("mean_x0_bps") is None or float((pack.get("d2_d3") or {}).get("mean_x0_bps")) > 0)
    )
    syms = list(pack.get("symbols") or [])
    top_share = max((float(r.get("share_of_positive_pnl") or 0) for r in syms), default=0.0)
    not_one = bool(top_share < MAX_TOP_SYMBOL_POS_SHARE and int(pack.get("symbol_n") or 0) >= 5)
    tail = dict(pack.get("tail") or {})
    drop5 = (tail.get("drop_top_5pct") or {}).get("mean_x0_bps")
    not_tail_only = bool(drop5 is not None and float(drop5) > 0)
    x1_ok = bool(x1 is not None and float(x1) > 0)
    pf_ok = bool(pf is not None and float(pf) > 1)
    survives = bool(x1_ok and pf_ok and d23_ok and not_one and not_tail_only)
    return {
        "survives": survives,
        "A_x1_gt_0": x1_ok,
        "B_pf_gt_1": pf_ok,
        "C_d2_d3_supportive": d23_ok,
        "D_not_one_symbol": not_one,
        "E_not_only_top_winners": not_tail_only,
        "top_symbol_pos_share": top_share,
        "drop_top_5pct_mean_x0": drop5,
        "pooled_mean_alone_not_sufficient": True,
    }
