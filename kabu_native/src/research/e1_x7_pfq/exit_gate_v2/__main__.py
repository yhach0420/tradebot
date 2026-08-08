"""CLI: EXIT Gate Reconciliation V2 — corrective rerun; no EXIT revision."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from research.e1_x7_pfq.exit_gate_v2.publish import publish
from research.e1_x7_pfq.exit_gate_v2.run_reconcile import PUBLISH, reapply_gates_with_ab, run_once


def _run_tests() -> dict:
    native = Path(__file__).resolve().parents[4]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{native / 'src'};{native / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short",
            "-p", "no:cacheprovider",
            str(native / "tests" / "test_e1_x7_pfq_exit_gate_v2.py"),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(native), env=env, timeout=180,
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
        "tail": (proc.stdout or "")[-2000:],
    }


def main() -> int:
    print("=== EXIT Gate V2 tests ===", flush=True)
    tests = _run_tests()
    print("tests", tests["passed"], "/", tests["total"], "exit", tests["exit_code"], flush=True)
    if tests["exit_code"] != 0:
        print(tests["tail"])
        return 1

    print("=== Run A ===", flush=True)
    report_a = run_once(label="A", ab_ok_placeholder=True)
    if report_a.get("verdict") == "E1_X7_PFQ_EXIT_GATE_IDENTITY_MISMATCH":
        PUBLISH.mkdir(parents=True, exist_ok=True)
        (PUBLISH / "report.json").write_text(json.dumps(report_a, indent=2, default=str), encoding="utf-8")
        print("STOP IDENTITY MISMATCH", flush=True)
        return 2

    print("=== Run B ===", flush=True)
    report_b = run_once(label="B", ab_ok_placeholder=True)

    keys = [
        "identity_sha",
        "denominator_sha",
        "repairable_subset_sha",
        "profitable_opportunity_cost_sha",
        "pair_gate",
        "selected_baseline",
        "verdict",
    ]
    sa = report_a.get("determinism_shas") or {}
    sb = report_b.get("determinism_shas") or {}
    mismatches = [k for k in keys if sa.get(k) != sb.get(k)]
    ab_ok = len(mismatches) == 0
    det = {"ab_match": ab_ok, "mismatches": mismatches, "A": sa, "B": sb}
    print("A/B", ab_ok, mismatches, flush=True)

    report_a = reapply_gates_with_ab(report_a, ab_ok=ab_ok)

    # Assert subset invariant on published repairable sheet
    for r in (report_a.get("_sheets") or {}).get("RepairableLoss") or []:
        assert r.get("in_denominator") is True

    print("=== Publish (V2 dir; V1 untouched) ===", flush=True)
    shas = publish(report_a, tests, det, PUBLISH)
    print("published", PUBLISH, shas, flush=True)
    print("VERDICT", report_a.get("verdict"), flush=True)
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
