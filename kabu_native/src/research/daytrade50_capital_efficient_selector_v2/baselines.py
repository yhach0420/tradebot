"""Read-only baselines. CURRENT50 and AUTO50 V1 stay comparison references."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from universe.opening_screen import volatility_liquidity_score

CURRENT50_BASELINE_ID = "CURRENT50_CORE10_DYNAMIC40"
AUTO50_BASELINE_ID = "AUTO50_V1_PRIOR_RANGE_LOG10_TV"


def auto50_v1_score(range_pct: float, trading_value: float) -> float | None:
    """Prior-day range percent times log10(prior trading value). Not the V2 score."""
    score = volatility_liquidity_score(range_pct, trading_value)
    if score is None:
        return None
    return float(score)


def load_current50_symbols(path: Path) -> list[str]:
    """Read a saved Core10+Dynamic40 or frozen membership. Does not write it."""
    if path.suffix.lower() == ".json":
        body = json.loads(path.read_text(encoding="utf-8"))
        raw = body.get("canonical_symbols") or body.get("symbols") or []
        return [str(symbol).split(".")[0].split("@")[0] for symbol in raw]
    symbols: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or row.get("symbol_key") or "").split(".")[0].split("@")[0]
            if symbol:
                symbols.append(symbol)
    return symbols


def baseline_catalog() -> dict[str, Any]:
    return {
        "A": {
            "id": CURRENT50_BASELINE_ID,
            "role": "comparison_only",
            "core10": True,
        },
        "B": {
            "id": AUTO50_BASELINE_ID,
            "role": "comparison_only",
            "formula": "prior_range_pct * log10(prior_trading_value)",
            "core10": False,
        },
        "C": {
            "id": "DAYTRADE50_SELECTOR_V2",
            "role": "candidate",
            "core10": False,
            "requires_user_g": True,
        },
    }
