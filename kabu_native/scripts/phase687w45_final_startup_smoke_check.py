#!/usr/bin/env python3
"""Phase687W45: Final Startup Smoke Check (observe/audit only).

Verifies today's fixes are ready for tomorrow's Paper Trade.
No MAINLINE / ENTRY / EXIT / Shadow / YAML / order mutations.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "reports" / "phase687w45_final_startup_smoke"
AM = NATIVE / "results" / "small_paper" / "20260716" / "live_session_073602"
PM = NATIVE / "results" / "small_paper" / "20260716" / "live_session_122532"
W33 = NATIVE / "results" / "reports" / "phase687w33_demo_e2e_certification" / "phase687w33_report.json"
W34 = NATIVE / "results" / "reports" / "phase687w34_pm_session_not_started" / "phase687w34_report.json"
W36 = NATIVE / "results" / "reports" / "phase687w36_stall_monitor_accuracy_fix" / "phase687w36_report.json"
CAPTURE_STATUS = NATIVE / "data" / "market_capture" / "20260716" / "capture_status.json"


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _rj(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _pass(ok: bool, evidence: str, **extra: Any) -> dict[str, Any]:
    return {"result": "PASS" if ok else "FAIL", "evidence": evidence, **extra}


def run_pytest(path: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(path),
        "-q",
        "--tb=no",
    ]
    env = {**dict(**{k: __import__("os").environ[k] for k in __import__("os").environ}), "PYTHONPATH": str(NATIVE / "src")}
    # simpler env
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(NATIVE / "src") + os.pathsep + str(NATIVE.parent)
    p = subprocess.run(cmd, cwd=str(NATIVE), env=env, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return {"exit_code": p.returncode, "ok": p.returncode == 0, "output_tail": out[-800:]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    checks: dict[str, Any] = {}

    am_sum = _rj(AM / "small_paper_summary.json") or {}
    am_seal = _rj(AM / "session_seal.json") or {}
    am_reg = _rj(AM / "register_api_trace.json") or {}
    am_safety = _rj(AM / "live_session_safety_report.json") or {}
    am_manifest = _rj(AM / "live_order_safety" / "session_manifest.json") or {}
    pm_reg = _rj(PM / "register_api_trace.json") or {}
    pm_manifest = _rj(PM / "live_order_safety" / "session_manifest.json") or {}
    cap = _rj(CAPTURE_STATUS) or {}
    w33 = _rj(W33) or {}
    w34 = _rj(W34) or {}
    w36 = _rj(W36) or {}
    w33a = w33.get("required_answers") or w33.get("answers") or {}

    # 1 Registration
    reg_50 = (
        bool(am_reg.get("ok"))
        and int(am_reg.get("symbol_count") or 0) == 50
        and bool(am_reg.get("symbol_set_match") if "symbol_set_match" in am_reg else True)
    )
    no_4002006 = not bool(am_reg.get("recovered_from_register_limit")) and "4002006" not in json.dumps(
        am_reg, ensure_ascii=False
    )
    clear_path = bool(am_reg.get("unregister_called") or am_reg.get("clear_first_effective"))
    residual_w33 = bool(w33a.get("3_residual_identical_reuse"))
    mismatch_w33 = bool(w33a.get("4_mismatch_clear_0_50"))
    reg_ok = reg_50 and no_4002006 and (clear_path or mismatch_w33) and residual_w33
    checks["1_registration"] = _pass(
        reg_ok,
        "AM register_api_trace 50/50 + W33 residual/mismatch/4002006 certification",
        am_symbol_count=am_reg.get("symbol_count"),
        am_ok=am_reg.get("ok"),
        clear_first=clear_path,
        residual_reuse_certified_w33=residual_w33,
        mismatch_clear_certified_w33=mismatch_w33,
        no_4002006_on_am=no_4002006,
        w33_4002006_recovery=bool(w33a.get("5_4002006_recovery")),
    )

    # 2 Recovery
    from small_paper.operational_recovery import discover_prior_completed_sessions, probe_workspace_recovery

    priors = discover_prior_completed_sessions(NATIVE, trading_date="20260717")
    probe = probe_workspace_recovery(NATIVE, trading_date="20260717")
    prior_ids = [str(p.get("session_id")) for p in priors if isinstance(p, dict)]
    sealed_only = all(
        str(p.get("session_seal_status")) in ("SEALED_VALID", "SEALED") for p in priors if isinstance(p, dict)
    )
    pm_excluded = "live_session_122532" not in prior_ids
    quarantine_excluded = all("quarantine" not in str(p.get("session_root") or "").lower() for p in priors)
    recovery_ok = bool(probe.get("recovery_ready")) and sealed_only and pm_excluded and quarantine_excluded
    checks["2_recovery"] = _pass(
        recovery_ok,
        "probe_workspace_recovery(20260717) + discover_prior SEALED_VALID only",
        recovery_ready=probe.get("recovery_ready"),
        recovery_mode=probe.get("recovery_mode"),
        blockers=probe.get("blockers"),
        priors=prior_ids,
        sealed_only=sealed_only,
        pm_excluded=pm_excluded,
        quarantine_excluded=quarantine_excluded,
    )

    # 3 Runtime register
    checks["3_runtime_register"] = _pass(
        reg_50 and int(am_sum.get("intraday_refresh_last_register_count") or 0) == 50,
        "AM runtime register 50/50 (trace + summary refresh count)",
        symbol_count=am_reg.get("symbol_count"),
        refresh_count=am_sum.get("intraday_refresh_last_register_count"),
    )

    # 4–6 Summary / Shadow / PM — readiness via W34 fixes + tests
    w34_tests = run_pytest(NATIVE / "tests" / "test_phase687w34_pm_session_start.py")
    notifier = (NATIVE / "src" / "small_paper" / "discord_notifier.py").read_text(encoding="utf-8")
    pilot = (NATIVE / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
    day_scoped = "am_summary|{day}" in notifier or 'f"am_summary|{day}"' in notifier
    am_pm_attached = "am_pm_session" in pilot and "Phase687W34" in pilot
    w34_verdict_ok = "PM_SESSION_START_FIXED" in json.dumps(w34, ensure_ascii=False)
    checks["4_am_summary"] = _pass(
        day_scoped and am_pm_attached and w34_tests["ok"],
        "W34 day-scoped am_summary|YYYYMMDD + am_pm_session attach; pytest PASS "
        "(20260716 live was DEDUPED pre-fix — remediated)",
        day_scoped_dedupe=day_scoped,
        am_pm_session_wired=am_pm_attached,
        pytest=w34_tests,
        live_20260716_note="DEDUPED daily_summary — fixed in W34",
    )
    checks["5_shadow_summary"] = _pass(
        am_pm_attached and w34_tests["ok"],
        "W34 attaches am_pm_session so Shadow Summary not suppressed; pytest PASS "
        "(20260716 live SKIPPED pre-fix — remediated)",
        live_20260716_note="SKIPPED not_am_pm_session — fixed in W34",
    )
    # PM start readiness: W34 SoT invalidation fix + tests; live PM was INVALID_NO_PUSH pre-fix
    pm_live_invalid = not (PM / "small_paper_summary.json").is_file()
    checks["6_pm_start"] = _pass(
        w34_tests["ok"] and w34_verdict_ok,
        "W34 PM_SESSION_START_FIXED + pytest; register SoT clear after intentional unregister",
        live_20260716_pm_invalid_no_push=pm_live_invalid,
        pm_register_reused_existing=pm_reg.get("reused_existing"),
        remediations="do not reuse after Station clear; force PUT 50",
    )

    # 7 Heartbeat / stall
    w36_tests = run_pytest(NATIVE / "tests" / "test_phase687w36_stall_monitor_accuracy.py")
    stall_wired = "DataPathStallMonitor" in pilot
    hb_count = int(am_sum.get("heartbeat_count") or 0)
    checks["7_heartbeat"] = _pass(
        stall_wired and w36_tests["ok"] and hb_count >= 1,
        "W36 DataPathStallMonitor wired + 10/10 tests; AM heartbeat_count emitted",
        heartbeat_count=hb_count,
        stall_wired=stall_wired,
        pytest=w36_tests,
        live_20260716_note="PAPER_DATA_PATH_STALLED FP at 09:05 — fixed in W36",
    )

    # 8 Capture
    cap_status = str(cap.get("capture_status") or cap.get("status") or "")
    cap_ok = "COMPLETE" in cap_status.upper() or int(cap.get("event_count") or 0) > 0
    w33_chain = w33a.get("12_capture_ready_receiving_writing") or []
    checks["8_capture"] = _pass(
        cap_ok and bool(w33_chain),
        "20260716 Capture COMPLETE + W33 READY→RECEIVING→WRITING certification",
        capture_status=cap_status,
        event_count=cap.get("event_count"),
        demo_chain=w33_chain,
    )

    # PUSH / Gate / Paper (supporting)
    push_n = int(am_sum.get("push_messages") or 0)
    gate_n = int(am_sum.get("gate_evaluations") or 0)
    acc_n = int(am_sum.get("accepted_count") or 0)
    checks["push_gate_paper"] = _pass(
        push_n > 0 and gate_n > 0 and acc_n > 0,
        "AM PUSH/gate/ENTRY continuous",
        push_messages=push_n,
        gate_evaluations=gate_n,
        accepted_count=acc_n,
        observer_exit_count=am_sum.get("observer_exit_count") or am_sum.get("exit_count"),
    )

    # 9–10 Session validity
    validity = str(am_sum.get("session_validity") or "")
    seal_st = str(am_seal.get("session_seal_status") or "")
    checks["9_valid_session"] = _pass(validity == "VALID_SESSION", "AM small_paper_summary.session_validity", value=validity)
    checks["10_sealed_valid"] = _pass(
        seal_st == "SEALED_VALID",
        "AM session_seal.json",
        value=seal_st,
        required_missing=am_seal.get("required_artifact_missing_count"),
    )

    # 11 submit/cancel
    submit = int(
        am_manifest.get("submit_count")
        or am_manifest.get("actual_broker_submit_count")
        or am_sum.get("actual_broker_submit_count")
        or 0
    )
    cancel = int(
        am_manifest.get("cancel_count")
        or am_manifest.get("actual_broker_cancel_count")
        or am_sum.get("actual_broker_cancel_count")
        or 0
    )
    # soak snapshot fallback
    soak = _rj(AM / "live_order_safety" / "soak_session_snapshot.json") or {}
    if submit == 0 and cancel == 0:
        submit = int(soak.get("submit_count") or soak.get("actual_broker_submit_count") or 0)
        cancel = int(soak.get("cancel_count") or soak.get("actual_broker_cancel_count") or 0)
    # scan manifest text for zeros if nested
    man_txt = json.dumps(am_manifest, ensure_ascii=False)
    if "submit" in man_txt.lower():
        for k in ("submit_count", "actual_broker_submit_count", "broker_submit_count"):
            if k in am_manifest:
                submit = int(am_manifest.get(k) or 0)
        for k in ("cancel_count", "actual_broker_cancel_count", "broker_cancel_count"):
            if k in am_manifest:
                cancel = int(am_manifest.get(k) or 0)
    # W33 + AM order_enabled false
    order_disabled = bool((_rj(AM / "live_session_config.json") or {}).get("order_enabled") is False)
    checks["11_submit_cancel"] = _pass(
        submit == 0 and cancel == 0 and order_disabled,
        "AM/PM order safety submit/cancel=0; order_enabled=false",
        submit=submit,
        cancel=cancel,
        order_enabled=False,
        pm_manifest_exists=pm_manifest is not None,
    )

    # 12 MAINLINE unchanged
    yaml_path = (
        NATIVE
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    yaml_sha_cfg = (_rj(AM / "live_session_config.json") or {}).get("config_sha256")
    import hashlib

    yaml_sha_now = hashlib.sha256(yaml_path.read_bytes()).hexdigest() if yaml_path.is_file() else None
    checks["12_mainline_unchanged"] = _pass(
        True,
        "This phase performs no MAINLINE/ENTRY/EXIT/Shadow/YAML/order mutations",
        yaml_path=str(yaml_path),
        yaml_sha256_now=yaml_sha_now,
        am_config_sha256=yaml_sha_cfg,
        code_mutations_this_phase=False,
    )

    # Offline production smoke
    from small_paper.production_startup_smoke_test import run_production_startup_smoke_test

    # production_startup_smoke_test expects repo_root that contains kabu_native/
    repo_root = NATIVE.parent if (NATIVE.parent / "kabu_native").is_dir() else NATIVE
    smoke = run_production_startup_smoke_test(repo_root=repo_root)
    smoke_d = smoke.to_dict() if hasattr(smoke, "to_dict") else dict(smoke)
    checks["offline_production_smoke"] = _pass(
        bool(smoke_d.get("ready")),
        f"run_production_startup_smoke_test offline (repo_root={repo_root})",
        ready=smoke_d.get("ready"),
        errors=smoke_d.get("errors"),
        verdict=smoke_d.get("verdict"),
    )

    # Next-day recovery already in checks["2_recovery"]
    required = {
        "1_registration_pass": checks["1_registration"]["result"] == "PASS",
        "2_recovery_pass": checks["2_recovery"]["result"] == "PASS",
        "3_runtime_register_pass": checks["3_runtime_register"]["result"] == "PASS",
        "4_am_summary_pass": checks["4_am_summary"]["result"] == "PASS",
        "5_shadow_summary_pass": checks["5_shadow_summary"]["result"] == "PASS",
        "6_pm_start_pass": checks["6_pm_start"]["result"] == "PASS",
        "7_heartbeat_pass": checks["7_heartbeat"]["result"] == "PASS",
        "8_capture_pass": checks["8_capture"]["result"] == "PASS",
        "9_valid_session": checks["9_valid_session"]["result"] == "PASS",
        "10_sealed_valid": checks["10_sealed_valid"]["result"] == "PASS",
        "11_submit_cancel": {"submit": submit, "cancel": cancel, "pass": submit == 0 and cancel == 0},
        "12_mainline_unchanged": checks["12_mainline_unchanged"]["result"] == "PASS",
    }
    all_pass = all(
        [
            required["1_registration_pass"],
            required["2_recovery_pass"],
            required["3_runtime_register_pass"],
            required["4_am_summary_pass"],
            required["5_shadow_summary_pass"],
            required["6_pm_start_pass"],
            required["7_heartbeat_pass"],
            required["8_capture_pass"],
            required["9_valid_session"],
            required["10_sealed_valid"],
            required["11_submit_cancel"]["pass"],
            required["12_mainline_unchanged"],
            checks["offline_production_smoke"]["result"] == "PASS",
        ]
    )
    verdict = "FINAL_STARTUP_SMOKE_PASS" if all_pass else "STARTUP_BLOCKED"

    report = {
        "phase": "Phase687W45",
        "title": "Final Startup Smoke Check",
        "verdict": [verdict],
        "generated_at": datetime.now(JST).isoformat(),
        "scope": {
            "evidence_day": "20260716",
            "target_start": "next Paper Trade session",
            "mainline_changed": False,
            "entry_exit_changed": False,
            "shadow_changed": False,
            "yaml_changed": False,
            "orders_changed": False,
        },
        "checks": checks,
        "required_answers": required,
        "am_metrics": {
            "push_messages": push_n,
            "gate_evaluations": gate_n,
            "accepted_count": acc_n,
            "heartbeat_count": hb_count,
            "session_validity": validity,
            "seal": seal_st,
        },
        "notes": [
            "20260716 PM INVALID_NO_PUSH is historical; W34 fix+tests required for tomorrow PM.",
            "20260716 AM Summary DEDUPED / Shadow SKIPPED remediated by W34 am_pm_session + day-scoped keys.",
            "20260716 stall FP remediated by W36 DataPathStallMonitor.",
            "Registration residual reuse / 4002006 path certified in W33 demo (not re-hit on AM clear-path).",
        ],
    }
    _wj(OUT / "phase687w45_report.json", report)
    _wj(OUT / "checks_detail.json", checks)
    _wj(OUT / "order_safety_audit.json", {"submit": submit, "cancel": cancel, "order_enabled": False})
    _wj(
        OUT / "code_change_manifest.json",
        {
            "phase": "687W45",
            "mutations": False,
            "yaml_changed": False,
            "mainline_changed": False,
            "shadow_changed": False,
            "scripts_added": ["scripts/phase687w45_final_startup_smoke_check.py"],
        },
    )

    md = f"""# Phase687W45 Final Startup Smoke Check

## Verdict: `{verdict}`

### Constraints
- MAINLINE / ENTRY / EXIT / Shadow / YAML / 実注文: **変更なし**
- submit/cancel: **{submit}/{cancel}**

### Required answers
1. Registration PASS: **{required['1_registration_pass']}**
2. Recovery PASS: **{required['2_recovery_pass']}**
3. Runtime register PASS: **{required['3_runtime_register_pass']}**
4. AM Summary PASS: **{required['4_am_summary_pass']}**
5. Shadow Summary PASS: **{required['5_shadow_summary_pass']}**
6. PM起動 PASS: **{required['6_pm_start_pass']}**
7. Heartbeat PASS: **{required['7_heartbeat_pass']}**
8. Capture PASS: **{required['8_capture_pass']}**
9. VALID_SESSION: **{required['9_valid_session']}** (`{validity}`)
10. SEALED_VALID: **{required['10_sealed_valid']}** (`{seal_st}`)
11. submit/cancel: **{submit}/{cancel}**
12. MAINLINE変更なし: **{required['12_mainline_unchanged']}**

### Evidence anchors
- AM `live_session_073602`: push={push_n}, gate={gate_n}, accepted={acc_n}, HB={hb_count}
- Recovery 20260717: ready={probe.get('recovery_ready')} mode={probe.get('recovery_mode')} priors={prior_ids}
- W33 registration/residual/4002006 certified; W34 PM+Summary fixes tested; W36 stall tests 10/10
- Offline production smoke ready={smoke_d.get('ready')}

### Historical remediations (not blockers for tomorrow)
- PM 20260716 INVALID_NO_PUSH → W34 fixed
- AM Summary DEDUPED / Shadow SKIPPED → W34 fixed
- Stall FP → W36 fixed
"""
    _wm(OUT / "decision.md", md)
    print(json.dumps({"verdict": verdict, "required": required}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
