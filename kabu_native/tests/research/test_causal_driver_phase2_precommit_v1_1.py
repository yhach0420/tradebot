"""Phase 2 precommit V1.1 tests. No lead outcomes. No Frozen Validation economics."""
from __future__ import annotations

from pathlib import Path

import pytest

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit import FX_LOOKBACKS_MIN, HORIZON_LAST_T, RESPONSE_HORIZONS_MIN
from research.causal_driver_pb1.phase2_precommit.eligibility import split_count_ordered
from research.causal_driver_pb1.phase2_precommit_v1_1 import (
    DAY_SHUFFLE_METHOD,
    PRECOMMIT_ID,
    SUPERSEDED_FOLD_BOUNDARY_SHA256,
    SUPERSEDES_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.phase2_precommit_v1_1.isolation import NATIVE, OUT, V1_PRECOMMIT_OUT
from research.causal_driver_pb1.phase2_precommit_v1_1.publish import SHEETS
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import (
    build_shuffle_plan,
    generate_shuffle_permutations,
    sattolo_derange,
    shuffle_reproducible,
)


def test_isolation_does_not_overwrite_v1():
    assert (NATIVE / "src" / "research" / "causal_driver_pb1" / "phase2_precommit_v1_1").is_dir()
    assert str(OUT).replace("\\", "/").endswith("phase2_usdjpy_standalone_lead_precommit_v1_1")
    assert OUT.resolve() != V1_PRECOMMIT_OUT.resolve()
    assert "src/results" not in str(OUT).replace("\\", "/")


def test_identity_supersedes_v1_not_reuse():
    assert PRECOMMIT_ID.endswith("V1_1")
    assert SUPERSEDES_PRECOMMIT_SHA256 == "8602f954afca5edfcc95d2b255bad60c14cdb2f7821c982b7f0c49ef7c132683"
    assert SUPERSEDED_FOLD_BOUNDARY_SHA256 == "27fb4e169a445fc6363bc7cf052d715adc830ed6ae4f53c3be73d701da7b700b"
    assert DAY_SHUFFLE_METHOD == "SATTOLO_CYCLE_DERANGEMENT_STRATIFIED"


def test_hypothesis_unchanged():
    assert FX_LOOKBACKS_MIN == (1, 3, 5, 10, 20, 30)
    assert RESPONSE_HORIZONS_MIN == (5, 10, 20, 30)
    assert HORIZON_LAST_T["5"] == "11:25"
    assert HORIZON_LAST_T["30"] == "11:00"


def test_fold_split_remainder_to_last():
    days = [f"202409{17 + i:02d}" for i in range(10)]
    a, b = split_count_ordered(days, 2)
    assert len(a) == 5 and len(b) == 5
    x, y, z = split_count_ordered(days, 3)
    assert len(x) == 3 and len(y) == 3 and len(z) == 4


def test_sattolo_no_fixed_points():
    import random

    items = ["20240917", "20240918", "20240919", "20240920"]
    der = sattolo_derange(items, random.Random(20251127))
    assert sorted(der) == sorted(items)
    assert all(der[i] != items[i] for i in range(len(items)))


def test_shuffle_plan_primary_then_month_leftover():
    dates = [
        "20240917",  # Tue
        "20240924",  # Tue same month -> primary
        "20240918",  # Wed singleton leftover
        "20240919",  # Thu singleton leftover -> month fallback with Wed
        "20241001",  # Tue only in Oct -> unshufflable if alone leftover
    ]
    plan = build_shuffle_plan(dates)
    assert plan["shuffle_primary_n"] == 2
    assert plan["shuffle_fallback_month_n"] == 2
    assert plan["shuffle_unshufflable_n"] == 1
    assert plan["unshufflable"] == ["20241001"]


def test_shuffle_reproducible_and_no_identity():
    dates = [f"202409{17 + i:02d}" for i in range(10)]
    a = generate_shuffle_permutations(dates)
    b = generate_shuffle_permutations(dates)
    assert a["permutation_sha256"] == b["permutation_sha256"]
    assert shuffle_reproducible(dates)
    from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import _one_mapping, build_shuffle_plan
    import random

    plan = build_shuffle_plan(dates)
    rng = random.Random(20251127)
    mapping = _one_mapping(plan, rng)
    assert mapping
    assert all(k != v for k, v in mapping.items())


def test_fv_and_prospective_denied():
    with pytest.raises(IngestDateDenied):
        assert_ingest_date_allowed("20260422")
    with pytest.raises(IngestDateDenied):
        assert_ingest_date_allowed("20260924")


def test_no_phase2_outcome_modules():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "phase2_precommit_v1_1"
    forbidden = ("USDJPY_RET_1M", "winning_symbols", "profit_factor", "net_pnl", "XGBoost", "random_forest")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []


def test_audit_sheets_include_diff():
    assert "Diff" in SHEETS
    assert "Calendar" in SHEETS
    assert "Shuffle" in SHEETS
    assert SHEETS[0] == "Manifest"
