"""Frozen CAP / same-symbol / occupancy replay. Event-gated R11 candidates only."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from research.freeze_group_mechanism_definitions_v1.rca import subset_econ, symbol_contribution, winner_concentration
from research.native_1m_path_state_strategy_freeze_v1 import ENTRY_CUTOFF, EVAL_BLOCKS, OCCUPANCY, SESSION_FLAT, X1_TAX_BPS
from research.native_1m_path_state_strategy_freeze_v1.exit import exit_reclaim
from research.native_1m_path_state_strategy_freeze_v1.r11 import match_r11
from research.native_path_state_discrimination_v1.replay import _exit as source_exit
from research.native_path_state_discrimination_v1.rules import match_rule


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _econ(xs: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    if not xs:
        return {"ok": False, "label": label, "trade_n": 0}
    x0 = np.asarray([float(t["x0_bps"]) for t in xs], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in xs:
        by_day[str(t["date"])].append(float(t["x0_bps"]))
        if t.get("block"):
            by_block[str(t["block"])].append(t)
    pos = float(np.sum(x0[x0 > 0]))
    neg = float(-np.sum(x0[x0 < 0]))
    day_means = np.asarray([float(np.mean(vs)) for vs in by_day.values()], dtype=float)
    eq = np.cumsum(day_means) if day_means.size else np.asarray([])
    dd = float(np.min(eq - np.maximum.accumulate(eq))) if eq.size else 0.0
    return {
        "ok": True,
        "label": label,
        "trade_n": int(x0.size),
        "day_n": len(by_day),
        "symbol_n": len({t["symbol"] for t in xs}),
        "mean_x0_bps": float(np.mean(x0)),
        "mean_x1_bps": float(np.mean(x0) - X1_TAX_BPS),
        "median_x0_bps": float(np.median(x0)),
        "hit_rate": float(np.mean(x0 > 0)),
        "profit_factor": (pos / neg) if neg > 0 else None,
        "max_dd_daily_mean_bps": dd,
        "block_mean_x0": {k: float(np.mean([float(t["x0_bps"]) for t in vs])) for k, vs in by_block.items()},
    }


def _pack(trades: list[dict[str, Any]], skipped: dict[str, int], candidate_n: int, *, rule_id: str) -> dict[str, Any]:
    eval_trades = [t for t in trades if str(t.get("block")) in EVAL_BLOCKS]
    core = [t for t in trades if str(t.get("block")) in {"D2", "D3"}]
    primary = _econ(eval_trades, label="D2_D4") if eval_trades else _econ(trades, label="ALL")
    slim = [{k: v for k, v in t.items() if k not in {"fwd_bars", "state", "pre_bars"}} for t in eval_trades]
    return {
        "ok": bool(trades),
        "rule_id": rule_id,
        "exit_kind": "reclaim",
        "thesis_exit_matches_entry": True,
        **primary,
        "discovery_all": _econ(trades, label="D1_D4"),
        "d2_d3": subset_econ(core, label="D2_D3") if core else {"label": "D2_D3", "trade_n": 0},
        "tail": winner_concentration(eval_trades or trades),
        "symbols": symbol_contribution(eval_trades or trades),
        "skipped": skipped,
        "occupancy": OCCUPANCY,
        "x1_tax_bps": X1_TAX_BPS,
        "static_counterfactual": False,
        "full_event_time_replay": True,
        "candidate_n": candidate_n,
        "metrics_window": "D2_D4_OOS",
        "trades": trades,
        "eval_trades": slim,
    }


def source_replay(rows: list[dict[str, Any]], rule: dict[str, Any], *, med: dict[str, float]) -> dict[str, Any]:
    preds = [(str(p["feature"]), str(p["op"]), float(p["threshold"])) for p in list(rule.get("predicates") or [])]
    kind = str(rule.get("exit_kind") or "reclaim")
    cand = [e for e in rows if e.get("x0_entry_open") and match_rule(e, preds, med)]
    cand.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades: list[dict[str, Any]] = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in cand:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        slots_before = len(occ[day])
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = source_exit(e, kind=kind)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else "15:20"
        exit_px = None
        if fwd:
            j = min(hold + 1, len(fwd) - 1)
            if hold + 1 < len(fwd) and _finite(fwd[hold + 1][1]):
                exit_px = float(fwd[hold + 1][1])
            elif _finite(fwd[hold][4]):
                exit_px = float(fwd[hold][4])
        rec = {
            "date": day,
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "episode_start": e.get("start"),
            "feature_bar": e.get("feature_bar"),
            "available_at": e.get("available_at"),
            "decision_time": hh,
            "event_time": hh,
            "entry_time": hh,
            "entry_price": e.get("x0_entry_open"),
            "block": e.get("block"),
            "sequence": e.get("sequence"),
            "path_type": e.get("path_type"),
            "rule_id": rule.get("rule_id"),
            **got,
            "exit_hh": exit_hh,
            "exit_price": exit_px,
            "slots_before": slots_before,
            "slots_after": slots_before + 1,
            "x0_entry_open": e.get("x0_entry_open"),
            "fwd_bars": fwd,
            "state": e.get("state"),
        }
        trades.append(rec)
        occ[day].append(trades[-1])
        traded_today[day].add(str(e["symbol"]))
    return _pack(trades, skipped, len(cand), rule_id=str(rule.get("rule_id") or "R11"))


def frozen_replay(rows: list[dict[str, Any]], *, med: dict[str, float], already_matched: bool = False) -> dict[str, Any]:
    if already_matched:
        cand = [e for e in rows if e.get("x0_entry_open")]
    else:
        cand = [e for e in rows if e.get("x0_entry_open") and match_r11(e, med=med)]
    cand.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades: list[dict[str, Any]] = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in cand:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        slots_before = len(occ[day])
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = exit_reclaim(e)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        rec = {
            "date": day,
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "episode_start": e.get("start"),
            "feature_bar": e.get("feature_bar"),
            "available_at": e.get("available_at"),
            "decision_time": hh,
            "event_time": hh,
            "entry_time": hh,
            "entry_price": e.get("x0_entry_open"),
            "block": e.get("block"),
            "sequence": e.get("sequence"),
            "path_type": e.get("path_type"),
            "rule_id": "R11",
            **got,
            "exit_fill_time": got.get("exit_hh"),
            "exit_hh": exit_hh,
            "slots_before": slots_before,
            "slots_after": slots_before + 1,
            "x0_entry_open": e.get("x0_entry_open"),
            "fwd_bars": fwd,
            "state": e.get("state"),
        }
        trades.append(rec)
        occ[day].append(trades[-1])
        traded_today[day].add(str(e["symbol"]))
    return _pack(trades, skipped, len(cand), rule_id="R11")


def identity_compare(source_trades: list[dict[str, Any]], frozen_trades: list[dict[str, Any]]) -> dict[str, Any]:
    def key(t: dict[str, Any]) -> tuple:
        return (
            str(t.get("date")),
            str(t.get("symbol")),
            str(t.get("event_time")),
            str(t.get("exit_reason") or ""),
            str(t.get("exit_hh") or ""),
            float(t.get("x0_bps")),
        )

    src = [t for t in source_trades if str(t.get("block")) in EVAL_BLOCKS]
    frz = [t for t in frozen_trades if str(t.get("block")) in EVAL_BLOCKS]
    n = min(len(src), len(frz))
    match = 0
    mismatches = []
    for i in range(n):
        if key(src[i]) == key(frz[i]) and float(src[i].get("entry_price") or 0) == float(frz[i].get("entry_price") or 0):
            match += 1
        elif i < 40:
            mismatches.append({"i": i, "source": key(src[i]), "frozen": key(frz[i])})
    src_set = {(str(t["date"]), str(t["symbol"]), str(t["event_time"])) for t in src}
    frz_set = {(str(t["date"]), str(t["symbol"]), str(t["event_time"])) for t in frz}
    order_ok = [ (str(t["date"]), str(t["symbol"]), str(t["event_time"])) for t in src ] == [
        (str(t["date"]), str(t["symbol"]), str(t["event_time"])) for t in frz
    ]
    return {
        "source_n": len(src),
        "frozen_n": len(frz),
        "match_n": match,
        "TRADE_IDENTITY_MATCH": f"{match} / 908",
        "match_908": bool(match == 908 and len(src) == 908 and len(frz) == 908),
        "missing_n": len(src_set - frz_set),
        "extra_n": len(frz_set - src_set),
        "order_identical": order_ok,
        "mismatches_head": mismatches,
    }


def portfolio_spec() -> dict[str, Any]:
    return {
        "CAP": OCCUPANCY,
        "same_symbol": "at most one trade per symbol per calendar day; no same-day re-entry after a fill",
        "occupancy_ordering": "candidates sorted (date, event_time, symbol); a slot is occupied until exit_hh > candidate event_time",
        "slot_release": "positions with exit_hh <= candidate event_time are dropped before CAP check",
        "reentry_policy": "same symbol blocked for the rest of that date after any fill, even after slot release",
        "session_flatten": "15:20 session_flat on the EXIT path",
        "x1_tax_bps": X1_TAX_BPS,
    }


def portfolio_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
