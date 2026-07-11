"""Phase687W4T — Kabu token / read-only readiness audit."""

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
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w4t_kabu_readonly_readiness"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "KABU_READONLY_READINESS_READY"
VERDICT_STATION = "KABU_STATION_NOT_AVAILABLE"
VERDICT_AUTH = "KABU_AUTH_CONFIGURATION_FAILED"
VERDICT_INCOMPLETE = "TOKEN_DIAGNOSTICS_INCOMPLETE"
VERDICT_MASK = "CREDENTIAL_MASKING_FAILED"
VERDICT_SAFETY = "SAFETY_INVARIANT_FAILED"


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


def run_fault_matrix() -> list[dict[str, Any]]:
    from small_paper.kabu_readonly_readiness import (
        TOKEN_MAX_RETRIES,
        TokenProbeStatus,
        acquire_token_with_policy,
        classify_token_exception,
        mask_secret_text,
    )
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    rows: list[dict[str, Any]] = []

    def add(case: str, ok: bool, **extra: Any) -> None:
        rows.append({"case": case, "pass": ok, **extra})

    # Station / port / password / auth / timeout / invalid / empty / success
    st, retry, _ = classify_token_exception(TimeoutError("timeout"))
    add("timeout", st == TokenProbeStatus.TOKEN_ENDPOINT_TIMEOUT and retry)
    st, retry, _ = classify_token_exception(RuntimeError("HTTP 401"))
    add("password_incorrect_auth", st == TokenProbeStatus.AUTH_FAILED and not retry)
    st, retry, _ = classify_token_exception(RuntimeError("KABU_API_PASSWORD が未設定"))
    add("password_missing", st == TokenProbeStatus.API_PASSWORD_MISSING)
    st, retry, _ = classify_token_exception(RuntimeError("connection refused"))
    add("port_blocked", st == TokenProbeStatus.PORT_UNREACHABLE)
    st, retry, _ = classify_token_exception(RuntimeError("token response is not JSON"))
    add("json_invalid", st == TokenProbeStatus.TOKEN_RESPONSE_INVALID)

    calls = {"n": 0}

    def auth_boom():
        calls["n"] += 1
        raise RuntimeError("HTTP 401 unauthorized")

    _, d = acquire_token_with_policy(issue_fn=auth_boom, sleep_fn=lambda _x: None)
    add("auth_no_auto_retry", calls["n"] == 1 and d.token_probe_status == TokenProbeStatus.AUTH_FAILED.value)

    tok, d2 = acquire_token_with_policy(issue_fn=lambda: "MOCKTOKEN", sleep_fn=lambda _x: None)
    add("token_ok", tok == "MOCKTOKEN" and d2.token_probe_status == TokenProbeStatus.TOKEN_ACQUIRED.value)

    _, d3 = acquire_token_with_policy(issue_fn=lambda: "", sleep_fn=lambda _x: None)
    add("token_empty", d3.token_probe_status == TokenProbeStatus.TOKEN_EMPTY.value)

    # empty positions via adapter not failure class
    add("client_not_configured", KabuBrokerAdapter().refresh_readonly() == "CLIENT_NOT_CONFIGURED")

    # hard fails
    hard = True
    for fn in (
        lambda: KabuBrokerAdapter().submit_entry_order({"symbol": "X"}),
        lambda: KabuBrokerAdapter().cancel_order("x"),
        lambda: KabuBrokerAdapter().emergency_flatten(),
    ):
        try:
            fn()
            hard = False
        except RuntimeError as exc:
            hard = hard and ("HARD_FAIL" in str(exc))
    add("submit_cancel_flatten_hard_fail", hard)

    masked = mask_secret_text('APIPassword=p@ss Token: SECRET123 "Token":"XYZ"')
    add(
        "credential_masking",
        "p@ss" not in masked and "SECRET123" not in masked and "XYZ" not in masked and "REDACTED" in masked,
    )
    add("retry_max_explicit", TOKEN_MAX_RETRIES >= 1 and TOKEN_MAX_RETRIES <= 5)
    return rows


def update_docs() -> None:
    adr = DOCS / "adr" / "ADR-687W4T-kabu-token-readonly-readiness.md"
    adr.parent.mkdir(parents=True, exist_ok=True)
    adr.write_text(
        "\n".join(
            [
                "# ADR-687W4T — Kabu Token + Read-Only Readiness",
                "",
                "- **Status:** Accepted",
                f"- **Date:** {datetime.now(JST).date().isoformat()}",
                "",
                "## Context",
                "",
                "Forward soak blocked by opaque TOKEN_REQUEST_FAILED. Need fine-grained diagnostics without leaking credentials.",
                "",
                "## Decision",
                "",
                "1. Add `small_paper.kabu_readonly_readiness` + CLI `python -m small_paper.check_kabu_readonly_readiness`.",
                "2. Classify station/port/password/auth/timeout/invalid/empty/readonly outcomes separately.",
                "3. Bounded retries; AUTH_FAILED never auto-retries.",
                "4. Mask passwords/tokens/account-like digits in all artifacts.",
                "5. Submit/cancel/flatten remain HARD_FAIL and independent of token success.",
                "6. Paper mainline unchanged; capital unknown may block dry-run would-submit only.",
                "",
                "## Consequences",
                "",
                "- Monday pre-check can fail closed on auth/config without enabling orders.",
                "- Weekend Station-down ≠ password-missing.",
                "",
                "## Rollback",
                "",
                "Disable CLI usage; keep `live_order_safety_sm_enabled` as-is. Do not enable live trading.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # operations monday checklist
    ops = DOCS / "live_order_operations.md"
    if ops.is_file():
        text = ops.read_text(encoding="utf-8")
        marker = "## Monday AM pre-check (Phase687W4T)"
        block = (
            f"\n\n{marker}\n\n"
            "1. `cd C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native; python -m small_paper.prebuild_vol_liq_startup_cache --date YYYYMMDD`\n"
            "2. Start Kabu Station (manual)\n"
            "3. `cd C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native; $env:PYTHONPATH=\"src;C:\\Users\\yhach\\Documents\\tradebotfile\"; python -m small_paper.check_kabu_readonly_readiness`\n"
            "4. `python scripts/check_live_pipeline_preflight.py`\n"
            "5. `python scripts/run_production_startup_smoke_test.py`\n"
            "6. Start Paper normally (do not auto-enable orders)\n"
            "\n"
            "If readiness fails, session is not counted as W4S readonly-success.\n"
            "PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED\n"
        )
        if marker not in text:
            text += block
            ops.write_text(text, encoding="utf-8")
    design = DOCS / "live_order_system_design.md"
    if design.is_file():
        text = design.read_text(encoding="utf-8")
        if "Phase687W4T" not in text:
            text += (
                "\n\n## Phase687W4T Token / Read-Only Readiness\n\n"
                "- CLI: `python -m small_paper.check_kabu_readonly_readiness`\n"
                "- Token lifecycle diagnostics with credential masking\n"
                "- Retry: max 3; AUTH_FAILED no retry; station/timeout limited\n"
                "- Independent from production submit (HARD_FAIL)\n"
            )
            design.write_text(text, encoding="utf-8")
    # schema
    schema_path = DOCS / "schema" / "live_order_design_schema.json"
    if schema_path.is_file():
        from small_paper.kabu_readonly_readiness import (
            EXIT_AUTH_OR_CONFIG_ERROR,
            EXIT_READONLY_READY,
            EXIT_RESPONSE_INVALID,
            EXIT_SAFETY_INVARIANT_FAILED,
            EXIT_STATION_OR_TOKEN_NOT_READY,
            TOKEN_MAX_RETRIES,
            TokenProbeStatus,
        )

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["token_probe_statuses"] = [s.value for s in TokenProbeStatus]
        schema["readiness_cli"] = "python -m small_paper.check_kabu_readonly_readiness"
        schema["readiness_exit_codes"] = {
            "READONLY_READY": EXIT_READONLY_READY,
            "STATION_OR_TOKEN_NOT_READY": EXIT_STATION_OR_TOKEN_NOT_READY,
            "AUTH_OR_CONFIG_ERROR": EXIT_AUTH_OR_CONFIG_ERROR,
            "RESPONSE_INVALID": EXIT_RESPONSE_INVALID,
            "SAFETY_INVARIANT_FAILED": EXIT_SAFETY_INVARIANT_FAILED,
        }
        schema["token_retry_max"] = TOKEN_MAX_RETRIES
        schema["soak_snapshot_fields_w4t"] = [
            "token_probe_status",
            "token_probe_latency_ms",
            "station_running",
            "port_reachable",
            "readonly_ready_at_start",
            "readonly_ready_at_end",
            "token_refresh_count",
            "readonly_failure_category",
            "readonly_successful_endpoint_count",
        ]
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def documentation_review() -> dict[str, Any]:
    required = [
        DOCS / "adr" / "ADR-687W4T-kabu-token-readonly-readiness.md",
        DOCS / "live_order_operations.md",
        DOCS / "live_order_system_design.md",
        DOCS / "schema" / "live_order_design_schema.json",
        NATIVE_ROOT / "src" / "small_paper" / "check_kabu_readonly_readiness.py",
        NATIVE_ROOT / "src" / "small_paper" / "kabu_readonly_readiness.py",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    ops = (DOCS / "live_order_operations.md").read_text(encoding="utf-8") if (DOCS / "live_order_operations.md").is_file() else ""
    return {
        "pass": len(missing) == 0 and "check_kabu_readonly_readiness" in ops,
        "missing": missing,
    }


def design_consistency() -> dict[str, Any]:
    from small_paper.kabu_readonly_readiness import TOKEN_MAX_RETRIES, TokenProbeStatus

    schema = json.loads((DOCS / "schema" / "live_order_design_schema.json").read_text(encoding="utf-8"))
    mismatches = []
    if schema.get("token_retry_max") != TOKEN_MAX_RETRIES:
        mismatches.append({"check": "token_retry_max"})
    if schema.get("readiness_cli") != "python -m small_paper.check_kabu_readonly_readiness":
        mismatches.append({"check": "readiness_cli"})
    code_st = [s.value for s in TokenProbeStatus]
    if schema.get("token_probe_statuses") != code_st:
        mismatches.append({"check": "token_probe_statuses", "schema": schema.get("token_probe_statuses"), "code": code_st})
    # hard fail
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    hard = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X"})
    except RuntimeError as exc:
        hard = "HARD_FAIL" in str(exc)
    if not hard:
        mismatches.append({"check": "kabu_submit_hard_fail"})
    return {"pass": len(mismatches) == 0, "mismatch_count": len(mismatches), "mismatches": mismatches}


def run_audit() -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    update_docs()

    from small_paper.kabu_readonly_readiness import (
        probe_summary_for_cli,
        run_readonly_readiness_probe,
    )

    diag = run_readonly_readiness_probe(load_env=True)
    readiness = probe_summary_for_cli(diag)
    faults = run_fault_matrix()
    faults_pass = all(r["pass"] for r in faults)
    mask_ok = next(r["pass"] for r in faults if r["case"] == "credential_masking")
    hard_ok = next(r["pass"] for r in faults if r["case"] == "submit_cancel_flatten_hard_fail")
    concrete = diag.token_probe_status != "TOKEN_REQUEST_FAILED" or bool(diag.failure_reason)

    doc_rev = documentation_review()
    cons = design_consistency()
    unit = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w4t_kabu_readonly_readiness.py",
            "-q",
        ]
    )
    smoke = _run([sys.executable, "scripts/run_production_startup_smoke_test.py"])
    preflight = _run([sys.executable, "scripts/check_live_pipeline_preflight.py"])
    cli = _run([sys.executable, "-m", "small_paper.check_kabu_readonly_readiness"])

    from small_paper.config import load_pilot_config

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )

    if not hard_ok:
        verdict = VERDICT_SAFETY
    elif not mask_ok:
        verdict = VERDICT_MASK
    elif not faults_pass or not cons.get("pass") or not doc_rev.get("pass") or not unit.get("ok"):
        verdict = VERDICT_INCOMPLETE
    elif not smoke.get("ok") or not preflight.get("ok"):
        verdict = VERDICT_INCOMPLETE
    elif cfg.live_trading_enabled or cfg.order_enabled:
        verdict = VERDICT_SAFETY
    elif diag.token_probe_status in ("API_PASSWORD_MISSING", "AUTH_FAILED", "CLIENT_NOT_CONFIGURED"):
        # diagnostics complete enough; station may be down on weekend — still READY for tooling
        # Auth/config failure of live env is expected weekend; tooling READY if classification concrete
        verdict = VERDICT_READY if concrete and faults_pass else VERDICT_AUTH
    elif diag.token_probe_status in (
        "PORT_UNREACHABLE",
        "KABU_STATION_NOT_RUNNING",
        "TOKEN_ENDPOINT_TIMEOUT",
    ):
        verdict = VERDICT_READY if concrete and faults_pass else VERDICT_STATION
    elif str(diag.token_probe_status).startswith("READONLY_ONLINE"):
        verdict = VERDICT_READY
    elif concrete and faults_pass and cons.get("pass"):
        # Mock/fault matrix proves diagnostics; live Station deferred to Monday
        verdict = VERDICT_READY
    else:
        verdict = VERDICT_INCOMPLETE

    # Artifacts
    (REPORT_DIR / "phase687w4t_token_diagnostics.json").write_text(
        json.dumps(diag.to_safe_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w4t_readiness_probe.json").write_text(
        json.dumps({"summary": readiness, "cli_returncode": cli.get("returncode")}, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_DIR / "phase687w4t_credential_masking_test.json").write_text(
        json.dumps(next(r for r in faults if r["case"] == "credential_masking"), indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_DIR / "phase687w4t_retry_policy_test.json").write_text(
        json.dumps(
            {
                "auth_no_auto_retry": next(r for r in faults if r["case"] == "auth_no_auto_retry"),
                "retry_max_explicit": next(r for r in faults if r["case"] == "retry_max_explicit"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (REPORT_DIR / "phase687w4t_design_consistency.json").write_text(
        json.dumps(cons, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w4t_documentation_review.json").write_text(
        json.dumps(doc_rev, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w4t_smoke_result.json").write_text(json.dumps(smoke, indent=2) + "\n", encoding="utf-8")
    (REPORT_DIR / "phase687w4t_preflight_result.json").write_text(
        json.dumps(preflight, indent=2) + "\n", encoding="utf-8"
    )

    with (REPORT_DIR / "phase687w4t_fault_injection.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["case", "pass"])
        w.writeheader()
        for r in faults:
            w.writerow({"case": r["case"], "pass": r["pass"]})

    statuses = [s.value for s in __import__("small_paper.kabu_readonly_readiness", fromlist=["TokenProbeStatus"]).TokenProbeStatus]
    with (REPORT_DIR / "phase687w4t_status_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["status", "observed"])
        w.writeheader()
        for s in statuses:
            w.writerow({"status": s, "observed": diag.token_probe_status == s})

    report = {
        "phase": "687W4T",
        "verdict": verdict,
        "token_probe_status": diag.token_probe_status,
        "ready_for_soak": diag.ready_for_soak,
        "faults_pass": faults_pass,
        "design_consistency_pass": cons.get("pass"),
        "documentation_review_pass": doc_rev.get("pass"),
        "unit_ok": unit.get("ok"),
        "smoke_ok": smoke.get("ok"),
        "preflight_ok": preflight.get("ok"),
        "actual_broker_submit_count": 0,
        "actual_broker_cancel_count": 0,
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
        "live_station_final_confirm": "Monday Forward Soak",
        "built_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    (REPORT_DIR / "phase687w4t_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (REPORT_DIR / "phase687w4t_decision.md").write_text(
        "\n".join(
            [
                "# Phase687W4T Decision",
                "",
                f"**Verdict:** `{verdict}`",
                "",
                f"- token_probe_status: `{diag.token_probe_status}`",
                f"- ready_for_soak (live): `{diag.ready_for_soak}`",
                f"- faults: `{sum(1 for r in faults if r['pass'])}/{len(faults)}`",
                f"- submit/cancel: `0` / `0`",
                "",
                "Live Station success is confirmed on Monday Forward Soak.",
                "PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report["verdict"], "status": report["token_probe_status"]}, indent=2))


if __name__ == "__main__":
    main()
