"""Attach causal cash states at the frozen 9 BOTH_UP confirmations."""
from __future__ import annotations

import math
from typing import Any, Optional

from research.futures_context_day1_effect_check_v1.engine import mean, stock_outcomes_at
from research.futures_reversal_confirmed_entry_day1_v1.engine import run_state_machine
from research.futures_reversal_x_cash_participation_day1_v1 import (
    CASH_LOOKBACK_SEC,
    FROZEN_TRIGGERS,
    TRADING_DATE,
)


def ew_past(tapes: dict[str, Any], names: list[str], te: float) -> Optional[float]:
    xs: list[float] = []
    for sym in names:
        r = tapes[sym].past_mid_ret(te, CASH_LOOKBACK_SEC)
        if r is None:
            continue
        try:
            x = float(r)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            xs.append(x)
    return mean(xs)


def cash_flags(
    *,
    market: Optional[float],
    top: Optional[float],
    bottom: Optional[float],
) -> dict[str, bool]:
    cash1 = market is not None and market > 0
    cash2 = top is not None and top > 0
    cash3 = top is not None and bottom is not None and (float(top) - float(bottom)) > 0
    return {
        "CASH1": bool(cash1),
        "CASH2": bool(cash2),
        "CASH3": bool(cash3),
        "CASH_CONFIRM": bool(cash1 and cash2 and cash3),
    }


def symbol_longs(quotes: dict[str, Any], names: list[str], te: float, horizon_sec: int = 600) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for sym in names:
        rec = stock_outcomes_at(quotes[sym], te, horizon_sec)
        v = rec.get("LONG_EXEC_MARKOUT_BPS")
        out[sym] = None if v is None else float(v)
    return out


def attach_cash(*, native_root: Any, day: str = TRADING_DATE) -> dict[str, Any]:
    machine = run_state_machine(native_root=native_root, day=day)
    events = list(machine["events"])
    got = tuple(e.get("trigger_hm") for e in events)
    if got != FROZEN_TRIGGERS:
        raise RuntimeError(f"frozen trigger inventory mismatch: {got} != {FROZEN_TRIGGERS}")
    tapes = machine["tapes"]
    quotes = machine["quotes"]
    symbols = list(machine["symbols"])
    leak = int(machine["future_leakage_n"])
    attached: list[dict[str, Any]] = []
    for ev in events:
        te = float(ev["trigger_t"])
        top = list(ev["top16"])
        middle = list(ev["middle16"])
        bottom = list(ev["bottom16"])
        m48 = ew_past(tapes, symbols, te)
        mtop = ew_past(tapes, top, te)
        mbot = ew_past(tapes, bottom, te)
        flags = cash_flags(market=m48, top=mtop, bottom=mbot)
        tb = None if mtop is None or mbot is None else float(mtop) - float(mbot)
        row = dict(ev)
        row.update(flags)
        row["ew_past_180"] = m48
        row["top_past_180"] = mtop
        row["bottom_past_180"] = mbot
        row["top_bottom_past_180"] = tb
        row["top_long_10m_by_symbol"] = symbol_longs(quotes, top, te, 600)
        attached.append(row)
    return {
        "symbols": symbols,
        "future_leakage_n": leak,
        "trigger_n": len(attached),
        "events": attached,
        "frozen_triggers": list(FROZEN_TRIGGERS),
        "episode_n": machine["episode_n"],
    }
