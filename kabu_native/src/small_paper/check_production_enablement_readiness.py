"""CLI: python -m small_paper.check_production_enablement_readiness

Exit codes:
  0  technical conditions PASS but still NOT_AUTHORIZED (does NOT enable orders)
  2  soak insufficient
  3  capability / policy / approval insufficiency
  4  reconciliation / safety failure
  5  design / config mismatch

Never mutates live_trading_enabled / order_enabled.
Never calls production write adapters for submit.
Never authorizes canary execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_evidence_json(path: Path):
    from small_paper.production_enablement_gate import evidence_from_mapping

    data = json.loads(path.read_text(encoding="utf-8"))
    if "evidence" in data and isinstance(data["evidence"], dict):
        data = data["evidence"]
    return evidence_from_mapping(data)


def main(argv: list[str] | None = None) -> int:
    from small_paper.production_enablement_gate import (
        evaluate_production_enablement,
        probe_current_workspace,
        technical_pass_evidence_not_authorized,
    )

    parser = argparse.ArgumentParser(
        description="Production enablement governance gate (fail-closed; no write adapter)."
    )
    parser.add_argument(
        "--evidence",
        type=str,
        default="",
        help="Optional JSON evidence file. Default: fail-closed workspace probe.",
    )
    parser.add_argument(
        "--demo-technical-pass",
        action="store_true",
        help="Evaluate synthetic all-green technical evidence with NOT_AUTHORIZED approval (tests only).",
    )
    args = parser.parse_args(argv)

    if args.demo_technical_pass:
        result = evaluate_production_enablement(technical_pass_evidence_not_authorized())
        result["probe_mode"] = "demo_technical_pass"
        result["flags_mutated"] = False
    elif args.evidence:
        evidence = _load_evidence_json(Path(args.evidence))
        result = evaluate_production_enablement(evidence)
        result["probe_mode"] = "evidence_file"
        result["flags_mutated"] = False
    else:
        result = probe_current_workspace()

    # Absolute: never claim production ready from this CLI in W6
    result["production_ready"] = False
    result["order_enabled"] = False
    result["live_trading_enabled"] = False
    result["canary_execution_forbidden"] = True
    result["production_order_enablement"] = "NOT_AUTHORIZED / NOT_IMPLEMENTED"

    summary = {
        "blocker_count": result.get("blocker_count"),
        "blockers": result.get("blockers"),
        "soak_status": result.get("soak_status"),
        "provenance_status": result.get("provenance_status"),
        "capability_status": result.get("capability_status"),
        "policy_status": result.get("policy_status"),
        "reconciliation_status": result.get("reconciliation_status"),
        "latency_status": result.get("latency_status"),
        "approval_status": result.get("approval_status"),
        "production_ready": result.get("production_ready"),
        "write_adapter_present": result.get("write_adapter_present"),
        "submit_hard_fail": result.get("submit_hard_fail"),
        "exit_code": result.get("exit_code"),
        "flags_mutated": False,
        "production_order_enablement": result.get("production_order_enablement"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(result.get("exit_code", 5))


if __name__ == "__main__":
    raise SystemExit(main())
