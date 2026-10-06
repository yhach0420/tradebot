"""Occupancy replay. Market-like uses 8bps stress. Passive limits do not treat 8bps as actual cost."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.freeze_group_mechanism_definitions_v1.rca import subset_econ, symbol_contribution, winner_concentration
from research.multi_touch_daily_zone_1m_price_action_v1 import (
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


def _exit(row: dict[str, Any], *, px: float | None = None) -> dict[str, Any]:
    fwd = list(row.get("fwd_bars") or [])
    px = px if _finite(px) else row.get("x0_entry_open")
    if not fwd or not _finite(px) or float(px) <= 0:
        return {"ok": False}
    px = float(px)
    level = row.get("level_value")
    for i, rec in enumerate(fwd):
        hh, o, cl, lv = rec[0], rec[1], rec[4], rec[6]
        if str(hh) >= SESSION_FLAT:
            exit_px = o if _finite(o) else cl
            if not _finite(exit_px):
                return {"ok": False}
            x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
            return {"ok": True, "exit_reason": "session_flat", "exit_hh": str(hh), "x0_bps": x0, "hold_min": i}
        if i == 0:
            continue
        L = level if _finite(level) else lv
        if _finite(cl) and _finite(L) and float(cl) < float(L):
            if i + 1 < len(fwd) and _finite(fwd[i + 1][1]):
                exit_px = float(fwd[i + 1][1])
                exit_hh = str(fwd[i + 1][0])
            else:
                exit_px = float(cl)
                exit_hh = str(hh)
            x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
            return {"ok": True, "exit_reason": "thesis_zone_lost", "exit_hh": exit_hh, "x0_bps": x0, "hold_min": i}
    last = fwd[-1]
    if not _finite(last[4]):
        return {"ok": False}
    x0 = float((float(last[4]) / px - 1.0) * 10_000.0)
    return {"ok": True, "exit_reason": "session_flat", "exit_hh": str(last[0]), "x0_bps": x0, "hold_min": len(fwd) - 1}


def replay_market(rows: list[dict[str, Any]], *, playbook_id: str) -> dict[str, Any]:
    cand = [r for r in rows if str(r.get("playbook_id")) == playbook_id and r.get("x0_entry_open")]
    cand.sort(key=lambda e: (str(e["date"]), str(e.get("event_time") or ""), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    sf_am = 0
    sf_1520 = 0
    for e in cand:
        day, hh = str(e["date"]), str(e.get("event_time") or "")
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        if "11:30" <= hh < "12:30":
            skipped["cutoff"] += 1
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
        x0 = float(got["x0_bps"])
        trades.append(
            {
                "date": day,
                "block": e.get("block"),
                "symbol": e["symbol"],
                "event_time": hh,
                "exit_hh": got["exit_hh"],
                "exit_reason": got["exit_reason"],
                "x0_bps": x0,
                "x1_bps": x0 - X1_TAX_BPS,
                "playbook_id": playbook_id,
            }
        )
        occ[day].append({"exit_hh": got["exit_hh"]})
        traded_today[day].add(str(e["symbol"]))
        if got["exit_reason"] == "session_flat" and str(got["exit_hh"]) < "12:30":
            sf_am += 1
        if str(got["exit_hh"]) >= SESSION_FLAT:
            sf_1520 += 1
    eval_tr = [t for t in trades if str(t.get("block") or "") in EVAL_BLOCKS]
    x0s = [float(t["x0_bps"]) for t in eval_tr]
    x0_sum = float(sum(x0s)) if x0s else 0.0
    return {
        "playbook_id": playbook_id,
        "execution": "BREAKOUT_MARKET_LIKE",
        "candidate_n": len(cand),
        "trade_n": len(eval_tr),
        "skipped": skipped,
        "X0_d2d4": x0_sum,
        "X1_8BPS_STRESS_d2d4": x0_sum - X1_TAX_BPS * len(eval_tr),
        "mean_x0": float(np.mean(x0s)) if x0s else None,
        "econ": subset_econ(eval_tr, label=playbook_id) if eval_tr else {"trade_n": 0},
        "winner_concentration": winner_concentration(eval_tr) if eval_tr else None,
        "top_symbol": (symbol_contribution(eval_tr)[0] if eval_tr else None),
        "session_flat_am_n": sf_am,
        "session_flat_1520_n": sf_1520,
        "implicit_1130_truncation": sf_am > 0,
    }


def replay_limits(orders: list[dict[str, Any]]) -> dict[str, Any]:
    eval_o = [o for o in orders if str(o.get("block") or "") in EVAL_BLOCKS]
    filled = [o for o in eval_o if o.get("filled")]
    unfilled = [o for o in eval_o if not o.get("filled")]
    fail_fill = sum(1 for o in filled if o.get("reversal") or o.get("path_type") in {"reversal", "giveback"})
    cont_unf = sum(1 for o in unfilled if o.get("cancel_reason") != "fail_back_below_before_fill")
    trades = []
    sf_am = 0
    for o in filled:
        if str(o.get("fill_time") or "") > ENTRY_CUTOFF:
            continue
        got = _exit(o, px=o.get("fill_price"))
        if not got.get("ok"):
            continue
        x0 = float(got["x0_bps"])
        trades.append({**{k: o.get(k) for k in ("date", "block", "symbol")}, "x0_bps": x0, "x1_bps": x0 - X1_TAX_BPS, "exit_hh": got["exit_hh"], "exit_reason": got["exit_reason"]})
        if got["exit_reason"] == "session_flat" and str(got["exit_hh"]) < "12:30":
            sf_am += 1
    eval_tr = [t for t in trades if str(t.get("block") or "") in EVAL_BLOCKS]
    x0s = [float(t["x0_bps"]) for t in eval_tr]
    n = max(len(eval_o), 1)
    failed_before = sum(1 for o in eval_o if o.get("cancel_reason") == "fail_back_below_before_fill")
    return {
        "playbook_id": "P2_LIMIT",
        "execution": "RETEST_PASSIVE_LIMIT",
        "order_n": len(eval_o),
        "fill_n": len(filled),
        "fill_rate": len(filled) / n,
        "no_fill_rate": len(unfilled) / n,
        "X0_after_fill_d2d4": float(sum(x0s)) if x0s else 0.0,
        "mean_x0_after_fill": float(np.mean(x0s)) if x0s else None,
        "X1_8BPS_STRESS_d2d4": (float(sum(x0s)) - X1_TAX_BPS * len(x0s)) if x0s else 0.0,
        "8bps_is_actual_passive_cost": False,
        "trade_n": len(eval_tr),
        "econ": subset_econ(eval_tr, label="P2_LIMIT") if eval_tr else {"trade_n": 0},
        "fill_conditional_failure_rate": fail_fill / max(len(filled), 1),
        "unfilled_n": len(unfilled),
        "failed_before_fill_n": failed_before,
        "unfilled_not_failed_n": cont_unf,
        "session_flat_am_n": sf_am,
        "implicit_1130_truncation": sf_am > 0,
        "adverse_selection": {
            "filled_failure_rate": fail_fill / max(len(filled), 1),
            "unfilled_cancel_fail_rate": failed_before / max(len(unfilled), 1) if unfilled else None,
            "question": "Are limit fills disproportionately failed-break cases?",
        },
    }
