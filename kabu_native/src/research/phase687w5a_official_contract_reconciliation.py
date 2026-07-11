"""Phase687W5A — Official Kabu sendorder contract reconciliation audit."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w5a_official_contract_reconciliation"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "KABU_OFFICIAL_CONTRACT_RECONCILED"
VERDICT_MISMATCH = "OFFICIAL_INTERNAL_SCHEMA_MISMATCH"
VERDICT_EXCHANGE = "EXCHANGE_POLICY_UNRESOLVED"
VERDICT_MARGIN = "MARGIN_CONTRACT_INCOMPLETE"
VERDICT_CLOSE = "CLOSE_POSITION_CONTRACT_INCOMPLETE"
VERDICT_NETWORK = "NETWORK_ISOLATION_FAILED"
VERDICT_DESIGN = "DESIGN_CODE_MISMATCH"


def _run(cmd: list[str]) -> dict[str, Any]:
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    proc = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-600:],
    }


def _write_json(name: str, obj: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(name: str, rows: list[dict[str, Any]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        (REPORT_DIR / name).write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with (REPORT_DIR / name).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.kabu_order_execution_policy import dryrun_limit_policy, dryrun_market_policy
    from small_paper.kabu_order_request_builder import (
        OrderIntentContract,
        OrderRequestBuilder,
        actual_broker_cancel_count,
        actual_broker_submit_count,
    )
    from small_paper.kabu_sendorder_contract import (
        EXCHANGE_SOR,
        EXCHANGE_TSE,
        EXCHANGE_TSE_PLUS,
        ClosePositionMode,
        ExchangePolicy,
        FundTypeMode,
        TransactionType,
        exchange_policy_matrix,
        load_official_contract,
        transaction_type_matrix,
    )
    from small_paper.config import load_pilot_config
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w5a_official_sendorder_contract.py",
            "tests/test_phase687w5_kabu_order_contract.py",
            "-q",
            "--tb=line",
        ]
    )
    _write_json("phase687w5a_smoke_result.json", smoke)

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _write_json("phase687w5a_preflight_result.json", preflight)

    official = load_official_contract()
    _write_json("phase687w5a_official_contract_snapshot.json", official)

    _write_csv("phase687w5a_exchange_policy_matrix.csv", exchange_policy_matrix())
    _write_csv("phase687w5a_transaction_type_matrix.csv", transaction_type_matrix())

    # Valid fixtures
    entry = OrderRequestBuilder().build(
        OrderIntentContract(
            intent_id="ve",
            idempotency_key="valid-entry-sor",
            side="BUY",
            symbol="7203.T",
            quantity=100,
            position_id="pos-1",
            entry_or_exit="ENTRY",
            limit_price=2851.0,
            price_snapshot=2851.0,
            exchange_policy=ExchangePolicy.SOR.value,
            margin_trade_type_source="FIXTURE_EXPLICIT_UNVERIFIED_LIVE_ACCOUNT",
            fund_type_mode=FundTypeMode.OMIT_AUTO_11.value,
        ),
        dryrun_limit_policy(),
    )
    _write_json("phase687w5a_valid_margin_entry.json", entry.to_dict())

    exit_r = OrderRequestBuilder().build(
        OrderIntentContract(
            intent_id="vx",
            idempotency_key="valid-exit-repay",
            side="SELL",
            symbol="7203.T",
            quantity=100,
            position_id="pos-1",
            entry_or_exit="EXIT",
            exit_reason="hard_stop",
            holding_qty=100,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=EXCHANGE_TSE,
            expected_margin_trade_type=3,
            margin_trade_type=3,
            close_position_mode=ClosePositionMode.CLOSE_POSITION_ORDER.value,
            close_position_order=0,
            margin_trade_type_source="FIXTURE_EXPLICIT_UNVERIFIED_LIVE_ACCOUNT",
        ),
        dryrun_market_policy(),
    )
    _write_json("phase687w5a_valid_margin_exit.json", exit_r.to_dict())

    # Invalid cases
    invalid_cases = []
    cases = [
        ("normal_new_exchange_not_selected", OrderIntentContract(
            intent_id="i1", idempotency_key="inv1", side="BUY", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="ENTRY", limit_price=1.0, price_snapshot=1.0,
            exchange_policy=ExchangePolicy.NOT_SELECTED.value,
        ), dryrun_limit_policy()),
        ("cash_buy", OrderIntentContract(
            intent_id="i2", idempotency_key="inv2", side="BUY", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="ENTRY", limit_price=1.0, price_snapshot=1.0,
            exchange_policy=ExchangePolicy.SOR.value,
            transaction_type=TransactionType.CASH_BUY.value,
        ), dryrun_limit_policy()),
        ("repay_exchange_unknown", OrderIntentContract(
            intent_id="i3", idempotency_key="inv3", side="SELL", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="EXIT", holding_qty=100,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=None,
        ), dryrun_market_policy()),
        ("close_both", OrderIntentContract(
            intent_id="i4", idempotency_key="inv4", side="SELL", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="EXIT", holding_qty=100,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=EXCHANGE_TSE,
            close_position_mode=ClosePositionMode.BOTH_FORBIDDEN.value,
        ), dryrun_market_policy()),
        ("mtt_mismatch", OrderIntentContract(
            intent_id="i5", idempotency_key="inv5", side="SELL", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="EXIT", holding_qty=100,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=EXCHANGE_TSE,
            margin_trade_type=3, expected_margin_trade_type=1,
        ), dryrun_market_policy()),
    ]
    for name, intent, pol in cases:
        r = OrderRequestBuilder().build(intent, pol)
        invalid_cases.append({
            "case": name,
            "request_valid": r.request_valid,
            "error_category": r.error_category,
            "final_state": r.final_state,
            "would_submit": r.would_submit,
            "actual_submit_count": actual_broker_submit_count(),
            "pass": r.request_valid is False and r.would_submit is False,
        })
    _write_csv("phase687w5a_invalid_contract_cases.csv", invalid_cases)

    # FundType audit compare omit vs explicit
    omit = OrderRequestBuilder().build(
        OrderIntentContract(
            intent_id="ft", idempotency_key="ft-omit", side="BUY", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="ENTRY", limit_price=1.0, price_snapshot=1.0,
            exchange_policy=ExchangePolicy.SOR.value,
            fund_type_mode=FundTypeMode.OMIT_AUTO_11.value,
            margin_trade_type_source="FIXTURE_EXPLICIT",
        ),
        dryrun_limit_policy(),
    )
    expl = OrderRequestBuilder().build(
        OrderIntentContract(
            intent_id="ft2", idempotency_key="ft-11", side="BUY", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="ENTRY", limit_price=1.0, price_snapshot=1.0,
            exchange_policy=ExchangePolicy.TSE_PLUS.value,
            fund_type_mode=FundTypeMode.EXPLICIT_11.value,
            margin_trade_type_source="FIXTURE_EXPLICIT",
        ),
        dryrun_limit_policy(),
    )
    _write_json(
        "phase687w5a_fund_type_audit.json",
        {
            "omit": omit.fund_type_audit,
            "explicit_11": expl.fund_type_audit,
            "omit_has_fundtype_field": "FundType" in omit.api_payload,
            "explicit_fundtype": expl.api_payload.get("FundType"),
            "pass": omit.request_valid and expl.request_valid and omit.fund_type_audit.get("intentional_omission"),
            "margin_trade_type_live_account": "NOT_VERIFIED_IN_THIS_PHASE",
            "wiring_default_MarginTradeType": 3,
            "official_label": "一般信用（デイトレ）",
        },
    )

    cpo = OrderRequestBuilder().build(
        OrderIntentContract(
            intent_id="cp", idempotency_key="cpo", side="SELL", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="EXIT", holding_qty=100,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=EXCHANGE_TSE,
            close_position_mode=ClosePositionMode.CLOSE_POSITION_ORDER.value,
            close_position_order=0,
        ),
        dryrun_market_policy(),
    )
    cps = OrderRequestBuilder().build(
        OrderIntentContract(
            intent_id="cps", idempotency_key="cps", side="SELL", symbol="7203.T", quantity=100,
            position_id="p", entry_or_exit="EXIT", holding_qty=100,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=EXCHANGE_TSE,
            close_position_mode=ClosePositionMode.CLOSE_POSITIONS.value,
            hold_id="MOCK-E20200702TEST",
        ),
        dryrun_market_policy(),
    )
    _write_json(
        "phase687w5a_close_position_audit.json",
        {
            "ClosePositionOrder_0_meaning": "date_asc_pnl_desc (official)",
            "production_review_required": True,
            "order_only_valid": cpo.request_valid,
            "positions_only_valid": cps.request_valid,
            "pass": cpo.request_valid and cps.request_valid,
        },
    )

    contract = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_kabu_sendorder_contract_consistency.py")])
    contract_path = REPORT_DIR / "phase687w5a_contract_consistency.json"
    contract_payload = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.is_file() else {"pass": False}

    # Network isolation
    hard = True
    for meth, args in (
        ("submit_entry_order", ({"symbol": "X", "quantity": 100},)),
        ("cancel_order", ("x",)),
        ("emergency_flatten", ()),
    ):
        try:
            getattr(KabuBrokerAdapter(), meth)(*args)
            hard = False
        except RuntimeError as exc:
            if "HARD_FAIL" not in str(exc):
                hard = False
    net = {
        "actual_broker_submit_count": actual_broker_submit_count(),
        "actual_broker_cancel_count": actual_broker_cancel_count(),
        "kabu_write_hard_fail": hard,
        "pass": hard and actual_broker_submit_count() == 0,
    }
    _write_json("phase687w5a_network_isolation.json", net)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _write_json("phase687w5a_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W5A-official-sendorder-contract-reconciliation.md"
    vendor = DOCS / "vendor" / "kabusapi_sendorder_contract.json"
    doc_rev = {
        "adr_present": adr.is_file(),
        "vendor_snapshot_present": vendor.is_file(),
        "pass": adr.is_file() and vendor.is_file(),
    }
    _write_json("phase687w5a_documentation_review.json", doc_rev)

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "valid_entry": entry.request_valid and entry.api_payload.get("Exchange") == EXCHANGE_SOR,
        "valid_exit": exit_r.request_valid,
        "invalid_blocked": all(c["pass"] for c in invalid_cases),
        "contract": contract_payload.get("pass", False),
        "network": net.get("pass", False),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "fund_type": omit.request_valid and expl.request_valid,
        "close_xor": cpo.request_valid and cps.request_valid,
        "entry_exchange_not_tse_default": entry.api_payload.get("Exchange") != EXCHANGE_TSE,
    }

    if not checks["network"]:
        verdict = VERDICT_NETWORK
    elif not checks["contract"]:
        verdict = VERDICT_MISMATCH
    elif not checks["valid_entry"] or not checks["entry_exchange_not_tse_default"]:
        verdict = VERDICT_EXCHANGE
    elif not checks["valid_exit"] or not checks["fund_type"]:
        verdict = VERDICT_MARGIN
    elif not checks["close_xor"]:
        verdict = VERDICT_CLOSE
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not all(checks.values()):
        verdict = VERDICT_MISMATCH
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W5A",
        "verdict": verdict,
        "checks": checks,
        "statuses": {
            "official_snapshot": "IMPLEMENTED",
            "request_builder": "IMPLEMENTED_DRYRUN",
            "cash_orders": "NOT_IMPLEMENTED",
            "execution_policy_selection": "NOT_IMPLEMENTED",
            "network_submit": "PRODUCTION_FORBIDDEN",
            "sor_vs_tse_plus_production": "NOT_SELECTED",
            "margin_trade_type_live_account": "NOT_VERIFIED",
        },
        "live_trading_enabled": False,
        "order_enabled": False,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _write_json("phase687w5a_report.json", report)
    (REPORT_DIR / "phase687w5a_decision.md").write_text(
        f"""# Phase687W5A Decision

**Verdict:** `{verdict}`

## SoT priority
1. Official kabusapi reference
2. vendor snapshot
3. live_order_api_wiring.py
4. OrderRequestBuilder
5. fixtures

## Key reconciliations
- Normal NEW Exchange=1 forbidden (SOR/TSE+ or maintenance exception only)
- MARGIN_NEW_BUY / MARGIN_REPAY_SELL dry-run; CASH_* NOT_IMPLEMENTED
- FundType omit (auto 11) audited; explicit 11 fixture compared
- ClosePositions XOR ClosePositionOrder; order=0 requires production policy review
- MarginTradeType=3 is wiring default / fixture explicit — live account NOT_VERIFIED

## Absolute gates
- live_trading_enabled=false / order_enabled=false
- submit/cancel=0 / HARD_FAIL
- production ExecutionPolicy NOT_SELECTED
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
