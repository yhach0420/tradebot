"""Build neutral-anchor panel: AGG/PASSIVE outcomes + pre-entry features."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x34a_execution_policy.arms import (
    evaluate_aggressive,
    evaluate_passive_or_inside,
    snapshot_t0,
)
from research.e1_x34a_execution_policy import ARM_PASSIVE

from . import WAIT_PASSIVE_SEC
from .features import attach_universe_median, preentry_from_board


def build_panel_row(
    board: dict[str, np.ndarray],
    *,
    date: str,
    symbol: str,
    session: str,
    signal_t: float,
) -> dict[str, Any] | None:
    # Aggressive path (X33C/X34A contract)
    snap = snapshot_t0(board, signal_t)
    # Aggressive needs ask; snapshot may fail if no bid — still try aggressive via arms path
    from research.e1_x28_executable_joint.board import first_valid_quote

    qa = first_valid_quote(board, signal_t, side="ask")
    if qa["status"] != "OK":
        return None
    ask0 = float(qa["price"])
    qb = first_valid_quote(board, signal_t, side="bid")
    if qb["status"] == "OK" and snap.get("ok"):
        snap_use = snap
    elif qb["status"] == "OK":
        bid0 = float(qb["price"])
        snap_use = {
            "ok": True,
            "ask": ask0,
            "bid": bid0,
            "mid": (ask0 + bid0) / 2.0,
            "spread_bps": (ask0 - bid0) / ((ask0 + bid0) / 2.0) * 10000.0,
        }
    else:
        snap_use = {"ok": True, "ask": ask0, "bid": None, "mid": ask0, "spread_bps": None}

    agg = evaluate_aggressive(
        board, date=date, session=session, signal_t=signal_t, snap=snap_use,
    )
    if not agg.get("filled"):
        return None

    if snap_use.get("bid") is None:
        pas = {
            "filled": False,
            "reason": "NO_BID_FOR_LIMIT",
            "ret_300": None,
            "ret_600": None,
            "ret_900": None,
        }
    else:
        pas = evaluate_passive_or_inside(
            board,
            date=date,
            session=session,
            signal_t=signal_t,
            snap=snap_use,
            arm=ARM_PASSIVE,
            wait_sec=WAIT_PASSIVE_SEC,
        )

    def _net(blob: dict, H: int, *, unfilled_zero: bool) -> float:
        if unfilled_zero and not blob.get("filled"):
            return 0.0
        if blob.get(f"ret_{H}_valid") and blob.get(f"ret_{H}") is not None:
            return float(blob[f"ret_{H}"])
        return 0.0 if unfilled_zero else float("nan")

    feats = preentry_from_board(board, signal_t)
    # Forbidden: never store fill as feature (outcome only)
    row = {
        "date": date,
        "symbol": symbol,
        "session": session,
        "signal_t": float(signal_t),
        # outcomes
        "AGG_NET_300": _net(agg, 300, unfilled_zero=False) if agg.get("ret_300_valid") else 0.0,
        "AGG_NET_600": _net(agg, 600, unfilled_zero=False) if agg.get("ret_600_valid") else 0.0,
        "AGG_NET_900": _net(agg, 900, unfilled_zero=False) if agg.get("ret_900_valid") else 0.0,
        "PASSIVE_FILL": bool(pas.get("filled")),
        "PASSIVE_NET_300": _net(pas, 300, unfilled_zero=True),
        "PASSIVE_NET_600": _net(pas, 600, unfilled_zero=True),
        "PASSIVE_NET_900": _net(pas, 900, unfilled_zero=True),
        # mid diagnostics (from aggressive mid path; not for adoption alone)
        "MID300": float(agg["mid_300"]) if agg.get("mid_300_valid") else None,
        "MID600": float(agg["mid_600"]) if agg.get("mid_600_valid") else None,
        "MFE": float(agg["mfe"]) if agg.get("mfe") is not None and np.isfinite(agg["mfe"]) else None,
        "MAE": float(agg["mae"]) if agg.get("mae") is not None and np.isfinite(agg["mae"]) else None,
        **feats,
    }
    # fix agg nets when invalid → use 0 only if we still include episode (X33C had valid paths)
    for H in (300, 600, 900):
        if not agg.get(f"ret_{H}_valid"):
            row[f"AGG_NET_{H}"] = 0.0  # rare within ok scan
        else:
            row[f"AGG_NET_{H}"] = float(agg[f"ret_{H}"])
    return row


def build_panel(
    planned: list[dict[str, Any]],
    boards: dict,
) -> list[dict[str, Any]]:
    rows = []
    for i, a in enumerate(planned):
        board = boards.get((a["date"], a["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        rec = build_panel_row(
            board,
            date=a["date"],
            symbol=a["symbol"],
            session=a["session"],
            signal_t=float(a["grid_epoch"]),
        )
        if rec is None:
            continue
        rows.append(rec)
        if (i + 1) % 2000 == 0:
            print(f"  panel {i+1}/{len(planned)} -> {len(rows)}", flush=True)
    attach_universe_median(rows)
    return rows
