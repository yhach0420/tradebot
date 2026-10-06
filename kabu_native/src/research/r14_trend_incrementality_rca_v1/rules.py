"""Frozen nested predicates. No threshold search."""
from __future__ import annotations

from typing import Any

from research.r14_trend_incrementality_rca_v1 import PRIOR5_RANGE_THR, PRIOR5_RET_THR, R15_THR


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def hit_t15(row: dict[str, Any]) -> bool:
    v = row.get("r15")
    return _finite(v) and float(v) > float(R15_THR)


def hit_t15_p5(row: dict[str, Any]) -> bool:
    p = row.get("prior5_ret")
    return hit_t15(row) and _finite(p) and float(p) > float(PRIOR5_RET_THR)


def hit_r14(row: dict[str, Any]) -> bool:
    r = row.get("prior5_range_rel")
    return hit_t15_p5(row) and _finite(r) and float(r) > float(PRIOR5_RANGE_THR)


RULES = {
    "BASE": lambda _row: True,
    "T15": hit_t15,
    "T15_P5": hit_t15_p5,
    "R14_FULL": hit_r14,
}

FEATURE_SEMANTICS = {
    "r15": {
        "formula": "clock_ret: close[T] / close[T-15 clock minutes] - 1, skipping windows that cross lunch; computed through completed event bar T",
        "source": "research.one_minute_native_playbook_discovery_v1.states.clock_ret(..., n=15)",
        "direction_normalized": False,
        "R15_DIRECTION_NORMALIZED": False,
        "note": "Unsigned price return. Positive means the last 15 clock minutes closed higher. Not multiplied by displacement sign.",
    },
    "prior5_ret": {
        "formula": "close[T] / close[session_idx[pos-5]] - 1 using prior 5 completed session bars excluding the event bar from the lookback start, numerator is event close",
        "source": "research.native_causal_context_stack_discovery_v1.features.local_structure",
        "direction_normalized": False,
        "PRIOR5_RET_DIRECTION_NORMALIZED": False,
        "note": "Unsigned 5-bar close-to-close return. Positive means price is higher than 5 session bars earlier. Not multiplied by displacement sign.",
    },
    "prior5_range_rel": {
        "formula": "(max high - min low of prior 5 completed session bars) / median bar range of prior 20 completed session bars",
        "source": "research.native_causal_context_stack_discovery_v1.features.local_structure",
        "direction_normalized": False,
        "note": "Unsigned local range expansion. Same meaning for bullish and bearish events.",
    },
}
