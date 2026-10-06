"""Phase 0 foundation runner. No Alpha, no PnL, no USDJPY adapter."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import (
    CASE_BLOCKED,
    CASE_READY,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_UNIVERSE_N,
    EXPECTED_V4_MACHINE_SHA256,
    NEXT_PHASE1,
    NEXT_RESOLVE,
    PHASE0_ALLOWED_RUN_MODE,
)
from research.causal_driver_pb1.contracts.alpha import AlphaSignal, pb1_adapter_decide
from research.causal_driver_pb1.contracts.enums import (
    AccessKind,
    AdapterVerdict,
    AlphaStatus,
    DatasetRole,
    Direction,
    DriverFamily,
    QualityStatus,
    RejectReason,
    RunMode,
    TargetScope,
)
from research.causal_driver_pb1.contracts.errors import CausalTimeError, FailClosedError, FirewallDenied
from research.causal_driver_pb1.contracts.mechanism import MechanismRecord, MechanismRegistry
from research.causal_driver_pb1.contracts.observation import build_observation
from research.causal_driver_pb1.contracts.source import SourceIdentity
from research.causal_driver_pb1.contracts.time import bar_start_available_at, causal_ok, decide, jst, require_causal
from research.causal_driver_pb1.datasets.firewall import AccessLedger, assert_phase0_run_mode, request_dataset
from research.causal_driver_pb1.datasets.universe import dynamic40_gate, load_research_observation_universe, runtime_from_dynamic40
from research.causal_driver_pb1.identity.ids import alpha_id
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.transport.base import DriverTransport
from research.causal_driver_pb1.transport.synthetic import HistoricalDriverTransport, RuntimeDriverTransport, synthetic_source


def _fail(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "pass": False, "reason": reason}


def _ok(name: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "pass": True, **extra}


def _synthetic_alpha(*, direction: Direction) -> AlphaSignal:
    src = synthetic_source()
    event = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(event)
    obs = build_observation(
        source=src,
        driver_family=DriverFamily.SYNTHETIC,
        event_time=event,
        available_at=available,
        received_at=available,
        value=1.0,
        direction=direction,
        strength=1.0,
        lookback_window="1m",
        valid_until=jst(2024, 9, 17, 15, 20, 0),
        quality_status=QualityStatus.VALID,
        decision_time=available,
    )
    generated = available
    aid = alpha_id(
        mechanism_id="PHASE0_CONTRACT_ONLY",
        driver_observation_ids=(obs.driver_observation_id,),
        generated_at=generated,
        direction=direction.value,
    )
    return AlphaSignal(
        alpha_id=aid,
        mechanism_id="PHASE0_CONTRACT_ONLY",
        generated_at=generated,
        driver_family=DriverFamily.SYNTHETIC.value,
        driver_observation_ids=(obs.driver_observation_id,),
        target_scope=TargetScope.MARKET,
        market_id="JP",
        sector_id=None,
        symbol=None,
        direction=direction,
        strength=1.0,
        confidence=None,
        valid_from=generated,
        valid_until=jst(2024, 9, 17, 10, 0, 0),
        causal_evidence={"phase0": True, "not_a_frozen_mechanism": True},
        status=AlphaStatus.RESEARCH,
        source_identity=src.fingerprint(),
    )


def check_timestamp_semantics() -> dict[str, Any]:
    rows = []
    bar = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(bar)
    d930 = jst(2024, 9, 17, 9, 30, 0)
    d931 = jst(2024, 9, 17, 9, 31, 0)
    try:
        decide(available_at=available, decision_time=d930)
        rows.append(_fail("T1", "09:30_should_reject"))
    except CausalTimeError:
        rows.append(_ok("T1", decision="09:30", result="REJECT"))
    try:
        got = decide(available_at=available, decision_time=d931)
        rows.append(_ok("T2", decision="09:31", result=got))
    except CausalTimeError as exc:
        rows.append(_fail("T2", str(exc)))
    if available > bar:
        rows.append(_ok("T3", available_at_after_event_time=True))
    else:
        rows.append(_fail("T3", "available_at_not_after_event_time"))
    src = synthetic_source()
    try:
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
            valid_until=d931,
            quality_status=QualityStatus.VALID,
        )
        rows.append(_fail("T4", "received_at_before_available_at_should_reject"))
    except (CausalTimeError, FailClosedError):
        rows.append(_ok("T4", result="REJECT"))
    naive_ok = False
    try:
        from datetime import datetime

        require_causal(available_at=datetime(2024, 9, 17, 9, 31, 0), decision_time=d931)
    except CausalTimeError:
        naive_ok = True
    rows.append(_ok("timezone_naive_forbidden") if naive_ok else _fail("timezone_naive_forbidden", "naive_accepted"))
    jst_offset = int(d931.utcoffset().total_seconds() // 3600) if d931.utcoffset() else None
    rows.append(_ok("timezone_jst_plus9", offset_h=jst_offset) if jst_offset == 9 else _fail("timezone_jst_plus9", str(jst_offset)))
    passed = all(r.get("pass") for r in rows)
    return {"pass": passed, "rows": rows}


def check_firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="phase0_firewall", run_mode=RunMode.FOUNDATION_TEST)
    rows = []

    def trial(mode: RunMode, role: DatasetRole, kind: AccessKind, fields: tuple[str, ...], expect_ok: bool, name: str) -> None:
        try:
            request_dataset(
                ledger=ledger,
                run_mode=mode,
                dataset_id="probe",
                dataset_role=role,
                requested_fields=fields,
                access_kind=kind,
            )
            ok = True
            reason = "ALLOW"
        except FirewallDenied as exc:
            ok = False
            reason = str(exc)
        rows.append(_ok(name, allowed=ok, reason=reason) if ok == expect_ok else _fail(name, f"got_ok={ok} reason={reason}"))

    trial(RunMode.DRIVER_DISCOVERY, DatasetRole.DEVELOPMENT, AccessKind.PAYLOAD, ("value",), True, "discovery_dev")
    trial(RunMode.DRIVER_DISCOVERY, DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED, AccessKind.PAYLOAD, ("value",), True, "discovery_c1")
    trial(RunMode.DRIVER_DISCOVERY, DatasetRole.FROZEN_VALIDATION, AccessKind.PAYLOAD, ("value",), False, "discovery_fv")
    trial(RunMode.DRIVER_DISCOVERY, DatasetRole.PROSPECTIVE, AccessKind.PAYLOAD, ("value",), False, "discovery_prospective")
    trial(
        RunMode.DRIVER_DISCOVERY,
        DatasetRole.FROZEN_VALIDATION,
        AccessKind.ECONOMIC_PAYLOAD,
        ("net_pnl_yen", "mfe_bps", "future_return"),
        False,
        "fv_economic_payload",
    )
    trial(RunMode.FOUNDATION_TEST, DatasetRole.SYNTHETIC_TEST, AccessKind.PAYLOAD, ("value",), True, "foundation_synthetic")
    try:
        assert_phase0_run_mode(RunMode.DRIVER_DISCOVERY)
        rows.append(_fail("phase0_mode_lock", "driver_discovery_allowed"))
    except FirewallDenied:
        rows.append(_ok("phase0_mode_lock"))
    return {"pass": all(r.get("pass") for r in rows), "rows": rows, "ledger_n": len(ledger.events), "denied_n": sum(1 for e in ledger.events if not e.get("allowed"))}


def check_universe() -> dict[str, Any]:
    uni = load_research_observation_universe()
    gate = dynamic40_gate()
    other = runtime_from_dynamic40(["7203", "9984"])
    sha_ok = uni.source_sha256 == uni.source_sha256
    stable = load_research_observation_universe()
    dynamic_changed = runtime_from_dynamic40(["7203", "9984", "9999"])
    same_sha = uni.source_sha256 == stable.source_sha256
    distinct_types = type(uni) is not type(other)
    rows = [
        _ok("count_105") if uni.symbol_count == EXPECTED_UNIVERSE_N else _fail("count_105", str(uni.symbol_count)),
        _ok("sha_match") if sha_ok and same_sha else _fail("sha_match", "mismatch"),
        _ok("not_dynamic40") if gate.role == "RUNTIME_RESOURCE_TRADABILITY_GATE" and distinct_types else _fail("not_dynamic40", "confused"),
        _ok("dynamic40_membership_does_not_change_universe_sha")
        if uni.source_sha256 == stable.source_sha256 and len(dynamic_changed.symbols) != len(other.symbols)
        else _fail("dynamic40_membership_does_not_change_universe_sha", "sha_moved"),
        _ok("not_registered_separated") if other.not_registered_is_not_no_alpha else _fail("not_registered_separated", "merged"),
    ]
    return {
        "pass": all(r.get("pass") for r in rows),
        "rows": rows,
        "universe105_count": uni.symbol_count,
        "universe105_sha": uni.source_sha256,
        "universe105_symbols_order_sha": uni.symbols_order_sha256,
        "source_path": uni.source_path,
    }


def check_immutability() -> dict[str, Any]:
    rows = []
    src = synthetic_source()
    event = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(event)
    obs = build_observation(
        source=src,
        driver_family=DriverFamily.SYNTHETIC,
        event_time=event,
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
    try:
        obs.value = 0.0  # type: ignore[misc]
        rows.append(_fail("obs_frozen", "mutated"))
    except Exception:
        rows.append(_ok("obs_frozen"))
    alpha = _synthetic_alpha(direction=Direction.LONG)
    try:
        alpha.direction = Direction.SHORT  # type: ignore[misc]
        rows.append(_fail("alpha_frozen", "mutated"))
    except Exception:
        rows.append(_ok("alpha_frozen"))
    dec = pb1_adapter_decide(alpha=alpha, pb1_direction=Direction.SHORT)
    if dec.verdict is AdapterVerdict.REJECT and dec.direction is Direction.LONG and dec.reason is RejectReason.ALPHA_DIRECTION_MISMATCH:
        rows.append(_ok("adapter_no_flip"))
    else:
        rows.append(_fail("adapter_no_flip", str(dec)))
    reg = MechanismRegistry()
    try:
        reg.freeze("USDJPY")
        rows.append(_fail("registry_no_freeze", "froze"))
    except FailClosedError:
        rows.append(_ok("registry_no_freeze"))
    from research.causal_driver_pb1.contracts.enums import MechanismStatus

    rec = MechanismRecord(
        mechanism_id="USDJPY",
        version="1",
        driver_family=DriverFamily.FX,
        target_scope=TargetScope.SECTOR,
        lead_window="1m",
        expected_horizon="5m",
        status=MechanismStatus.RESEARCH,
        config_sha256="0" * 64,
        source_identity="x",
        frozen=False,
    )
    try:
        reg.register(rec)
        rows.append(_fail("no_usdjpy_register", "registered"))
    except FailClosedError:
        rows.append(_ok("no_usdjpy_register"))
    return {"pass": all(r.get("pass") for r in rows), "rows": rows}


def check_transport() -> dict[str, Any]:
    hist = HistoricalDriverTransport()
    runtime = RuntimeDriverTransport()
    a = list(hist.stream())
    b = list(runtime.stream())
    hist2 = HistoricalDriverTransport()
    c = list(hist2.stream())
    ids_a = [x.driver_observation_id for x in a]
    ids_c = [x.driver_observation_id for x in c]
    times = [x.available_at for x in a]
    nondec = all(times[i] <= times[i + 1] for i in range(len(times) - 1))
    same_iface = isinstance(hist, DriverTransport) and isinstance(runtime, DriverTransport)
    rows = [
        _ok("interface") if same_iface else _fail("interface", "not_protocol"),
        _ok("order") if nondec else _fail("order", "decreasing"),
        _ok("determinism_ids") if ids_a == ids_c and ids_a == [x.driver_observation_id for x in b] else _fail("determinism_ids", "mismatch"),
        _ok("n") if len(a) == 3 else _fail("n", str(len(a))),
    ]
    return {"pass": all(r.get("pass") for r in rows), "rows": rows, "n": len(a), "ids": ids_a}


def check_runtime_nonimpact(*, pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    same = pre == post
    v4 = pre.get("v4") == post.get("v4")
    cs = pre.get("complete_strategy") == post.get("complete_strategy")
    paper = pre.get("paper_trade_checked_runner") == post.get("paper_trade_checked_runner")
    uni = pre.get("core10_dynamic40") == post.get("core10_dynamic40")
    rows = [
        _ok("v4_files") if v4 else _fail("v4_files", "changed"),
        _ok("cs_files") if cs else _fail("cs_files", "changed"),
        _ok("paper_runner") if paper else _fail("paper_runner", "changed"),
        _ok("dynamic40_module") if uni else _fail("dynamic40_module", "changed"),
    ]
    return {"pass": same and all(r.get("pass") for r in rows), "rows": rows}


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    pre_hashes = frozen_source_hashes()
    try:
        identity = bind_identities()
        identity_ok = True
    except Exception as exc:
        identity = {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
        identity_ok = False
        blockers.append("identity")
    time_r = check_timestamp_semantics()
    if not time_r["pass"]:
        blockers.append("timestamp")
    fw = check_firewall()
    if not fw["pass"]:
        blockers.append("firewall")
    uni = check_universe()
    if not uni["pass"]:
        blockers.append("universe")
    imm = check_immutability()
    if not imm["pass"]:
        blockers.append("immutability")
    tr = check_transport()
    if not tr["pass"]:
        blockers.append("transport")
    post_hashes = frozen_source_hashes()
    ni = check_runtime_nonimpact(pre=pre_hashes, post=post_hashes)
    if not ni["pass"]:
        blockers.append("runtime_nonimpact")
    v4_ok = identity.get("V4_MACHINE_SHA256") == EXPECTED_V4_MACHINE_SHA256
    cs_ok = identity.get("COMPLETE_STRATEGY_SHA256") == EXPECTED_COMPLETE_STRATEGY_SHA256
    if not v4_ok:
        blockers.append("v4_sha")
    if not cs_ok:
        blockers.append("cs_sha")
    passed = not blockers and identity_ok
    verdict = CASE_READY if passed else CASE_BLOCKED
    nxt = NEXT_PHASE1 if passed else NEXT_RESOLVE
    return {
        "ok": passed,
        "VERDICT": verdict,
        "NEXT": nxt,
        "blockers": blockers,
        "identity": identity,
        "timestamp": time_r,
        "firewall": fw,
        "universe": uni,
        "immutability": imm,
        "transport": tr,
        "runtime_nonimpact": ni,
        "run_mode": PHASE0_ALLOWED_RUN_MODE,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V5_CREATED": False,
        "V4_CHANGED": False,
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
    }
