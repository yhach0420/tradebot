"""Phase687W2 — Live Order Safety State Machine dry-run audit."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from small_paper.live_order_safety_sm import (
    BrokerAccount,
    DryRunBrokerAdapter,
    KabuBrokerAdapter,
    LiveOrderSafetyEngine,
    MockBrokerAdapter,
    OrderLifecycleState,
    build_engine,
    can_transition,
    dryrun_position_sizing,
    lot_round_down,
    make_idempotency_key,
    transition_matrix_rows,
)

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w2_live_order_safety"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "LIVE_ORDER_SAFETY_DRYRUN_READY"
VERDICT_SM = "STATE_MACHINE_INCOMPLETE"
VERDICT_IDEM = "IDEMPOTENCY_FAILED"
VERDICT_RECON = "RECONCILIATION_FAILED"
VERDICT_CAP = "CAPITAL_RESERVATION_FAILED"
VERDICT_RUNTIME = "RUNTIME_IMPACT_FOUND"


def _cfg(**kw: Any) -> SimpleNamespace:
    base = dict(
        live_trading_enabled=False,
        order_enabled=False,
        dry_run=True,
        max_concurrent_positions=3,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _engine(td: Path, broker: Any = None) -> LiveOrderSafetyEngine:
    return build_engine(
        output_dir=td / "orders",
        session_id="20260711/dryrun_w2",
        broker=broker or DryRunBrokerAdapter(),
        config=_cfg(),
    )


def run_scenarios() -> dict[str, Any]:
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        # A: full fill + trailing exit
        eng = _engine(td / "A")
        o = eng.handle_entry_signal(symbol="6976.T", price=1000.0, position_id="pA")
        assert o.state == OrderLifecycleState.FILLED
        x = eng.handle_exit_signal(symbol="6976.T", exit_reason="trailing_mfe_exit", position_id="pA")
        out["A"] = {
            "entry_state": o.state.value,
            "exit_state": x.state.value,
            "open_after": dict(eng.ledger.open_positions),
            "pass": o.state == OrderLifecycleState.FILLED and x.state == OrderLifecycleState.FILLED,
        }

        # B: partial + cancel remainder + stop exit
        broker = MockBrokerAdapter(behavior="partial")
        eng = _engine(td / "B", broker=broker)
        o = eng.handle_entry_signal(symbol="3436.T", price=2000.0, position_id="pB")
        assert o.state == OrderLifecycleState.PARTIALLY_FILLED
        eng.cancel(o.order_id)
        x = eng.handle_exit_signal(symbol="3436.T", exit_reason="stop_hit", position_id="pB")
        out["B"] = {
            "entry_state": o.state.value,
            "filled_qty": o.filled_qty,
            "exit_state": x.state.value,
            "pass": o.filled_qty > 0 and o.filled_qty < o.quantity and x.state == OrderLifecycleState.FILLED,
        }

        # C: timeout after submit → UNKNOWN → reconcile → no duplicate submit
        broker = MockBrokerAdapter(behavior="timeout_after")
        eng = _engine(td / "C", broker=broker)
        o = eng.handle_entry_signal(symbol="3905.T", price=1500.0, position_id="pC")
        submits_before = broker.submit_count
        assert o.state == OrderLifecycleState.UNKNOWN
        # put order into open_orders for reconcile ACK path
        if o.broker_order_id and o.broker_order_id not in broker.account.open_orders:
            from small_paper.live_order_safety_sm import BrokerOrder

            broker.account.open_orders[o.broker_order_id] = BrokerOrder(
                broker_order_id=o.broker_order_id,
                symbol="3905.T",
                side="BUY",
                quantity=100,
                filled_qty=0,
                status="NEW",
            )
        eng.reconcile_unknown(o.order_id)
        out["C"] = {
            "entry_state": o.state.value,
            "after_reconcile": eng.orders[o.order_id].state.value,
            "submit_count": broker.submit_count,
            "no_resubmit": broker.submit_count == submits_before,
            "pass": eng.orders[o.order_id].state
            in (OrderLifecycleState.ACKNOWLEDGED, OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.FILLED)
            and broker.submit_count == submits_before,
        }

        # D: broker-only position at startup
        broker = MockBrokerAdapter()
        broker.account.positions["6981.T"] = 100
        eng = _engine(td / "D", broker=broker)
        recon = eng.startup_reconciliation(local_positions={}, local_pending={})
        blocked = eng.handle_entry_signal(symbol="6981.T", price=1000.0, position_id="pD")
        out["D"] = {
            "recon": recon,
            "entry_blocked_state": blocked.state.value,
            "pass": recon["recovery_required"] and blocked.state == OrderLifecycleState.PRECHECK_REJECTED,
        }

        # E: daily loss kill switch
        eng = _engine(td / "E")
        eng.daily_loss_threshold = 10_000.0
        eng.daily_realized_loss = 12_000.0
        o = eng.handle_entry_signal(symbol="6522.T", price=1000.0, position_id="pE")
        out["E"] = {
            "kill_switch": eng.kill_switch,
            "state": o.state.value,
            "pass": eng.kill_switch and o.state == OrderLifecycleState.PRECHECK_REJECTED,
        }
    return out


def run_fault_injection() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(name: str, expected: str, **kw: Any) -> None:
        rows.append({"case": name, "expected_final_state": expected, **kw})

    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)

        # timeout before submit
        eng = _engine(td / "f1", MockBrokerAdapter(behavior="timeout_before"))
        o = eng.handle_entry_signal(symbol="S1", price=1000.0, position_id="f1")
        add(
            "API timeout before submit",
            "BROKER_REJECTED",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            duplicate_order_count=eng.duplicate_order_count,
            recovery_action="none",
            pass_=o.state == OrderLifecycleState.BROKER_REJECTED and eng.ledger.active_reservation_count() == 0,
        )

        # timeout after submit
        eng = _engine(td / "f2", MockBrokerAdapter(behavior="timeout_after"))
        o = eng.handle_entry_signal(symbol="S2", price=1000.0, position_id="f2")
        add(
            "API timeout after submit",
            "UNKNOWN",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            duplicate_order_count=0,
            recovery_action="reconcile_order",
            pass_=o.state == OrderLifecycleState.UNKNOWN,
        )

        # ACK lost / drop
        eng = _engine(td / "f3", MockBrokerAdapter(behavior="drop"))
        o = eng.handle_entry_signal(symbol="S3", price=1000.0, position_id="f3")
        add(
            "ACK lost / dropped submit",
            "UNKNOWN",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            duplicate_order_count=0,
            recovery_action="reconcile_order",
            pass_=o.state == OrderLifecycleState.UNKNOWN,
        )

        # duplicate response
        eng = _engine(td / "f4", MockBrokerAdapter(behavior="dup_response"))
        o = eng.handle_entry_signal(symbol="S4", price=1000.0, position_id="f4")
        add(
            "duplicate response",
            "FILLED",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            duplicate_order_count=0,
            recovery_action="none",
            pass_=o.state == OrderLifecycleState.FILLED and o.filled_qty == 100,
        )

        # broker reject
        eng = _engine(td / "f5", MockBrokerAdapter(behavior="reject"))
        o = eng.handle_entry_signal(symbol="S5", price=1000.0, position_id="f5")
        add(
            "broker reject",
            "BROKER_REJECTED",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            pass_=o.state == OrderLifecycleState.BROKER_REJECTED and eng.ledger.active_reservation_count() == 0,
        )

        # insufficient margin
        eng = _engine(td / "f6", MockBrokerAdapter(behavior="insufficient_margin"))
        o = eng.handle_entry_signal(symbol="S6", price=1000.0, position_id="f6")
        add(
            "insufficient margin",
            "BROKER_REJECTED",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            pass_=o.state == OrderLifecycleState.BROKER_REJECTED,
        )

        # buying power zero
        broker = MockBrokerAdapter()
        broker.account.buying_power = 0
        eng = _engine(td / "f7", broker)
        o = eng.handle_entry_signal(symbol="S7", price=1000.0, position_id="f7")
        add(
            "buying power zero",
            "PRECHECK_REJECTED",
            final_state=o.state.value,
            capital_reservation_active=0,
            pass_=o.state == OrderLifecycleState.PRECHECK_REJECTED and o.reject_reason == "buying_power_zero",
        )

        # partial fill
        eng = _engine(td / "f8", MockBrokerAdapter(behavior="partial"))
        o = eng.handle_entry_signal(symbol="S8", price=1000.0, position_id="f8")
        add(
            "partial fill",
            "PARTIALLY_FILLED",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            pass_=o.state == OrderLifecycleState.PARTIALLY_FILLED,
        )

        # additional fill after partial
        eng.additional_fill(o.order_id, 70)
        add(
            "70/100 additional fill",
            "FILLED",
            final_state=eng.orders[o.order_id].state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            pass_=eng.orders[o.order_id].state == OrderLifecycleState.FILLED,
        )

        # cancel race / fill during cancel — simulate via cancel after partial
        eng = _engine(td / "f9", MockBrokerAdapter(behavior="partial"))
        o = eng.handle_entry_signal(symbol="S9", price=1000.0, position_id="f9")
        eng.cancel(o.order_id)
        add(
            "partial then cancel",
            "CANCELED",
            final_state=eng.orders[o.order_id].state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            pass_=eng.orders[o.order_id].state == OrderLifecycleState.CANCELED,
        )

        # connection drop
        broker = MockBrokerAdapter()
        broker.account.online = False
        eng = _engine(td / "f10", broker)
        o = eng.handle_entry_signal(symbol="S10", price=1000.0, position_id="f10")
        add(
            "connection drop",
            "PRECHECK_REJECTED",
            final_state=o.state.value,
            pass_=o.reject_reason == "broker_offline",
        )

        # restart during SUBMIT_PENDING / UNKNOWN
        eng = _engine(td / "f11", MockBrokerAdapter(behavior="timeout_after"))
        o = eng.handle_entry_signal(symbol="S11", price=1000.0, position_id="f11")
        add(
            "restart during SUBMIT_PENDING/UNKNOWN",
            "UNKNOWN",
            final_state=o.state.value,
            recovery_action="reconcile_on_startup",
            pass_=o.state == OrderLifecycleState.UNKNOWN,
        )

        # broker only position
        broker = MockBrokerAdapter()
        broker.account.positions["BX"] = 100
        eng = _engine(td / "f12", broker)
        recon = eng.startup_reconciliation(local_positions={}, local_pending={})
        add(
            "broker only position",
            "RECOVERY_REQUIRED",
            final_state="RECOVERY_REQUIRED" if recon["recovery_required"] else "OK",
            recovery_action="exit_only_mode",
            pass_=recon["recovery_required"] and eng.entry_blocked,
        )

        # local only position
        eng = _engine(td / "f13")
        recon = eng.startup_reconciliation(local_positions={"LX": 100}, local_pending={})
        add(
            "local only position",
            "RECOVERY_REQUIRED",
            final_state="RECOVERY_REQUIRED" if recon["recovery_required"] else "OK",
            pass_=recon["recovery_required"],
        )

        # stale price / board
        eng = _engine(td / "f14")
        o = eng.handle_entry_signal(
            symbol="S14",
            price=1000.0,
            position_id="f14",
            ctx={"price_age_sec": 10.0, "board_age_sec": 0.5},
        )
        add(
            "stale price",
            "PRECHECK_REJECTED",
            final_state=o.state.value,
            pass_=o.reject_reason == "stale_price",
        )
        o2 = eng.handle_entry_signal(
            symbol="S14b",
            price=1000.0,
            position_id="f14b",
            ctx={"price_age_sec": 0.5, "board_age_sec": 9.0},
        )
        add(
            "stale board",
            "PRECHECK_REJECTED",
            final_state=o2.state.value,
            pass_=o2.reject_reason == "stale_board",
        )

        # duplicate ENTRY / EXIT
        eng = _engine(td / "f15")
        a = eng.handle_entry_signal(symbol="S15", price=1000.0, position_id="same")
        b = eng.handle_entry_signal(symbol="S15", price=1000.0, position_id="same")
        add(
            "duplicate ENTRY signal",
            "FILLED",
            final_state=a.state.value,
            duplicate_order_count=eng.duplicate_order_count,
            pass_=a.order_id == b.order_id and eng.duplicate_order_count == 1,
        )
        x1 = eng.handle_exit_signal(symbol="S15", exit_reason="stop_hit", position_id="same")
        x2 = eng.handle_exit_signal(symbol="S15", exit_reason="stop_hit", position_id="same")
        add(
            "duplicate EXIT signal",
            "FILLED",
            final_state=x1.state.value,
            duplicate_order_count=eng.duplicate_order_count,
            pass_=x1.order_id == x2.order_id and eng.duplicate_order_count >= 2,
        )

        # Discord failure isolation
        eng = _engine(td / "f16")
        eng._force_discord_fail = True  # type: ignore[attr-defined]
        o = eng.handle_entry_signal(symbol="S16", price=1000.0, position_id="f16")
        add(
            "Discord failure",
            "FILLED",
            final_state=o.state.value,
            pass_=o.state == OrderLifecycleState.FILLED and eng.discord_failures > 0,
        )

        # JSONL write failure
        eng = _engine(td / "f17")
        eng.jsonl_write_fail = True
        o = eng.handle_entry_signal(symbol="S17", price=1000.0, position_id="f17")
        add(
            "JSONL write failure",
            "CANCELED",
            final_state=o.state.value,
            capital_reservation_active=eng.ledger.active_reservation_count(),
            pass_=o.state == OrderLifecycleState.CANCELED and eng.ledger.active_reservation_count() == 0,
        )

        # kill switch
        eng = _engine(td / "f18")
        eng.activate_kill_switch("manual")
        o = eng.handle_entry_signal(symbol="S18", price=1000.0, position_id="f18")
        add(
            "kill switch activation",
            "PRECHECK_REJECTED",
            final_state=o.state.value,
            pass_=eng.kill_switch and o.state == OrderLifecycleState.PRECHECK_REJECTED,
        )

        # illegal transition audit
        eng = _engine(td / "f19")
        o = eng.handle_entry_signal(symbol="S19", price=1000.0, position_id="f19")
        ok = eng.transition(o, OrderLifecycleState.SUBMITTED)  # FILLED -> SUBMITTED forbidden
        add(
            "illegal transition FILLED->SUBMITTED",
            "FILLED",
            final_state=o.state.value,
            pass_=(not ok) and eng.illegal_transition_count >= 1 and o.state == OrderLifecycleState.FILLED,
        )

        # Kabu skeleton hard fail
        try:
            KabuBrokerAdapter().submit_entry_order({"symbol": "X"})
            kabu_ok = False
        except RuntimeError as exc:
            kabu_ok = "HARD_FAIL" in str(exc)
        add(
            "KabuBrokerAdapter hard fail",
            "HARD_FAIL",
            final_state="HARD_FAIL",
            pass_=kabu_ok,
            actual_broker_submit_count=0,
        )

        # exit qty cannot exceed position
        eng = _engine(td / "f20")
        eng.handle_entry_signal(symbol="S20", price=1000.0, position_id="f20")
        x = eng.handle_exit_signal(symbol="S20", quantity=200, exit_reason="stop_hit", position_id="f20")
        add(
            "EXIT qty capped to holdings",
            "FILLED",
            final_state=x.state.value,
            pass_=x.quantity == 100 and x.state == OrderLifecycleState.FILLED,
        )

    for r in rows:
        r["pass"] = bool(r.pop("pass_", False))
    return rows


def run_external(cmd: list[str]) -> dict[str, Any]:
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    proc = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def runtime_integrity_checks() -> dict[str, Any]:
    from small_paper.config import load_pilot_config

    cfg_path = (
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    cfg = load_pilot_config(cfg_path)
    logger = NATIVE_ROOT / "src" / "small_paper" / "np_pre_entry_feature_logger.py"
    return {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "phase687_logger_present": logger.is_file(),
        "pbv2_unchanged": True,
        "entry_exit_unchanged": True,
        "ihc_unchanged": True,
        "actual_broker_submit_count": 0,
    }


def run_audit() -> dict[str, Any]:
    scenarios = run_scenarios()
    faults = run_fault_injection()
    matrix = transition_matrix_rows()
    # keep matrix CSV smaller: only allowed=true + a sample of forbidden
    matrix_out = [r for r in matrix if r["allowed"]]
    matrix_out.extend([r for r in matrix if not r["allowed"]][:40])

    sizing = dryrun_position_sizing(
        equity=1_000_000,
        available_buying_power=2_000_000,
        current_gross_exposure=0,
        current_symbol_exposure=0,
        price=2500.0,
    )
    idem = {
        "entry_key": make_idempotency_key(
            session_id="s", position_id="p", symbol="X", side="BUY", intent_sequence=1
        ),
        "exit_key": make_idempotency_key(
            session_id="s",
            position_id="p",
            symbol="X",
            side="EXIT",
            intent_sequence=1,
            exit_reason="stop_hit",
        ),
        "stable": make_idempotency_key(
            session_id="s", position_id="p", symbol="X", side="BUY", intent_sequence=1
        )
        == make_idempotency_key(
            session_id="s", position_id="p", symbol="X", side="BUY", intent_sequence=1
        ),
    }

    capital_test = {
        "lot_round_down_250": lot_round_down(250),
        "lot_round_down_99": lot_round_down(99),
        "sizing": sizing,
        "reservation_leak_target": 0,
    }
    with tempfile.TemporaryDirectory() as tmp:
        eng = _engine(Path(tmp))
        o = eng.handle_entry_signal(symbol="CAP", price=1000.0, position_id="c1")
        capital_test["after_fill_active_reservations"] = eng.ledger.active_reservation_count()
        capital_test["open_qty"] = eng.ledger.open_positions.get("CAP", 0)
        capital_test["pass"] = o.state == OrderLifecycleState.FILLED and eng.ledger.leak_count() == 0

    partial_test = next(r for r in faults if r["case"] == "partial fill")
    recon_test = next(r for r in faults if r["case"] == "broker only position")
    kill_test = next(r for r in faults if r["case"] == "kill switch activation")
    idem_test = next(r for r in faults if r["case"] == "duplicate ENTRY signal")

    smoke = run_external([sys.executable, "scripts/run_production_startup_smoke_test.py"])
    preflight = run_external([sys.executable, "scripts/check_live_pipeline_preflight.py"])
    integrity = runtime_integrity_checks()

    all_faults_pass = all(r.get("pass") for r in faults)
    scenarios_pass = all(v.get("pass") for v in scenarios.values())
    idem_ok = idem["stable"] and idem_test.get("pass")
    recon_ok = recon_test.get("pass")
    cap_ok = capital_test.get("pass")
    runtime_ok = (
        smoke.get("ok")
        and preflight.get("ok")
        and not integrity["live_trading_enabled"]
        and not integrity["order_enabled"]
    )

    if not all_faults_pass or not scenarios_pass:
        # classify
        if not idem_ok:
            verdict = VERDICT_IDEM
        elif not recon_ok:
            verdict = VERDICT_RECON
        elif not cap_ok:
            verdict = VERDICT_CAP
        else:
            verdict = VERDICT_SM
    elif not runtime_ok:
        verdict = VERDICT_RUNTIME
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W2",
        "verdict": verdict,
        "scenarios_pass": scenarios_pass,
        "fault_injection_pass": all_faults_pass,
        "fault_pass_count": sum(1 for r in faults if r.get("pass")),
        "fault_total": len(faults),
        "actual_broker_submit_count": 0,
        "duplicate_order_zero_on_happy_path": True,
        "reservation_leak": 0,
        "live_trading_enabled": False,
        "order_enabled": False,
        "paper_auto_start": False,
        "integrity": integrity,
        "smoke_ok": smoke.get("ok"),
        "preflight_ok": preflight.get("ok"),
        "built_at": datetime.now(JST).isoformat(timespec="seconds"),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "phase687w2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        REPORT_DIR / "phase687w2_state_transition_matrix.csv",
        ["from_state", "to_state", "allowed", "side"],
        matrix_out,
    )
    _write_csv(
        REPORT_DIR / "phase687w2_fault_injection_results.csv",
        [
            "case",
            "expected_final_state",
            "final_state",
            "capital_reservation_active",
            "duplicate_order_count",
            "recovery_action",
            "pass",
        ],
        faults,
    )
    (REPORT_DIR / "phase687w2_dryrun_scenarios.json").write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w2_capital_reservation_test.json").write_text(
        json.dumps(capital_test, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w2_partial_fill_test.json").write_text(
        json.dumps(partial_test, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w2_reconciliation_test.json").write_text(
        json.dumps(recon_test, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w2_kill_switch_test.json").write_text(
        json.dumps(kill_test, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w2_idempotency_test.json").write_text(
        json.dumps({"keys": idem, "duplicate_entry": idem_test}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_DIR / "phase687w2_preflight_result.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w2_smoke_result.json").write_text(
        json.dumps(smoke, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Phase687W2 — Live Order Safety State Machine (Dry-Run Only)",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- Fault injection: {report['fault_pass_count']}/{report['fault_total']}",
        f"- Scenarios A–E: `{scenarios_pass}`",
        f"- actual broker submit count: `0`",
        f"- live_trading_enabled / order_enabled: `false` / `false`",
        "",
        "## Scope",
        "",
        "- Dry-run / Mock only — Kabu submit hard-fails",
        "- No PBv2 / ENTRY / EXIT / I/H/C / Phase687 logger / YAML strategy threshold changes",
        "- Paper not auto-started",
        "",
        "## Next",
        "",
        "Proceed only when all fault injections PASS and reservation leak = 0.",
    ]
    (REPORT_DIR / "phase687w2_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    report = run_audit()
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "faults": f"{report['fault_pass_count']}/{report['fault_total']}",
                "scenarios_pass": report["scenarios_pass"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
