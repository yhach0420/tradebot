"""Mismatch taxonomy vs P1 canonical fills. No new fill rule."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.fixed_anchor_mechanism_audit_p3_0.grid import hm_epoch
from research.fixed_selection_diagnostic_reconcile_p3_0r.boards import ticks_in_wait
from small_paper.v1r_primary_runtime import WAIT_SEC


def grid_t0(day: str, anchor: str) -> Optional[float]:
    try:
        h, m = [int(x) for x in str(anchor).split(":")]
    except Exception:
        return None
    return hm_epoch(day, h, m)


def classify_canonical_fill(
    trade: dict[str, Any],
    selected_row: Optional[dict[str, Any]],
    board: Optional[dict],
    *,
    diag_fill: Optional[dict[str, Any]],
    recon_limit: Optional[float],
) -> dict[str, Any]:
    day = str(trade["date"])
    an = str(trade.get("anchor_time") or "")
    t0_grid = grid_t0(day, an)
    canon_t0 = t0_grid
    canon_lim = trade.get("limit")
    canon_ft = trade.get("fill_time")
    canon_px = trade.get("fill_price")
    sess = str(trade.get("session") or "AM")

    out: dict[str, Any] = {
        "date": day,
        "anchor_time": an,
        "symbol": trade.get("symbol"),
        "signal_time": canon_t0,
        "limit_price": canon_lim,
        "fill_time": canon_ft,
        "fill_price": canon_px,
        "selected_present": selected_row is not None,
        "recon_limit": recon_limit,
        "diag_fill_found": bool(diag_fill and diag_fill.get("filled")),
        "diag_fill_time": (diag_fill or {}).get("fill_t"),
        "diag_fill_price": (diag_fill or {}).get("fill_price"),
        "klass": "OTHER",
        "note": "",
    }
    if selected_row is None:
        out["klass"] = "CANDIDATE_MISSING"
        out["note"] = "MISSING_SELECTED_CANONICAL_FILL"
        return out
    if t0_grid is None:
        out["klass"] = "ANCHOR_TIME_MISMATCH"
        out["note"] = "unparseable_anchor"
        return out
    row_t0 = selected_row.get("t0")
    if row_t0 is not None and abs(float(row_t0) - float(t0_grid)) > 1e-6:
        out["klass"] = "ANCHOR_TIME_MISMATCH"
        out["note"] = f"row_t0={row_t0} grid_t0={t0_grid}"
        return out
    if recon_limit is None:
        out["klass"] = "LIMIT_PRICE_MISMATCH"
        out["note"] = "no_bid_at_or_before_t0"
        return out
    if canon_lim is not None and abs(float(recon_limit) - float(canon_lim)) > 1e-6:
        out["klass"] = "LIMIT_PRICE_MISMATCH"
        out["note"] = f"recon_limit={recon_limit} canonical={canon_lim}"
        return out

    sess_end = session_end_epoch(day, sess)
    wait_end = float(t0_grid) + float(WAIT_SEC)
    if wait_end > float(sess_end) + 1e-9 and not (diag_fill and diag_fill.get("filled")):
        out["klass"] = "SESSION_BOUNDARY_MISMATCH"
        out["note"] = f"t0+WAIT beyond session_end={sess_end}"
        return out

    tw = ticks_in_wait(board if board is not None else {"t": None}, t0_grid, WAIT_SEC)
    if board is None or (board.get("t") is None) or board["t"].size == 0:
        out["klass"] = "SEQUENCE_BOUNDARY_MISMATCH"
        out["note"] = "empty_uncompacted_board"
        return out
    min_t = tw.get("min_t")
    if min_t is not None and float(min_t) > float(t0_grid) + float(WAIT_SEC):
        out["klass"] = "SEQUENCE_BOUNDARY_MISMATCH"
        out["note"] = f"board_starts_after_wait min_t={min_t} t0={t0_grid}"
        return out
    if tw["n"] == 0:
        out["klass"] = "SEQUENCE_BOUNDARY_MISMATCH"
        out["note"] = "no_capture_ticks_in_wait_window"
        return out

    if diag_fill and diag_fill.get("filled"):
        ft = float(diag_fill["fill_t"])
        px = float(diag_fill.get("fill_price") or recon_limit)
        time_ok = canon_ft is not None and abs(ft - float(canon_ft)) <= 5e-3
        px_ok = canon_px is None or abs(px - float(canon_px)) <= 1e-6
        if time_ok and px_ok:
            out["klass"] = "MATCH"
            return out
        if abs(ft - float(t0_grid)) <= float(WAIT_SEC) + 1e-6:
            out["klass"] = "ASK_EVENT_SELECTION_MISMATCH"
            out["note"] = f"diag_fill_t={ft} canonical={canon_ft}"
            return out
        out["klass"] = "WAIT_WINDOW_MISMATCH"
        out["note"] = f"diag_fill_t={ft} outside wait vs canonical={canon_ft}"
        return out

    reason = str((diag_fill or {}).get("reason") or "NO_FILL")
    if reason == "NO_BOARD_IN_WINDOW":
        out["klass"] = "SEQUENCE_BOUNDARY_MISMATCH"
        out["note"] = reason
        return out
    if reason == "NO_ASK_CROSS_IN_WINDOW":
        out["klass"] = "ASK_EVENT_SELECTION_MISMATCH"
        out["note"] = "ticks_in_wait_but_no_conservative_ask_cross"
        return out
    out["klass"] = "WAIT_WINDOW_MISMATCH"
    out["note"] = reason
    return out


def run_fill(board, t0: float, limit: float) -> dict[str, Any]:
    return find_ask_cross_fill(
        board,
        t0=float(t0),
        wait_sec=float(WAIT_SEC),
        limit_price=float(limit),
        sess_end=float(t0) + 3 * 3600.0,
    )
