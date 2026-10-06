"""Phase 2 discovery tests. Does not run the 288-test discovery."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401  load contracts before identity
from research.causal_driver_pb1.phase2_discovery import (
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    TARGET_SCOPES,
)
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, clock_ok_for_horizon
from research.causal_driver_pb1.phase2_discovery.isolation import OUT, PRECOMMIT_V11_OUT
from research.causal_driver_pb1.phase2_discovery.placebos import freeze_payload
from research.causal_driver_pb1.phase2_discovery.publish import SHEETS
from research.causal_driver_pb1.phase2_precommit import FX_LOOKBACKS_MIN, RESPONSE_HORIZONS_MIN
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations


def test_isolation_and_precommit_bind_constants():
    assert str(OUT).replace("\\", "/").endswith("phase2_usdjpy_standalone_lead_discovery")
    assert (PRECOMMIT_V11_OUT / "report.json").is_file()
    assert EXPECTED_PRECOMMIT_SHA256 != FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256
    assert EXPECTED_PRECOMMIT_SHA256 == "40b4ac4b7574cb4980bf2047e12e4045db57f8399355a39317ddd8322d6b6e9a"


def test_family_size_is_288():
    assert len(FX_LOOKBACKS_MIN) * len(RESPONSE_HORIZONS_MIN) * len(TARGET_SCOPES) == 288
    assert TARGET_SCOPES[0] == "MKT105_EQW"
    assert len(TARGET_SCOPES) == 12


def test_clock_horizon_last_t():
    assert N_CLOCK == 136
    assert CLOCK_MINS[0] == 9 * 60 + 10
    assert clock_ok_for_horizon(11 * 60 + 25, 5)
    assert not clock_ok_for_horizon(11 * 60 + 25, 30)
    assert clock_ok_for_horizon(11 * 60, 30)


def test_freeze_empty_is_deterministic():
    a, sa = freeze_payload([])
    b, sb = freeze_payload([])
    assert a == []
    assert sa == sb
    assert len(sa) == 64


def test_shuffle_return_maps_does_not_change_sha():
    dates = [f"202409{17 + i:02d}" for i in range(10)]
    a = generate_shuffle_permutations(dates)
    b = generate_shuffle_permutations(dates, return_maps=True)
    assert a["permutation_sha256"] == b["permutation_sha256"]
    assert "maps" in b and len(b["maps"]) == 1000


def test_audit_sheets():
    assert SHEETS[0] == "Manifest"
    assert "Candidate_Freeze" in SHEETS
    assert "Safety" in SHEETS
    assert len(SHEETS) == 16


def test_no_forbidden_tokens_in_discovery():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "phase2_discovery"
    forbidden = ("profit_factor", "net_pnl", "XGBoost", "PB1 SEED", "winning_symbols")
    hits = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []
