"""Leader-laggard precommit V1.1 tests. No discovery outcomes."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.cross_sectional_precommit import FAMILY_N, LOOKBACKS_MIN, RESPONSE_HORIZONS_MIN, SCOPE_N
from research.causal_driver_pb1.cross_sectional_precommit_v1_1 import (
    CASE_READY,
    EXPECTED_LEADER_PAIRS,
    EXPECTED_LEADER_SET_SHA256,
    EXPECTED_TARGET_SET_SHA256,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.contract import C7_GATE, D7_GATE, IDENTITY_SPECIFICITY_GATE
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.days import N_DEP, N_DEP_MIN_OK
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.isolation import OUT, V1_OUT
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.publish import SHEETS
from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.phase1.errors import IngestDateDenied


def test_isolation_does_not_overwrite_v1():
    assert str(OUT).replace("\\", "/").endswith("cross_sectional_leader_laggard_precommit_v1_1")
    assert str(V1_OUT).replace("\\", "/").endswith("cross_sectional_leader_laggard_precommit_v1")
    assert OUT.resolve() != V1_OUT.resolve()
    assert (V1_OUT / "report.json").is_file()
    assert PRECOMMIT_ID == "CROSS_SECTIONAL_LEADER_LAGGARD_CAUSAL_DISCOVERY_PRECOMMIT_V1_1"
    assert SUPERSEDES_PRECOMMIT_SHA256 == "95a0c1564451a04a44f32fe66cbb2ea8c9993d80e9256a6005033e8558c05b65"
    assert CASE_READY.endswith("READY_V1_1")


def test_frozen_identities_preserved():
    assert EXPECTED_LEADER_PAIRS == (
        ("3500", "5803"),
        ("3600", "6146"),
        ("3650", "6857"),
        ("3700", "7012"),
        ("3800", "7974"),
        ("5250", "9984"),
        ("6050", "8136"),
        ("7050", "8306"),
    )
    assert EXPECTED_LEADER_SET_SHA256 == "e571c658f03894447d0b38eb6ea530895eb68056749e5d52fe56333fb0d10c56"
    assert EXPECTED_TARGET_SET_SHA256 == "b7bec805e4faf5b5af5c2fa15f0411622b6295bc0dc366f1889b1e109852b66d"


def test_family_still_144():
    assert len(LOOKBACKS_MIN) * len(RESPONSE_HORIZONS_MIN) * SCOPE_N == FAMILY_N == 144


def test_dependency_window_math():
    assert N_DEP == 151
    assert N_DEP_MIN_OK == 144
    assert (150 / 151) >= 0.95
    assert (143 / 151) < 0.95


def test_d7_c7_and_identity_gate():
    assert D7_GATE["cannot_rescue_D1_D6_failure"] is True
    assert D7_GATE["conjunctive"] is True
    assert "date-block bootstrap 95% CI for beta_60 excludes 0" in D7_GATE["pass_requires_all"]
    assert C7_GATE["cannot_rescue_C1_C6_failure"] is True
    assert IDENTITY_SPECIFICITY_GATE["label"] == "LEADER_IDENTITY_SPECIFICITY_GATE"
    assert IDENTITY_SPECIFICITY_GATE["not_empirical_p95"] is True
    assert IDENTITY_SPECIFICITY_GATE["date_resampling_extension"] is False
    assert IDENTITY_SPECIFICITY_GATE["GLOBAL_LEADER_BASKET"] == "NOT_APPLICABLE"
    assert IDENTITY_SPECIFICITY_GATE["same_sector"]["ties_fail"] is True


def test_required_audit_sheets():
    assert "Diff" in SHEETS
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
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "cross_sectional_precommit_v1_1"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
