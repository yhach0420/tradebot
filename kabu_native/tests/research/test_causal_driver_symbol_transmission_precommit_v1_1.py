"""V1.1 sector-control regime tests. No symbol future-return beta."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_state_transmission_precommit.family import build_family
from research.causal_driver_pb1.sector_state_transmission_precommit.targets import bind_targets
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import BLOCKED_SYMBOLS, CONTROL_CONTRACT_ID, FAMILY_SERIALIZATION_SHA256
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.isolation import BLOCKED_V1_OUT, OUT
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.regimes import (
    REGIME_MULTI,
    REGIME_NONE,
    REGIME_SINGLE,
    assign_structural_regimes,
    contract_hashes,
    peer_control_ok,
)


def test_peer_regimes_do_not_zero_fill():
    listed = np.array([[True, True], [True, True]])
    past = np.array([[True, True], [False, True]])
    ok = peer_control_ok(listed, past)
    assert ok.shape == (2, 2)
    assert bool(ok[0, 0]) is False
    assert bool(ok[1, 0]) is True
    singleton = peer_control_ok(np.array([[True, False]]), np.array([[True, False]]))
    assert bool(singleton.all())
    none_day = peer_control_ok(np.array([[True], [False]]), np.array([[True], [False]]))
    assert bool(none_day[0, 0]) is True
    assert bool(none_day[1, 0]) is True


def test_structural_examples_and_family_identity():
    targets = bind_targets()
    assignment = assign_structural_regimes(symbols=list(targets["symbols"]), sector_of=dict(targets["sector_of"]))
    by = assignment["by_symbol"]
    assert by["9501"]["control_regime"] == REGIME_NONE
    assert by["9501"]["pool_n_peer_pit"] == 0
    assert by["9501"]["sector_control_status"] == "NOT_APPLICABLE"
    assert by["1515"]["control_regime"] == REGIME_SINGLE
    assert by["1515"]["sole_peer"] == "1605"
    assert by["1605"]["sole_peer"] == "1515"
    example = targets["sector3650_symbols"][0]
    assert by[example]["control_regime"] == REGIME_MULTI
    assert by[example]["pool_n_peer_pit"] == 29
    assert set(assignment["single_peer_targets"]) | set(assignment["no_peer_targets"]) == set(BLOCKED_SYMBOLS)
    assert len(assignment["multi_peer_targets"]) == 88
    assert len(assignment["single_peer_targets"]) == 10
    assert len(assignment["no_peer_targets"]) == 7
    family = build_family(symbols=list(targets["symbols"]), sector_of=dict(targets["sector_of"]), sector3650_symbols=list(targets["sector3650_symbols"]))
    assert family["family_sha256"] == FAMILY_SERIALIZATION_SHA256
    assert family["family_n"] == 270
    hashes = contract_hashes(assignment=assignment, family_rows=family["rows"])
    assert hashes["symbol_control_contract_id"] == CONTROL_CONTRACT_ID
    assert hashes["family_hypothesis_sha256"] != FAMILY_SERIALIZATION_SHA256
    assert hashes["model_contract_sha256"] != hashes["symbol_control_contract_sha256"]


def test_isolation_and_no_beta_fit():
    assert OUT.resolve() != BLOCKED_V1_OUT.resolve()
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "sector_state_transmission_precommit_v1_1"
    text = (root / "feasibility.py").read_text(encoding="utf-8") + (root / "regimes.py").read_text(encoding="utf-8")
    assert "np.log" not in text
    assert "fit_frozen" not in text
    assert "point_b_fx" not in text
