"""Matching covariate audit. What question did A vs B actually answer?"""
from __future__ import annotations

from typing import Any


def matching_audit() -> dict[str, Any]:
    nuis = ["symbol", "direction", "time-of-day bucket", "gap sign", "tod_min", "rng_rel", "gap_num", "target_ret_1m", "target_ret_3m", "target_ret_5m"]
    return {
        "current_covariates": nuis,
        "variables": [
            {
                "name": "symbol / DIR / gap / TOD bucket",
                "class": "CONFOUNDER",
                "keep": True,
                "why": "Different names and session regimes are not the treatment.",
            },
            {
                "name": "target_ret_1m / 3m / 5m",
                "class": "MEDIATOR / TREATMENT-DEFINING",
                "keep": False,
                "why": "A pullback setup is defined by recent adverse travel. Matching it away asks: given the same pullback already happened, does the MA label add anything? That is not 'does the playbook have edge vs not taking the pullback'.",
                "question_answered": "incremental label value after the tape has already pulled back the same amount",
                "question_not_answered": "whether the pullback-in-trend playbook itself has absolute path utility",
            },
            {
                "name": "rng_rel (recent realized range)",
                "class": "MEDIATOR",
                "keep": "context-dependent",
                "why": "Volatility expansion is often part of why the name is in play. Matching it can remove the economic state the setup is meant to represent.",
            },
            {
                "name": "5m SMA stack itself",
                "class": "TREATMENT-DEFINING",
                "keep": False,
                "why": "Correctly not matched. A vs B used this as treatment.",
            },
        ],
        "over_conditioned_on_setup_state": True,
        "absolute_gate_missing_in_prior_sma_tests": True,
        "relative_vs_bad_control": {
            "mtf_5m_example": "A matched 10m beat B while unconditional A 10m was negative (~-0.11 bps). Relative improvement over a worse 1m trigger is not trade utility.",
        },
        "future_rule": "For each playbook, declare confounders vs mediators vs treatment. Do not match on the pullback, the location touch, or the volatility state the setup requires unless the question is purely incremental labeling.",
    }
