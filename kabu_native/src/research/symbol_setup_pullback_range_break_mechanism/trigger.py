"""Prior-3-bar range break. This does not replace the frozen baseline predicate."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def range_break(ind: dict[str, np.ndarray], i: int) -> tuple[bool, Optional[float]]:
    """Return (trigger, local resistance).

    A missing finite prior high returns (False, None): STRUCTURE_NOT_AVAILABLE.
    The current bar high is not part of the resistance.
    """
    if int(i) < 3:
        return False, None
    highs = [float(ind["high"][int(i) - k]) for k in (3, 2, 1)]
    close = float(ind["close"][i])
    ema9 = float(ind["ema9"][i])
    upper = float(ind["bb_upper"][i])
    if not all(_finite(v) for v in highs + [close, ema9, upper]):
        return False, None
    local = max(highs)
    return bool(close > ema9 and close > local and close <= upper), float(local)
