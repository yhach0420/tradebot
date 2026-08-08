"""Fixed grid snap rules (mechanical; no Evaluation retune)."""
from __future__ import annotations

from typing import Optional, Sequence


def snap_ceil(value: Optional[float], grid: Sequence[float]) -> Optional[float]:
    """Smallest grid >= value (stop / giveback / time)."""
    if value is None or value != value:
        return None
    v = float(value)
    for g in grid:
        if g + 1e-12 >= v:
            return float(g)
    return float(grid[-1])


def snap_floor(value: Optional[float], grid: Sequence[float]) -> Optional[float]:
    """Largest grid <= value (target / activation)."""
    if value is None or value != value:
        return None
    v = float(value)
    best = None
    for g in grid:
        if g - 1e-12 <= v:
            best = float(g)
        else:
            break
    if best is None:
        return float(grid[0])
    return best


def grid_index(value: Optional[float], grid: Sequence[float]) -> Optional[int]:
    if value is None:
        return None
    for i, g in enumerate(grid):
        if abs(g - float(value)) < 1e-9:
            return i
    return None


def disagree_by_more_than_one_step(
    a: Optional[float], b: Optional[float], grid: Sequence[float],
) -> bool:
    ia, ib = grid_index(a, grid), grid_index(b, grid)
    if ia is None or ib is None:
        return a != b
    return abs(ia - ib) > 1
