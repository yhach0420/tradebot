"""Stage-hard-stopped equity panel. STAGE_A cannot materialize dates after 20251126."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST
from research.causal_driver_pb1.cross_sectional_discovery import STAGE_A_HARD_STOP
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.phase2_discovery.panels import load_stock_panel


def load_equity_stage(
    *,
    symbols: tuple[str, ...],
    dates: list[str],
    ledger: StageLedger,
    stage: str,
) -> dict[str, Any]:
    if stage == "STAGE_A":
        if any(d > STAGE_A_HARD_STOP for d in dates):
            raise RuntimeError("stage_a_dates_beyond_hard_stop")
        date_lo, date_hi = DEV_FIRST, STAGE_A_HARD_STOP
        assert date_hi == DEV_LAST == STAGE_A_HARD_STOP
    elif stage == "STAGE_B":
        if any(d <= STAGE_A_HARD_STOP for d in dates):
            raise RuntimeError("stage_b_contains_dev_dates")
        if any(d >= FV_FIRST for d in dates):
            raise RuntimeError("stage_b_opened_fv")
        date_lo, date_hi = C1_FIRST, C1_LAST
    else:
        raise RuntimeError(f"unknown_stage:{stage}")
    return load_stock_panel(
        symbols=symbols,
        dates=dates,
        date_lo=date_lo,
        date_hi=date_hi,
        ledger=ledger,
        stage=stage,
    )
