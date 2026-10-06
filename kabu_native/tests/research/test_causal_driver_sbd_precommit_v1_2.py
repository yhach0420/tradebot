"""Sector breadth/dispersion precommit V1.2 tests. No discovery rerun."""
from __future__ import annotations

from math import ceil
from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import (
    CASE_READY,
    FAMILY_N,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_BOOTSTRAP_INDEX_SHA256,
    V1_ELIGIBLE_C1_N,
    V1_ELIGIBLE_DAY_SHA256,
    V1_ELIGIBLE_DEV_N,
    V1_FAMILY_384_SHA256,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import (
    new_gate_structurally_possible,
    old_gate_structurally_possible,
    required_ex_sector_valid_n,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.isolation import DISC_OUT, OUT, V1_OUT, V11_OUT
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.publish import SHEETS


def test_isolation_does_not_overwrite_priors():
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_precommit_v1_2")
    assert OUT.resolve() != V11_OUT.resolve()
    assert OUT.resolve() != V1_OUT.resolve()
    assert OUT.resolve() != DISC_OUT.resolve()
    assert (V11_OUT / "report.json").is_file()
    assert (DISC_OUT / "report.json").is_file()
    assert PRECOMMIT_ID == "SECTOR_BREADTH_DISPERSION_CAUSAL_DISCOVERY_PRECOMMIT_V1_2"
    assert SUPERSEDES_PRECOMMIT_SHA256 == "1062c5b6b05679dc5f0abfe68623b54f9493adc64da90d36f27e8056b0071986"
    assert CASE_READY.endswith("READY_V1_2")


def test_identities_unchanged():
    assert V1_ELIGIBLE_DAY_SHA256 == "9b97f2cb1f9bbee2800623f4b899722014db9d72939dcc82131ef247765da20c"
    assert V1_FOLD_BOUNDARY_SHA256 == "fc03a260483786125295012d84c1b6e8559b9667885b9bd905292f8c03547934"
    assert V1_PERMUTATION_SHA256 == "d65197857e48f0cda457ac56a11622f51fefdf4be5cc6c8c9ab7dae3a8258ebc"
    assert V1_BOOTSTRAP_INDEX_SHA256 == "7066c52ee76b53492ddf1a679a7e85d99457a83441cd2d896d6aef88bf7c7f58"
    assert V1_FAMILY_384_SHA256 == "265fe8b2c598bf4ee5902c4fe83233c1636d8a7b71bdd4446c0a0a45e3bd683a"
    assert V1_ELIGIBLE_DEV_N == 260
    assert V1_ELIGIBLE_C1_N == 89
    assert FAMILY_N == 384


def test_unit_3650_ex_sector_75():
    assert required_ex_sector_valid_n(75) == ceil(0.8 * 75) == 60
    assert old_gate_structurally_possible(75) is False
    assert new_gate_structurally_possible(75) is True


def test_unit_normal_ex_sector_91():
    assert required_ex_sector_valid_n(91) == 80
    assert required_ex_sector_valid_n(91) != 73
    assert old_gate_structurally_possible(91) is True
    assert new_gate_structurally_possible(91) is True


def test_required_audit_sheets():
    for name in (
        "Manifest",
        "Technical_Invalidation",
        "Old_Control_Rule",
        "Corrected_Control_Rule",
        "Sector_Structural_Feasibility",
        "C1_Firewall",
        "Safety",
    ):
        assert name in SHEETS


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
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "sector_breadth_precommit_v1_2"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
