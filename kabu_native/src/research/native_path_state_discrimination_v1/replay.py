"""Full event-time occupancy replay for extracted rules. EXIT matches the rule thesis."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_path_state_discrimination_v1 import (
    ENTRY_CUTOFF,
    FAVOR_BPS,
    OCCUPANCY,
    SESSION_FLAT,
    TIME_STOP_MIN,
    X1_TAX_BPS,
)
from research.native_path_state_discrimination_v1.rules import match_rule
from research.freeze_group_mechanism_definitions_v1.rca import subset_econ, winner_concentration, symbol_contribution


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _exit(ep: dict[str, Any], *, kind: str) -> dict[str, Any]:
    fwd = list(ep.get("fwd_bars") or [])
    px = ep.get("x0_entry_open")
    if not fwd or not _finite(px) or float(px) <= 0:
        return {"ok": False}
    px = float(px)
    reason = "session_flat"
    exit_i = min(len(fwd) - 1, TIME_STOP_MIN)
    below_run = 0
    rs_run = 0
    for i, row in enumerate(fwd):
        hh, cl, above_vw, brk, rs1 = row[0], row[4], row[6], row[7], row[9]
        if str(hh) >= SESSION_FLAT:
            exit_i, reason = i, "session_flat"
            break
        if kind == "reclaim" and above_vw is False:
            exit_i, reason = i, "vwap_loss"
            break
        if kind == "impulse":
            if _finite(cl) and float(cl) < px * (1.0 - FAVOR_BPS / 10_000.0):
                below_run += 1
            else:
                below_run = 0
            if below_run >= 2:
                exit_i, reason = i, "structure_loss"
                break
        if kind == "sector":
            if _finite(rs1) and float(rs1) < 0:
                rs_run += 1
            else:
                rs_run = 0
            if rs_run >= 2:
                exit_i, reason = i, "sector_rs_failure"
                break
        if kind == "breakout" and _finite(brk) and _finite(cl) and float(cl) < float(brk):
            exit_i, reason = i, "breakout_fail"
            break
        if i >= TIME_STOP_MIN:
            exit_i, reason = i, "time_stop"
            break
    if exit_i + 1 < len(fwd) and _finite(fwd[exit_i + 1][1]):
        exit_px = float(fwd[exit_i + 1][1])
        fill = "next_open_after_invalidation_bar"
    else:
        exit_px = float(fwd[exit_i][4]) if _finite(fwd[exit_i][4]) else None
        fill = "invalidation_close_last_bar"
    if not _finite(exit_px):
        return {"ok": False}
    x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
    return {
        "ok": True,
        "exit_reason": reason,
        "exit_fill": fill,
        "x0_bps": x0,
        "x1_bps": x0 - float(X1_TAX_BPS),
        "hold_min": int(exit_i),
    }


def replay_rule(rows: list[dict[str, Any]], rule: dict[str, Any], *, med: dict[str, float]) -> dict[str, Any]:
    preds = [(str(p["feature"]), str(p["op"]), float(p["threshold"])) for p in list(rule.get("predicates") or [])]
    kind = str(rule.get("exit_kind") or "impulse")
    cand = [e for e in rows if e.get("x0_entry_open") and match_rule(e, preds, med)]
    cand.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in cand:
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
        got = _exit(e, kind=kind)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        trades.append(
            {
                "date": day,
                "symbol": e["symbol"],
                "sector": e.get("sector"),
                "event_time": hh,
                "block": e.get("block"),
                "sequence": e.get("sequence"),
                "path_type": e.get("path_type"),
                "rule_id": rule.get("rule_id"),
                **got,
                "exit_hh": exit_hh,
            }
        )
        occ[day].append(trades[-1])
        traded_today[day].add(str(e["symbol"]))
    if not trades:
        return {"ok": False, "rule_id": rule.get("rule_id"), "trade_n": 0, "skipped": skipped, "exit_kind": kind}
    def _econ(xs: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
        if not xs:
            return {"ok": False, "label": label, "trade_n": 0}
        x0 = np.asarray([float(t["x0_bps"]) for t in xs], dtype=float)
        by_day: dict[str, list[float]] = defaultdict(list)
        by_block: dict[str, list[float]] = defaultdict(list)
        for t in xs:
            by_day[str(t["date"])].append(float(t["x0_bps"]))
            if t.get("block"):
                by_block[str(t["block"])].append(float(t["x0_bps"]))
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
            "block_mean_x0": {k: float(np.mean(vs)) for k, vs in by_block.items()},
        }

    eval_trades = [t for t in trades if str(t.get("block")) in {"D2", "D3", "D4"}]
    core = [t for t in trades if str(t.get("block")) in {"D2", "D3"}]
    primary = _econ(eval_trades, label="D2_D4") if eval_trades else _econ(trades, label="ALL")
    return {
        "ok": True,
        "rule_id": rule.get("rule_id"),
        "exit_kind": kind,
        "thesis_exit_matches_entry": True,
        **primary,
        "discovery_all": _econ(trades, label="D1_D4"),
        "d2_d3": subset_econ(core, label="D2_D3") if core else {"label": "D2_D3", "trade_n": 0},
        "tail": winner_concentration(eval_trades or trades),
        "symbols": symbol_contribution(eval_trades or trades),
        "exclude_8136": subset_econ([t for t in (eval_trades or trades) if str(t["symbol"]) != "8136"], label="EXCLUDE_8136"),
        "skipped": skipped,
        "occupancy": OCCUPANCY,
        "x1_tax_bps": X1_TAX_BPS,
        "static_counterfactual": False,
        "full_event_time_replay": True,
        "candidate_n": len(cand),
        "metrics_window": "D2_D4_OOS",
    }
