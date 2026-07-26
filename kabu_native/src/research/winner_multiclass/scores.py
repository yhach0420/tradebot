"""Expected value and entry quality scores."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_multiclass.labels import CLASS_ORDER, CLASS_TO_ID, MulticlassRow


def estimate_payoffs_train(labeled: Sequence[MulticlassRow]) -> dict[str, float]:
    """Train-only mean pnl by class (signed)."""
    by = {c: [] for c in CLASS_ORDER}
    for r in labeled:
        by[r.class_label].append(r.pnl_5bps)
    out = {}
    for c, xs in by.items():
        out[c] = float(np.mean(xs)) if xs else 0.0
    # ensure STOP/NP costs are non-positive for formula clarity
    out["STOP"] = min(out["STOP"], -1.0) if by["STOP"] else -1000.0
    out["NoProgress"] = min(out["NoProgress"], -1.0) if by["NoProgress"] else -200.0
    return out


def expected_value_score(proba: np.ndarray, payoffs: Mapping[str, float]) -> np.ndarray:
    """EV = Σ P(c) * payoff(c) with costs as signed means from train."""
    # proba columns follow CLASS_ORDER ids
    w = np.array([payoffs[c] for c in CLASS_ORDER], dtype=float)
    return proba @ w


def entry_quality_score_v1(proba: np.ndarray) -> np.ndarray:
    """EQ1 = P(W) - P(S) - P(NP)."""
    return (
        proba[:, CLASS_TO_ID["Winner"]]
        - proba[:, CLASS_TO_ID["STOP"]]
        - proba[:, CLASS_TO_ID["NoProgress"]]
    )


def entry_quality_score_v2(proba: np.ndarray, *, coverage: Optional[np.ndarray] = None) -> np.ndarray:
    """EQ2 = 1.2*P(W) - 1.4*P(S) - 1.0*P(NP) + 0.2*P(N)  (+ coverage bonus)."""
    s = (
        1.2 * proba[:, CLASS_TO_ID["Winner"]]
        - 1.4 * proba[:, CLASS_TO_ID["STOP"]]
        - 1.0 * proba[:, CLASS_TO_ID["NoProgress"]]
        + 0.2 * proba[:, CLASS_TO_ID["Normal"]]
    )
    if coverage is not None:
        s = s + 0.05 * coverage
    return s


def scores_from_proba(
    proba: np.ndarray,
    payoffs: Mapping[str, float],
    *,
    coverage: Optional[np.ndarray] = None,
) -> dict[str, np.ndarray]:
    return {
        "winner_prob": proba[:, CLASS_TO_ID["Winner"]],
        "stop_prob": proba[:, CLASS_TO_ID["STOP"]],
        "no_progress_prob": proba[:, CLASS_TO_ID["NoProgress"]],
        "normal_prob": proba[:, CLASS_TO_ID["Normal"]],
        "expected_value_score": expected_value_score(proba, payoffs),
        "entry_quality_score": entry_quality_score_v2(proba, coverage=coverage),
        "entry_quality_score_v1": entry_quality_score_v1(proba),
    }


SCORE_FORMULAS = {
    "expected_value_score": (
        "EV = P(W)*payoff_W + P(N)*payoff_N + P(S)*payoff_S + P(NP)*payoff_NP ; "
        "payoffs = train-only mean pnl_5bps by class"
    ),
    "entry_quality_score": "EQ2 = 1.2*P(W) - 1.4*P(S) - 1.0*P(NP) + 0.2*P(N) + 0.05*coverage",
    "entry_quality_score_v1": "EQ1 = P(W) - P(S) - P(NP)",
}
