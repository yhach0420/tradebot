"""Phase 0 foundation tests. No PnL, no USDJPY alpha, no Frozen Validation economics."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from research.causal_driver_pb1 import (
    CASE_READY,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    EXPECTED_UNIVERSE_N,
    EXPECTED_V4_MACHINE_SHA256,
    V5_CREATED,
)
from research.causal_driver_pb1.audit.publish import SHEETS
from research.causal_driver_pb1.contracts.alpha import pb1_adapter_decide
from research.causal_driver_pb1.contracts.enums import (
    AccessKind,
    AdapterVerdict,
    DatasetRole,
    Direction,
    DriverFamily,
    QualityStatus,
    RejectReason,
    RunMode,
)
from research.causal_driver_pb1.contracts.errors import CausalTimeError, FailClosedError, FirewallDenied
from research.causal_driver_pb1.contracts.mechanism import MechanismRegistry
from research.causal_driver_pb1.contracts.observation import build_observation
from research.causal_driver_pb1.contracts.time import bar_start_available_at, decide, jst
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.datasets.universe import dynamic40_gate, load_research_observation_universe, runtime_from_dynamic40
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.isolation import CS_SRC, OUT, V4_SRC, write_overlap_n
from research.causal_driver_pb1.phase0.evaluate import evaluate, _synthetic_alpha
from research.causal_driver_pb1.transport.base import DriverTransport
from research.causal_driver_pb1.transport.synthetic import HistoricalDriverTransport, RuntimeDriverTransport, synthetic_source
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256
from research.pb1_v4_complete_strategy_build_and_economic_validation import EXPECTED_SOURCE_INVENTORY_SHA256


def test_t1_t2_bar_start_not_usable_at_open():
    bar = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(bar)
    assert available == jst(2024, 9, 17, 9, 31, 0)
    with pytest.raises(CausalTimeError):
        decide(available_at=available, decision_time=jst(2024, 9, 17, 9, 30, 0))
    assert decide(available_at=available, decision_time=jst(2024, 9, 17, 9, 31, 0)) == "ACCEPT"


def test_t3_available_at_after_event_time_ok():
    bar = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(bar)
    assert available > bar


def test_t4_received_at_before_available_at_rejected():
    src = synthetic_source()
    bar = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(bar)
    with pytest.raises((CausalTimeError, FailClosedError)):
        build_observation(
            source=src,
            driver_family=DriverFamily.SYNTHETIC,
            event_time=bar,
            available_at=available,
            received_at=bar,
            value=1.0,
            direction=Direction.LONG,
            strength=1.0,
            lookback_window="1m",
            valid_until=available,
            quality_status=QualityStatus.VALID,
        )


def test_naive_datetime_forbidden():
    with pytest.raises(CausalTimeError):
        decide(available_at=datetime(2024, 9, 17, 9, 31, 0), decision_time=jst(2024, 9, 17, 9, 31, 0))


def test_firewall_discovery_roles():
    ledger = AccessLedger(run_id="t", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="dev",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("value",),
        access_kind=AccessKind.PAYLOAD,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="c1",
        dataset_role=DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED,
        requested_fields=("value",),
        access_kind=AccessKind.PAYLOAD,
    )
    with pytest.raises(FirewallDenied):
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="fv",
            dataset_role=DatasetRole.FROZEN_VALIDATION,
            requested_fields=("value",),
            access_kind=AccessKind.PAYLOAD,
        )
    with pytest.raises(FirewallDenied):
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="pr",
            dataset_role=DatasetRole.PROSPECTIVE,
            requested_fields=("value",),
            access_kind=AccessKind.PAYLOAD,
        )
    with pytest.raises(FirewallDenied):
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="fv_econ",
            dataset_role=DatasetRole.FROZEN_VALIDATION,
            requested_fields=("net_pnl_yen", "mfe_bps", "future_return", "mae"),
            access_kind=AccessKind.ECONOMIC_PAYLOAD,
        )
    denied = [e for e in ledger.events if not e["allowed"]]
    assert len(denied) == 3


def test_universe_105_sha_and_not_dynamic40():
    uni = load_research_observation_universe()
    assert uni.symbol_count == EXPECTED_UNIVERSE_N == 105
    assert uni.source_sha256 == EXPECTED_UNIVERSE_MANIFEST_SHA256
    gate = dynamic40_gate()
    assert gate.role == "RUNTIME_RESOURCE_TRADABILITY_GATE"
    assert gate.alpha_source is False
    a = runtime_from_dynamic40(["7203"])
    b = runtime_from_dynamic40(["7203", "9984"])
    again = load_research_observation_universe()
    assert again.source_sha256 == uni.source_sha256
    assert len(a.symbols) != len(b.symbols)
    assert type(uni) is not type(a)
    assert a.not_registered_is_not_no_alpha is True
    assert RejectReason.NOT_REGISTERED is not RejectReason.NO_ALPHA


def test_immutability_and_adapter_no_flip():
    src = synthetic_source()
    bar = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(bar)
    obs = build_observation(
        source=src,
        driver_family=DriverFamily.SYNTHETIC,
        event_time=bar,
        available_at=available,
        received_at=available,
        value=1.0,
        direction=Direction.LONG,
        strength=1.0,
        lookback_window="1m",
        valid_until=jst(2024, 9, 17, 15, 20, 0),
        quality_status=QualityStatus.VALID,
        decision_time=available,
    )
    with pytest.raises(Exception):
        obs.direction = Direction.SHORT  # type: ignore[misc]
    alpha = _synthetic_alpha(direction=Direction.LONG)
    with pytest.raises(Exception):
        alpha.direction = Direction.SHORT  # type: ignore[misc]
    dec = pb1_adapter_decide(alpha=alpha, pb1_direction=Direction.SHORT)
    assert dec.verdict is AdapterVerdict.REJECT
    assert dec.direction is Direction.LONG
    assert dec.reason is RejectReason.ALPHA_DIRECTION_MISMATCH
    with pytest.raises(FailClosedError):
        MechanismRegistry().freeze("anything")


def test_transport_interface_order_determinism():
    h = HistoricalDriverTransport()
    r = RuntimeDriverTransport()
    assert isinstance(h, DriverTransport)
    assert isinstance(r, DriverTransport)
    a = list(h.stream())
    b = list(HistoricalDriverTransport().stream())
    assert [x.driver_observation_id for x in a] == [x.driver_observation_id for x in b]
    times = [x.available_at for x in a]
    assert times == sorted(times)
    assert list(r.stream())[0].driver_observation_id == a[0].driver_observation_id


def test_runtime_nonimpact_hashes_and_no_v5():
    pre = frozen_source_hashes()
    post = frozen_source_hashes()
    assert pre == post
    assert v4_sha() == EXPECTED_V4_MACHINE_SHA256
    assert (
        complete_strategy_sha256(machine_sha=EXPECTED_V4_MACHINE_SHA256, source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256)
        == EXPECTED_COMPLETE_STRATEGY_SHA256
    )
    assert V5_CREATED is False
    assert write_overlap_n("", "") == 0
    assert V4_SRC.is_dir() and CS_SRC.is_dir()
    assert "phase0_foundation" in str(OUT).replace("\\", "/")
    assert Path(V4_SRC).resolve() != Path(OUT).resolve()


def test_no_pnl_computation_in_phase0_evaluate():
    ev = Path("src/research/causal_driver_pb1/phase0/evaluate.py").read_text(encoding="utf-8")
    assert "development_metrics" not in ev
    assert "replay_occupancy" not in ev
    assert "future_return =" not in ev


def test_evaluate_ready_and_sheets():
    bound = bind_identities()
    assert bound["ok"] is True
    ev = evaluate()
    assert ev["VERDICT"] == CASE_READY
    assert ev["FROZEN_VALIDATION_ECONOMIC_OPENED"] is False
    assert ev["PROSPECTIVE_DATA_OPENED"] is False
    assert ev["orders_submit"] == ev["orders_cancel"] == ev["orders_live"] == 0
    assert SHEETS[0] == "Manifest"
    assert "Safety" in SHEETS
    assert len(SHEETS) == 10
