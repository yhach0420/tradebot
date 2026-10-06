"""Phase 1 USDJPY historical adapter tests. No PnL, no sector response, no Frozen Validation economics."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from research.causal_driver_pb1 import EXPECTED_COMPLETE_STRATEGY_SHA256, EXPECTED_V4_MACHINE_SHA256, V5_CREATED
from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.contracts.errors import CausalTimeError
from research.causal_driver_pb1.contracts.time import UTC, bar_start_available_at, decide, jst
from research.causal_driver_pb1.identity.pin import bind_identities, contract_schema_sha256
from research.causal_driver_pb1.phase1 import EXPECTED_PHASE0_CONTRACT_SCHEMA_SHA256, SOURCE_ID
from research.causal_driver_pb1.phase1.bars import pair_raw_rows, synthetic_side
from research.causal_driver_pb1.phase1.checks import bidask_checks, gap_checks, restricted_date_checks, timestamp_checks
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.map_obs import map_bar
from research.causal_driver_pb1.phase1.publish import SHEETS
from research.causal_driver_pb1.phase1.transport import UsdJpyHistoricalDriverTransport, assert_transport_protocol
from research.causal_driver_pb1.transport.synthetic import HistoricalDriverTransport as Phase0HistoricalDriverTransport
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256
from research.pb1_v4_complete_strategy_build_and_economic_validation import EXPECTED_SOURCE_INVENTORY_SHA256


def test_u1_bar_start_available_at():
    bar = jst(2024, 9, 17, 9, 30, 0)
    assert bar_start_available_at(bar) == jst(2024, 9, 17, 9, 31, 0)
    ts = int(bar.astimezone(UTC).timestamp() * 1000)
    packed = pair_raw_rows(
        bid_rows=[synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")],
        ask_rows=[synthetic_side(ts_utc_ms=ts, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")],
        source_id=SOURCE_ID,
    )
    from research.causal_driver_pb1.phase1.checks import _src

    obs = map_bar(packed["bars"][0], _src())
    assert obs.event_time == bar
    assert obs.available_at == jst(2024, 9, 17, 9, 31, 0)


def test_u2_decision_093059_reject():
    from research.causal_driver_pb1.phase1.checks import _src

    bar = jst(2024, 9, 17, 9, 30, 0)
    ts = int(bar.astimezone(UTC).timestamp() * 1000)
    packed = pair_raw_rows(
        bid_rows=[synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")],
        ask_rows=[synthetic_side(ts_utc_ms=ts, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")],
        source_id=SOURCE_ID,
    )
    obs = map_bar(packed["bars"][0], _src())
    with pytest.raises(CausalTimeError):
        decide(available_at=obs.available_at, decision_time=jst(2024, 9, 17, 9, 30, 59))
    assert obs.usable_at(jst(2024, 9, 17, 9, 30, 59)) is False


def test_u3_decision_093100_accept():
    from research.causal_driver_pb1.phase1.checks import _src

    bar = jst(2024, 9, 17, 9, 30, 0)
    ts = int(bar.astimezone(UTC).timestamp() * 1000)
    packed = pair_raw_rows(
        bid_rows=[synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")],
        ask_rows=[synthetic_side(ts_utc_ms=ts, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")],
        source_id=SOURCE_ID,
    )
    obs = map_bar(packed["bars"][0], _src())
    assert decide(available_at=obs.available_at, decision_time=jst(2024, 9, 17, 9, 31, 0)) == "ACCEPT"
    assert obs.usable_at(jst(2024, 9, 17, 9, 31, 0)) is True


def test_utc_jst_date_wrap():
    utc_bar = datetime(2024, 9, 16, 15, 0, 0, tzinfo=UTC)
    packed = pair_raw_rows(
        bid_rows=[synthetic_side(ts_utc_ms=int(utc_bar.timestamp() * 1000), open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")],
        ask_rows=[synthetic_side(ts_utc_ms=int(utc_bar.timestamp() * 1000), open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")],
        source_id=SOURCE_ID,
    )
    bar = packed["bars"][0]
    assert bar.bar_start == jst(2024, 9, 17, 0, 0, 0)
    assert bar.available_at == jst(2024, 9, 17, 0, 1, 0)
    assert bar.source_timezone == "UTC"


def test_bid_ask_sanity_and_missing_side():
    got = bidask_checks()
    assert got["pass"], got


def test_duplicates_and_gaps_and_restricted():
    assert timestamp_checks()["pass"]
    assert gap_checks()["pass"]
    assert restricted_date_checks()["pass"]
    with pytest.raises(IngestDateDenied):
        assert_ingest_date_allowed("20260422")
    with pytest.raises(IngestDateDenied):
        assert_ingest_date_allowed("20260924")


def test_conflicting_duplicate_detected():
    ts = int(jst(2024, 9, 17, 9, 30, 0).astimezone(UTC).timestamp() * 1000)
    packed = pair_raw_rows(
        bid_rows=[
            synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID"),
            synthetic_side(ts_utc_ms=ts, open_=151.0, high=151.0, low=151.0, close=151.0, side="BID"),
        ],
        ask_rows=[synthetic_side(ts_utc_ms=ts, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")],
        source_id=SOURCE_ID,
    )
    assert packed["conflicting_duplicate_n"] >= 1


def test_missing_side_not_valid_for_alpha():
    ts = int(jst(2024, 9, 17, 9, 30, 0).astimezone(UTC).timestamp() * 1000)
    packed = pair_raw_rows(
        bid_rows=[synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")],
        ask_rows=[],
        source_id=SOURCE_ID,
    )
    bar = packed["bars"][0]
    assert bar.quality_status in {QualityStatus.DEGRADED, QualityStatus.REJECTED}
    from research.causal_driver_pb1.phase1.checks import _src

    obs = map_bar(bar, _src())
    assert obs.usable_at(bar.available_at) is False


def test_transport_same_interface_as_phase0():
    from research.causal_driver_pb1.phase1.checks import _src

    bar = jst(2024, 9, 17, 9, 30, 0)
    ts = int(bar.astimezone(UTC).timestamp() * 1000)
    packed = pair_raw_rows(
        bid_rows=[synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")],
        ask_rows=[synthetic_side(ts_utc_ms=ts, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")],
        source_id=SOURCE_ID,
    )
    obs = map_bar(packed["bars"][0], _src())
    real = UsdJpyHistoricalDriverTransport([obs])
    syn = Phase0HistoricalDriverTransport([])
    assert assert_transport_protocol(real)
    assert assert_transport_protocol(syn)
    assert next(real.stream()).direction.value == "NEUTRAL"


def test_phase0_contracts_unchanged_and_runtime_hashes():
    assert contract_schema_sha256() == EXPECTED_PHASE0_CONTRACT_SCHEMA_SHA256
    ident = bind_identities()
    assert ident["V4_MACHINE_SHA256"] == EXPECTED_V4_MACHINE_SHA256
    assert ident["COMPLETE_STRATEGY_SHA256"] == EXPECTED_COMPLETE_STRATEGY_SHA256
    assert v4_sha() == EXPECTED_V4_MACHINE_SHA256
    assert complete_strategy_sha256(machine_sha=EXPECTED_V4_MACHINE_SHA256, source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256) == EXPECTED_COMPLETE_STRATEGY_SHA256
    assert V5_CREATED is False


def test_no_economics_in_phase1_modules():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "phase1"
    forbidden = ("future_return", "mfe_bps", "mae_bps", "profit_factor", "net_pnl", "USDJPY_RET_1M")
    hits = []
    for path in root.glob("*.py"):
        if path.name == "evaluate.py":
            continue
        text = path.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                hits.append(f"{path.name}:{tok}")
    assert hits == []


def test_isolation_native_is_repo_root():
    from research.causal_driver_pb1.phase1.isolation import NATIVE, OUT, PHASE0_OUT

    assert (NATIVE / "src" / "research" / "causal_driver_pb1").is_dir()
    assert (PHASE0_OUT / "report.json").is_file()
    assert "src\\results" not in str(OUT) and "/src/results" not in str(OUT).replace("\\", "/")


def test_audit_sheet_names():
    assert SHEETS == (
        "Manifest",
        "Source_Identity",
        "Raw_Inventory",
        "Schema",
        "Timestamp_Semantics",
        "BidAsk_Alignment",
        "Duplicates",
        "Gaps",
        "Japan_Session_Coverage",
        "Transport",
        "Determinism",
        "Firewall",
        "Contamination",
        "Runtime_NonImpact",
        "Safety",
    )
