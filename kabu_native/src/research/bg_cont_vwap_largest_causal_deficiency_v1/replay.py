"""Full event-time Complete Strategy replay. Occupancy depends on each EXIT. Not a static 486 swap."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.behavior_group_sequence_mechanism_v1 import OCCUPANCY, SESSION_FLAT
from research.behavior_group_sequence_mechanism_v1.replay import _select
from research.bg_cont_vwap_largest_causal_deficiency_v1.exits import run_exit
from research.freeze_group_mechanism_definitions_v1.rca import (
    annotate_trade,
    giveback_summary,
    subset_econ,
    symbol_contribution,
    winner_concentration,
)


def replay_exit(events: list[dict[str, Any]], parent_spec: dict[str, Any], *, exit_id: str) -> dict[str, Any]:
    rows = _select(events, parent_spec)
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    occ_at_entry: list[int] = []
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = run_exit(e, exit_id=exit_id, parent_spec=parent_spec)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        occ_at_entry.append(len(occ[day]))
        trade = {
            "date": day,
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "event_time": hh,
            "block": e.get("block"),
            "preq_group": e.get("preq_group"),
            "sequence": parent_spec["sequence"],
            "exit_id": exit_id,
            **{k: v for k, v in got.items() if k != "state_log"},
            "state_log": got.get("state_log"),
            "exit_hh": exit_hh,
            "fwd_bars": fwd,
            "x0_entry_open": e.get("x0_entry_open"),
            "feature_bar": e.get("feature_bar"),
            "available_at": e.get("available_at"),
            "slots_occupied_at_entry": len(occ[day]),
        }
        trades.append(trade)
        occ[day].append(trade)
        traded_today[day].add(str(e["symbol"]))
    annotated = [annotate_trade(t) for t in trades]
    for t in annotated:
        t.pop("fwd_bars", None)
    return summarize(annotated, skipped=skipped, exit_id=exit_id, occ_at_entry=occ_at_entry, candidate_n=len(rows))


def summarize(trades: list[dict[str, Any]], *, skipped: dict[str, int], exit_id: str, occ_at_entry: list[int], candidate_n: int) -> dict[str, Any]:
    empty = {
        "ok": False,
        "exit_id": exit_id,
        "trade_n": 0,
        "skipped": skipped,
        "trades": [],
    }
    if not trades:
        return empty
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[float]] = defaultdict(list)
    holds = [int(t.get("hold_min") or 0) for t in trades]
    for t in trades:
        by_day[str(t["date"])].append(float(t["x0_bps"]))
        if t.get("block"):
            by_block[str(t["block"])].append(float(t["x0_bps"]))
    day_means = np.asarray([float(np.mean(vs)) for vs in by_day.values()], dtype=float)
    day_meds = np.asarray([float(np.median(vs)) for vs in by_day.values()], dtype=float)
    eq = np.cumsum(day_means)
    dd = float(np.min(eq - np.maximum.accumulate(eq))) if eq.size else 0.0
    pos = float(np.sum(x0[x0 > 0]))
    neg = float(-np.sum(x0[x0 < 0]))
    pf = (pos / neg) if neg > 0 else None
    block_mean = {k: float(np.mean(vs)) for k, vs in by_block.items()}
    core = [t for t in trades if str(t.get("block")) in {"D2", "D3"}]
    d23 = subset_econ(core, label="D2_D3") if core else {"label": "D2_D3", "trade_n": 0}
    gb = giveback_summary(trades)
    win = winner_concentration(trades)
    sym = symbol_contribution(trades)
    imm = [t for t in trades if int(t.get("hold_min") or 0) <= 1 and float(t.get("x0_bps") or 0) < 0]
    imm_fail = [t for t in trades if t.get("entry_path_class") == "IMMEDIATE_FAILURE"]
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[str(t.get("exit_reason") or "")] += 1
    return {
        "ok": True,
        "exit_id": exit_id,
        "trade_n": int(x0.size),
        "day_n": len(by_day),
        "symbol_n": len({t["symbol"] for t in trades}),
        "mean_x0_bps": float(np.mean(x0)),
        "mean_x1_bps": float(np.mean(x0) - 8.0),
        "median_x0_bps": float(np.median(x0)),
        "hit_rate": float(np.mean(x0 > 0)),
        "profit_factor": pf,
        "daily_mean_bps": float(np.mean(day_means)) if day_means.size else None,
        "daily_median_bps": float(np.median(day_meds)) if day_meds.size else None,
        "max_dd_daily_mean_bps": dd,
        "mean_hold_min": float(np.mean(holds)) if holds else None,
        "median_hold_min": float(np.median(holds)) if holds else None,
        "block_mean_x0": block_mean,
        "d2_d3": d23,
        "giveback": gb,
        "tail": win,
        "symbols": sym,
        "exclude_8136": subset_econ([t for t in trades if str(t["symbol"]) != "8136"], label="EXCLUDE_8136_DIAGNOSTIC"),
        "only_8136": subset_econ([t for t in trades if str(t["symbol"]) == "8136"], label="ONLY_8136_DIAGNOSTIC"),
        "exit_reasons": dict(reasons),
        "skipped": skipped,
        "candidate_n": candidate_n,
        "occupancy_skips": skipped.get("occupancy"),
        "same_symbol_skips": skipped.get("same_symbol"),
        "mean_slots_at_entry": float(np.mean(occ_at_entry)) if occ_at_entry else None,
        "immediate_loss_n": len(imm),
        "immediate_failure_path_n": len(imm_fail),
        "warning_cancel_n_total": int(sum(int(t.get("warning_cancel_n") or 0) for t in trades)),
        "mfe8_grace_used_n": int(sum(1 for t in trades if t.get("mfe8_grace_used"))),
        "occupancy": OCCUPANCY,
        "same_symbol": "one_live_no_same_day_reentry",
        "session_close": SESSION_FLAT,
        "x1_tax_bps": 8.0,
        "trades": trades,
        "static_counterfactual": False,
        "full_event_time_replay": True,
    }
