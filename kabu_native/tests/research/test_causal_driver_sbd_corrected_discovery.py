"""Contract-corrected sector breadth/dispersion discovery tests. Does not run the 384-family."""
from __future__ import annotations

from math import ceil

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.cross_sectional_discovery.panels import load_equity_stage
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.sector_breadth_corrected_discovery import (
    CONTROL_GATE_RULE_ID,
    EXPECTED_FAMILY_N,
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_V11_PRECOMMIT_SHA256,
    HORIZONS,
    LOOKBACKS,
    METRICS,
    PRECOMMIT_ID,
    STAGE_A_HARD_STOP,
)
from research.causal_driver_pb1.sector_breadth_corrected_discovery.isolation import DISC_V1_OUT, OUT, PRECOMMIT_V11_OUT, PRECOMMIT_V12_OUT
from research.causal_driver_pb1.sector_breadth_corrected_discovery.publish import SHEETS
from research.causal_driver_pb1.sector_breadth_precommit import EXPECTED_SECTOR_IDS
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import control_gate_spec, required_ex_sector_valid_n


def test_binds_v1_2_not_v1_1():
    assert PRECOMMIT_ID.endswith("V1_2")
    assert EXPECTED_PRECOMMIT_SHA256 == "d93a65804cd316462deeec01a0723b8565bbf2b4504e54244059ccac75c73598"
    assert FORBIDDEN_V11_PRECOMMIT_SHA256 == "1062c5b6b05679dc5f0abfe68623b54f9493adc64da90d36f27e8056b0071986"
    assert EXPECTED_PRECOMMIT_SHA256 != FORBIDDEN_V11_PRECOMMIT_SHA256
    assert CONTROL_GATE_RULE_ID == "MKT_EX_SECTOR_REQUIRED_VALID_N_FEASIBILITY_BRANCH_V1_2"
    assert (PRECOMMIT_V12_OUT / "report.json").is_file()
    assert OUT.resolve() != PRECOMMIT_V12_OUT.resolve()
    assert OUT.resolve() != PRECOMMIT_V11_OUT.resolve()
    assert OUT.resolve() != DISC_V1_OUT.resolve()
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_contract_corrected_discovery_v1")


def test_family_is_384():
    elig = [{"sector_id": s, "sector_name": s, "constituent_n": 3} for s in EXPECTED_SECTOR_IDS]
    fam = family_identity(eligible_sectors=elig)
    assert fam["family_n"] == EXPECTED_FAMILY_N == 384
    assert len(fam["tests"]) == 2 * 12 * len(LOOKBACKS) * len(HORIZONS)
    assert fam["tests"][0]["metric"] == METRICS[0]
    assert fam["scopes"][-1]["scope_id"] == "GLOBAL_105"
    assert fam["family_384_sha256"] == "265fe8b2c598bf4ee5902c4fe83233c1636d8a7b71bdd4446c0a0a45e3bd683a"


def test_control_gate_3650_vs_normal():
    assert required_ex_sector_valid_n(75) == 60 == ceil(0.80 * 75)
    assert required_ex_sector_valid_n(74) == 60 == ceil(0.80 * 74)
    assert required_ex_sector_valid_n(91) == 80
    assert required_ex_sector_valid_n(80) == 80
    spec = control_gate_spec()
    assert spec["rule_id"] == CONTROL_GATE_RULE_ID
    assert spec["not_unconditional_80pct_relaxation"] is True


def test_stage_a_hard_stop_rejects_c1_dates():
    assert STAGE_A_HARD_STOP == "20251126"
    ledger = StageLedger()
    try:
        load_equity_stage(symbols=("5803",), dates=["20251127"], ledger=ledger, stage="STAGE_A")
        raise AssertionError("stage_a_allowed_c1")
    except RuntimeError as exc:
        assert "hard_stop" in str(exc)


def test_audit_sheets_and_c1_barrier():
    for name in (
        "Technical_Invalidation",
        "Control_Gate",
        "Structural_Feasibility",
        "Corrected_3650",
        "Unaffected_Parity",
        "C1_Access_Ledger",
        "Candidate_Freeze",
        "DEV_All_384",
        "Safety",
    ):
        assert name in SHEETS
    assert "C1_Confirmation" in SHEETS
