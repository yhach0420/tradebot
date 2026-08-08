"""CLI entry: run Bridge Audit V2 A/B, tests, publish; then stop."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from research.e1_x7_pfq.bridge_v2.publish import publish
from research.e1_x7_pfq.bridge_v2.run_bridge import PUBLISH, run_once


def _run_tests() -> dict:
    native = Path(__file__).resolve().parents[4]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{native / 'src'};{native / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-rA", "--tb=line",
            "-p", "no:cacheprovider",
            str(native / "tests" / "test_e1_x7_pfq_bridge_v2.py"),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(native), env=env, timeout=300,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        for st in ("PASSED", "FAILED", "ERROR"):
            if line.strip().startswith(st + " "):
                rows.append({"test": line.strip().split(" ", 1)[1].split(" - ")[0], "outcome": st})
                break
    return {
        "exit_code": proc.returncode,
        "passed": sum(1 for r in rows if r["outcome"] == "PASSED"),
        "failed": sum(1 for r in rows if r["outcome"] != "PASSED"),
        "total": len(rows),
        "rows": rows,
        "tail": (proc.stdout or "")[-2500:],
    }


def main() -> int:
    print("=== Bridge V2 tests ===", flush=True)
    tests = _run_tests()
    print("tests", tests["passed"], "/", tests["total"], "exit", tests["exit_code"], flush=True)
    if tests["exit_code"] != 0:
        print(tests["tail"])
        # continue to produce audit with test failure note — but prefer fail-fast for unit issues
        # Unit tests should pass before long run
        print("WARN: unit tests failed; abort before heavy run", flush=True)
        return 1

    print("=== Run A ===", flush=True)
    report_a = run_once(label="A")
    if report_a.get("verdict") in (
        "E1_X7_PFQ_BRIDGE_IDENTITY_MISMATCH",
        "E1_X7_PFQ_JOINT_REPLAY_IDENTITY_MISMATCH",
    ):
        print("STOP", report_a.get("verdict"), flush=True)
        PUBLISH.mkdir(parents=True, exist_ok=True)
        (PUBLISH / "report.json").write_text(json.dumps(report_a, indent=2, default=str), encoding="utf-8")
        (PUBLISH / "report.md").write_text(f"# STOP\n\n{report_a.get('verdict')}\n", encoding="utf-8")
        return 2

    print("=== Run B ===", flush=True)
    report_b = run_once(label="B")

    keys = [
        "identity_sha",
        "candidate_membership_sha",
        "matched_parent_sha",
        "event_time_outcome_sha",
        "fixed_grid_outcome_sha",
        "first_touch_sha",
        "counterfactual_sha",
        "failure_classification_sha",
        "verdict",
    ]
    sa = report_a.get("determinism_shas") or {}
    sb = report_b.get("determinism_shas") or {}
    mismatches = [k for k in keys if sa.get(k) != sb.get(k)]
    det = {
        "ab_match": len(mismatches) == 0,
        "mismatches": mismatches,
        "A": sa,
        "B": sb,
    }
    print("A/B match", det["ab_match"], mismatches, flush=True)

    # annotate source prospective status (non-destructive pointer)
    src = Path.home() / "e1x6_research_store" / "e1_x7_pfq" / "e1x7_pfq_20260804_080510"
    ptr = {
        "prospective_status": "BLOCKED_PENDING_REALIZABILITY_BRIDGE_AUDIT",
        "bridge_analysis_id": "E1_X7_PFQ_REALIZABILITY_BRIDGE_AUDIT_V2",
        "bridge_run_id": report_a.get("run_id"),
        "bridge_verdict": report_a.get("verdict"),
    }
    (src / "PROSPECTIVE_STATUS.json").write_text(json.dumps(ptr, indent=2), encoding="utf-8")

    print("=== Publish ===", flush=True)
    shas = publish(report_a, tests, det, PUBLISH)
    print("published", PUBLISH, shas, flush=True)
    print("VERDICT", report_a.get("verdict"), flush=True)
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
