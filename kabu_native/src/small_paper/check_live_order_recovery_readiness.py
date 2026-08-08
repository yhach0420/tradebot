"""CLI: python -m small_paper.check_live_order_recovery_readiness

Exit codes:
  0  Dry-run recovery ready (does NOT authorize real orders)
  2  journal / reconciliation issue
  3  kill switch / operator ack issue
  4  disk / clock issue
  5  design / config mismatch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from small_paper.operational_recovery import (
        PRODUCTION_ORDER_ENABLEMENT,
        dryrun_ready_evidence,
        evaluate_recovery_readiness,
        probe_workspace_recovery,
    )

    parser = argparse.ArgumentParser(description="Live order operational recovery readiness (dry-run).")
    parser.add_argument(
        "--demo-ready",
        action="store_true",
        help="Evaluate synthetic dry-run-ready evidence (tests only).",
    )
    parser.add_argument(
        "--native-root",
        type=str,
        default="",
        help="Workspace root for fail-closed probe.",
    )
    args = parser.parse_args(argv)

    if args.demo_ready:
        result = evaluate_recovery_readiness(dryrun_ready_evidence())
        result["probe_mode"] = "demo_ready"
    else:
        root = Path(args.native_root) if args.native_root else Path(__file__).resolve().parents[2]
        result = probe_workspace_recovery(root)

    result["production_authorized"] = False
    result["canary_forbidden"] = True
    result["production_order_enablement"] = PRODUCTION_ORDER_ENABLEMENT
    result["flags_mutated"] = False
    result["live_trading_enabled"] = False
    result["order_enabled"] = False

    art = result.get("artifact_trace") or {}
    ref = art.get("reference_session") or {}
    prior_eval = art.get("prior_eval") or {}
    sha = art.get("config_sha") or {}
    design = art.get("design") or {}
    summary = {
        "session_manifest_valid": result.get("session_manifest_valid"),
        "session_seal_valid": result.get("session_seal_valid"),
        "journal_integrity": result.get("journal_integrity"),
        "recovery_mode": result.get("recovery_mode"),
        "kill_switch_state": result.get("kill_switch_state"),
        "reconciliation_state": result.get("reconciliation_state"),
        "disk_state": result.get("disk_state"),
        "clock_state": result.get("clock_state"),
        "operator_ack_status": result.get("operator_ack_status"),
        "production_flags": result.get("production_flags"),
        "write_adapter_present": result.get("write_adapter_present"),
        "submit_hard_fail": result.get("submit_hard_fail"),
        "recovery_ready": result.get("recovery_ready"),
        "blockers": result.get("blockers"),
        "exit_code": result.get("exit_code"),
        "probe_mode": result.get("probe_mode"),
        "design_consistency_pass": result.get("design_consistency_pass"),
        "config_sha_match": result.get("config_sha_match"),
        "selected_prior": {
            "session_root": ref.get("session_root"),
            "trading_day": ref.get("trading_day"),
            "session_id": ref.get("session_id"),
            "session_seal_status": ref.get("session_seal_status"),
            "reconciliation_state": prior_eval.get("reconciliation_state"),
            "reconciliation_classification": (prior_eval.get("detail") or {}).get(
                "reconciliation_classification"
            ),
        }
        if ref
        else None,
        "prior_sessions_found": art.get("prior_sessions_found"),
        "config_sha": {
            "match": sha.get("match"),
            "disk_sha256": sha.get("disk_sha256"),
            "pin_sha256": sha.get("pin_sha256"),
            "status": sha.get("status"),
        },
        "design_consistency": {
            "pass": design.get("pass"),
            "status": design.get("status"),
        },
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "flags_mutated": False,
        "live_trading_enabled": False,
        "order_enabled": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(result.get("exit_code", 5))


if __name__ == "__main__":
    raise SystemExit(main())
