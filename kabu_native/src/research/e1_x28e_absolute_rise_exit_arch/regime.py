"""Frozen Regime Gate library R0–R5 (no dynamic addition)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import (
    ADDITIONAL_STRESS,
    DISCOVERY,
    EVALUATION,
    PERIOD_BLOCKS,
    REGIME_LIBRARY,
    STRESS_DAY,
    CONSUMED_DAY,
)


def period_mask(dates: np.ndarray, block: str) -> np.ndarray:
    if block == "Discovery":
        return np.isin(dates, list(DISCOVERY))
    if block == "Evaluation":
        return np.isin(dates, list(EVALUATION))
    if block == "20260803":
        return dates == STRESS_DAY
    if block == "20260804":
        return dates == CONSUMED_DAY
    if block == "20260805_07":
        return np.isin(dates, list(ADDITIONAL_STRESS))
    raise ValueError(block)


def regime_mask(rows: list[dict[str, Any]], regime_id: str) -> np.ndarray:
    """Global regime only — never candidate-specific."""
    n = len(rows)
    if regime_id == "R0_NO_REGIME_GATE":
        return np.ones(n, dtype=bool)

    med60 = np.array([
        float(r["universe_median_return_60s"]) if r.get("universe_median_return_60s") is not None else np.nan
        for r in rows
    ])
    med180 = np.array([
        float(r["universe_median_return_180s"]) if r.get("universe_median_return_180s") is not None else np.nan
        for r in rows
    ])
    adv = np.array([
        float(r["advancing_symbol_fraction"]) if r.get("advancing_symbol_fraction") is not None else np.nan
        for r in rows
    ])
    dec = np.array([
        float(r["declining_symbol_fraction"]) if r.get("declining_symbol_fraction") is not None else np.nan
        for r in rows
    ])

    if regime_id == "R1_UNIVERSE_MEDIAN_RETURN_60S_GT0":
        return np.isfinite(med60) & (med60 > 0)
    if regime_id == "R2_UNIVERSE_MEDIAN_RETURN_180S_GT0":
        return np.isfinite(med180) & (med180 > 0)
    if regime_id == "R3_ADVANCING_GT_DECLINING":
        return np.isfinite(adv) & np.isfinite(dec) & (adv > dec)
    if regime_id == "R4_MED60_AND_ADV_GT_DEC":
        return np.isfinite(med60) & (med60 > 0) & np.isfinite(adv) & np.isfinite(dec) & (adv > dec)
    if regime_id == "R5_MED180_AND_ADV_GT_DEC":
        return np.isfinite(med180) & (med180 > 0) & np.isfinite(adv) & np.isfinite(dec) & (adv > dec)
    raise ValueError(f"unknown regime {regime_id}")


def assert_regime_library_frozen() -> list[str]:
    assert len(REGIME_LIBRARY) == 6
    return list(REGIME_LIBRARY)


def regime_stability(
    *,
    eval_abs: Optional[float],
    stress_abs: Optional[float],
    eval_delta: Optional[float],
    stress_delta: Optional[float],
    disc_abs: Optional[float],
    d0803_abs: Optional[float],
    d0804_abs: Optional[float],
) -> bool:
    """Minimum stability for Gate candidacy."""
    if eval_abs is None or stress_abs is None or eval_delta is None or stress_delta is None:
        return False
    if not (eval_abs > 0 and stress_abs > 0 and eval_delta > 0 and stress_delta > 0):
        return False
    # extreme reversal diagnostic: Discovery / 0803 / 0804 all strongly opposite
    # reject if Discovery and both diagnostics are strongly negative while gates pass eval/stress
    # (soft check: any of disc/0803/0804 is None → ok; if all three < -20bps treat as extreme)
    checks = [x for x in (disc_abs, d0803_abs, d0804_abs) if x is not None]
    if len(checks) >= 2 and all(x < -20.0 for x in checks):
        return False
    return True


def select_global_regime(gate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Pick at most one global LONG regime. No candidate-specific selection.
    Prefer simpler gates among stable ones (R1 < R2 < R3 < R4 < R5 complexity).
    """
    stable = [g for g in gate_rows if g.get("stable_candidate")]
    if not stable:
        # check if R0 already absolute+relative on eval+0805_07
        r0 = next((g for g in gate_rows if g["regime_id"] == "R0_NO_REGIME_GATE"), None)
        if r0 and (r0.get("Evaluation_abs") or 0) > 0 and (r0.get("20260805_07_abs") or 0) > 0:
            return {
                "conclusion": "NO_GLOBAL_REGIME_NEEDED",
                "selected_regime": "R0_NO_REGIME_GATE",
                "stable_gates": [],
            }
        # any mixed signal?
        any_pos = any(
            (g.get("Evaluation_abs") or 0) > 0 or (g.get("20260805_07_abs") or 0) > 0
            for g in gate_rows if g["regime_id"] != "R0_NO_REGIME_GATE"
        )
        if any_pos:
            return {"conclusion": "REGIME_SIGNAL_MIXED", "selected_regime": None, "stable_gates": []}
        return {
            "conclusion": "NO_STABLE_ABSOLUTE_RISE_REGIME",
            "selected_regime": None,
            "stable_gates": [],
        }

    # prefer simpler: R1, R2, R3, R4, R5 order
    order = {rid: i for i, rid in enumerate(REGIME_LIBRARY)}
    stable.sort(key=lambda g: order.get(g["regime_id"], 99))
    best = stable[0]
    return {
        "conclusion": "GLOBAL_REGIME_SUPPORTED",
        "selected_regime": best["regime_id"],
        "stable_gates": [g["regime_id"] for g in stable],
    }
