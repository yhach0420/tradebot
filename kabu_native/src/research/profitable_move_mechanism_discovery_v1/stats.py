"""Day-level sign-flip permutation and Benjamini-Hochberg. No event-level iid."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x25_long_horizon_path.stats import bh_qvalues
from research.profitable_move_mechanism_discovery_v1 import PERM_ITERS, PERM_SEED


def sign_flip_p_one_sided(daily_means: np.ndarray, *, iters: int = PERM_ITERS, seed: int = PERM_SEED) -> dict[str, Any]:
    x = np.asarray(daily_means, dtype=float)
    x = x[np.isfinite(x)]
    n = int(x.size)
    if n <= 0:
        return {"p": 1.0, "obs_mean": float("nan"), "n_days": 0, "iters": int(iters)}
    obs = float(np.mean(x))
    rng = np.random.default_rng(int(seed))
    ge = 0
    for _ in range(int(iters)):
        signs = rng.choice(np.array([-1.0, 1.0]), size=n)
        m = float(np.mean(signs * x))
        if m >= obs - 1e-15:
            ge += 1
    p = float(ge + 1) / float(int(iters) + 1)
    return {"p": p, "obs_mean": obs, "n_days": n, "iters": int(iters), "seed": int(seed)}


def bh_correct(pvals: list[float]) -> list[float]:
    arr = np.asarray(pvals, dtype=float)
    q = bh_qvalues(arr)
    return [float(v) for v in q]
