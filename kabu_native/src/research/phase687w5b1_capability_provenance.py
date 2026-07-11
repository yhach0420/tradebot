"""Phase687W5B1 — Capability provenance hardening audit."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w5b1_capability_provenance"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "CAPABILITY_PROVENANCE_FIXED"
VERDICT_BOUNDARY = "LIVE_FIXTURE_BOUNDARY_FAILED"
VERDICT_LEAK = "CREDENTIAL_LEAK_FOUND"
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
            w.writerow({k: r.get(k) for k in keys})


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.kabu_account_capability import (
        CapabilityProvenance,
        LiveVerificationEvidence,
        build_account_capability_profile,
        normalize_provenance,
        provenance_matrix_rows,
        soak_provenance_fields,
    )
    from small_paper.kabu_position_identity import artifact_has_raw_hold_id, parse_position_lots
    from small_paper.config import load_pilot_config
    from small_paper.live_order_safety_sm import KabuBrokerAdapter
    from small_paper.kabu_order_request_builder import actual_broker_submit_count

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w5b1_capability_provenance.py",
            "tests/test_phase687w5b_account_execution_policy_shadow.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w5b1_smoke_result.json", smoke)

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w5b1_preflight_result.json", preflight)

    # Corrected W5B fixture result (do not overwrite W5B report dir)
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
    corrected = build_account_capability_profile(
        account_status="ONLINE_VALID",
        cash_buying_power=500_000,
        margin_buying_power=2_000_000,
        position_lots=[L.to_artifact_dict() for L in lots],
        capability_source="fixture_live_shaped_positions",
    )
    assert normalize_provenance("fixture_live_shaped_positions") == "FIXTURE"
    assert corrected.capability_status == "FIXTURE_ONLY"
    assert corrected.margin_trade_type_status == "NOT_VERIFIED"
    assert corrected.verification_confidence == "low"
    assert corrected.request_valid_for_submit is False
    assert corrected.production_authorized is False
    _wj(
        "phase687w5b1_corrected_capability.json",
        {
            "note": "Corrected interpretation of W5B fixture_live_shaped_positions; W5B artifacts not overwritten",
            "legacy_source_string": "fixture_live_shaped_positions",
            "normalized_provenance": "FIXTURE",
            "profile": corrected.to_safe_dict(),
        },
    )

    _wc("phase687w5b1_provenance_matrix.csv", provenance_matrix_rows())

    # Verification test matrix results
    ts = datetime.now(JST).isoformat(timespec="seconds")
    live_lots = parse_position_lots(
        [dict(raw[0], ExecutionID="E20260711LIVE01")],
        provenance="LIVE_API_POSITION_RESPONSE",
        source_timestamp=ts,
    )
    live_ok = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in live_lots],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=LiveVerificationEvidence(
            provenance="LIVE_API_POSITION_RESPONSE",
            token_acquired=True,
            positions_endpoint_ok=True,
            response_timestamp=ts,
            schema_validation_pass=True,
        ),
    )
    zero = build_account_capability_profile(
        position_lots=[],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=LiveVerificationEvidence(
            provenance="LIVE_API_POSITION_RESPONSE",
            token_acquired=True,
            positions_endpoint_ok=True,
            response_timestamp=ts,
            schema_validation_pass=True,
        ),
    )
    stale_ts = (datetime.now(JST) - timedelta(hours=2)).isoformat(timespec="seconds")
    stale = build_account_capability_profile(
        position_lots=[L.to_artifact_dict() for L in live_lots],
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        evidence=LiveVerificationEvidence(
            provenance="LIVE_API_POSITION_RESPONSE",
            token_acquired=True,
            positions_endpoint_ok=True,
            response_timestamp=stale_ts,
            schema_validation_pass=True,
            max_age_sec=300,
        ),
    )
    verification_tests = {
        "fixture_not_verified": corrected.capability_status == "FIXTURE_ONLY",
        "live_verified_when_evidence_complete": live_ok.capability_status
        == "VERIFIED_FROM_LIVE_POSITION",
        "zero_positions_not_mtt_verified": zero.capability_status == "LIVE_API_NO_POSITIONS"
        and zero.margin_trade_type_status == "NOT_VERIFIED",
        "stale_not_verified": stale.capability_status == "NOT_VERIFIED",
        "config_not_verified": build_account_capability_profile(
            capability_provenance="CONFIG"
        ).capability_status
        == "CONFIG_ONLY",
        "pass": True,
    }
    verification_tests["pass"] = all(
        v for k, v in verification_tests.items() if k != "pass" and isinstance(v, bool)
    )
    _wj("phase687w5b1_verification_tests.json", verification_tests)

    mask = {
        "raw_hold_in_corrected": artifact_has_raw_hold_id(
            json.dumps(corrected.to_safe_dict()), [L.raw_hold_id for L in lots]
        ),
        "fixture_hold_id_live_verified": any(
            L.to_artifact_dict().get("hold_id_live_verified") for L in lots
        ),
        "pass": True,
    }
    mask["pass"] = (not mask["raw_hold_in_corrected"]) and (not mask["fixture_hold_id_live_verified"])
    _wj("phase687w5b1_holdid_masking.json", mask)

    # Soak snapshot provenance field presence (unit-level)
    soak_fields = soak_provenance_fields(corrected)
    _wj(
        "phase687w5b1_soak_snapshot_test.json",
        {
            "fields": soak_fields,
            "required_keys_present": all(
                k in soak_fields
                for k in (
                    "capability_provenance",
                    "fixture_used",
                    "synthetic_used",
                    "live_account_response_received",
                    "live_position_response_received",
                    "live_position_count",
                    "margin_trade_type_live_verified",
                    "exchange_live_verified",
                    "hold_id_live_verified",
                    "verified_response_time",
                    "verification_failure_reason",
                )
            ),
            "bridge_contains_soak_provenance": "capability_provenance"
            in (NATIVE_ROOT / "src" / "small_paper" / "live_order_runtime_bridge.py").read_text(
                encoding="utf-8"
            ),
            "pass": True,
        },
    )
    soak_test = json.loads((REPORT_DIR / "phase687w5b1_soak_snapshot_test.json").read_text(encoding="utf-8"))
    soak_test["pass"] = soak_test["required_keys_present"] and soak_test["bridge_contains_soak_provenance"]
    _wj("phase687w5b1_soak_snapshot_test.json", soak_test)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w5b1_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W5B-account-capability-execution-policy-shadow.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "adr_mentions_provenance": "provenance" in adr.read_text(encoding="utf-8").lower()
        if adr.is_file()
        else False,
        "pass": adr.is_file(),
    }
    _wj("phase687w5b1_documentation_review.json", doc_rev)

    hard = True
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 100})
        hard = False
    except RuntimeError as exc:
        hard = "HARD_FAIL" in str(exc)

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "corrected_fixture": corrected.capability_status == "FIXTURE_ONLY",
        "verification_tests": verification_tests.get("pass", False),
        "masking": mask.get("pass", False),
        "soak_fields": soak_test.get("pass", False),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "submit_zero": actual_broker_submit_count() == 0 and hard,
        "boundary": normalize_provenance("fixture_live_shaped_positions") == "FIXTURE"
        and live_ok.capability_status == "VERIFIED_FROM_LIVE_POSITION",
    }

    if not checks["masking"]:
        verdict = VERDICT_LEAK
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not checks["boundary"] or not checks["corrected_fixture"]:
        verdict = VERDICT_BOUNDARY
    elif not all(checks.values()):
        verdict = VERDICT_BOUNDARY
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W5B1",
        "verdict": verdict,
        "checks": checks,
        "note": "W5B artifacts preserved; corrected fixture interpretation stored here",
        "live_trading_enabled": False,
        "order_enabled": False,
        "production_policy_selection": "NOT_IMPLEMENTED",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w5b1_report.json", report)
    (REPORT_DIR / "phase687w5b1_decision.md").write_text(
        f"""# Phase687W5B1 Decision

**Verdict:** `{verdict}`

## Fix
- `fixture_live_shaped_positions` → provenance `FIXTURE` → `FIXTURE_ONLY` / `NOT_VERIFIED`
- VERIFIED_FROM_LIVE_POSITION only with LIVE_API_POSITION_RESPONSE + full evidence
- Zero live positions → `LIVE_API_NO_POSITIONS` / MTT `NOT_VERIFIED`
- Fixture results are not policy evidence

## Absolute gates
- request_valid_for_submit=false
- production_authorized=false
- MarginTradeType=3 production adoption forbidden
- Execution Policy selection forbidden
- Real orders forbidden

Monday W4S Forward must use real API provenance for verification.
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
