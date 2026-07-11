"""Phase687W7A2 — W4S session seal propagation integrity audit."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w7a2_w4s_seal_propagation"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "W4S_SEAL_PROPAGATION_FIXED"
VERDICT_MISMATCH = "SEAL_SNAPSHOT_MISMATCH"
VERDICT_ORDER = "FINALIZE_ORDER_INVALID"
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


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.config import load_pilot_config
    from small_paper.kabu_order_request_builder import actual_broker_submit_count
    from small_paper.live_order_safety_sm import KabuBrokerAdapter
    from small_paper.stateful_journal_recovery import PRODUCTION_ORDER_ENABLEMENT, SCHEMA_VERSION, w4s_ready_extra_ok
    from small_paper.w4s_seal_propagation import (
        SEAL_PROPAGATION_VERSION,
        build_synthetic_full_seal_session,
        compare_seal_snapshot,
        finalize_session_seal_propagation,
        run_negative_seal_mismatch_tests,
        w4s_seal_success_ok,
    )

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w7a2_w4s_seal_propagation.py",
            "tests/test_phase687w7a1_recovery_assertion_integrity.py",
            "tests/test_phase687w7a_stateful_recovery.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w7a2_smoke_result.json", smoke)

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7a2_preflight_result.json", preflight)

    import shutil
    import tempfile

    synth_root = Path(tempfile.mkdtemp(prefix="w7a2_synth_", dir=str(REPORT_DIR)))
    try:
        built = build_synthetic_full_seal_session(synth_root, session_id="W7A2-AUDIT")
        snap = built["snapshot"]
        seal = built["seal"]
        cmp = compare_seal_snapshot(snap, seal, verified=True, post_mutation=False)

        _wj(
            "phase687w7a2_corrected_w4s_snapshot.json",
            {
                "session_seal_status": snap.get("session_seal_status"),
                "session_seal_entry_count": snap.get("session_seal_entry_count"),
                "session_seal_required_count": snap.get("session_seal_required_count"),
                "required_artifact_missing_count": snap.get("required_artifact_missing_count"),
                "session_seal_verified": snap.get("session_seal_verified"),
                "session_seal_generated_at": snap.get("session_seal_generated_at"),
                "session_seal_schema_version": snap.get("session_seal_schema_version"),
                "session_seal_manifest_sha256": snap.get("session_seal_manifest_sha256"),
                "post_seal_mutation_detected": snap.get("post_seal_mutation_detected"),
                "seal_propagation_status": snap.get("seal_propagation_status"),
                "seal_propagation_version": SEAL_PROPAGATION_VERSION,
                "pass": built["pass"],
            },
        )
        _wj(
            "phase687w7a2_seal_snapshot_comparison.json",
            {
                "comparison": cmp,
                "seal_entry_count": seal.get("entry_count"),
                "seal_required_count": seal.get("required_count"),
                "snapshot_entry_count": snap.get("session_seal_entry_count"),
                "mismatch_count": cmp["mismatch_count"],
                "w4s_seal_success_ok": w4s_seal_success_ok(snap, seal),
                "w4s_ready_extra_ok": w4s_ready_extra_ok(snap),
                "pass": cmp["pass"] and built["pass"],
            },
        )

        neg = run_negative_seal_mismatch_tests(good_snap=snap, good_seal=seal)
        _wj("phase687w7a2_negative_mismatch_tests.json", neg)

        dup = finalize_session_seal_propagation(
            synth_root,
            safety_dir=synth_root / "live_order_safety",
            session_id="W7A2-AUDIT",
        )
        pilot_txt = (NATIVE_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
        order_ok = (
            "write_soak_session_snapshot" in pilot_txt
            and "finalize_session_manifest" in pilot_txt
            and "finalize_session_seal_propagation" in pilot_txt
            and pilot_txt.find("write_soak_session_snapshot")
            < pilot_txt.find("finalize_session_manifest")
            < pilot_txt.find("finalize_session_seal_propagation")
        )
        _wj(
            "phase687w7a2_finalize_order_test.json",
            {
                "finalize_order": built["finalize"].get("finalize_order"),
                "duplicate_finalize": dup.get("duplicate_finalize"),
                "duplicate_pass": dup.get("pass"),
                "pilot_order_ok": order_ok,
                "circular_dependency_policy": (
                    "pre-seal snapshot hashed in seal; seal metadata overlay updates snapshot; "
                    "final_snapshot_sha256 recorded on session_seal only; manifest not rewritten post-seal"
                ),
                "pass": order_ok and bool(dup.get("duplicate_finalize")) and bool(dup.get("pass")),
            },
        )
    finally:
        shutil.rmtree(synth_root, ignore_errors=True)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w7a2_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W7A2-w4s-seal-propagation.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "adr_mentions_propagation": "propagation" in adr.read_text(encoding="utf-8").lower() if adr.is_file() else False,
        "system_design": "Phase687W7A2" in (DOCS / "live_order_system_design.md").read_text(encoding="utf-8"),
        "operations": "687W7A2" in (DOCS / "live_order_operations.md").read_text(encoding="utf-8"),
        "pass": False,
    }
    doc_rev["pass"] = all(
        [doc_rev["adr_present"], doc_rev["adr_mentions_propagation"], doc_rev["system_design"], doc_rev["operations"]]
    )
    _wj("phase687w7a2_documentation_review.json", doc_rev)

    hard = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
    except RuntimeError as exc:
        hard = "HARD_FAIL" in str(exc)

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "propagation_14": built["pass"] and snap.get("session_seal_entry_count") == 14,
        "cross_artifact": cmp["pass"] and cmp["mismatch_count"] == 0,
        "negative_mismatch": neg.get("pass") is True,
        "finalize_order": json.loads((REPORT_DIR / "phase687w7a2_finalize_order_test.json").read_text(encoding="utf-8")).get(
            "pass"
        ),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "submit_zero": actual_broker_submit_count() == 0 and hard,
    }

    if not checks["finalize_order"]:
        verdict = VERDICT_ORDER
    elif not checks["cross_artifact"] or not checks["propagation_14"]:
        verdict = VERDICT_MISMATCH
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not all(checks.values()):
        verdict = VERDICT_MISMATCH
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W7A2",
        "verdict": verdict,
        "checks": checks,
        "seal_propagation_version": SEAL_PROPAGATION_VERSION,
        "schema_version": SCHEMA_VERSION,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "live_trading_enabled": False,
        "order_enabled": False,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7a2_report.json", report)
    (REPORT_DIR / "phase687w7a2_decision.md").write_text(
        f"""# Phase687W7A2 Decision

**Verdict:** `{verdict}`

## Fix
- session_seal.json is Source of Truth for seal fields
- Finalize: pre-seal snapshot → manifest → full seal → verify → propagate → resave snapshot
- final_snapshot_sha256 on seal only (manifest not rewritten post-seal)
- W4S success rejects entry_count 0 / mismatch / unverified / mutated

## Absolute gates
- submit/cancel/resubmit=0
- PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
