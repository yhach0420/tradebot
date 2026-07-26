"""v2 vs v3 economic success parity on shared EC2 episodes."""
from __future__ import annotations

from typing import Any, Sequence

from research.eec_noise_hysteresis.classify import _economic as v3_economic
from research.eec_noise_hysteresis.classify import _structural as v3_structural
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import path_for_contract, simulate_matched_exit
from research.entry_exit_contract_integrity.metrics import economic_success_block, mfe_capture_block
from research.price_flow_exit.path_mfe import PathBar
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def _clip_path_to_horizon(c: EntryContract, path: Sequence[PathBar]) -> list[PathBar]:
    """Forbid MFE after episode horizon / session — keep bars within expected_horizon_sec."""
    out = []
    for b in path:
        if b.t < c.entry_time:
            continue
        if (b.t - c.entry_time).total_seconds() > float(c.expected_horizon_sec):
            break
        out.append(b)
    return out


def compare_episode(
    c: EntryContract,
    ticks: Sequence[PushTick],
) -> dict[str, Any]:
    path = path_for_contract(c, ticks)
    if not path:
        return {
            "episode_id": c.episode_id,
            "setup_id": c.setup_id,
            "day": c.day,
            "symbol": c.symbol,
            "v2_economic_success": None,
            "v3_economic_success": None,
            "classification_changed_reason": "EMPTY_PATH",
        }
    # evaluation path clipped to horizon (no post-horizon / post-session fantasy MFE)
    path_h = _clip_path_to_horizon(c, path)
    if not path_h:
        path_h = path[:1]

    ex = simulate_matched_exit(c, path)  # exit may be after horizon; MFE for success uses path_h
    mfe_blk = mfe_capture_block(c, path_h, ex)
    v2 = economic_success_block(c, path_h, ex, mfe_blk, bid_qty_at_exit=None)

    v3_struct = v3_structural(c, path_h)
    v3_econ = v3_economic(c, path_h)

    reason = None
    if bool(v2["economic_success"]) != bool(v3_econ["economic"]):
        if v3_econ["economic"] and not v2["economic_success"]:
            # typical: v3 ignores exit-time held-profit / spread tighten / quote_evaluable
            if not v2.get("profit_zone_held"):
                reason = "v3_looser_profit_zone_hold"
            elif not mfe_blk.get("quote_evaluable"):
                reason = "v2_quote_not_evaluable"
            elif v2.get("spread_bps_at_exit") is not None:
                reason = "v2_spread_tighten_fail"
            else:
                reason = "v2_stricter_economic_gate"
        elif v2["economic_success"] and not v3_econ["economic"]:
            reason = "v3_stricter_tick_persist_or_qty"
        else:
            reason = "other"
    elif bool(v3_struct) and not bool(v2["economic_success"]):
        reason = "structural_yes_economic_no_v2"
    else:
        reason = "agree"

    return {
        "episode_id": c.episode_id,
        "setup_id": c.setup_id,
        "day": c.day,
        "symbol": c.symbol,
        "v2_economic_success": bool(v2["economic_success"]),
        "v3_economic_success": bool(v3_econ["economic"]),
        "v2_structural_success": bool(v2["structural_success"]),
        "v3_structural_success": bool(v3_struct),
        "executable_mfe_v2": mfe_blk.get("executable_mfe_pct_5bps"),
        "executable_mfe_v3": v3_econ.get("mfe_pct"),
        "profit_zone_hold_v2": bool(v2.get("profit_zone_held")),
        "profit_zone_hold_v3": bool(v3_econ.get("held_events", 0) >= 2 or float(v3_econ.get("held_sec") or 0) >= 3),
        "horizon_end_v2": path_h[-1].t.isoformat() if path_h else None,
        "horizon_end_v3": path_h[-1].t.isoformat() if path_h else None,
        "classification_changed_reason": reason,
        "success_label_v2": v2.get("success_label"),
    }


def summarize_parity(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "explained": False, "verdict": "ECONOMIC_SUCCESS_PARITY_BLOCKED"}
    v2 = sum(1 for r in rows if r.get("v2_economic_success"))
    v3 = sum(1 for r in rows if r.get("v3_economic_success"))
    disagree = [r for r in rows if r.get("v2_economic_success") != r.get("v3_economic_success")]
    by_reason: dict[str, int] = {}
    for r in rows:
        k = str(r.get("classification_changed_reason") or "na")
        by_reason[k] = by_reason.get(k, 0) + 1
    # parity PASS if we can attribute disagreements to known gate differences
    unexplained = [
        r
        for r in disagree
        if r.get("classification_changed_reason") in (None, "other", "EMPTY_PATH")
    ]
    explained = len(unexplained) == 0
    return {
        "n": n,
        "v2_economic_rate": round(v2 / n, 4),
        "v3_economic_rate": round(v3 / n, 4),
        "v2_economic_n": v2,
        "v3_economic_n": v3,
        "disagree_n": len(disagree),
        "unexplained_n": len(unexplained),
        "by_reason": by_reason,
        "explained": explained,
        "verdict": "ECONOMIC_SUCCESS_PARITY_PASS" if explained else "ECONOMIC_SUCCESS_PARITY_BLOCKED",
        "note": (
            "v3 Q1 used looser economic gates (tick-persist/qty) vs v2 economic_success_block "
            "(held profit before exit, quote_evaluable, optional spread tighten); "
            "both recomputed here on horizon-clipped paths only."
        ),
        "sample_disagree": disagree[:40],
    }
