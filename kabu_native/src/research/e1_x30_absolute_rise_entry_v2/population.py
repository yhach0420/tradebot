"""Population loader — reuse X28E combined historical (no 20260810+)."""
from __future__ import annotations

from typing import Any

from research.e1_x28e_absolute_rise_exit_arch.population import load_combined_population

from . import FORBIDDEN_FROM, HISTORICAL_DAYS


def load_population() -> list[dict[str, Any]]:
    rows = load_combined_population()
    dates = sorted({r["date"] for r in rows})
    assert not any(d >= FORBIDDEN_FROM for d in dates), dates
    assert set(HISTORICAL_DAYS).issubset(set(dates)), (
        f"missing days: {set(HISTORICAL_DAYS) - set(dates)}"
    )
    # drop anything outside the 14-day historical set
    rows = [r for r in rows if r["date"] in HISTORICAL_DAYS]
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows)
    print(f"=== X30 population n={len(rows)} days={len(dates)} ===", flush=True)
    return rows
