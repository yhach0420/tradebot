"""At most 2 complete-strategy candidates from high-magnitude episodes. CS1–CS3 not rescued."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

import numpy as np

from research.causal_path_to_complete_strategy_v1.candidates import _exit_from_fwd
from research.higher_magnitude_state_discovery_v1 import (
    MATERIAL_GROSS_BPS,
    MAX_CANDIDATES,
    OCCUPANCY,
    PRIOR_GROSS_BPS_CEILING,
    REJECTED_CS,
    X1_TAX_BPS,
)

ENTRY_CUTOFF = "14:50"
SESSION_FLAT = "15:20"


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _spec_sha(spec: dict[str, Any]) -> str:
    raw = json.dumps({k: v for k, v in spec.items() if k != "spec_sha256"}, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _specs() -> list[dict[str, Any]]:
    rows = [
        {
            "candidate_id": "HM1_CONTROLLED_PULLBACK_RECLAIM",
            "archetype": "CONTROLLED_PULLBACK_RECLAIM",
            "thesis": "Positive structure survives a pullback; VWAP reclaim resumes demand while sector RS is not negative.",
            "event_sequence": "PULLBACK_START → VWAP_RECLAIM (optional preceding SECTOR_RS_IMPROVE / sector_rs>0)",
            "ranking_rule": "contemporaneous rank by (sector_rs + rs_5m); enter if cs_rank<=3 (predeclared occupancy, not Top-K search)",
            "exit_state_machine": "reclaim",
            "technical_invalidation": [
                "VWAP reclaim failure (close back below VWAP)",
                "EMA9<EMA21 persists 2 bars (V27 idea retested, not copied k-bar)",
                "time stop 20m",
                "session flatten 15:20",
            ],
        },
        {
            "candidate_id": "HM2_COMPRESSION_BREAKOUT_EXPANSION",
            "archetype": "COMPRESSION_BREAKOUT_EXPANSION",
            "thesis": "Compression release then 20-bar breakout; participation implied by the episode sequence; sector context carried, not a broad-market strong/weak gate.",
            "event_sequence": "COMPRESSION_RELEASE → BREAKOUT20 (optional sector support)",
            "ranking_rule": "contemporaneous rank by (sector_rs + rs_5m); enter if cs_rank<=3",
            "exit_state_machine": "breakout",
            "technical_invalidation": [
                "breakout-level failure (close back below pre-event 20-bar high)",
                "EMA9<EMA21 persists 2 bars",
                "time stop 20m",
                "session flatten 15:20",
            ],
        },
        {
            "candidate_id": "HM3_SECTOR_LEADER_TRANSITION",
            "archetype": "SECTOR_LEADER_TRANSITION",
            "thesis": "Sector strengthens or is already relatively strong; stock RS improves; local EMA/VWAP/breakout confirmation.",
            "event_sequence": "SECTOR_RS_IMPROVE or sector_rs>0 → RS_IMPROVE → technical (EMA_IMPROVE / VWAP_RECLAIM / BREAKOUT20)",
            "ranking_rule": "contemporaneous rank by (sector_rs + rs_5m); enter if cs_rank<=3",
            "exit_state_machine": "reclaim",
            "technical_invalidation": [
                "VWAP loss",
                "EMA structure-loss persistence 2 bars",
                "time stop 20m",
                "session flatten 15:20",
            ],
        },
    ]
    out = []
    for spec in rows:
        spec.update(
            {
                "entry_availability_time": "episode decision_time = last causal event in 15m cluster + BAR_START T+1m already encoded as event_time",
                "historical_execution_assumption": "X0=next bar open after decision_time; X1=X0-8bps tax (not Bid/Ask)",
                "position_state": "long 1 unit",
                "CAP_semantics": "skip if occupancy==3; slot released on exit",
                "occupancy": OCCUPANCY,
                "same_symbol_behavior": "one live position; no same-day reentry",
                "reentry_behavior": "none_same_day",
                "slot_release": "on_exit",
                "session_close": SESSION_FLAT,
                "copied_cs1_cs3": False,
                "copied_m4_m6": False,
                "kabu_50": False,
                "uses_frozen_validation": False,
                "uses_old_confirmation_to_design": False,
                "not_in": list(REJECTED_CS),
            }
        )
        spec["spec_sha256"] = _spec_sha(spec)
        out.append(spec)
    return out


def replay(
    *,
    episodes: list[dict[str, Any]],
    spec: dict[str, Any],
    date_to_block: dict[str, str] | None = None,
    mode: str = "ranked_top3",
) -> dict[str, Any]:
    arch = spec["archetype"]
    rows = [e for e in episodes if e.get("archetype") == arch and e.get("x0_entry_open")]
    if mode == "absolute_sector_rs_pos":
        rows = [e for e in rows if _finite(e.get("sector_rs")) and float(e["sector_rs"]) > 0]
    elif mode == "ranked_top3":
        rows = [e for e in rows if e.get("cs_rank") is not None and int(e["cs_rank"]) <= int(OCCUPANCY)]
    elif mode == "ranked_top1":
        rows = [e for e in rows if e.get("cs_rank") is not None and int(e["cs_rank"]) <= 1]
    rows.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), int(e.get("cs_rank") or 99), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = _exit_from_fwd(e, spec)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        trade = {
            "date": day,
            "month": day[:6],
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "episode_id": e.get("episode_id"),
            "event_time": hh,
            "block": (date_to_block or {}).get(day),
            "cs_rank": e.get("cs_rank"),
            "sequence_class": e.get("sequence_class"),
            **got,
            "exit_hh": exit_hh,
        }
        trades.append(trade)
        occ[day].append(trade)
        traded_today[day].add(str(e["symbol"]))
    if not trades:
        return {"ok": False, "trade_n": 0, "skipped": skipped, "mode": mode, "spec_sha256": spec.get("spec_sha256")}
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    x1 = np.asarray([float(t["x1_bps"]) for t in trades], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_month: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[float]] = defaultdict(list)
    by_sym: dict[str, float] = defaultdict(float)
    by_sec: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t["date"]].append(float(t["x0_bps"]))
        by_month[t["month"]].append(float(t["x0_bps"]))
        if t.get("block"):
            by_block[str(t["block"])].append(float(t["x0_bps"]))
        if float(t["x0_bps"]) > 0:
            by_sym[str(t["symbol"])] += float(t["x0_bps"])
            by_sec[str(t.get("sector") or "")] += float(t["x0_bps"])
    day_means = np.asarray([float(np.mean(vs)) for vs in by_day.values()], dtype=float)
    eq = np.cumsum(day_means)
    dd = float(np.min(eq - np.maximum.accumulate(eq))) if eq.size else 0.0
    pos = float(np.sum(x0[x0 > 0]))
    neg = float(-np.sum(x0[x0 < 0]))
    pf = (pos / neg) if neg > 0 else None
    top_sec = max(by_sec.items(), key=lambda kv: kv[1]) if by_sec and pos > 0 else (None, 0.0)
    top_sec_share = (top_sec[1] / pos) if pos > 0 and top_sec[0] is not None else None
    sector_specific = bool(top_sec_share is not None and float(top_sec_share) >= 0.55)
    return {
        "ok": True,
        "mode": mode,
        "trade_n": int(x0.size),
        "day_n": len(by_day),
        "symbol_n": len({t["symbol"] for t in trades}),
        "sector_n": len({t.get("sector") for t in trades}),
        "mean_x0_bps": float(np.mean(x0)),
        "mean_x1_bps": float(np.mean(x1)),
        "median_x0_bps": float(np.median(x0)),
        "hit_rate": float(np.mean(x0 > 0)),
        "profit_factor": pf,
        "daily_mean_x0": float(np.mean(day_means)) if day_means.size else None,
        "daily_positive_share": float(np.mean(day_means > 0)) if day_means.size else None,
        "max_dd_daily_mean_bps": dd,
        "top_symbol_share_of_positive_bps": (max(by_sym.values()) / pos) if pos > 0 and by_sym else None,
        "top_sector": top_sec[0],
        "top_sector_share_of_positive_bps": top_sec_share,
        "sector_specific_strategy": sector_specific,
        "month_mean_x0": {k: float(np.mean(vs)) for k, vs in sorted(by_month.items())},
        "block_mean_x0": {k: float(np.mean(vs)) for k, vs in by_block.items()},
        "block_positive_n": sum(1 for vs in by_block.values() if float(np.mean(vs)) > 0),
        "block_n": len(by_block),
        "trades_per_day_mean": float(np.mean([len(vs) for vs in by_day.values()])) if by_day else None,
        "skipped": skipped,
        "exit_reasons": {k: int(sum(1 for t in trades if t.get("exit_reason") == k)) for k in {t.get("exit_reason") for t in trades}},
        "exceeds_prior_1_6bps": bool(float(np.mean(x0)) > PRIOR_GROSS_BPS_CEILING),
        "spec_sha256": spec.get("spec_sha256"),
        "candidate_id": spec.get("candidate_id"),
    }


def promote(econ: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if int(econ.get("trade_n") or 0) < 40:
        reasons.append("trade_n")
    if int(econ.get("day_n") or 0) < 20:
        reasons.append("day_n")
    if int(econ.get("symbol_n") or 0) < 8:
        reasons.append("symbol_n")
    if float(econ.get("mean_x0_bps") or 0) <= PRIOR_GROSS_BPS_CEILING:
        reasons.append("magnitude_not_above_prior_1_6bps")
    if float(econ.get("mean_x0_bps") or 0) < MATERIAL_GROSS_BPS:
        reasons.append("gross_x0_below_material_10bps")
    if float(econ.get("mean_x1_bps") or -999) <= 0:
        reasons.append("mean_x1_not_positive")
    pf = econ.get("profit_factor")
    if pf is None or float(pf) < 1.10:
        reasons.append("profit_factor")
    if float(econ.get("top_symbol_share_of_positive_bps") or 0) >= 0.40:
        reasons.append("symbol_concentration")
    if int(econ.get("block_positive_n") or 0) < 3:
        reasons.append("block_consistency")
    if bool(econ.get("sector_specific_strategy")) and int(econ.get("symbol_n") or 0) < 5:
        reasons.append("sector_specific_too_few_symbols")
    return {
        "promoted": not reasons,
        "fail_reasons": reasons,
        "sector_specific_ok_if_multi_symbol_and_blocks": bool(econ.get("sector_specific_strategy")),
        "did_not_use_day_stability_gate": True,
        "did_not_rescue_cs1_cs3": True,
    }


def build_candidates(*, episodes: list[dict[str, Any]], date_to_block: dict[str, str], rank_ordered: bool) -> dict[str, Any]:
    out = []
    for spec in _specs():
        ranked = replay(episodes=episodes, spec=spec, date_to_block=date_to_block, mode="ranked_top3")
        abs_ = replay(episodes=episodes, spec=spec, date_to_block=date_to_block, mode="absolute_sector_rs_pos")
        top1 = replay(episodes=episodes, spec=spec, date_to_block=date_to_block, mode="ranked_top1")
        gate = promote(ranked)
        out.append(
            {
                "spec": spec,
                "discovery_economics": ranked,
                "absolute_economics": abs_,
                "rank_top1_diagnostic": top1,
                "promotion": gate,
            }
        )
    ranked_clears = [c for c in out if c["promotion"]["promoted"]]
    abs_clears_ids = []
    for c in out:
        g = promote(c["absolute_economics"])
        if g.get("promoted"):
            abs_clears_ids.append((c.get("spec") or {}).get("candidate_id"))
    promoted = ranked_clears[:MAX_CANDIDATES]
    return {
        "proposals": out,
        "promoted": promoted,
        "promoted_n": len(promoted),
        "ranking_provided_magnitude": bool(promoted) and not abs_clears_ids,
        "absolute_also_cleared": abs_clears_ids,
        "rank_response_ordered": bool(rank_ordered),
        "designed_on_discovery_only": True,
        "old_confirmation_used_to_design": False,
        "cs1_cs3_rescued": False,
    }
