"""Complete-strategy replay. Hold through lunch, resume PM, flatten 15:20. No i>=20 time stop."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.freeze_group_mechanism_definitions_v1.rca import subset_econ, symbol_contribution, winner_concentration
from research.reference_level_1m_price_action_discovery_v1 import (
    ENTRY_CUTOFF,
    EVAL_BLOCKS,
    OCCUPANCY,
    SESSION_FLAT,
    X1_TAX_BPS,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _exit(row: dict[str, Any]) -> dict[str, Any]:
    fwd = list(row.get("fwd_bars") or [])
    px = row.get("x0_entry_open")
    if not fwd or not _finite(px) or float(px) <= 0:
        return {"ok": False}
    px = float(px)
    kind = str(row.get("exit_kind") or "lose_level")
    level = row.get("level_value")
    reason = "session_flat"
    exit_i = len(fwd) - 1
    for i, rec in enumerate(fwd):
        hh, o, _h, _l, cl, vw, lv = rec[0], rec[1], rec[2], rec[3], rec[4], rec[5], rec[6]
        if str(hh) >= SESSION_FLAT:
            exit_i, reason = i, "session_flat"
            exit_px = o if _finite(o) else cl
            fill = "session_flat_open"
            x0 = float((float(exit_px) / px - 1.0) * 10_000.0) if _finite(exit_px) else None
            if x0 is None:
                return {"ok": False}
            return {
                "ok": True,
                "exit_reason": reason,
                "exit_fill": fill,
                "exit_hh": str(hh),
                "x0_bps": x0,
                "x1_bps": x0 - float(X1_TAX_BPS),
                "hold_min": int(i),
            }
        if i == 0:
            continue
        lost = False
        L = level if _finite(level) else lv
        if kind == "vwap_loss":
            lost = _finite(cl) and _finite(vw) and float(cl) <= float(vw)
            if lost:
                reason = "vwap_loss"
        else:
            lost = _finite(cl) and _finite(L) and float(cl) < float(L)
            if lost:
                reason = "thesis_level_lost"
        if lost:
            exit_i = i
            if i + 1 < len(fwd) and _finite(fwd[i + 1][1]):
                exit_px = float(fwd[i + 1][1])
                fill = "next_open_after_invalidation_bar"
                exit_hh = str(fwd[i + 1][0])
            else:
                exit_px = float(cl) if _finite(cl) else None
                fill = "invalidation_close_last_bar"
                exit_hh = str(hh)
            if not _finite(exit_px):
                return {"ok": False}
            x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
            return {
                "ok": True,
                "exit_reason": reason,
                "exit_fill": fill,
                "exit_hh": exit_hh,
                "x0_bps": x0,
                "x1_bps": x0 - float(X1_TAX_BPS),
                "hold_min": int(exit_i),
            }
    last = fwd[exit_i]
    exit_px = last[4]
    if not _finite(exit_px):
        return {"ok": False}
    x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
    return {
        "ok": True,
        "exit_reason": reason,
        "exit_fill": "last_fwd_close",
        "exit_hh": str(last[0]),
        "x0_bps": x0,
        "x1_bps": x0 - float(X1_TAX_BPS),
        "hold_min": int(exit_i),
    }


def _block_sums(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by[str(t.get("block") or "")].append(float(t["x0_bps"]))
    out = {}
    for b, xs in by.items():
        arr = np.asarray(xs, dtype=float)
        out[b] = {
            "n": int(arr.size),
            "X0": float(np.sum(arr)),
            "X1": float(np.sum(arr) - X1_TAX_BPS * arr.size),
            "mean_x0": float(np.mean(arr)),
        }
    return out


def replay_playbook(rows: list[dict[str, Any]], *, playbook_id: str) -> dict[str, Any]:
    cand = [r for r in rows if str(r.get("playbook_id")) == playbook_id and r.get("x0_entry_open")]
    cand.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades: list[dict[str, Any]] = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0, "lunch_entry": 0}
    sf_hh: dict[str, int] = defaultdict(int)
    for e in cand:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        if "11:30" <= hh < "12:30":
            skipped["lunch_entry"] += 1
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = _exit(e)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        exit_hh = str(got["exit_hh"])
        trades.append(
            {
                "date": day,
                "block": e.get("block"),
                "symbol": e["symbol"],
                "sector": e.get("sector"),
                "event_time": hh,
                "exit_hh": exit_hh,
                "exit_reason": got["exit_reason"],
                "exit_fill": got["exit_fill"],
                "x0_bps": got["x0_bps"],
                "x1_bps": got["x1_bps"],
                "hold_min": got["hold_min"],
                "playbook_id": playbook_id,
                "path_type": e.get("path_type"),
            }
        )
        occ[day].append({"exit_hh": exit_hh})
        traded_today[day].add(str(e["symbol"]))
        if got["exit_reason"] == "session_flat":
            sf_hh[exit_hh] += 1
    eval_trades = [t for t in trades if str(t.get("block") or "") in EVAL_BLOCKS]
    econ = subset_econ(eval_trades, label=playbook_id) if eval_trades else {"label": playbook_id, "trade_n": 0}
    x0_sum = float(sum(float(t["x0_bps"]) for t in eval_trades)) if eval_trades else 0.0
    x1_sum = float(sum(float(t["x1_bps"]) for t in eval_trades)) if eval_trades else 0.0
    am_sf = sum(n for hh, n in sf_hh.items() if hh < "12:30")
    pm_sf = sum(n for hh, n in sf_hh.items() if hh >= SESSION_FLAT)
    contrib = symbol_contribution(eval_trades) if eval_trades else []
    top_share = None
    if eval_trades:
        pos = float(sum(float(t["x0_bps"]) for t in eval_trades if float(t["x0_bps"]) > 0))
        if contrib and pos > 0:
            top_share = abs(float(contrib[0].get("total_gross_contribution") or 0.0)) / pos if float(contrib[0].get("total_gross_contribution") or 0) > 0 else None
            if float(contrib[0].get("total_gross_contribution") or 0) > 0:
                top_share = float(contrib[0]["total_gross_contribution"]) / pos
    return {
        "playbook_id": playbook_id,
        "candidate_n": len(cand),
        "candidate_n_eval": sum(1 for r in cand if str(r.get("block") or "") in EVAL_BLOCKS),
        "trade_n": len(eval_trades),
        "trade_n_all_discovery": len(trades),
        "skipped": skipped,
        "econ_d2d4": econ,
        "X0_d2d4": x0_sum,
        "X1_d2d4": x1_sum,
        "block_sums": _block_sums(trades),
        "winner_concentration": winner_concentration(eval_trades) if eval_trades else None,
        "top_symbol": (contrib[0] if contrib else None),
        "top_symbol_pos_share": top_share,
        "session_flat_by_hh": dict(sf_hh),
        "session_flat_am_n": am_sf,
        "session_flat_1520_n": pm_sf,
        "implicit_1130_truncation": am_sf > 0,
        "lunch_policy": "HOLD_THROUGH_LUNCH_RESUME_PM",
        "time_stop_used": False,
        "same_bar_execution": False,
        "cap": OCCUPANCY,
        "x1_tax_bps": X1_TAX_BPS,
    }


def replay_selected(playbook_rows: list[dict[str, Any]], selected_ids: list[str], all_ids: list[str]) -> dict[str, Any]:
    packs = []
    for pid in all_ids:
        packs.append(replay_playbook(playbook_rows, playbook_id=pid))
    any_x1 = [p for p in packs if (p.get("X1_d2d4") or 0) > 0 and int(p.get("trade_n") or 0) > 0]
    dominated = [
        p
        for p in any_x1
        if (p.get("top_symbol_pos_share") is not None and float(p["top_symbol_pos_share"]) > 0.50)
        or (int((p.get("econ_d2d4") or {}).get("symbol_n") or 0) <= 3)
    ]
    return {
        "packs": packs,
        "selected_ids": selected_ids,
        "any_x1_positive": bool(any_x1),
        "x1_positive_ids": [p["playbook_id"] for p in any_x1],
        "dominated_ids": [p["playbook_id"] for p in dominated],
        "complete_strategy_candidate_ids": [p["playbook_id"] for p in any_x1 if p["playbook_id"] in selected_ids and p["playbook_id"] not in {d["playbook_id"] for d in dominated}],
    }
