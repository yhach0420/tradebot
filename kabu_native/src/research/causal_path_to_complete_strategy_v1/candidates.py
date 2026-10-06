"""Build at most 3 complete-strategy candidates from Discovery RCA. No Confirmation peek."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

import numpy as np

from research.causal_path_to_complete_strategy_v1 import MAX_CANDIDATES, X1_TAX_BPS

MAX_OCCUPANCY = 3
TIME_STOP_MIN = 20
SESSION_FLAT = "15:20"
ENTRY_CUTOFF = "14:50"


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _spec_sha(spec: dict[str, Any]) -> str:
    raw = json.dumps(spec, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cuts(events: list[dict[str, Any]], feat: str, sign: int) -> dict[str, Any]:
    xs = np.asarray([float(e[feat]) for e in events if _finite(e.get(feat))], dtype=float)
    if xs.size < 40:
        return {"ok": False}
    q20, q80 = float(np.percentile(xs, 20)), float(np.percentile(xs, 80))
    if sign >= 0:
        return {"ok": True, "feature": feat, "op": "gte", "cut": q80, "side": "Q5", "sign": 1}
    return {"ok": True, "feature": feat, "op": "lte", "cut": q20, "side": "Q1", "sign": -1}


def _pass_cut(e: dict[str, Any], cut: dict[str, Any]) -> bool:
    if not cut or not cut.get("ok"):
        return False
    v = e.get(cut["feature"])
    if not _finite(v):
        return False
    if cut["op"] == "gte":
        return float(v) >= float(cut["cut"])
    return float(v) <= float(cut["cut"])


def _propose_from_rca(rca: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposals = []
    archetypes = [
        {
            "candidate_id": "CS1_TREND_PULLBACK_RECLAIM",
            "archetype": "TREND_PULLBACK_RECLAIM",
            "event_family": "VWAP_RECLAIM",
            "alt_family": "PULLBACK_START",
            "thesis": "Market/sector not deteriorating; stock relative strength intact; pullback then VWAP reclaim as demand resume.",
            "exit_kind": "reclaim",
        },
        {
            "candidate_id": "CS2_BREAKOUT_EXPANSION",
            "archetype": "BREAKOUT_EXPANSION",
            "event_family": "BREAKOUT20",
            "alt_family": "COMPRESSION_RELEASE",
            "thesis": "Market/sector supportive; stock leadership; compression or 20-bar high escape with activity.",
            "exit_kind": "breakout",
        },
    ]
    by_fam = dict(rca.get("by_family") or {})
    for arch in archetypes:
        fam = arch["event_family"]
        info = by_fam.get(fam) or {}
        if int(info.get("n") or 0) < 80:
            fam = arch["alt_family"]
            info = by_fam.get(fam) or {}
        if int(info.get("n") or 0) < 80:
            continue
        feats = list(info.get("features") or [])
        stable = [f for f in feats if f.get("stable_across_blocks")]
        if not stable:
            continue
        fam_events = [e for e in events if e.get("event_family") == fam and e.get("layer") == "STOCK"]
        cuts = []
        used = set()
        for layer in ("MARKET", "SECTOR", "STOCK"):
            layer_feats = [f for f in stable if f.get("layer") == layer and f["feature"] not in used]
            if not layer_feats:
                continue
            top = layer_feats[0]
            cut = _cuts(fam_events, top["feature"], 1 if (top.get("spearman") or 0) >= 0 else -1)
            if cut.get("ok"):
                cuts.append({**cut, "layer": layer, "spearman": top.get("spearman")})
                used.add(top["feature"])
        if not cuts:
            continue
        if arch["exit_kind"] == "reclaim":
            invalidation = [
                "VWAP loss after entry (thesis: demand resume failed)",
                "EMA9<EMA21 for 2 consecutive post-entry bars (structure-loss persistence, V27 retested not copied)",
                "time stop 20m",
                "session flatten 15:20",
            ]
        else:
            invalidation = [
                "close back below pre-event 20-bar high (breakout level failure)",
                "EMA9<EMA21 for 2 consecutive post-entry bars",
                "time stop 20m",
                "session flatten 15:20",
            ]
        spec = {
            "candidate_id": arch["candidate_id"],
            "archetype": arch["archetype"],
            "event_family": fam,
            "thesis": arch["thesis"],
            "market_context": next((c for c in cuts if c.get("layer") == "MARKET"), {"role": "none_stable"}),
            "sector_context": next((c for c in cuts if c.get("layer") == "SECTOR"), {"role": "none_stable"}),
            "stock_selection_state": next((c for c in cuts if c.get("layer") == "STOCK"), {"role": "none_stable"}),
            "setup": f"causal onset {fam}",
            "trigger": f"{fam} at event_time = feature_bar + 1m",
            "entry_availability_time": "event_time = T+1m (BAR_START)",
            "historical_execution_assumption": "X0=next bar open after event_time; X1=X0-8bps tax (not spread proof)",
            "cuts": cuts,
            "position_state": "long 1 unit, one symbol, max occupancy 3",
            "technical_invalidation": invalidation,
            "exit_state_machine": arch["exit_kind"],
            "same_symbol_behavior": "one live position; no same-day reentry after exit",
            "reentry_behavior": "none_same_day",
            "CAP_semantics": "skip if occupancy==3; slot released on exit",
            "occupancy": MAX_OCCUPANCY,
            "slot_release": "on_exit",
            "session_close": SESSION_FLAT,
            "time_stop_min": TIME_STOP_MIN,
            "copied_m4_m6": False,
            "kabu_50": False,
            "uses_frozen_validation": False,
            "uses_old_confirmation_to_design": False,
        }
        spec["spec_sha256"] = _spec_sha({k: v for k, v in spec.items() if k != "spec_sha256"})
        proposals.append(spec)
        if len(proposals) >= MAX_CANDIDATES:
            break

    # Optional third only if another family has stable structure not already used.
    used_fams = {p["event_family"] for p in proposals}
    for fam in ("RS_IMPROVE", "EMA_IMPROVE", "RELVOL_EXPAND"):
        if fam in used_fams or len(proposals) >= MAX_CANDIDATES:
            break
        info = by_fam.get(fam) or {}
        if int(info.get("n") or 0) < 80 or not info.get("stable_features"):
            continue
        fam_events = [e for e in events if e.get("event_family") == fam and e.get("layer") == "STOCK"]
        feats = [f for f in list(info.get("features") or []) if f.get("stable_across_blocks")]
        cuts = []
        for f in feats[:3]:
            cut = _cuts(fam_events, f["feature"], 1 if (f.get("spearman") or 0) >= 0 else -1)
            if cut.get("ok"):
                cuts.append({**cut, "layer": f.get("layer"), "spearman": f.get("spearman")})
        if len(cuts) < 2:
            continue
        spec = {
            "candidate_id": f"CS3_{fam}",
            "archetype": "RCA_SUPPORTED_OTHER",
            "event_family": fam,
            "thesis": f"Within-family RCA found block-stable pre-event structure for {fam}; not a forced reversal book.",
            "market_context": next((c for c in cuts if c.get("layer") == "MARKET"), {"role": "none_stable"}),
            "sector_context": next((c for c in cuts if c.get("layer") == "SECTOR"), {"role": "none_stable"}),
            "stock_selection_state": next((c for c in cuts if c.get("layer") == "STOCK"), {"role": "none_stable"}),
            "setup": f"causal onset {fam}",
            "trigger": f"{fam} at event_time = feature_bar + 1m",
            "entry_availability_time": "event_time = T+1m (BAR_START)",
            "historical_execution_assumption": "X0=next bar open after event_time; X1=X0-8bps tax (not spread proof)",
            "cuts": cuts,
            "position_state": "long 1 unit, one symbol, max occupancy 3",
            "technical_invalidation": [
                "EMA9<EMA21 for 2 consecutive post-entry bars",
                "VWAP loss",
                "time stop 20m",
                "session flatten 15:20",
            ],
            "exit_state_machine": "reclaim",
            "same_symbol_behavior": "one live position; no same-day reentry after exit",
            "reentry_behavior": "none_same_day",
            "CAP_semantics": "skip if occupancy==3; slot released on exit",
            "occupancy": MAX_OCCUPANCY,
            "slot_release": "on_exit",
            "session_close": SESSION_FLAT,
            "time_stop_min": TIME_STOP_MIN,
            "copied_m4_m6": False,
            "forced_reversal": False,
            "kabu_50": False,
            "uses_frozen_validation": False,
            "uses_old_confirmation_to_design": False,
        }
        spec["spec_sha256"] = _spec_sha({k: v for k, v in spec.items() if k != "spec_sha256"})
        proposals.append(spec)
    return proposals[:MAX_CANDIDATES]


def _exit_from_fwd(e: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    fwd = list(e.get("fwd_bars") or [])
    px = e.get("x0_entry_open")
    if not fwd or not _finite(px):
        return {"ok": False}
    hi20 = e.get("hi20")
    kind = spec.get("exit_state_machine")
    ema_loss_run = 0
    exit_px = None
    reason = "time_stop"
    exit_i = min(TIME_STOP_MIN, len(fwd) - 1)
    for i, bar in enumerate(fwd):
        hh, o, h, l, c, vw, ema_ok, above_vw = bar
        if str(hh) >= SESSION_FLAT:
            exit_i = i
            reason = "session_close"
            break
        if i == 0:
            continue
        if not ema_ok:
            ema_loss_run += 1
        else:
            ema_loss_run = 0
        if kind == "breakout" and _finite(hi20) and _finite(c) and float(c) < float(hi20):
            exit_i = i
            reason = "breakout_level_fail"
            break
        if kind != "breakout" and above_vw is False:
            exit_i = i
            reason = "vwap_loss"
            break
        if ema_loss_run >= 2:
            exit_i = i
            reason = "ema_structure_loss_persist"
            break
        if i >= TIME_STOP_MIN:
            exit_i = i
            reason = "time_stop"
            break
    if exit_i + 1 < len(fwd) and _finite(fwd[exit_i + 1][1]):
        exit_px = float(fwd[exit_i + 1][1])
        fill = "next_open_after_invalidation_bar"
    else:
        exit_px = float(fwd[exit_i][4]) if _finite(fwd[exit_i][4]) else None
        fill = "invalidation_close_last_bar"
    if not _finite(exit_px):
        return {"ok": False}
    x0 = float((exit_px / float(px) - 1.0) * 10_000.0)
    return {
        "ok": True,
        "exit_reason": reason,
        "exit_fill": fill,
        "x0_bps": x0,
        "x1_bps": x0 - float(X1_TAX_BPS),
        "hold_min": int(exit_i),
    }


def replay(*, events: list[dict[str, Any]], spec: dict[str, Any], date_to_block: dict[str, str] | None = None) -> dict[str, Any]:
    fam = spec["event_family"]
    cuts = list(spec.get("cuts") or [])
    rows = [e for e in events if e.get("event_family") == fam and e.get("layer") == "STOCK" and e.get("x0_entry_open")]
    rows.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    live: dict[str, Any] = {}
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "filters": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        if any(not _pass_cut(e, c) for c in cuts):
            skipped["filters"] += 1
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= MAX_OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = _exit_from_fwd(e, spec)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        exit_hh = SESSION_FLAT
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        if 0 <= hold < len(fwd):
            exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0])
        trade = {
            "date": day,
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "event_id": e.get("event_id"),
            "event_time": hh,
            "block": (date_to_block or {}).get(day),
            **got,
            "exit_hh": exit_hh,
            "taxonomy": e.get("path_taxonomy"),
        }
        trades.append(trade)
        occ[day].append(trade)
        traded_today[day].add(str(e["symbol"]))
        _ = live
    if not trades:
        return {"ok": False, "trade_n": 0, "skipped": skipped, "spec_sha256": spec.get("spec_sha256")}
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    x1 = np.asarray([float(t["x1_bps"]) for t in trades], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[float]] = defaultdict(list)
    by_sym: dict[str, float] = defaultdict(float)
    by_sec: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t["date"]].append(float(t["x0_bps"]))
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
    top_sym_share = (max(by_sym.values()) / pos) if pos > 0 and by_sym else None
    top_sec_share = (max(by_sec.values()) / pos) if pos > 0 and by_sec else None
    block_means = {k: float(np.mean(vs)) for k, vs in by_block.items()}
    block_pos_n = sum(1 for v in block_means.values() if v > 0)
    return {
        "ok": True,
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
        "top_symbol_share_of_positive_bps": top_sym_share,
        "top_sector_share_of_positive_bps": top_sec_share,
        "block_mean_x0": block_means,
        "block_positive_n": block_pos_n,
        "block_n": len(block_means),
        "skipped": skipped,
        "exit_reasons": {k: int(sum(1 for t in trades if t.get("exit_reason") == k)) for k in {t.get("exit_reason") for t in trades}},
        "spec_sha256": spec.get("spec_sha256"),
        "candidate_id": spec.get("candidate_id"),
    }


def promote(econ: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if int(econ.get("trade_n") or 0) < 40:
        reasons.append("trade_n")
    if int(econ.get("day_n") or 0) < 15:
        reasons.append("day_n")
    if float(econ.get("mean_x0_bps") or 0) < 4.0:
        reasons.append("mean_x0")
    if float(econ.get("mean_x1_bps") or -999) <= 0:
        reasons.append("mean_x1")
    pf = econ.get("profit_factor")
    if pf is None or float(pf) < 1.05:
        reasons.append("profit_factor")
    if float(econ.get("top_symbol_share_of_positive_bps") or 0) >= 0.40:
        reasons.append("symbol_concentration")
    if float(econ.get("top_sector_share_of_positive_bps") or 0) >= 0.55:
        reasons.append("sector_concentration")
    if int(econ.get("block_positive_n") or 0) < 3:
        reasons.append("block_consistency")
    if int(econ.get("symbol_n") or 0) < 8:
        reasons.append("symbol_n")
    return {"promoted": not reasons, "fail_reasons": reasons, "not_treatment_vs_control_only": True}


def build_candidates(*, rca: dict[str, Any], events: list[dict[str, Any]], date_to_block: dict[str, str]) -> dict[str, Any]:
    proposals = _propose_from_rca(rca, events)
    out = []
    for spec in proposals:
        econ = replay(events=events, spec=spec, date_to_block=date_to_block)
        gate = promote(econ)
        out.append({"spec": spec, "discovery_economics": econ, "promotion": gate})
    promoted = [c for c in out if c["promotion"]["promoted"]]
    structure = bool(rca.get("level_useful") or rca.get("transition_useful") or rca.get("acceleration_useful") or rca.get("stable_market_sector_stock_chain"))
    return {
        "proposals": out,
        "promoted": promoted[:MAX_CANDIDATES],
        "promoted_n": min(len(promoted), MAX_CANDIDATES),
        "structure_found": structure,
        "designed_on_discovery_only": True,
        "old_confirmation_used_to_design": False,
        "frozen_validation_used": False,
    }
