"""Phase687W5B — Account capability + execution policy shadow audit (no write API)."""

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
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w5b_account_execution_policy_shadow"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "ACCOUNT_POLICY_SHADOW_READY"
VERDICT_MARGIN = "MARGIN_CAPABILITY_NOT_VERIFIED"
VERDICT_IDENTITY = "POSITION_IDENTITY_UNRESOLVED"
VERDICT_DATA = "POLICY_SHADOW_DATA_INSUFFICIENT"
VERDICT_LEAK = "CREDENTIAL_LEAK_FOUND"
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


def _wj(name: str, obj: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wc(name: str, rows: list[dict[str, Any]]) -> None:
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
            flat = {}
            for k in keys:
                v = r.get(k)
                if isinstance(v, (dict, list)):
                    flat[k] = json.dumps(v, ensure_ascii=False)
                else:
                    flat[k] = v
            w.writerow(flat)


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.kabu_account_capability import (
        build_account_capability_profile,
        margin_trade_type_matrix_rows,
    )
    from small_paper.kabu_close_policy import close_policy_matrix, decide_close_policy
    from small_paper.kabu_execution_policy_shadow import (
        build_board_snapshot,
        shadow_entry_exchange_candidates,
        shadow_entry_order_styles,
        shadow_exit_order_styles,
        summarize_fill_simulations,
    )
    from small_paper.kabu_order_request_builder import actual_broker_cancel_count, actual_broker_submit_count
    from small_paper.kabu_position_identity import artifact_has_raw_hold_id, parse_position_lots
    from small_paper.config import load_pilot_config
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w5b_account_execution_policy_shadow.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w5b_smoke_result.json", smoke)

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
        "request_valid_for_submit": False,
        "production_authorized": False,
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w5b_preflight_result.json", preflight)

    # Fixture live-like positions (not real account numbers / not real HoldIDs in artifacts)
    raw = [
        {
            "ExecutionID": "E20260711FIXTUREHOLD01",
            "Symbol": "7203",
            "LeavesQty": 100,
            "Exchange": 1,
            "MarginTradeType": 3,
            "AccountType": 4,
            "Price": 2850.0,
            "Side": "2",
            "ExecutionDay": 20260711,
        }
    ]
    lots = parse_position_lots(raw, provenance="FIXTURE")
    cap = build_account_capability_profile(
        account_status="ONLINE_VALID",
        cash_buying_power=500_000,
        margin_buying_power=2_000_000,
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_source="fixture_live_shaped_positions",
        capability_provenance="FIXTURE",
    )
    # W5B1: fixture must be FIXTURE_ONLY / NOT_VERIFIED (not live verified)
    assert cap.capability_status == "FIXTURE_ONLY"
    assert cap.margin_trade_type_status == "NOT_VERIFIED"
    _wj("phase687w5b_account_capability.json", cap.to_safe_dict())
    _wc("phase687w5b_margin_trade_type_matrix.csv", margin_trade_type_matrix_rows(cap))

    # Config-only must not be verified
    config_only = build_account_capability_profile(capability_provenance="CONFIG")
    assert config_only.wiring_default_treated_as_verified is False

    identity_audit = {
        "lots_artifact": [L.to_artifact_dict() for L in lots],
        "raw_hold_ids_in_artifact": False,
        "schema_version": "687W5B.1",
    }
    blob = json.dumps(identity_audit)
    identity_audit["raw_hold_ids_in_artifact"] = artifact_has_raw_hold_id(
        blob, [L.raw_hold_id for L in lots]
    )
    _wj("phase687w5b_position_identity_audit.json", identity_audit)

    _wc("phase687w5b_close_policy_matrix.csv", close_policy_matrix())
    close_ok = decide_close_policy(
        paper_position_id="paper-1", symbol="7203.T", paper_qty=100, lots=lots
    )
    close_none = decide_close_policy(
        paper_position_id="paper-x", symbol="9999.T", paper_qty=100, lots=lots
    )

    board = build_board_snapshot(best_bid=2849.0, best_ask=2851.0, last=2850.0, tick_size=1.0)
    path = [(200.0, 2850.5), (1500.0, 2848.0), (4000.0, 2845.0), (8000.0, 2840.0)]
    ex_rows = shadow_entry_exchange_candidates(
        symbol="7203.T",
        position_id="paper-1",
        accepted_at=datetime.now(JST).isoformat(timespec="seconds"),
        accept_price=2850.0,
        board=board,
    )
    _wc("phase687w5b_exchange_shadow.csv", ex_rows)

    entry_rows = shadow_entry_order_styles(
        symbol="7203.T",
        position_id="paper-1",
        accept_price=2850.0,
        board=board,
        price_path_after_accept=path,
        paper_fill_price=2850.5,
    )
    _wc("phase687w5b_entry_policy_shadow.csv", entry_rows)

    exit_rows = []
    for reason in ("stop_hit", "no_progress_exit", "trailing_mfe_exit", "session_close"):
        exit_rows.extend(
            shadow_exit_order_styles(
                symbol="7203.T",
                position_id="paper-1",
                exit_reason=reason,
                accept_price=2848.0,
                board=board,
                price_path_after_signal=path,
                paper_fill_price=2847.0,
                open_position_exchange=1,
                margin_trade_type=3,
            )
        )
    _wc("phase687w5b_exit_policy_shadow.csv", exit_rows)

    fill_summary = {
        "entry": summarize_fill_simulations(entry_rows),
        "exit": summarize_fill_simulations(exit_rows),
        "production_policy_selected": False,
        "w4s_sessions_required_before_selection": 3,
    }
    _wj("phase687w5b_fill_simulation_summary.json", fill_summary)

    faults = [
        {
            "case": "fixture_only_not_verified",
            "pass": cap.capability_status == "FIXTURE_ONLY"
            and cap.margin_trade_type_status == "NOT_VERIFIED",
        },
        {
            "case": "config_only_not_verified",
            "pass": config_only.margin_trade_type_status == "CONFIG_ONLY"
            and not config_only.wiring_default_treated_as_verified,
        },
        {
            "case": "exact_hold_close",
            "pass": close_ok.policy_id == "CLOSE_EXACT_HOLD_ID" and close_ok.request_valid,
        },
        {
            "case": "missing_hold_recovery",
            "pass": close_none.policy_id == "RECOVERY_REQUIRED",
        },
        {
            "case": "no_silent_order0",
            "pass": close_none.close_position_order is None,
        },
        {
            "case": "exchange_shadow_both",
            "pass": len(ex_rows) == 2 and all(not r["production_authorized"] for r in ex_rows),
        },
        {
            "case": "no_future_as_policy_input",
            "pass": all(not r["future_data_used_as_policy_input"] for r in entry_rows),
        },
        {
            "case": "hold_id_masked",
            "pass": not identity_audit["raw_hold_ids_in_artifact"],
        },
        {
            "case": "submit_zero",
            "pass": actual_broker_submit_count() == 0 and actual_broker_cancel_count() == 0,
        },
    ]
    _wc("phase687w5b_fault_injection.csv", faults)

    mask = {
        "raw_hold_in_capability": artifact_has_raw_hold_id(
            json.dumps(cap.to_safe_dict()), [L.raw_hold_id for L in lots]
        ),
        "raw_hold_in_identity_audit": identity_audit["raw_hold_ids_in_artifact"],
        "pass": (not identity_audit["raw_hold_ids_in_artifact"]),
    }
    _wj("phase687w5b_credential_masking.json", mask)

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
    _wj("phase687w5b_network_isolation.json", net)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w5b_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W5B-account-capability-execution-policy-shadow.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "modules": [
            "kabu_account_capability.py",
            "kabu_position_identity.py",
            "kabu_close_policy.py",
            "kabu_execution_policy_shadow.py",
        ],
        "pass": adr.is_file(),
    }
    _wj("phase687w5b_documentation_review.json", doc_rev)

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "faults": all(f["pass"] for f in faults),
        "masking": mask.get("pass", False),
        "network": net.get("pass", False),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "close_exact": close_ok.request_valid,
        "exchange_shadow": len(ex_rows) == 2,
        "entry_shadow": len(entry_rows) >= 4,
        "exit_shadow": len(exit_rows) >= 2,
        "no_policy_selection": True,
    }

    # READY = shadow collection ready; config-only alone would be MARGIN_CAPABILITY_NOT_VERIFIED
    # Fixture-shaped live positions demonstrate the verified-from-position path for readiness of the collector
    if not checks["network"]:
        verdict = VERDICT_NETWORK
    elif not checks["masking"]:
        verdict = VERDICT_LEAK
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not close_ok.request_valid and close_none.policy_id != "RECOVERY_REQUIRED":
        verdict = VERDICT_IDENTITY
    elif not checks["exchange_shadow"] or not checks["entry_shadow"]:
        verdict = VERDICT_DATA
    elif config_only.wiring_default_treated_as_verified:
        verdict = VERDICT_MARGIN
    elif not all(checks.values()):
        verdict = VERDICT_DATA
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W5B",
        "verdict": verdict,
        "checks": checks,
        "statuses": {
            "account_capability_collector": "IMPLEMENTED_DRYRUN",
            "position_identity": "IMPLEMENTED_DRYRUN",
            "close_policy": "IMPLEMENTED_DRYRUN",
            "exchange_shadow": "IMPLEMENTED_DRYRUN",
            "order_style_shadow": "IMPLEMENTED_DRYRUN",
            "fill_simulator": "IMPLEMENTED_MOCK",
            "production_policy_selection": "NOT_IMPLEMENTED",
            "network_submit": "PRODUCTION_FORBIDDEN",
            "w4s_min_sessions_before_selection": 3,
        },
        "live_trading_enabled": False,
        "order_enabled": False,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w5b_report.json", report)
    (REPORT_DIR / "phase687w5b_decision.md").write_text(
        f"""# Phase687W5B Decision

**Verdict:** `{verdict}`

READY means **shadow collection readiness**, not production Execution Policy adoption.

## Absolute gates
- live_trading_enabled=false / order_enabled=false
- submit/cancel=0 / HARD_FAIL
- request_valid_for_submit=false / production_authorized=false
- MarginTradeType wiring default never VERIFIED alone
- ClosePositionOrder=0 not production; no silent fallback
- SOR/TSE+ and MARKET/LIMIT are shadow-only

## W4S
- Soak snapshot fields extended for capability/shadow counts
- ≥3 W4S sessions required before any production policy selection
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
