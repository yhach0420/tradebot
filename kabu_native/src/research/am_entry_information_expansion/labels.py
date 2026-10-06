"""3-class profitable-fill label. Nonfill is NEUTRAL, not a yen-zero regression target."""
from __future__ import annotations

from typing import Any

from research.am_entry_information_expansion import CLASS_LOSS, CLASS_NEUTRAL, CLASS_WIN, TARGET
from research.canonical_entry_performance_rebase.analyze import _f


def profitable_fill_class(r: dict[str, Any]) -> str:
    filled = int(r.get("Y_FILL5") or 0) == 1
    pnl = _f(r.get("pnl_yen_100"))
    if pnl is None:
        pnl = 0.0
    if filled and float(pnl) > 0:
        return CLASS_WIN
    if filled and float(pnl) < 0:
        return CLASS_LOSS
    return CLASS_NEUTRAL


def attach_target(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec[TARGET] = profitable_fill_class(rec)
        out.append(rec)
    return out
