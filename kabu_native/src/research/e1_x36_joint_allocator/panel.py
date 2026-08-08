"""Build signal panel: features (t0-only) + fill/exit labels (train-only)."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x34b_entry_execution.features import attach_universe_median, preentry_from_board
from research.e1_x34c_passive_deployability.events import build_events
from research.e1_x35_passive_exit.paths import build_path
from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit

from . import HORIZON_SEC, LOT_QTY


def enrich_events(events: list[dict], boards: dict) -> list[dict[str, Any]]:
    """
    Attach:
      - pre-entry features (causal)
      - FILL_1S label
      - canonical FIXED600 exit (filled only) → FIXED600_NET_BPS, exit_time, OPPORTUNITY_VALUE_600
    """
    rows = [dict(e) for e in events]
    for r in rows:
        board = boards.get((r["date"], r["symbol"]))
        feats = preentry_from_board(board, float(r["signal_time"])) if board is not None else {}
        for k, v in feats.items():
            r[k] = v
        r["signal_t"] = float(r["signal_time"])  # alias for univ median
        r["FILL_1S"] = 1 if r.get("filled") else 0
        r["FIXED600_NET_BPS"] = None
        r["canonical_exit_time"] = None
        r["canonical_exit_ret_bps"] = None
        r["canonical_hold_sec"] = None
        r["canonical_exit_reason"] = None
        r["OPPORTUNITY_VALUE_600"] = 0.0
        r["canonical_ret_600"] = None  # for summarize key compatibility

        if r.get("filled") and r.get("fill_time") is not None and board is not None:
            sess_end = session_end_epoch(r["date"], r["session"])
            path = build_path(
                board,
                entry_price=float(r["fill_price"]),
                entry_t=float(r["fill_time"]),
                sess_end=sess_end,
            )
            ex = canonical_fixed_exit(path, HORIZON_SEC)
            if ex.get("ok"):
                ret = float(ex["exit_ret_bps"])
                r["FIXED600_NET_BPS"] = ret
                r["canonical_exit_ret_bps"] = ret
                r["canonical_exit_time"] = float(ex["exit_time"])
                r["canonical_hold_sec"] = float(ex["exit_off"])
                r["canonical_exit_reason"] = ex.get("reason")
                r["OPPORTUNITY_VALUE_600"] = ret
                r["canonical_ret_600"] = ret
                # also set fill_based_ret_600 alias for any legacy summarizers
                r["fill_based_ret_600"] = ret

    attach_universe_median(rows)
    # relative feature
    for r in rows:
        m = r.get("mid_ret_60s")
        u = r.get("univ_med_mid_ret_60s")
        r["rel_mid_ret_60s"] = (
            float(m) - float(u) if m is not None and u is not None
            and np.isfinite(m) and np.isfinite(u) else None
        )
    return rows


def pnl_yen(fill_price: float, ret_bps: float) -> float:
    """100 shares × price × bps/10000."""
    return float(LOT_QTY) * float(fill_price) * float(ret_bps) / 10000.0
