"""Load jpx_tick_size_yen from low_price_risk_review without executing its heavy imports.

Source of truth: research.low_price_risk_review.jpx_tick_size_yen (JPX Other Issues table).
Do not substitute the simplified eec tick table.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Callable


@lru_cache(maxsize=1)
def _load() -> Callable[[float], float]:
    path = Path(__file__).resolve().parents[1] / "low_price_risk_review.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "jpx_tick_size_yen"]
    if len(fns) != 1:
        raise RuntimeError("jpx_tick_size_yen_not_found")
    ns: dict = {}
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(path), "exec"), ns)
    return ns["jpx_tick_size_yen"]


def jpx_tick_size_yen(price: float, *, narrow_topix500: bool = False) -> float:
    return float(_load()(float(price), narrow_topix500=narrow_topix500))
