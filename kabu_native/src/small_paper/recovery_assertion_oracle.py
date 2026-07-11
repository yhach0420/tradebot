"""Phase687W7A1 — Strict recovery assertion oracle (expected vs actual).

Pass is computed only from assertion AND results. Never set pass=True manually.
Does not change restore/runtime strategy logic — oracle and count semantics only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.live_order_safety_sm import OrderLifecycleState

TEST_ORACLE_VERSION = "687W7A1.1"

# Pre-intent aggregate states (order object may exist without Intent journal row)
PRE_INTENT_STATES = frozenset(
    {
        OrderLifecycleState.SIGNAL_RECEIVED,
        OrderLifecycleState.PRECHECK_PENDING,
        OrderLifecycleState.PRECHECK_REJECTED,
        OrderLifecycleState.CAPITAL_RESERVED,
    }
)

INTENT_OR_LATER = frozenset(
    {
        OrderLifecycleState.ORDER_INTENT_CREATED,
        OrderLifecycleState.SUBMIT_PENDING,
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.ACKNOWLEDGED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCEL_PENDING,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.BROKER_REJECTED,
        OrderLifecycleState.EXPIRED,
        OrderLifecycleState.UNKNOWN,
        OrderLifecycleState.RECOVERY_REQUIRED,
    }
)

# Policy A for kill_switch_active: hold pending reservation until operator/recon.
# Policy B (release on kill) is a separate scenario name — not used here.
KILL_SWITCH_RESERVATION_POLICY = {
    "scenario": "kill_switch_active",
    "policy_id": "HOLD_UNTIL_OPERATOR",
    "policy_letter": "A",
    "expected_active_reservation_count": 1,
    "expected_reserved_quantity": 100,
    "release_on_kill": False,
    "aligns_with_w7_drill": "E reservation_state=unchanged; B CANCEL_REQUIRED+release is separate",
    "separate_scenario_for_release": "kill_switch_pending_release",
    "reason": (
        "kill_switch_active writes intent+reserve then activate kill without release event; "
        "pending reservation held for operator/reconciliation (CANCEL_REQUIRED dry-run only)."
    ),
}

CAPITAL_RESERVED_SEMANTICS = {
    "expected_intent_count": 0,
    "expected_entry_intent_count": 0,
    "expected_exit_intent_count": 0,
    "expected_order_aggregate_count": 1,  # CAPITAL_RESERVED state object from state journal
    "expected_active_reservation_count": 1,
    "expected_reservation_record_count": 1,
    "expected_reserved_quantity": 100,
    "expected_reserved_notional_yen": 285000.0,
    "rationale": (
        "capital_reserved is pre-Intent: no order_intents.jsonl row. "
        "State event CAPITAL_RESERVED creates order aggregate count=1."
    ),
}


@dataclass
class CountSnapshot:
    order_aggregate_count: int = 0
    intent_count: int = 0
    entry_intent_count: int = 0
    exit_intent_count: int = 0
    active_reservation_count: int = 0
    reservation_record_count: int = 0
    reserved_quantity: int = 0
    reserved_notional_yen: float = 0.0
    position_count: int = 0
    position_quantity: int = 0
    remaining_quantity: int = 0
    entry_fill_quantity: int = 0
    exit_fill_quantity: int = 0
    entry_state: str = ""
    exit_state: str = ""
    broker_order_id: str = ""
    idempotency_key: str = ""
    kill_switch: bool = False
    reservation_leak: int = 0
    journal_sequence: int = 0
    automatic_resubmit_count: int = 0
    duplicate_intent_count: int = 0
    submit_count: int = 0
    cancel_count: int = 0
    recovery_mode: str = "NORMAL"

    def to_dict(self, prefix: str) -> dict[str, Any]:
        out = {}
        for k, v in self.__dict__.items():
            out[f"{prefix}_{k}" if not k.startswith(prefix) else k] = v
        # explicit required names
        return {
            f"{prefix}_order_aggregate_count": self.order_aggregate_count,
            f"{prefix}_intent_count": self.intent_count,
            f"{prefix}_entry_intent_count": self.entry_intent_count,
            f"{prefix}_exit_intent_count": self.exit_intent_count,
            f"{prefix}_active_reservation_count": self.active_reservation_count,
            f"{prefix}_reservation_record_count": self.reservation_record_count,
            f"{prefix}_reserved_quantity": self.reserved_quantity,
            f"{prefix}_reserved_notional_yen": self.reserved_notional_yen,
            f"{prefix}_position_count": self.position_count,
            f"{prefix}_position_quantity": self.position_quantity,
            f"{prefix}_remaining_quantity": self.remaining_quantity,
            f"{prefix}_entry_fill_quantity": self.entry_fill_quantity,
            f"{prefix}_exit_fill_quantity": self.exit_fill_quantity,
            f"{prefix}_entry_state": self.entry_state,
            f"{prefix}_exit_state": self.exit_state,
            f"{prefix}_broker_order_id": self.broker_order_id,
            f"{prefix}_idempotency_key": self.idempotency_key,
            f"{prefix}_kill_switch": self.kill_switch,
            f"{prefix}_reservation_leak": self.reservation_leak,
            f"{prefix}_journal_sequence": self.journal_sequence,
            f"{prefix}_automatic_resubmit_count": self.automatic_resubmit_count,
            f"{prefix}_duplicate_intent_count": self.duplicate_intent_count,
            f"{prefix}_submit_count": self.submit_count,
            f"{prefix}_cancel_count": self.cancel_count,
            f"{prefix}_recovery_mode": self.recovery_mode,
        }


def measure_actual(eng: Any, restore: Mapping[str, Any], *, submit_delta: int = 0) -> CountSnapshot:
    orders = list(eng.orders.values())
    entry = [o for o in orders if str(o.side).upper() in ("BUY", "2", "LONG")]
    exit_o = [o for o in orders if str(o.side).upper() in ("SELL", "1", "SHORT")]
    entry_intents = [o for o in entry if o.state in INTENT_OR_LATER]
    exit_intents = [o for o in exit_o if o.state in INTENT_OR_LATER]
    all_res = list(eng.ledger.reservations.values())
    active = [r for r in all_res if not r.released]
    reserved_qty = sum(max(0, r.quantity - r.filled_qty) for r in active)
    reserved_yen = 0.0
    for r in active:
        rem = max(0, r.quantity - r.filled_qty)
        reserved_yen += float(r.capital_yen) * (rem / max(1, r.quantity))
    pos = dict(eng.ledger.open_positions)
    pos_qty = sum(int(v) for v in pos.values() if int(v) > 0)
    pos_count = sum(1 for v in pos.values() if int(v) > 0)
    rem_qty = 0
    if entry:
        o = entry[0]
        if o.state == OrderLifecycleState.FILLED:
            rem_qty = 0
        else:
            rem_qty = max(0, o.quantity - o.filled_qty)
        if active and o.state == OrderLifecycleState.PARTIALLY_FILLED:
            rem_qty = max(0, active[0].quantity - active[0].filled_qty)
    return CountSnapshot(
        order_aggregate_count=len(orders),
        intent_count=len(entry_intents) + len(exit_intents),
        entry_intent_count=len(entry_intents),
        exit_intent_count=len(exit_intents),
        active_reservation_count=len(active),
        reservation_record_count=len(all_res),
        reserved_quantity=reserved_qty,
        reserved_notional_yen=round(reserved_yen, 2),
        position_count=pos_count,
        position_quantity=pos_qty,
        remaining_quantity=rem_qty,
        entry_fill_quantity=sum(o.filled_qty for o in entry),
        exit_fill_quantity=sum(o.filled_qty for o in exit_o),
        entry_state=entry[0].state.value if entry else "",
        exit_state=exit_o[0].state.value if exit_o else "",
        broker_order_id=entry[0].broker_order_id if entry else "",
        idempotency_key=entry[0].idempotency_key if entry else (exit_o[0].idempotency_key if exit_o else ""),
        kill_switch=bool(eng.kill_switch),
        reservation_leak=int(eng.ledger.leak_count()),
        journal_sequence=int(restore.get("journal_sequence_after") or getattr(eng.store, "_seq", 0) or 0),
        automatic_resubmit_count=int(restore.get("automatic_resubmit_count") or 0),
        duplicate_intent_count=int(restore.get("duplicate_intent_count") or 0),
        submit_count=int(submit_delta),
        cancel_count=0,
        recovery_mode=str(restore.get("recovery_mode") or "NORMAL"),
    )


def expected_for_stop_point(name: str, written: Mapping[str, Any]) -> CountSnapshot:
    """Canonical expected snapshot per stop point."""
    exp = CountSnapshot(
        journal_sequence=int(written.get("_seq_before") or 0),
        automatic_resubmit_count=0,
        duplicate_intent_count=0,
        submit_count=0,
        cancel_count=0,
        recovery_mode="NORMAL",
        kill_switch=False,
        reservation_leak=0,
    )
    if name == "session_startup":
        return exp
    if name == "readonly_token_acquired":
        return exp
    if name == "entry_signal_received":
        # signal event may create aggregate without intent
        exp.order_aggregate_count = 0  # our writer has no order_id on signal-only row
        return exp
    if name == "capital_reserved":
        s = CAPITAL_RESERVED_SEMANTICS
        exp.order_aggregate_count = s["expected_order_aggregate_count"]
        exp.intent_count = s["expected_intent_count"]
        exp.entry_intent_count = 0
        exp.exit_intent_count = 0
        exp.active_reservation_count = s["expected_active_reservation_count"]
        exp.reservation_record_count = s["expected_reservation_record_count"]
        exp.reserved_quantity = s["expected_reserved_quantity"]
        exp.reserved_notional_yen = s["expected_reserved_notional_yen"]
        exp.remaining_quantity = 100
        exp.entry_state = OrderLifecycleState.CAPITAL_RESERVED.value
        exp.idempotency_key = "idem-cap-001"
        return exp
    if name in ("intent_created", "journal_committed"):
        exp.order_aggregate_count = 1
        exp.intent_count = 1
        exp.entry_intent_count = 1
        exp.active_reservation_count = 1
        exp.reservation_record_count = 1
        exp.reserved_quantity = 100
        exp.reserved_notional_yen = 285000.0
        exp.remaining_quantity = 100
        exp.entry_state = OrderLifecycleState.ORDER_INTENT_CREATED.value
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        return exp
    if name == "acknowledged":
        exp.order_aggregate_count = 1
        exp.intent_count = 1
        exp.entry_intent_count = 1
        exp.active_reservation_count = 1
        exp.reservation_record_count = 1
        exp.reserved_quantity = 100
        exp.reserved_notional_yen = 285000.0
        exp.remaining_quantity = 100
        exp.entry_state = OrderLifecycleState.ACKNOWLEDGED.value
        exp.broker_order_id = str(written.get("broker_order_id") or "B-ACK-FIXTURE-001")
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        return exp
    if name == "partially_filled":
        exp.order_aggregate_count = 1
        exp.intent_count = 1
        exp.entry_intent_count = 1
        exp.active_reservation_count = 1
        exp.reservation_record_count = 1
        exp.reserved_quantity = 70
        exp.reserved_notional_yen = round(285000.0 * 70 / 100, 2)
        exp.position_count = 1
        exp.position_quantity = 30
        exp.remaining_quantity = 70
        exp.entry_fill_quantity = 30
        exp.entry_state = OrderLifecycleState.PARTIALLY_FILLED.value
        exp.broker_order_id = str(written.get("broker_order_id") or "B-ACK-FIXTURE-001")
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        exp.reservation_leak = 0
        return exp
    if name == "entry_filled":
        exp.order_aggregate_count = 1
        exp.intent_count = 1
        exp.entry_intent_count = 1
        exp.active_reservation_count = 0
        exp.reservation_record_count = 1  # released record still present
        exp.reserved_quantity = 0
        exp.reserved_notional_yen = 0.0
        exp.position_count = 1
        exp.position_quantity = 100
        exp.remaining_quantity = 0
        exp.entry_fill_quantity = 100
        exp.entry_state = OrderLifecycleState.FILLED.value
        exp.broker_order_id = str(written.get("broker_order_id") or "B-ACK-FIXTURE-001")
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        exp.reservation_leak = 0
        return exp
    if name == "exit_intent":
        exp.order_aggregate_count = 2
        exp.intent_count = 2
        exp.entry_intent_count = 1
        exp.exit_intent_count = 1
        exp.active_reservation_count = 0
        exp.reservation_record_count = 1
        exp.position_count = 1
        exp.position_quantity = 100
        exp.remaining_quantity = 0
        exp.entry_fill_quantity = 100
        exp.exit_fill_quantity = 0
        exp.entry_state = OrderLifecycleState.FILLED.value
        exp.exit_state = OrderLifecycleState.ORDER_INTENT_CREATED.value
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        return exp
    if name == "partial_exit":
        exp.order_aggregate_count = 2
        exp.intent_count = 2
        exp.entry_intent_count = 1
        exp.exit_intent_count = 1
        exp.active_reservation_count = 0
        exp.reservation_record_count = 1
        exp.position_count = 1
        exp.position_quantity = 60
        exp.remaining_quantity = 0
        exp.entry_fill_quantity = 100
        exp.exit_fill_quantity = 40
        exp.entry_state = OrderLifecycleState.FILLED.value
        exp.exit_state = OrderLifecycleState.PARTIALLY_FILLED.value
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        return exp
    if name == "kill_switch_active":
        pol = KILL_SWITCH_RESERVATION_POLICY
        exp.order_aggregate_count = 1
        exp.intent_count = 1
        exp.entry_intent_count = 1
        exp.active_reservation_count = int(pol["expected_active_reservation_count"])
        exp.reservation_record_count = 1
        exp.reserved_quantity = int(pol["expected_reserved_quantity"])
        exp.reserved_notional_yen = 285000.0
        exp.remaining_quantity = 100
        exp.entry_state = OrderLifecycleState.ORDER_INTENT_CREATED.value
        exp.idempotency_key = str(written.get("idempotency_key") or "idem-intent-001")
        exp.kill_switch = True
        exp.recovery_mode = "KILL_SWITCH_ACTIVE"
        return exp
    # unknown → force fail by impossible expected
    exp.order_aggregate_count = -1
    return exp


def _assert_eq(failures: list[dict[str, Any]], name: str, actual: Any, expected: Any, *, tol: float = 0.0) -> bool:
    if tol and isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        ok = abs(float(actual) - float(expected)) <= tol
    else:
        ok = actual == expected
    if not ok:
        failures.append({"assertion": name, "actual": actual, "expected": expected})
    return ok


def evaluate_assertions(
    stop_point: str,
    written: Mapping[str, Any],
    eng: Any,
    restore: Mapping[str, Any],
    *,
    submit_delta: int = 0,
    expected_override: Optional[CountSnapshot] = None,
    actual_override: Optional[CountSnapshot] = None,
) -> dict[str, Any]:
    """Compute pass solely from expected/actual equality assertions."""
    expected = expected_override or expected_for_stop_point(stop_point, written)
    actual = actual_override or measure_actual(eng, restore, submit_delta=submit_delta)
    failures: list[dict[str, Any]] = []

    pairs = [
        ("order_aggregate_count", actual.order_aggregate_count, expected.order_aggregate_count),
        ("intent_count", actual.intent_count, expected.intent_count),
        ("entry_intent_count", actual.entry_intent_count, expected.entry_intent_count),
        ("exit_intent_count", actual.exit_intent_count, expected.exit_intent_count),
        ("active_reservation_count", actual.active_reservation_count, expected.active_reservation_count),
        ("reservation_record_count", actual.reservation_record_count, expected.reservation_record_count),
        ("reserved_quantity", actual.reserved_quantity, expected.reserved_quantity),
        ("position_count", actual.position_count, expected.position_count),
        ("position_quantity", actual.position_quantity, expected.position_quantity),
        ("remaining_quantity", actual.remaining_quantity, expected.remaining_quantity),
        ("entry_fill_quantity", actual.entry_fill_quantity, expected.entry_fill_quantity),
        ("exit_fill_quantity", actual.exit_fill_quantity, expected.exit_fill_quantity),
        ("entry_state", actual.entry_state, expected.entry_state),
        ("exit_state", actual.exit_state, expected.exit_state),
        ("kill_switch", actual.kill_switch, expected.kill_switch),
        ("reservation_leak", actual.reservation_leak, expected.reservation_leak),
        ("automatic_resubmit_count", actual.automatic_resubmit_count, expected.automatic_resubmit_count),
        ("duplicate_intent_count", actual.duplicate_intent_count, expected.duplicate_intent_count),
        ("submit_count", actual.submit_count, expected.submit_count),
        ("cancel_count", actual.cancel_count, expected.cancel_count),
        ("recovery_mode", actual.recovery_mode, expected.recovery_mode),
    ]
    for name, a, e in pairs:
        # skip empty expected state strings when both empty already handled by ==
        _assert_eq(failures, name, a, e)

    _assert_eq(failures, "reserved_notional_yen", actual.reserved_notional_yen, expected.reserved_notional_yen, tol=0.02)

    if expected.idempotency_key:
        _assert_eq(failures, "idempotency_key", actual.idempotency_key, expected.idempotency_key)
    if expected.broker_order_id:
        _assert_eq(failures, "broker_order_id", actual.broker_order_id, expected.broker_order_id)

    # journal sequence: actual must be >= written before (continued), and match restore after if set
    if expected.journal_sequence:
        if actual.journal_sequence < expected.journal_sequence:
            failures.append(
                {
                    "assertion": "journal_sequence",
                    "actual": actual.journal_sequence,
                    "expected": f">={expected.journal_sequence}",
                }
            )

    # resubmit flag
    if restore.get("resubmit") is not False:
        failures.append({"assertion": "resubmit_flag", "actual": restore.get("resubmit"), "expected": False})

    # unexpected objects: aggregates beyond expected
    unexpected = 0
    if actual.order_aggregate_count > expected.order_aggregate_count:
        unexpected += actual.order_aggregate_count - expected.order_aggregate_count
    if actual.active_reservation_count > expected.active_reservation_count:
        unexpected += actual.active_reservation_count - expected.active_reservation_count
    if unexpected:
        failures.append(
            {
                "assertion": "unexpected_restored_object_count",
                "actual": unexpected,
                "expected": 0,
            }
        )

    assertion_count = len(pairs) + 2 + (1 if expected.idempotency_key else 0) + (1 if expected.broker_order_id else 0) + (
        1 if expected.journal_sequence else 0
    )
    # recount from failures vs total tracked
    assertion_pass_count = assertion_count - len(failures)
    # fix: assertion_count should be number of assertions we ran
    ran = len(pairs) + 1  # notional
    ran += 1  # resubmit
    if expected.idempotency_key:
        ran += 1
    if expected.broker_order_id:
        ran += 1
    if expected.journal_sequence:
        ran += 1
    ran += 1  # unexpected (always evaluated; may pass)
    assertion_count = ran
    assertion_failure_count = len(failures)
    assertion_pass_count = assertion_count - assertion_failure_count

    passed = assertion_failure_count == 0
    out: dict[str, Any] = {
        "stop_point": stop_point,
        "test_oracle_version": TEST_ORACLE_VERSION,
        **expected.to_dict("expected"),
        **actual.to_dict("restored"),
        # legacy aliases for CSV compatibility (strict values, not loose)
        "restored_order_aggregate_count": actual.order_aggregate_count,
        "restored_intent_count": actual.intent_count,
        "restored_entry_intent_count": actual.entry_intent_count,
        "restored_exit_intent_count": actual.exit_intent_count,
        "restored_active_reservation_count": actual.active_reservation_count,
        "restored_reservation_record_count": actual.reservation_record_count,
        "restored_reserved_quantity": actual.reserved_quantity,
        "restored_reserved_notional_yen": actual.reserved_notional_yen,
        "restored_position_count": actual.position_count,
        "restored_position_quantity": actual.position_quantity,
        "restored_fill_quantity": actual.entry_fill_quantity,
        "restored_remaining_quantity": actual.remaining_quantity,
        # deprecated ambiguous aliases (equal to aggregate / active / position counts)
        "restored_order_count": actual.order_aggregate_count,
        "restored_reservation_count": actual.active_reservation_count,
        "position_qty": actual.position_quantity,
        "expected_order_count": expected.order_aggregate_count,
        "expected_reservation_count": expected.active_reservation_count,
        "expected_position_count": expected.position_count,
        "written_intent_count": written.get("intent_count", 0),
        "written_reservation_count": written.get("reservation_count", 0),
        "written_position_count": written.get("position_count", 0),
        "journal_sequence_before": written.get("_seq_before", 0),
        "journal_sequence_after": actual.journal_sequence,
        "idempotency_key_match": actual.idempotency_key == expected.idempotency_key
        if expected.idempotency_key
        else True,
        "position_quantity_match": actual.position_quantity == expected.position_quantity,
        "reservation_amount_match": abs(actual.reserved_notional_yen - expected.reserved_notional_yen) <= 0.02
        and actual.reserved_quantity == expected.reserved_quantity,
        "kill_switch_match": actual.kill_switch == expected.kill_switch,
        "automatic_resubmit_count": actual.automatic_resubmit_count,
        "duplicate_intent_count": actual.duplicate_intent_count,
        "submit_count": actual.submit_count,
        "cancel_count": actual.cancel_count,
        "recovery_mode": actual.recovery_mode,
        "entry_state": actual.entry_state,
        "exit_state": actual.exit_state,
        "broker_order_id": actual.broker_order_id,
        "assertion_count": assertion_count,
        "assertion_pass_count": assertion_pass_count,
        "assertion_failure_count": assertion_failure_count,
        "assertion_failures": failures,
        "unexpected_restored_object_count": unexpected,
        "pass": passed,  # derived only
    }
    return out


def run_negative_oracle_tests(eng_factory, writer_factory) -> dict[str, Any]:
    """Intentionally break expected/actual and require pass=false for each."""
    cases: list[dict[str, Any]] = []

    def one(label: str, stop: str, mutate) -> None:
        session_dir, written, eng, restore = writer_factory(stop)
        expected = expected_for_stop_point(stop, written)
        actual = measure_actual(eng, restore)
        mutate(expected, actual, eng, restore)
        row = evaluate_assertions(
            stop, written, eng, restore, expected_override=expected, actual_override=actual
        )
        cases.append(
            {
                "case": label,
                "stop_point": stop,
                "pass": row["pass"],
                "assertion_failure_count": row["assertion_failure_count"],
                "detected_fail": row["pass"] is False and row["assertion_failure_count"] > 0,
                "failures": row["assertion_failures"][:3],
            }
        )

    one(
        "wrong_order_aggregate",
        "intent_created",
        lambda e, a, eng, r: setattr(e, "order_aggregate_count", e.order_aggregate_count + 1),
    )
    one(
        "wrong_reservation_count",
        "intent_created",
        lambda e, a, eng, r: setattr(e, "active_reservation_count", 0),
    )
    one(
        "wrong_position_count",
        "entry_filled",
        lambda e, a, eng, r: setattr(e, "position_count", 0),
    )
    one(
        "wrong_quantity",
        "partially_filled",
        lambda e, a, eng, r: setattr(e, "position_quantity", 31),
    )
    one(
        "wrong_state",
        "acknowledged",
        lambda e, a, eng, r: setattr(e, "entry_state", "FILLED"),
    )
    one(
        "wrong_idempotency",
        "intent_created",
        lambda e, a, eng, r: setattr(e, "idempotency_key", "TAMPERED"),
    )
    one(
        "wrong_remaining_reservation_71",
        "partially_filled",
        lambda e, a, eng, r: setattr(e, "reserved_quantity", 71),
    )
    one(
        "wrong_partial_exit_59",
        "partial_exit",
        lambda e, a, eng, r: setattr(e, "position_quantity", 59),
    )
    one(
        "wrong_submit_count_1",
        "intent_created",
        lambda e, a, eng, r: setattr(a, "submit_count", 1),
    )

    all_detected = all(c["detected_fail"] for c in cases)
    return {
        "test_oracle_version": TEST_ORACLE_VERSION,
        "cases": cases,
        "all_negative_detected_as_fail": all_detected,
        "pass": all_detected,
    }
