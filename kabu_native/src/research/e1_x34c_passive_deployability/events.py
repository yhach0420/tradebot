"""Passive fill events: fill_time as ENTRY; signal- vs fill-based horizons."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x30_absolute_rise_entry_v2.labels import _scan_episode
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x34a_execution_policy.arms import evaluate_signal_all_arms

from . import HOLD_SEC_FOR_CAPACITY, HORIZONS, WAIT_SEC


def _horizon_from_entry(
    board: dict[str, np.ndarray],
    *,
    entry_price: float,
    entry_t: float,
    date: str,
    session: str,
) -> dict[str, Any]:
    sess_end = session_end_epoch(date, session)
    ep = _scan_episode(board, ask=entry_price, ask_t=entry_t, sess_end=sess_end)
    out: dict[str, Any] = {"ok": bool(ep.get("ok")), "mfe": ep.get("mfe"), "mae": ep.get("mae")}
    for H in HORIZONS:
        if ep.get(f"return_{H}_valid"):
            out[f"ret_{H}"] = float(ep[f"return_{H}"])
            out[f"ret_{H}_valid"] = True
        else:
            out[f"ret_{H}"] = None
            out[f"ret_{H}_valid"] = False
    return out


def build_events(
    planned: list[dict[str, Any]],
    boards: dict,
) -> list[dict[str, Any]]:
    """
    Same signal universe as X34A/B (aggressive-eligible).
    PASSIVE fill ⇒ ENTRY at fill_time; unfilled ⇒ not an ENTRY (opp contrib 0).
    """
    rows: list[dict[str, Any]] = []
    for i, a in enumerate(planned):
        board = boards.get((a["date"], a["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        rec = evaluate_signal_all_arms(
            board,
            date=a["date"],
            session=a["session"],
            signal_t=float(a["grid_epoch"]),
            wait_sec=WAIT_SEC,
        )
        if rec is None:
            continue
        pas = rec["passive"]
        signal_t = float(a["grid_epoch"])
        filled = bool(pas.get("filled"))
        row: dict[str, Any] = {
            "date": a["date"],
            "symbol": str(a["symbol"]),
            "session": a["session"],
            "signal_time": signal_t,
            "order_time": signal_t,
            "cancel_time": signal_t + WAIT_SEC,
            "limit_price": pas.get("limit_price"),
            "ask0": rec.get("ask0"),
            "bid0": rec.get("bid0"),
            "mid0": rec.get("mid0"),
            "spread_bps": rec.get("spread_bps"),
            "filled": filled,
            "entry_is_fill": filled,
            "CAPACITY_BLOCKED": False,
            "DUPLICATE_BLOCKED": False,
            "accepted": False,
        }
        if not filled:
            row.update({
                "fill_time": None,
                "fill_delay_ms": None,
                "fill_price": None,
                "reason": pas.get("reason"),
            })
            for H in HORIZONS:
                row[f"signal_based_ret_{H}"] = None
                row[f"fill_based_ret_{H}"] = None
                row[f"delta_fill_minus_signal_{H}"] = None
                # opportunity: unfilled = 0
                row[f"opp_signal_{H}"] = 0.0
                row[f"opp_fill_{H}"] = 0.0
            rows.append(row)
            continue

        fill_t = float(pas["fill_t"])
        fill_px = float(pas["fill_price"])
        row["fill_time"] = fill_t
        row["fill_delay_ms"] = float((fill_t - signal_t) * 1000.0)
        row["fill_price"] = fill_px
        row["cross_ask"] = pas.get("cross_ask")
        row["evidence"] = pas.get("evidence")
        row["exit_time_capacity"] = fill_t + HOLD_SEC_FOR_CAPACITY

        # signal/X34A-based returns (from arm, horizons from fill_t already)
        for H in HORIZONS:
            sig = float(pas[f"ret_{H}"]) if pas.get(f"ret_{H}_valid") else None
            row[f"signal_based_ret_{H}"] = sig
            row[f"opp_signal_{H}"] = float(sig) if sig is not None else 0.0

        fill_based = _horizon_from_entry(
            board,
            entry_price=fill_px,
            entry_t=fill_t,
            date=a["date"],
            session=a["session"],
        )
        for H in HORIZONS:
            fil = fill_based.get(f"ret_{H}") if fill_based.get(f"ret_{H}_valid") else None
            row[f"fill_based_ret_{H}"] = fil
            row[f"opp_fill_{H}"] = float(fil) if fil is not None else 0.0
            sig = row[f"signal_based_ret_{H}"]
            row[f"delta_fill_minus_signal_{H}"] = (
                float(fil - sig) if fil is not None and sig is not None else None
            )
        row["mfe"] = fill_based.get("mfe")
        row["mae"] = fill_based.get("mae")
        rows.append(row)
        if (i + 1) % 2000 == 0:
            print(
                f"  events {i+1}/{len(planned)} -> {len(rows)} "
                f"fills={sum(1 for r in rows if r['filled'])}",
                flush=True,
            )
    return rows
