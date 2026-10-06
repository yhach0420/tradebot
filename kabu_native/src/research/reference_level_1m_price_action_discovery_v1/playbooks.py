"""A priori mechanistically distinct playbooks. Selection uses path separation, not PnL mining."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.reference_level_1m_price_action_discovery_v1 import (
    EVAL_BLOCKS,
    MAX_PLAYBOOKS,
    MIN_DAY_N,
    MIN_EVENT_N,
    MIN_PLAYBOOKS_TARGET,
    MIN_SYMBOL_N,
)

PLAYBOOK_SPECS: tuple[dict[str, Any], ...] = (
    {
        "playbook_id": "PB_PDH_BREAK",
        "level_id": "PDH",
        "event_kind": "BREAK_ABOVE",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "Prior-day high is crossed on a closing basis; buyers are attempting to take the overnight supply boundary.",
        "interpretation": "PDH is yesterday's auction high. A close through it is a causal claim that overnight supply is no longer capping price.",
        "exit": "Close back below the same PDH after entry.",
    },
    {
        "playbook_id": "PB_PDH_BREAK_ACCEPT2",
        "level_id": "PDH",
        "event_kind": "ACCEPT2_ABOVE",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "PDH break then two subsequent completed closes still above PDH. Entry at the second accept bar, not the break bar.",
        "interpretation": "Two post-break closes above PDH is online acceptance that the supply boundary has flipped to demand.",
        "exit": "Close back below the accepted PDH.",
    },
    {
        "playbook_id": "PB_PDH_RETEST_HOLD",
        "level_id": "PDH",
        "event_kind": "RETEST_HOLD_ABOVE",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "After accepted PDH break, price returns to the level and holds above. Sellers fail to recapture PDH.",
        "interpretation": "Prior-day supply was crossed, retested, and buyers retained control. That is a different claim than the initial break.",
        "exit": "Close back below PDH after the hold.",
    },
    {
        "playbook_id": "PB_PDL_FAIL_RECLAIM",
        "level_id": "PDL",
        "event_kind": "FAILED_BREAK_BELOW",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "Price breaks previous-day low then fails to accept below; the breakdown bar's subsequent close is back above PDL.",
        "interpretation": "Yesterday's demand floor was probed and sellers could not keep price below it. Buyers reclaimed the boundary.",
        "exit": "Close back below the reclaimed PDL.",
    },
    {
        "playbook_id": "PB_OR15_BREAK_ACCEPT2",
        "level_id": "OR15H",
        "event_kind": "ACCEPT2_ABOVE",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "Opening-range 15-minute high break plus two subsequent closes still above OR15 high.",
        "interpretation": "The first 15 minutes defined a known range. Acceptance above that high is a continuation of the opening auction, not a wick.",
        "exit": "Close back below OR15 high.",
    },
    {
        "playbook_id": "PB_OR15_FALSE_BREAK_RECLAIM",
        "level_id": "OR15L",
        "event_kind": "FAILED_BREAK_BELOW",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "OR15 low is broken then immediately failed; price reclaims the range low.",
        "interpretation": "A false breakdown of the opening range returns inventory to the range. Thesis is that the OR15 low holds as demand.",
        "exit": "Close back below OR15 low.",
    },
    {
        "playbook_id": "PB_GAP_FILL_RECLAIM",
        "level_id": "GAP_UP_PDC",
        "event_kind": "GAP_FILL_RECLAIM",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "A gap-up fully trades to previous close, then price reclaims back above PDC.",
        "interpretation": "Overnight gap inventory was filled; reclaiming PDC says the fill was a test, not a continuation through yesterday's close.",
        "exit": "Close back below PDC after the reclaim.",
    },
    {
        "playbook_id": "PB_VWAP_RECLAIM",
        "level_id": "VWAP",
        "event_kind": "VWAP_RECLAIM",
        "extra": None,
        "exit_kind": "vwap_loss",
        "thesis": "Close crosses back above session VWAP from below. Historical R11 clue, without last-event clustering or R11 thresholds.",
        "interpretation": "Session VWAP is the volume-weighted participant cost. Reclaim is a change in location versus that cost.",
        "exit": "Close back at or below session VWAP.",
    },
    {
        "playbook_id": "PB_VWAP_RECLAIM_NEAR_PDH",
        "level_id": "VWAP",
        "event_kind": "VWAP_RECLAIM",
        "extra": "near_pdh",
        "exit_kind": "vwap_loss",
        "thesis": "VWAP reclaim while price is within 20bps of previous-day high. Tests whether VWAP remains special next to a prior-day boundary.",
        "interpretation": "Two known levels coincide: participant cost and yesterday's high. A reclaim here is not an isolated VWAP event.",
        "exit": "Close back at or below session VWAP.",
    },
    {
        "playbook_id": "PB_D20H_BREAK_ACCEPT2",
        "level_id": "D20H",
        "event_kind": "ACCEPT2_ABOVE",
        "extra": None,
        "exit_kind": "lose_level",
        "thesis": "Break of the completed prior 20-day high plus two subsequent closes still above that high.",
        "interpretation": "A 20-day high is a multi-session supply boundary built only from finished days. Acceptance is a regime claim, not a 1-minute wick.",
        "exit": "Close back below the 20-day high.",
    },
)


def match_playbook(ev: dict[str, Any], spec: dict[str, Any]) -> bool:
    if str(ev.get("level_id") or "") != str(spec["level_id"]):
        return False
    if str(ev.get("event_kind") or "") != str(spec["event_kind"]):
        return False
    extra = spec.get("extra")
    if extra == "near_pdh":
        return bool(ev.get("near_pdh"))
    if extra == "isolated_vwap":
        return (not ev.get("near_pdh")) and (not ev.get("near_pdl"))
    return True


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def path_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"event_n": 0, "day_n": 0, "symbol_n": 0}
    n = len(rows)
    cont = sum(1 for r in rows if r.get("continuation"))
    rev = sum(1 for r in rows if r.get("reversal"))
    stall = sum(1 for r in rows if r.get("stall"))
    fav = sum(1 for r in rows if r.get("favorable_first"))
    adv = sum(1 for r in rows if r.get("adverse_first"))
    mfe = [float(r["mfe_bps"]) for r in rows if _finite(r.get("mfe_bps"))]
    mae = [float(r["mae_bps"]) for r in rows if _finite(r.get("mae_bps"))]
    return {
        "event_n": n,
        "day_n": len({str(r["date"]) for r in rows}),
        "symbol_n": len({str(r["symbol"]) for r in rows}),
        "continuation_n": cont,
        "reversal_n": rev,
        "stall_n": stall,
        "continuation_rate": cont / n,
        "reversal_rate": rev / n,
        "stall_rate": stall / n,
        "favorable_first_rate": fav / n,
        "adverse_first_rate": adv / n,
        "median_mfe": float(np.median(mfe)) if mfe else None,
        "median_mae": float(np.median(mae)) if mae else None,
    }


def _sep(a: dict[str, Any], b: dict[str, Any] | None) -> float:
    if not b or not a.get("event_n") or not b.get("event_n"):
        return float(a.get("continuation_rate") or 0.0)
    return abs(float(a.get("continuation_rate") or 0.0) - float(b.get("continuation_rate") or 0.0)) + abs(
        float(a.get("favorable_first_rate") or 0.0) - float(b.get("favorable_first_rate") or 0.0)
    )


def select_playbooks(playbook_rows: list[dict[str, Any]]) -> dict[str, Any]:
    eval_set = {str(b) for b in EVAL_BLOCKS}
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in playbook_rows:
        if str(r.get("block") or "") in eval_set:
            by_id[str(r["playbook_id"])].append(r)
    stats = {pid: path_stats(xs) for pid, xs in by_id.items()}
    nested = {
        "PB_PDH_BREAK_ACCEPT2": "PB_PDH_BREAK",
        "PB_PDH_RETEST_HOLD": "PB_PDH_BREAK_ACCEPT2",
        "PB_VWAP_RECLAIM_NEAR_PDH": "PB_VWAP_RECLAIM",
    }
    scored = []
    for spec in PLAYBOOK_SPECS:
        pid = spec["playbook_id"]
        st = stats.get(pid) or {"event_n": 0, "day_n": 0, "symbol_n": 0}
        floor_ok = (
            int(st.get("event_n") or 0) >= MIN_EVENT_N
            and int(st.get("day_n") or 0) >= MIN_DAY_N
            and int(st.get("symbol_n") or 0) >= MIN_SYMBOL_N
        )
        parent = nested.get(pid)
        parent_st = stats.get(parent) if parent else None
        scored.append(
            {
                **spec,
                "stats_d2d3": st,
                "floor_ok": floor_ok,
                "path_separation_vs_parent": _sep(st, parent_st) if parent_st else None,
                "score": (_sep(st, parent_st) if parent_st else float(st.get("continuation_rate") or 0.0))
                + abs(float(st.get("favorable_first_rate") or 0.0) - 0.5),
            }
        )
    eligible = [s for s in scored if s["floor_ok"]]
    eligible.sort(key=lambda s: (-float(s["score"]), str(s["playbook_id"])))
    selected = eligible[:MAX_PLAYBOOKS]
    if len(selected) < MIN_PLAYBOOKS_TARGET:
        selected = eligible[:]
    return {
        "all": scored,
        "selected_ids": [s["playbook_id"] for s in selected],
        "selected": selected,
        "eligible_n": len(eligible),
        "selected_n": len(selected),
        "selection_used_pnl": False,
        "selection_used_path_separation": True,
    }
