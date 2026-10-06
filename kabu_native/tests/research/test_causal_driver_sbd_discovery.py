"""Sector breadth/dispersion discovery tests. Does not run the 384-family."""
from __future__ import annotations

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_breadth_discovery import (
    EXPECTED_FAMILY_N,
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    HORIZONS,
    LOOKBACKS,
    METRICS,
    PRECOMMIT_ID,
    STAGE_A_HARD_STOP,
)
from research.causal_driver_pb1.cross_sectional_discovery.panels import load_equity_stage
from research.causal_driver_pb1.sector_breadth_discovery.isolation import OUT, PRECOMMIT_V1_OUT, PRECOMMIT_V11_OUT
from research.causal_driver_pb1.sector_breadth_discovery.publish import SHEETS
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity
from research.causal_driver_pb1.sector_breadth_precommit import EXPECTED_SECTOR_IDS
from research.causal_driver_pb1.phase2_discovery.access import StageLedger


def test_binds_v1_1_not_v1():
    assert PRECOMMIT_ID.endswith("V1_1")
    assert EXPECTED_PRECOMMIT_SHA256 == "1062c5b6b05679dc5f0abfe68623b54f9493adc64da90d36f27e8056b0071986"
    assert FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256 == "1dfc54fe98cb5a7ecce13ea4a4cfb6832b5c3aa2befbfa859722e0ac7b998c44"
    assert EXPECTED_PRECOMMIT_SHA256 != FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256
    assert (PRECOMMIT_V11_OUT / "report.json").is_file()
    assert OUT.resolve() != PRECOMMIT_V11_OUT.resolve()
    assert OUT.resolve() != PRECOMMIT_V1_OUT.resolve()
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_discovery_v1")


def test_family_is_384():
    elig = [{"sector_id": s, "sector_name": s, "constituent_n": 3} for s in EXPECTED_SECTOR_IDS]
    fam = family_identity(eligible_sectors=elig)
    assert fam["family_n"] == EXPECTED_FAMILY_N == 384
    assert len(fam["tests"]) == 2 * 12 * len(LOOKBACKS) * len(HORIZONS)
    assert fam["tests"][0]["metric"] == METRICS[0]
    assert fam["scopes"][-1]["scope_id"] == "GLOBAL_105"
    assert fam["family_384_sha256"] == "265fe8b2c598bf4ee5902c4fe83233c1636d8a7b71bdd4446c0a0a45e3bd683a"


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
    assert "DEV_All_384" in SHEETS
    assert "Inference_Contract" in SHEETS
    assert "Safety" in SHEETS
    assert "Sector_Identity" in SHEETS
