"""Recover exact existing Full Causal executed-fill ledgers. Occupancy reconstruction only. No new strategy."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from replay.pnl_yen import compute_pnl_yen_100
from research.discovery_search_space_reassessment_v1 import (
    CSB_EXPECTED_PNL,
    CSB_EXPECTED_TRADE_N,
    DEVELOPMENT_DAYS,
    NEGATIVE_CONTROL_ID,
    P1_EXPECTED_PNL,
    P1_EXPECTED_TRADE_N,
    P1_ID,
    P2_EXPECTED_PNL,
    P2_EXPECTED_TRADE_N,
    P2_ID,
)
from research.discovery_search_space_reassessment_v1.isolation import RESEARCH_ROOT
from research.systematic_state_transition_full_strategy_v1.analyze import replay

ST_CACHE = RESEARCH_ROOT / "_work" / "systematic_state_transition_full_strategy_v1"
CSB_CACHE = RESEARCH_ROOT / "_work" / "new_full_strategy_implementation_and_dev_eval_v1"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_st_rows(candidate_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for day in DEVELOPMENT_DAYS:
        path = ST_CACHE / f"DEVELOPMENT_{day}_grid.json"
        body = _load(path)
        if not body.get("ok") or str(body.get("date") or "") != str(day):
            missing.append(str(day))
            continue
        extra = False
        rows.extend(list((body.get("rows_by") or {}).get(candidate_id) or []))
        _ = extra
    return {"rows": rows, "missing_days": missing, "candidate_id": candidate_id}


def occupancy_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    occ = replay(rows)
    out: list[dict[str, Any]] = []
    for t in list(occ.get("trades") or []):
        src = dict(t.get("src") or {})
        fill_t = t.get("fill_time")
        fill_px = t.get("fill_price")
        exit_t = t.get("exit_time")
        exit_px = t.get("exit_price")
        pnl = t.get("pnl_yen_100")
        if fill_t is None or fill_px is None or exit_t is None or pnl is None:
            continue
        hold = t.get("hold_sec")
        if hold is None:
            hold = src.get("hold_sec")
        if hold is None:
            hold = float(exit_t) - float(fill_t)
        out.append(
            {
                "date": str(t.get("date") or src.get("date") or ""),
                "symbol": str(t.get("symbol") or src.get("symbol") or ""),
                "signal_t0": src.get("signal_t0") if src.get("signal_t0") is not None else t.get("t0"),
                "entry_fill_t": float(fill_t),
                "entry_fill_price": float(fill_px),
                "exit_fill_t": float(exit_t),
                "exit_fill_price": None if exit_px is None else float(exit_px),
                "actual_pnl_yen100": float(pnl),
                "hold_sec": float(hold),
                "exec_id": str(src.get("exec_id") or ""),
                "candidate_id": str(src.get("candidate_id") or ""),
                "exit_reason": str(t.get("exit_reason") or src.get("exit_reason") or ""),
            }
        )
    return out


def recover_positive_control(cid: str, expected_n: int, expected_pnl: float) -> dict[str, Any]:
    packed = load_st_rows(cid)
    rows = list(packed.get("rows") or [])
    if packed.get("missing_days") or not rows:
        return {
            "CONTROL_ID": cid,
            "CONTROL_EXECUTED_COHORT_AVAILABLE": False,
            "reason": "MISSING_HARVEST" if packed.get("missing_days") else "EMPTY_ROWS",
            "missing_days": packed.get("missing_days"),
            "trades": [],
        }
    trades = occupancy_trades(rows)
    pnl = float(sum(float(t["actual_pnl_yen100"]) for t in trades))
    n = int(len(trades))
    ok = n == int(expected_n) and abs(pnl - float(expected_pnl)) < 1e-6
    x1_n = sum(1 for t in trades if str(t.get("exec_id") or "") == "X1")
    return {
        "CONTROL_ID": cid,
        "CONTROL_EXECUTED_COHORT_AVAILABLE": bool(ok),
        "reason": None if ok else "LEDGER_MISMATCH",
        "TRADE_N": n,
        "EXPECTED_TRADE_N": int(expected_n),
        "ACTUAL_FULL_STRATEGY_TOTAL_PNL": pnl,
        "EXPECTED_PNL": float(expected_pnl),
        "SIGNAL_N": int(len(rows)),
        "WOULD_FILL_N": int(sum(1 for r in rows if r.get("WOULD_FILL"))),
        "X1_EXEC_ID_N": int(x1_n),
        "X1_EXEC_ID_ALL": bool(trades) and x1_n == n,
        "missing_days": packed.get("missing_days"),
        "trades": trades if ok else [],
    }


def recover_csb() -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    missing: list[str] = []
    for day in DEVELOPMENT_DAYS:
        path = CSB_CACHE / f"day_{day}_base.json"
        body = _load(path)
        if str(body.get("date") or "") != str(day) or body.get("trades") is None:
            missing.append(str(day))
            continue
        for t in list(body.get("trades") or []):
            fill_t = t.get("fill_t")
            fill_px = t.get("fill_price")
            exit_t = t.get("exit_t")
            exit_px = t.get("exit_price")
            if fill_t is None or fill_px is None or exit_t is None or exit_px is None:
                continue
            pnl = float(compute_pnl_yen_100(float(fill_px), float(exit_px)))
            hold = t.get("hold_sec")
            if hold is None:
                hold = float(exit_t) - float(fill_t)
            trades.append(
                {
                    "date": str(t.get("date") or day),
                    "symbol": str(t.get("symbol") or ""),
                    "signal_t0": t.get("signal_t0"),
                    "entry_fill_t": float(fill_t),
                    "entry_fill_price": float(fill_px),
                    "exit_fill_t": float(exit_t),
                    "exit_fill_price": float(exit_px),
                    "actual_pnl_yen100": pnl,
                    "hold_sec": float(hold),
                    "exec_id": "X1",
                    "candidate_id": NEGATIVE_CONTROL_ID,
                    "exit_reason": str(t.get("exit_reason") or ""),
                }
            )
    pnl = float(sum(float(t["actual_pnl_yen100"]) for t in trades))
    n = int(len(trades))
    ok = (not missing) and n == int(CSB_EXPECTED_TRADE_N) and abs(pnl - float(CSB_EXPECTED_PNL)) < 1e-6
    return {
        "CONTROL_ID": NEGATIVE_CONTROL_ID,
        "CONTROL_EXECUTED_COHORT_AVAILABLE": bool(ok),
        "reason": None if ok else "LEDGER_MISMATCH_OR_MISSING",
        "TRADE_N": n,
        "EXPECTED_TRADE_N": int(CSB_EXPECTED_TRADE_N),
        "ACTUAL_FULL_STRATEGY_TOTAL_PNL": pnl,
        "EXPECTED_PNL": float(CSB_EXPECTED_PNL),
        "missing_days": missing,
        "trades": trades if ok else [],
    }


def recover_all() -> dict[str, Any]:
    p1 = recover_positive_control(P1_ID, P1_EXPECTED_TRADE_N, P1_EXPECTED_PNL)
    p2 = recover_positive_control(P2_ID, P2_EXPECTED_TRADE_N, P2_EXPECTED_PNL)
    neg = recover_csb()
    return {"P1": p1, "P2": p2, "NEGATIVE": neg}
