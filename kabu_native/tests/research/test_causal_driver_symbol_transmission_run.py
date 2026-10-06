"""Transmission structure tests. No full-family outcome fit."""
from __future__ import annotations

import numpy as np

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_state_transmission.features import breadth_ex_target
from research.causal_driver_pb1.sector_state_transmission.infer import bh_monotone, gate_discovery
from research.causal_driver_pb1.sector_state_transmission.isolation import BLOCKED_V1_OUT, OUT, PRECOMMIT_V11_OUT


def test_loo_breadth_excludes_target():
    ret = np.zeros((4, 2, 2), dtype=np.float64)
    ret[0] = 1.0
    ret[1] = -1.0
    ret[2] = 1.0
    ret[3] = -1.0
    listed = np.ones((4, 2), dtype=bool)
    base = breadth_ex_target(ret=ret, listed=listed, global_scope=False)
    flipped = ret.copy()
    flipped[0] = -1.0
    other = breadth_ex_target(ret=flipped, listed=listed, global_scope=False)
    assert np.isfinite(base[0]).all()
    assert np.allclose(base[0], other[0])
    assert not np.allclose(base[1], other[1])


def test_bh_and_exact_zero_ci():
    q = bh_monotone(np.array([0.01, 0.02, 0.2]))
    assert q.shape == (3,)
    assert q[0] <= q[2]
    row = {"beta_dev": 1.0, "beta_c1": 1.0, "ci_lo": 0.0, "minute_mod5_positive_n": 5, "lomo_positive_fraction": 1.0, "beta_60": 1.0, "ci60_lo": 1.0, "beta_pooled": 1.0}
    gated = gate_discovery(row, 0.01)
    assert gated["T2"] is False
    assert gated["first_fail"] == "T2"
    assert gated["discovery_candidate"] is False


def test_output_isolation():
    assert OUT.resolve() not in {PRECOMMIT_V11_OUT.resolve(), BLOCKED_V1_OUT.resolve()}
