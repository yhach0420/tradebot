"""Running evidence totals. State episodes and fills keep separate denominators."""
from __future__ import annotations

from typing import Any, Optional

from research.symbol_setup_exit_causal_evidence import COMPARISON_MIN_N, FILL_TARGET, HISTORICAL, STATE_TARGET


def _rate(n: int, d: int) -> Optional[float]:
    return None if d <= 0 else n / float(d)


def _block(prefix: str, counts: dict[str, int]) -> dict[str, Any]:
    d = int(counts["n"])
    return {
        f"{prefix}_n": d,
        f"{prefix}_temporary_rate": _rate(int(counts["temporary"]), d),
        f"{prefix}_temporary_and_price_recovered_rate": _rate(int(counts["price_recovered"]), d),
        f"{prefix}_terminal_rate": _rate(int(counts["terminal"]), d),
        f"{prefix}_ambiguous_rate": _rate(int(counts["ambiguous"]), d),
    }


def summarize(prospective_state: dict[str, int], prospective_fills: dict[str, int]) -> dict[str, Any]:
    hist_state = {
        "n": HISTORICAL["slope_state_n"],
        "temporary": HISTORICAL["temporary_n"],
        "price_recovered": HISTORICAL["temporary_and_price_recovered_n"],
        "terminal": HISTORICAL["terminal_n"],
        "ambiguous": HISTORICAL["ambiguous_n"],
    }
    hist_fill = {
        "n": HISTORICAL["filled_slope_n"],
        "temporary": 3,
        "price_recovered": HISTORICAL["filled_temporary_and_price_recovered_n"],
        "terminal": 1,
        "ambiguous": 1,
    }
    comb_state = {k: int(hist_state[k]) + int(prospective_state[k]) for k in hist_state}
    comb_fill = {k: int(hist_fill[k]) + int(prospective_fills[k]) for k in hist_fill}
    fast_slow = int(HISTORICAL["fast_slow_episode_n"]) + int(prospective_state.get("fast_slow", 0))
    comparison = "NOT_AVAILABLE"
    if comb_state["n"] >= COMPARISON_MIN_N and fast_slow >= COMPARISON_MIN_N:
        comparison = "AVAILABLE"
    return {
        "state": {
            "historical": _block("historical", hist_state),
            "prospective": _block("prospective", prospective_state),
            "combined": _block("combined", comb_state),
            "combined_n": comb_state["n"],
            "target": STATE_TARGET,
        },
        "fills": {
            "historical": _block("historical", hist_fill),
            "prospective": _block("prospective", prospective_fills),
            "combined": _block("combined", comb_fill),
            "combined_n": comb_fill["n"],
            "target": FILL_TARGET,
        },
        "SLOPE_VS_FAST_SLOW_NOISE_COMPARISON": comparison,
        "fast_slow_episode_n": fast_slow,
        "target_reached": comb_state["n"] >= STATE_TARGET and comb_fill["n"] >= FILL_TARGET,
    }
