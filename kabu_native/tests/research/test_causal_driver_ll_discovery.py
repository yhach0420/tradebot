"""Leader-laggard discovery tests. Does not run the 144-family."""
from __future__ import annotations

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.cross_sectional_discovery import (
    EXPECTED_FAMILY_N,
    EXPECTED_LEADER_PAIRS,
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    HORIZONS,
    LOOKBACKS,
    PRECOMMIT_ID,
    STAGE_A_HARD_STOP,
)
from research.causal_driver_pb1.cross_sectional_discovery.features import build_scope_catalog
from research.causal_driver_pb1.cross_sectional_discovery.isolation import OUT, PRECOMMIT_V1_OUT, PRECOMMIT_V11_OUT
from research.causal_driver_pb1.cross_sectional_discovery.panels import load_equity_stage
from research.causal_driver_pb1.cross_sectional_discovery.publish import SHEETS
from research.causal_driver_pb1.phase2_discovery.access import StageLedger


def test_binds_v1_1_not_v1():
    assert PRECOMMIT_ID.endswith("V1_1")
    assert EXPECTED_PRECOMMIT_SHA256 == "7cc118005173c236f7e8548f4f9dd8a4d8d36504722fa30d27fc52b98164e52e"
    assert FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256 == "95a0c1564451a04a44f32fe66cbb2ea8c9993d80e9256a6005033e8558c05b65"
    assert EXPECTED_PRECOMMIT_SHA256 != FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256
    assert (PRECOMMIT_V11_OUT / "report.json").is_file()
    assert OUT.resolve() != PRECOMMIT_V11_OUT.resolve()
    assert OUT.resolve() != PRECOMMIT_V1_OUT.resolve()
    assert str(OUT).replace("\\", "/").endswith("cross_sectional_leader_laggard_discovery_v1")


def test_family_is_144():
    frozen = [{"sector_id": a, "leader_symbol": b, "sector_name": a} for a, b in EXPECTED_LEADER_PAIRS]
    scopes = build_scope_catalog(frozen)
    assert len(scopes) * len(LOOKBACKS) * len(HORIZONS) == EXPECTED_FAMILY_N == 144
    assert scopes[-1]["scope_id"] == "GLOBAL_LEADER_BASKET"


def test_stage_a_hard_stop_rejects_c1_dates():
    assert STAGE_A_HARD_STOP == "20251126"
    ledger = StageLedger()
    try:
        load_equity_stage(symbols=("5803",), dates=["20251127"], ledger=ledger, stage="STAGE_A")
        raise AssertionError("stage_a_allowed_c1")
    except RuntimeError as exc:
        assert "hard_stop" in str(exc)


def test_audit_sheets_include_c1_barrier():
    assert "C1_Access_Ledger" in SHEETS
    assert "DEV_All_144" in SHEETS
    assert "Safety" in SHEETS
