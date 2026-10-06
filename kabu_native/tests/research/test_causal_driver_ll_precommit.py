"""Leader-laggard precommit tests. No discovery outcomes."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.cross_sectional_precommit import (
    DRIVER_FAMILY_ID,
    FAMILY_N,
    LEADER_N,
    LOOKBACKS_MIN,
    PARENT_CANDIDATE_LIST_SHA256,
    PARENT_USDJPY_VERDICT,
    RESPONSE_HORIZONS_MIN,
    SCOPE_N,
)
from research.causal_driver_pb1.cross_sectional_precommit.clock import N_CLOCK, N_DECISION_MIN_OK
from research.causal_driver_pb1.cross_sectional_precommit.isolation import NEXT_DRIVER_OUT, OUT, USDJPY_CORRECTED_OUT
from research.causal_driver_pb1.cross_sectional_precommit.publish import SHEETS
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM


def test_isolation_and_parent_identity():
    assert str(OUT).replace("\\", "/").endswith("cross_sectional_leader_laggard_precommit_v1")
    assert OUT.resolve() != USDJPY_CORRECTED_OUT.resolve()
    assert OUT.resolve() != NEXT_DRIVER_OUT.resolve()
    assert (USDJPY_CORRECTED_OUT / "report.json").is_file()
    assert PARENT_USDJPY_VERDICT == "USDJPY_STANDALONE_CAUSAL_LEAD_NOT_FOUND_V2"
    assert PARENT_CANDIDATE_LIST_SHA256 == "f7c7c704d4e46a70abce303a7c874acb1ba3f01c6831d9d2bc6526d167d6b0c0"
    assert DRIVER_FAMILY_ID == "CROSS_SECTIONAL_DRIVER"
    assert LEADER_N == 8


def test_family_is_144():
    assert len(LOOKBACKS_MIN) * len(RESPONSE_HORIZONS_MIN) * SCOPE_N == FAMILY_N == 144
    assert LOOKBACKS_MIN == (1, 3, 5, 10)
    assert RESPONSE_HORIZONS_MIN == (1, 3, 5, 10)


def test_clock_freshness_math():
    assert N_CLOCK == 136
    assert N_DECISION_MIN_OK == 130


def test_required_audit_sheets():
    required = (
        "Manifest",
        "USDJPY_Final_Closeout",
        "Driver_Finalization",
        "Universe105",
        "Sector_Mapping",
        "Leader_Eligibility",
        "Sector_Liquidity",
        "Leader_Liquidity",
        "Frozen_Leader_Set",
        "Frozen_Target_Set",
        "Eligible_Days",
        "Folds",
        "Timestamp_Semantics",
        "Freshness",
        "Research_Family",
        "Models",
        "DEV_Gates",
        "C1_Gates",
        "Placebos",
        "Concentration",
        "Contamination",
        "Firewall",
        "Safety",
    )
    assert list(SHEETS) == list(required)


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
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "cross_sectional_precommit"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
