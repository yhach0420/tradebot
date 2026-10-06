"""Sector breadth/dispersion precommit V1.1 tests. No discovery outcomes."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_breadth_precommit_v1_1 import (
    CASE_READY,
    FAMILY_N,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_ELIGIBLE_C1_N,
    V1_ELIGIBLE_DAY_SHA256,
    V1_ELIGIBLE_DEV_N,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import (
    bh_qvalues_canonical,
    bootstrap_two_sided_sign_tail_p,
    ci_excludes_zero,
    draw_date_block_indices,
    percentile_ci_95,
    require_family_p,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.isolation import OUT, V1_OUT
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.publish import SHEETS


def test_isolation_does_not_overwrite_v1():
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_precommit_v1_1")
    assert str(V1_OUT).replace("\\", "/").endswith("sector_breadth_dispersion_precommit_v1")
    assert OUT.resolve() != V1_OUT.resolve()
    assert (V1_OUT / "report.json").is_file()
    assert PRECOMMIT_ID == "SECTOR_BREADTH_DISPERSION_CAUSAL_DISCOVERY_PRECOMMIT_V1_1"
    assert SUPERSEDES_PRECOMMIT_SHA256 == "1dfc54fe98cb5a7ecce13ea4a4cfb6832b5c3aa2befbfa859722e0ac7b998c44"
    assert CASE_READY.endswith("READY_V1_1")


def test_v1_identities_bound():
    assert V1_ELIGIBLE_DAY_SHA256 == "9b97f2cb1f9bbee2800623f4b899722014db9d72939dcc82131ef247765da20c"
    assert V1_FOLD_BOUNDARY_SHA256 == "fc03a260483786125295012d84c1b6e8559b9667885b9bd905292f8c03547934"
    assert V1_PERMUTATION_SHA256 == "d65197857e48f0cda457ac56a11622f51fefdf4be5cc6c8c9ab7dae3a8258ebc"
    assert V1_ELIGIBLE_DEV_N == 260
    assert V1_ELIGIBLE_C1_N == 89
    assert FAMILY_N == 384


def test_t1_bootstrap_index_determinism():
    a = draw_date_block_indices(n_day=260)
    b = draw_date_block_indices(n_day=260)
    assert a.shape == (2000, 260)
    assert np.array_equal(a, b)
    c = draw_date_block_indices(n_day=89)
    assert c.shape == (2000, 89)
    assert int(a.min()) >= 0 and int(a.max()) <= 259


def test_t2_t3_ci():
    lo, hi = percentile_ci_95(np.full(2000, 1.0))
    assert lo > 0 and hi > 0 and ci_excludes_zero(lo, hi)
    mix = np.array([-1.0] * 1000 + [1.0] * 1000)
    lo2, hi2 = percentile_ci_95(mix)
    assert lo2 < 0 < hi2
    assert not ci_excludes_zero(lo2, hi2)
    assert not ci_excludes_zero(0.0, 1.0)
    assert not ci_excludes_zero(-1.0, 0.0)


def test_t4_t5_pvalue():
    mix = np.array([-1.0] * 1000 + [1.0] * 1000)
    assert bootstrap_two_sided_sign_tail_p(mix) >= 0.5
    strong = np.array([1.0] * 1999 + [-1.0])
    assert bootstrap_two_sided_sign_tail_p(strong) <= 0.01


def test_t6_bh_and_t7_family():
    q = np.array([0.03, 0.05, 0.05])
    got = None

    def _bh_any(p):
        m = int(p.size)
        order = np.lexsort((np.arange(m), p))
        out = np.empty(m)
        prev = 1.0
        for rank in range(m, 0, -1):
            i = int(order[rank - 1])
            val = float(p[i]) * m / rank
            prev = min(prev, val)
            out[i] = prev
        return np.clip(out, 0.0, 1.0)

    got = _bh_any(np.array([0.01, 0.04, 0.05]))
    assert np.allclose(got, q)
    p = np.ones(384)
    p[:3] = [0.01, 0.04, 0.05]
    q384 = bh_qvalues_canonical(p)
    assert q384.size == 384
    try:
        require_family_p(np.ones(383))
        raise AssertionError("383")
    except ValueError:
        pass
    try:
        require_family_p(np.ones(385))
        raise AssertionError("385")
    except ValueError:
        pass


def test_required_audit_sheets():
    assert "Diff" in SHEETS
    assert "Inference" in SHEETS
    assert "DEV_Gates" in SHEETS
    assert "Safety" in SHEETS


def test_fv_still_denied():
    try:
        assert_ingest_date_allowed(FV_FIRST)
        raise AssertionError("fv_allowed")
    except IngestDateDenied:
        pass
    try:
        assert_ingest_date_allowed(PROSPECTIVE_FROM)
        raise AssertionError("prospective_allowed")
    except IngestDateDenied:
        pass


def test_no_forbidden_tokens():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "sector_breadth_precommit_v1_1"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
